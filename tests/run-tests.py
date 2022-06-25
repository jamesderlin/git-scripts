#!/usr/bin/env python3

"""Unit tests for Git scripts."""

import argparse
from collections import namedtuple
import io
import math
import os
import re
import shlex
import subprocess
import sys
import unittest
import unittest.mock

script_dir = os.path.dirname(__file__)
sys.path.insert(1, os.path.join(script_dir, ".."))
import gitutils  # pylint: disable=wrong-import-position  # noqa: E402


git_have_commit = gitutils.import_file(os.path.join(script_dir,
                                                    "../git-have-commit"))
git_prev = gitutils.import_file(os.path.join(script_dir, "../git-prev"))
git_next = gitutils.import_file(os.path.join(script_dir, "../git-next"))


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
    old_argv = sys.argv
    try:
        sys.argv = [script.__file__, *args]
        return script.main(sys.argv)
    finally:
        sys.argv = old_argv


IOResults = namedtuple("IOResults",
                       ["return_value", "exception", "stdout", "stderr"])


@unittest.mock.patch("sys.stdin", new_callable=io.StringIO)
@unittest.mock.patch("sys.stderr", new_callable=io.StringIO)
@unittest.mock.patch("sys.stdout", new_callable=io.StringIO)
# `input` is the most sensible name, and it matches what the `subprocess`
# module uses.
def call_with_io(callee, mock_stdout=None, mock_stderr=None, mock_stdin=None,
                 *, input=None):  # pylint: disable=redefined-builtin
    """
    Invokes the callable specified by `callee`, capturing and returning stdout
    and stderr output.

    `input`, if specified, will be used as fake input to stdin.

    Returns an `IOResults` with the result of the invocation.
    """
    # These arguments are always provided, but to avoid bogus pylint
    # complaints (see <https://stackoverflow.com/a/62252941/>), we have to make
    # them optional and then sanity-check them at runtime.
    assert mock_stdout
    assert mock_stderr
    assert mock_stdin

    if input is not None:
        mock_stdin.write(input)
        mock_stdin.seek(0)

    return_value = None
    exception = None
    try:
        return_value = callee()
    except gitutils.AbortError as e:
        exception = e
    return IOResults(return_value=return_value,
                     exception=exception,
                     stdout=mock_stdout.getvalue(),
                     stderr=mock_stderr.getvalue())


class TestUtils(unittest.TestCase):
    """Tests for miscellaneous `gitutils` functions."""

    def setUp(self):
        gitutils.terminal_size = lambda: os.terminal_size((math.inf, math.inf))

    def test_ellipsize(self):
        """Test `gitutils.ellipsize`."""
        ellipsize = gitutils.ellipsize
        self.assertRaises(AssertionError, ellipsize, "Lorem ipsum", -1)
        self.assertRaises(AssertionError, ellipsize, "Lorem ipsum", 0)
        self.assertEqual(ellipsize("Lorem ipsum", 1), "L")
        self.assertEqual(ellipsize("Lorem ipsum", 2), "Lo")
        self.assertEqual(ellipsize("Lorem ipsum", 3), "...")
        self.assertEqual(ellipsize("Lorem ipsum", 4), "L...")
        self.assertEqual(ellipsize("Lorem ipsum", 5), "Lo...")
        self.assertEqual(ellipsize("Lorem ipsum", 10), "Lorem i...")
        self.assertEqual(ellipsize("Lorem ipsum", 11), "Lorem ipsum")
        self.assertEqual(ellipsize("Lorem ipsum", 12), "Lorem ipsum")
        self.assertEqual(ellipsize("Lorem ipsum", 20), "Lorem ipsum")

    def test_remove_prefix(self):
        """Test `gitutils.remove_prefix`."""
        remove_prefix = gitutils.remove_prefix
        self.assertIs(remove_prefix("", prefix="foo"), None)
        self.assertEqual(remove_prefix("foobar", prefix="foo"), "bar")
        self.assertIs(remove_prefix("foobar", prefix="fool"), None)
        self.assertIs(remove_prefix("xfoobar", prefix="foo"), None)
        self.assertEqual(remove_prefix("foobar", prefix=""), "foobar")
        self.assertEqual(remove_prefix("foobar", prefix="foobar"), "")
        self.assertIs(remove_prefix("foobar", prefix="foobarbaz"), None)
        self.assertIs(remove_prefix("foobar", prefix="bar", default="default"),
                      "default")

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
        self.assertEqual(result.return_value, 0)
        self.assertEqual(result.stdout, "child has commit parent.\n")

    def test_failure(self):
        """Test that a parent does not include its child."""
        result = call_with_io(self.run_have_commit("--leaf=parent", "child"))
        self.assertEqual(result.return_value, 1)
        self.assertTrue(not result.stdout)

    def test_implicit_head(self):
        """Test that a "HEAD" is used as the default leaf."""
        result = call_with_io(self.run_have_commit("parent"))
        self.assertEqual(result.return_value, 0)
        self.assertEqual(result.stdout, "HEAD has commit parent.\n")


