#!/usr/bin/env python3

"""Unit tests for Git scripts."""

import dataclasses
import io
import optparse
import os
import re
import shlex
import subprocess
import sys
import typing
import unittest
import unittest.mock

script_dir = os.path.dirname(__file__)
sys.path.insert(1, os.path.abspath(os.path.join(script_dir, "..")))
import gitutils  # pylint: disable=wrong-import-position  # noqa: E402


git_have_commit = gitutils.import_file(os.path.join(script_dir,
                                                    "../git-have-commit"))
git_prev = gitutils.import_file(os.path.join(script_dir, "../git-prev"))
git_next = gitutils.import_file(os.path.join(script_dir, "../git-next"))
git_submit = gitutils.import_file(os.path.join(script_dir, "../git-submit"))


class FakeRunCommand:
    """A class to manage faked calls to `gitutils.run_command`."""
    class FakeRunResult:
        """
        Stores a predetermined result for a faked command.

        `return_code` specifies the exit code to be returned by the command.

        `stdout` and `stderr` are strings that specify the faked output from
        the command.

        `action` specifies a callback to invoke when the faked command is
        executed.  The callback must take three arguments: the command-line
        (as a shell-quoted string), the regular expression `Match` object
        (if the command-line was matched to a RE) or `None`, and the
        `FakeRunResult` object that should be used as the result of the
        faked command.
        """
        def __init__(self, return_code=0, stdout=None, stderr=None,
                     action=None):
            self.return_code = return_code
            self.stdout = stdout
            self.stderr = stderr
            self.action = action

    def __init__(self):
        self.fake_results = {}
        self.fake_results_re = {}

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
        self.fake_results_re[command_pattern] = \
            ((re.compile(command_pattern), self.FakeRunResult(**kwargs)))

    def __call__(self, *args, **kwargs):
        command_line = gitutils.quoted_join((*args[0],))
        result = self.fake_results.get(command_line)
        match = None
        if result is None:
            for (regexp, r) in self.fake_results_re.values():
                match = regexp.match(command_line)
                if match:
                    result = r
                    break
        if result is None:
            print(self.fake_results)
            raise NotImplementedError(f"No results faked for command: "
                                      f"{command_line}")

        if result.action:
            result.action(command_line, match, result)

        return subprocess.CompletedProcess(args[0],
                                           result.return_code,
                                           stdout=result.stdout,
                                           stderr=result.stderr)


@dataclasses.dataclass
class CapturedCommand:
    command_line: typing.Optional[str] = None


def capture_command(captured_command):
    """
    Returns an action for a `FakeRunResult` that saves the executed
    command-line.
    """
    def action(command_line, match, result):
        assert command_line is not None
        captured_command.command_line = command_line
    return action


def run_script(script, *args):
    """Executes the `main` function from the specified script module."""
    old_argv = sys.argv
    try:
        sys.argv = [script.__file__, *args]
        return script.main(sys.argv)
    finally:
        sys.argv = old_argv


