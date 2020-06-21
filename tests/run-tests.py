#!/usr/bin/env python3

"""Unit tests for Git scripts."""

import argparse
from collections import namedtuple
import importlib
import importlib.machinery
import importlib.util
import io
import os
import re
import shlex
import subprocess
import sys
import unittest
import unittest.mock


script_dir = os.path.dirname(__file__)


def import_file(file_path, module_name=None):
    """
    Imports a Python module from a file path.

    Unlike normal `import`, allows importing from files that have `-`
    characters in their names and that do not end with a `.py` extension.

    If `module_name` is not specified, the module name will be derived from
    the filename, replacing any `-` characters with `_`s.
    """
    # Derived from: <https://stackoverflow.com/a/56090741/>.
    if not module_name:
        (stem, _extension) = os.path.splitext(os.path.basename(file_path))
        module_name = stem.replace("-", "_")

    loader = importlib.machinery.SourceFileLoader(module_name, file_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    globals()[module_name] = module
    sys.modules[module_name] = module

    # Add the module path in case it imports other local modules.
    module_dir = os.path.dirname(file_path)
    if module_dir not in sys.path:
        sys.path.insert(1, module_dir)


# To suppress lint warnings about using undefined variables.
gitutils = None
git_have_commit = None
git_next = None
import_file(os.path.join(script_dir, "../gitutils.py"))


def args_to_command_line(args):
    """Converts a list of arguments to a single command-line string."""
    return " ".join((shlex.quote(a) for a in args))


class FakeRunCommand:
    """A class to manage faked calls to `gitutils.run_command`."""
    class FakeRunResult:
        """
        Stores a predetermined result for a faked command.

        `return_code` specifies the exit code to be returned by the command.

        `stdout` and `stderr` are strings that specify the faked output from
        the command.

        `action` specifies a callback to invoke when the faked command is
        executed.
        """
        def __init__(self, return_code=0, stdout=None, stderr=None,
                     action=None):
            self.return_code = return_code
            self.stdout = stdout
            self.stderr = stderr
            self.action = action

    def __init__(self):
        self.fake_results = {}
        self.fake_results_re = []

    def set_fake_result(self, command_line, **kwargs):
        """
        Sets the predetermined result for the specified command-line.

        `kwargs` is passed through to `FakeRunResult`.
        """
        self.fake_results[command_line] = self.FakeRunResult(**kwargs)

    def set_fake_result_re(self, command_pattern, **kwargs):
        """
        Like `set_fake_result`, but sets predetermined results for all
        command-lines that match the specified regular expression.
        """
        self.fake_results_re.append((re.compile(command_pattern),
                                     self.FakeRunResult(**kwargs)))

    def __call__(self, *args, **kwargs):
        command_line = args_to_command_line((*args[0],))
        result = self.fake_results.get(command_line)
        match = None
        if result is None:
            for (regexp, r) in self.fake_results_re:
                match = regexp.match(command_line)
                if match:
                    result = r
                    break
        if result is None:
            print(self.fake_results)
            raise NotImplementedError(f"No results faked for command: "
                                      f"{command_line}")

        if result.action:
            result.action(match, result)

        return subprocess.CompletedProcess(args[0],
                                           result.return_code,
                                           stdout=result.stdout,
                                           stderr=result.stderr)


def run_script(script, *args):
    """Executes the `main` function from the specified script module."""
    script.__name__ = os.path.basename(script.__file__)
    return script.main([script.__name__, *args])


IOResults = namedtuple("IOResults", ["return_code", "stdout", "stderr"])


@unittest.mock.patch("sys.stdin", new_callable=io.StringIO)
@unittest.mock.patch("sys.stderr", new_callable=io.StringIO)
@unittest.mock.patch("sys.stdout", new_callable=io.StringIO)
# `input` is the most sensible name, and it matches what the `subprocess`
# module uses.
def call_with_io(callee, mock_stdout=None, mock_stderr=None, mock_stdin=None, *, input=None):  # pylint: disable=redefined-builtin
    """
    Invokes the callable specified by `callee`, capturing and returning stdout
    and stderr output.

    `input`, if specified, will be used as fake input to stdin.

    Returns an `IOResults` with the result of the invocation.
    """
    # These arguments are not actually optional, but to avoid bogus pylint
    # complaints (see <https://stackoverflow.com/a/62252941/>), we have to make
    # them optional and then sanity-check them at runtime.
    assert mock_stdout
    assert mock_stderr
    assert mock_stdin

    if input is not None:
        mock_stdin.write(input)
        mock_stdin.seek(0)

    return_code = callee()
    return IOResults(return_code=return_code,
                     stdout=mock_stdout.getvalue(),
                     stderr=mock_stderr.getvalue())


class TestGitCommand(unittest.TestCase):
    """A base class for tests that use faked `git` commands."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fake_run_command = FakeRunCommand()

    def setUp(self):
        gitutils.run_command = self.fake_run_command

        def fake_git_commit_hash_action(match, result):
            result.stdout = match.group("commitish")

        def fake_git_checkout_action(match, result):
            commitish = match.group("commitish")
            self.set_fake_git_head(commitish)
            result.stdout = f"HEAD is now at {commitish}"

        self.fake_run_command.set_fake_result_re(
            r"git rev-parse --verify (--short )?(--quiet )?(?P<commitish>.+)",
            action=fake_git_commit_hash_action)

        self.fake_run_command.set_fake_result_re(
            r"git checkout --detach (?P<commitish>.+)",
            action=fake_git_checkout_action)

    def set_fake_git_head(self, commitish):
        """Fakes the current Git HEAD commit."""
        self.fake_run_command.set_fake_result(
            "git rev-parse --verify --quiet HEAD",
            stdout=commitish)


class TestGitHaveCommit(TestGitCommand):
    """Tests for `git-have-commit`."""

    @staticmethod
    def run_have_commit(*args):
        """Runs `git-have-commit`."""
        return lambda: run_script(git_have_commit, *args)

    @classmethod
    def setUpClass(cls):
        import_file(os.path.join(script_dir, "../git-have-commit"))

    def setUp(self):
        super().setUp()

        self.fake_run_command.set_fake_result(
            "git merge-base --is-ancestor parent child")
        self.fake_run_command.set_fake_result(
            "git merge-base --is-ancestor parent HEAD")
        self.fake_run_command.set_fake_result(
            "git merge-base --is-ancestor child parent",
            return_code=1)

    def test_success(self):
        """Test that a child includes it parent."""
        result = call_with_io(self.run_have_commit("--leaf=child", "parent"))
        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.stdout, "child has commit parent.\n")

    def test_failure(self):
        """Test that a parent does not include its child."""
        result = call_with_io(self.run_have_commit("--leaf=parent", "child"))
        self.assertEqual(result.return_code, 1)
        self.assertTrue(not result.stdout)

    def test_implicit_head(self):
        """Test that a "HEAD" is used as the default leaf."""
        result = call_with_io(self.run_have_commit("parent"))
        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.stdout, "HEAD has commit parent.\n")


class TestGitNext(TestGitCommand):
    """Tests for `git-next`."""

    @staticmethod
    def run_git_next():
        """Runs `git-next`."""
        return run_script(git_next)

    @classmethod
    def setUpClass(cls):
        import_file(os.path.join(script_dir, "../git-next"))

    def setUp(self):
        super().setUp()

        # initial --- child1 --- child2 --- child3a --- merge --- child4 --- leaf3
        #                            \                  /   \ \
        #                             child3b --- child3b1   \ leaf1
        #                                                     \
        #                                                      leaf2
        commit_tree_string = ("leaf3\n"
                              "leaf2\n"
                              "leaf1\n"
                              "child4 leaf3\n"
                              "merge child4 leaf1 leaf2\n"
                              "child3a merge\n"
                              "child3b1 merge\n"
                              "child3b child3b1\n"
                              "child2 child3a child3b\n"
                              "child1 child2\n"
                              "initial child1\n")

        self.fake_run_command.set_fake_result(
            "git rev-list --children --all",
            stdout=commit_tree_string)

        def fake_summarize_git_commit_action(match, result):
            commitish = match.group("commitish")
            result.stdout = f"{commitish} description"

        self.fake_run_command.set_fake_result_re(
            r"git log --max-count=1 '--format=%h %s' (?P<commitish>.+)",
            action=fake_summarize_git_commit_action)

    def test(self):
        """Test that `git-next` navigates to the expected commits."""
        self.set_fake_git_head("initial")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "initial")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child1")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child2")

        call_with_io(self.run_git_next, input="2")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child3b")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child3b1")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "merge")

        call_with_io(self.run_git_next, input="1")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child4")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "leaf3")

        result = call_with_io(self.run_git_next)
        self.assertNotEqual(result.return_code, 0)
        self.assertTrue(not result.stdout)
        self.assertEqual(result.stderr, "git-next: Could not find a child commit for leaf3\n")


@gitutils.entrypoint(globals())
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.strip(), add_help=False)
    ap.add_argument("-h", "--help", action="help",
                    help="Show this help message and exit.")

    ap.parse_args(argv[1:])

    unittest.main()


if __name__ == "__main__":
    __name__ = os.path.basename(__file__)  # pylint: disable=redefined-builtin

    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        sys.exit(1)