class TestGitPrevNext(TestGitCommand):
    """Tests for `git-prev` and `git-next`."""

    @staticmethod
    def run_git_prev():
        """Runs `git-prev`."""
        return run_script(git_prev)

    @staticmethod
    def run_git_next():
        """Runs `git-next`."""
        return run_script(git_next)

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
                              "child3b1 merge\n"
                              "child3a merge\n"
                              "child3b child3b1\n"
                              "child2 child3a child3b\n"
                              "child1 child2\n"
                              "initial child1\n")

        self.fake_run_command.set_fake_result(
            "git rev-list --children --all",
            stdout=commit_tree_string)

        self.fake_run_command.set_fake_result_re(
            r"git config --type=bool (prev|next).attach",
            stdout="false")

        def fake_summarize_git_commit_action(match, result):
            commitish = match.group("commitish")
            result.stdout = f"{commitish} description"

        self.fake_run_command.set_fake_result_re(
            r"git log --max-count=1 '--format=%h %s' (?P<commitish>.+)",
            action=fake_summarize_git_commit_action)

    def test_graph(self):
        """
        Test that `gitutils.git_commit_graph()` parses `git rev-list` output.
        """
        def hashes_from_nodes(nodes):
            """Returns a list of commit hashes for a list of `GraphNode`s."""
            return [node.commit_hash for node in nodes]

        def expect_node(graph, commit_hash, *, parent_hashes, child_hashes):
            """
            Verifies that the `GraphNode` with the specified commit hash has
            the expected properties.
            """
            node = graph[commit_hash]
            self.assertEqual(node.commit_hash, commit_hash)
            self.assertEqual(hashes_from_nodes(node.parents), parent_hashes)
            self.assertEqual(hashes_from_nodes(node.children), child_hashes)

        graph = gitutils.git_commit_graph()
        expect_node(graph, "initial",
                    parent_hashes=[],
                    child_hashes=["child1"])
        expect_node(graph, "child1",
                    parent_hashes=["initial"],
                    child_hashes=["child2"])
        expect_node(graph, "child2",
                    parent_hashes=["child1"],
                    child_hashes=["child3a", "child3b"])
        expect_node(graph, "child3a",
                    parent_hashes=["child2"],
                    child_hashes=["merge"])
        expect_node(graph, "child3b",
                    parent_hashes=["child2"],
                    child_hashes=["child3b1"])
        expect_node(graph, "child3b1",
                    parent_hashes=["child3b"],
                    child_hashes=["merge"])
        expect_node(graph, "merge",
                    parent_hashes=["child3a", "child3b1"],
                    child_hashes=["child4", "leaf1", "leaf2"])
        expect_node(graph, "leaf1",
                    parent_hashes=["merge"],
                    child_hashes=[])
        expect_node(graph, "leaf2",
                    parent_hashes=["merge"],
                    child_hashes=[])
        expect_node(graph, "child4",
                    parent_hashes=["merge"],
                    child_hashes=["leaf3"])
        expect_node(graph, "leaf3",
                    parent_hashes=["child4"],
                    child_hashes=[])

    def test_prev(self):
        """Test that `git-prev` navigates to the expected commits."""
        self.set_fake_git_head("leaf3")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "leaf3")

        call_with_io(self.run_git_prev)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child4")

        call_with_io(self.run_git_prev)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "merge")

        call_with_io(self.run_git_prev, input="2\n")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child3b1")

        call_with_io(self.run_git_prev)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child3b")

        call_with_io(self.run_git_prev)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child2")

        call_with_io(self.run_git_prev)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child1")

        call_with_io(self.run_git_prev)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "initial")

        result = call_with_io(self.run_git_prev)
        self.assertNotEqual(result.return_value, 0)
        self.assertTrue(not result.stdout)
        self.assertEqual(
            result.stderr,
            "git-prev: Could not find a parent commit for initial\n")

    def test_next(self):
        """Test that `git-next` navigates to the expected commits."""
        self.set_fake_git_head("initial")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "initial")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child1")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child2")

        call_with_io(self.run_git_next, input="2\n")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child3b")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child3b1")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "merge")

        call_with_io(self.run_git_next, input="1\n")
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "child4")

        call_with_io(self.run_git_next)
        self.assertEqual(gitutils.git_commit_hash("HEAD"), "leaf3")

        result = call_with_io(self.run_git_next)
        self.assertNotEqual(result.return_value, 0)
        self.assertTrue(not result.stdout)
        self.assertEqual(result.stderr,
                         "git-next: Could not find a child commit for leaf3\n")


@gitutils.entrypoint(globals())
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.strip(), add_help=False)
    ap.add_argument("-h", "--help", action="help",
                    help="Show this help message and exit.")

    ap.parse_args(argv[1:])

    unittest.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