@dataclasses.dataclass(kw_only=True)
class IOResults:
    return_value: int
    exception: Exception
    stdout: str
    stderr: str


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

    `callee` must be a 0-argument closure.

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

    def test_parse_known_options(self):
        """Tests `gitutils.parse_known_options`."""

        @dataclasses.dataclass(kw_only=True, frozen=True)
        class ExpectedResults:
            verbose: typing.Optional[bool]
            flag: typing.Optional[bool]
            string: typing.Optional[str]
            multi: typing.Optional[typing.Tuple[str, str]]
            extra_opts: typing.List[str]
            args: typing.List[str]

        @dataclasses.dataclass(kw_only=False, frozen=True)
        class TestData:
            description: str
            args: typing.List[str]
            expected: ExpectedResults

        test_data = [
            TestData("No arguments",
                     [],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string=None,
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Recognized long option",
                     ["--verbose"],
                     ExpectedResults(verbose=True,
                                     flag=None,
                                     string=None,
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Recognized long option with separate argument",
                     ["--string", "value"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Recognized long option with joined argument",
                     ["--string=value"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Recognized short option with separate argument",
                     ["-s", "value"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Recognized short option with separate argument",
                     ["-svalue"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Combined recognized short options",
                     ["-vf"],
                     ExpectedResults(verbose=True,
                                     flag=True,
                                     string=None,
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Combined recognized and unrecognized short option",
                     ["-vfu"],
                     ExpectedResults(verbose=True,
                                     flag=True,
                                     string=None,
                                     multi=None,
                                     extra_opts=["-u"],
                                     args=[])),
            TestData("Unrecognized short option with argument",
                     ["-fuvx"],
                     ExpectedResults(verbose=None,
                                     flag=True,
                                     string=None,
                                     multi=None,
                                     extra_opts=["-uvx"],
                                     args=[])),
            TestData("Combined recognized short options with separate argument",
                     ["-vfs", "value"],
                     ExpectedResults(verbose=True,
                                     flag=True,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Combined recognized short options with joined argument",
                     ["-vfsvalue"],
                     ExpectedResults(verbose=True,
                                     flag=True,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=[])),
            TestData("Combined recognized short options with multiple arguments, first joined",
                     ["-vfmfoo", "bar", "baz", "-u"],
                     ExpectedResults(verbose=True,
                                     flag=True,
                                     string=None,
                                     multi=("foo", "bar", "baz"),
                                     extra_opts=["-u"],
                                     args=[])),
            TestData("Combined recognized short options with multiple separate arguments",
                     ["-vfm", "foo", "bar", "baz", "-u"],
                     ExpectedResults(verbose=True,
                                     flag=True,
                                     string=None,
                                     multi=("foo", "bar", "baz"),
                                     extra_opts=["-u"],
                                     args=[])),
            TestData("Positional argument",
                     ["--string", "value", "arg"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=[],
                                     args=["arg"])),
            TestData("Unrecognized long and short options",
                     ["--string", "--value", "--unknown", "-u"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="--value",
                                     multi=None,
                                     extra_opts=["--unknown", "-u"],
                                     args=[])),
            TestData("Unrecognized long option with positional argument",
                     ["--string", "--value", "--unknown", "arg"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="--value",
                                     multi=None,
                                     extra_opts=["--unknown"],
                                     args=["arg"])),
            TestData("Recognized long options with multiple arguments, first joined",
                     ["--multi=foo", "bar", "baz", "--unknown", "arg"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string=None,
                                     multi=("foo", "bar", "baz"),
                                     extra_opts=["--unknown"],
                                     args=["arg"])),
            TestData("Recognized long options with multiple separate arguments",
                     ["--multi", "foo", "bar", "baz", "--unknown", "arg"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string=None,
                                     multi=("foo", "bar", "baz"),
                                     extra_opts=["--unknown"],
                                     args=["arg"])),
            TestData("Unrecognized long and short options",
                     ["--unknown", "--string", "value", "-u", "-xyz", "arg"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=["--unknown", "-u", "-xyz"],
                                     args=["arg"])),
            TestData("Positional argument stops parsing",
                     ["--unknown1", "--string", "value", "arg", "--unknown2", "-u"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=["--unknown1"],
                                     args=["arg", "--unknown2", "-u"])),
            TestData("Unrecognized options with arguments",
                     ["--unknown1=arg", "-uarg2", "--string=value", "--unknown2", "arg"],
                     ExpectedResults(verbose=None,
                                     flag=None,
                                     string="value",
                                     multi=None,
                                     extra_opts=["--unknown1=arg", "-uarg2", "--unknown2"],
                                     args=["arg"])),
        ]

        for test in test_data:
            parser = optparse.OptionParser(add_help_option=False)
            parser.disable_interspersed_args()

            parser.add_option("-v", "--verbose", action="store_true")
            parser.add_option("-f", "--flag", action="store_true")
            parser.add_option("-s", "--string")
            parser.add_option("-m", "--multi", nargs=3)

            (opts, extra_opts, args) = gitutils.parse_known_options(parser,
                                                                    test.args)
            msg = f"{test.description} ({test.args=})"
            self.assertEqual(opts.verbose, test.expected.verbose, msg=msg)
            self.assertEqual(opts.flag, test.expected.flag, msg=msg)
            self.assertEqual(opts.string, test.expected.string, msg=msg)
            self.assertEqual(opts.multi, test.expected.multi, msg=msg)
            self.assertEqual(extra_opts, test.expected.extra_opts, msg=msg)
            self.assertEqual(args, test.expected.args, msg=msg)


class TestGitCommand(unittest.TestCase):
    """A base class for tests that use faked `git` commands."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fake_run_command = FakeRunCommand()

    def setUp(self):
        gitutils.run_command = self.fake_run_command

        def fake_git_commit_hash_action(command_line, match, result):
            result.stdout = match.group("commitish")

        def fake_git_checkout_action(command_line, match, result):
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
        """Returns a 0-argument closure that runs `git-have-commit`."""
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
            "git rev-parse --show-toplevel",
            stdout="/dev/null")

        self.fake_run_command.set_fake_result(
            "git rev-list --children --all",
            stdout=commit_tree_string)

        self.fake_run_command.set_fake_result_re(
            r"git config --type=bool (prev|next).attach",
            stdout="false")

        def fake_summarize_git_commit_action(command_line, match, result):
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


class TestGitSubmit(TestGitCommand):
    """Tests for `git-submit`."""
    def test(self):
        """
        Test that `git-submit` executes the expected `git commit` command.
        """
        self.fake_run_command.set_fake_result(
            "git config --type=bool submit.all", return_code=1)

        def make_fake_status(path, code):
            def fake_git_status(*paths, untracked_files="no"):
                return {path: {"code": code}} if path else {}
            return fake_git_status

        @dataclasses.dataclass(kw_only=True, frozen=True)
        class TestEntry:
            all: bool
            path: str
            code: str
            expected: str

        dummy_path = "foo"
        test_entries = [
            TestEntry(all=False, path="", code="  ", expected="git commit --"),
            TestEntry(all=True,  path="", code="  ", expected="git commit --all --"),

            TestEntry(all=False, path=dummy_path, code=" M", expected="git commit --all --"),
            TestEntry(all=True,  path=dummy_path, code=" M", expected="git commit --all --"),

            TestEntry(all=False, path=dummy_path, code="M ", expected="git commit --all --"),
            TestEntry(all=True,  path=dummy_path, code="M ", expected="git commit --all --"),

            TestEntry(all=False, path=dummy_path, code="MM", expected="git commit --"),
        ]

        for entry in test_entries:
            with unittest.mock.patch("gitutils.git_status",
                                     new=make_fake_status(entry.path, entry.code)):
                captured_command = CapturedCommand()
                self.fake_run_command.set_fake_result_re(
                    r"git commit.*",
                    action=capture_command(captured_command))

                opts = ("--all",) if entry.all else ()
                self.assertEqual(run_script(git_submit, *opts), 0,
                                 f"{entry}")
                self.assertEqual(captured_command.command_line, entry.expected,
                                 f"{entry}")

        @dataclasses.dataclass(kw_only=True, frozen=True)
        class TestPromptEntry:
            input: str
            success: bool
            command_line: typing.Optional[str]

        test_prompt_entries = [
            TestPromptEntry(input="staged", success=True, command_line="git commit --"),
            TestPromptEntry(input="all", success=True, command_line="git commit --all --"),
            TestPromptEntry(input="quit", success=False, command_line=None),
        ]

        for entry in test_prompt_entries:
            with unittest.mock.patch("gitutils.git_status",
                                     new=make_fake_status(dummy_path, "MM")):
                captured_command = CapturedCommand()
                self.fake_run_command.set_fake_result_re(
                    r"git commit.*", action=capture_command(captured_command))

                result = call_with_io(lambda: run_script(git_submit, "--all"),
                                      input=f"{entry.input}\n")

                self.assertIn("staged and unstaged changes detected", result.stdout)

                if entry.success:
                    self.assertEqual(result.return_value, 0, f"{entry}")
                else:
                    self.assertNotEqual(result.return_value, 0, f"{entry}")

                if entry.command_line is None:
                    self.assertIs(captured_command.command_line,
                                  None,
                                  f"{entry}")
                else:
                    self.assertEqual(captured_command.command_line,
                                     entry.command_line,
                                     f"{entry}")


@gitutils.entrypoint
def main(argv):
    parser = optparse.OptionParser(
        description=__doc__.strip(),
        add_help_option=False,
    )
    parser.disable_interspersed_args()

    parser.add_option("-h", "--help", action="help",
                      help="Show this help message and exit.")

    (_opts, args) = parser.parse_args(argv[1:])
    gitutils.expect_positional_args(parser, args, max=0)

    unittest.main()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
