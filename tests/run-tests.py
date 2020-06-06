#!/usr/bin/env python3

"""TODO"""
# * Hot-reloadability?

import argparse
import code
from collections import namedtuple
import importlib
import inspect
import io
import os
import re
import shlex
import subprocess
import sys
import unittest
import unittest.mock

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
        (stem, extension) = os.path.splitext(os.path.basename(file_path))
        module_name = stem.replace("-", "_")

    loader = importlib.machinery.SourceFileLoader(module_name, file_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    globals()[module_name] = module
    sys.modules[module_name] = module


import_file("../gitutils.py")
import_file("../git-next")
import_file("../git-have-commit")


def args_to_command_line(args):
    """Converts a list of arguments to a single command-line string."""
    return " ".join((shlex.quote(a) for a in args))


_FakeRunResult = namedtuple("_FakerunResult", ["args", "kwargs", "action"])


class FakeRunCommand:
    """A class to manage faked calls to `gitutils.run_command`."""
    def __init__(self):
        self.fake_results = {}
        self.fake_results_re = []

    def set_fake_result(self, command_line, *args, action=None, **kwargs):
        self.fake_results[command_line] = _FakeRunResult(args, kwargs, action)

    def set_fake_result_re(self, command_pattern, *args, action=None, **kwargs):
        self.fake_results_re.append((re.compile(command_pattern),
                                     _FakeRunResult(args, kwargs, action)))

    def __call__(self, *args, **kwargs):
        command_line = args_to_command_line((*args[0],))
        result = self.fake_results.get(command_line)
        match = None
        if result is None:
            for (re, r) in self.fake_results_re:
                match = re.match(command_line)
                if match:
                    result = r
                    break
        if result is None:
            raise NotImplementedError(f"No results faked for command: "
                                      f"{command_line}")

        if result.action:
           result.action(match, result)

        return subprocess.CompletedProcess(args[0],
                                           *result.args,
                                           **result.kwargs)


def run_script(script, *args):
    """Executes the `main` function from the specified script module."""
    script.__name__ = os.path.basename(script.__file__)
    return script.main([script.__name__, *args])


def set_fake_git_head(fake_run_command, commitish):
    """Fakes the current Git HEAD commit."""
    print(f"HEAD is now at {commitish}")
    fake_run_command.set_fake_result(
        "git rev-parse --verify --quiet HEAD",
        0, stdout=commitish),


IOResults = namedtuple("IOResults", ["stdout", "stderr"])


@unittest.mock.patch("sys.stdin", new_callable=io.StringIO)
@unittest.mock.patch("sys.stderr", new_callable=io.StringIO)
@unittest.mock.patch("sys.stdout", new_callable=io.StringIO)
def call_with_io(callable, mock_stdout, mock_stderr, mock_stdin, *, input=None):
    """
    Invokes the specified callable, capturing and returning stdout and stderr
    output.

    `input`, if specified, will be used as fake input to stdin.
    """
    if input is not None:
        mock_stdin.write(input)
        mock_stdin.seek(0)

    callable()
    return IOResults(stdout=mock_stdout.getvalue(),
                     stderr=mock_stderr.getvalue())


def expect(actual, expected=True):
    """Verifies that an actual value matches an expected value."""
    if actual != expected:
        previous_frame = inspect.currentframe().f_back
        info = inspect.getframeinfo(previous_frame)
        raise gitutils.AbortError(f"Test failed ({info.filename}:{info.lineno}):\n"
                                  f"  Expected: {repr(expected)}\n"
                                  f"    Actual: {repr(actual)}\n")


def expect_eval(expression_string):
    previous_frame = inspect.currentframe().f_back
    if not eval(expression_string, globals(), previous_frame.f_locals):
        info = inspect.getframeinfo(previous_frame)
        raise gitutils.AbortError(f"Test failed ({info.filename}:{info.lineno}):\n"
                                  f"  Expected: {expression_string}\n")


def debug_prompt():
    """Starts an interactive Python prompt."""
    previous_frame = inspect.currentframe().f_back
    code.interact(local=dict(globals(), **previous_frame.f_locals))


@gitutils.entrypoint(globals())
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.strip(), add_help=False)
    ap.add_argument("-h", "--help", action="help",
                    help="Show this help message and exit.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print verbose debugging messages.")

    args = ap.parse_args(argv[1:])

    gitutils.verbose = args.verbose

    fake_run_command = FakeRunCommand()
    fake_run_command.set_fake_result(
        "git rev-parse --verify --quiet commitish",
        0, stdout="1234567890")
    fake_run_command.set_fake_result(
        "git merge-base --is-ancestor parent child",
        0),
    fake_run_command.set_fake_result(
        "git merge-base --is-ancestor child parent",
        1),


    # initial --- child1 --- child2 --- child3a --- merge --- child4 --- leaf3
    #                            \                  /   \ \
    #                             child3b --- child3b1   \  leaf1
    #                                                     \
    #                                                       leaf2
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

    fake_run_command.set_fake_result(
        "git rev-list --children --all",
        0, commit_tree_string)
    fake_run_command.set_fake_result(
        "git rev-parse --abbrev-ref HEAD",
        0, stdout="my-branch"),

    def fake_git_commit_hash_action(match, result):
        result.kwargs["stdout"] = match.group("commitish")

    fake_run_command.set_fake_result_re(
        r"git rev-parse --verify (--short )?(--quiet )?(?P<commitish>.+)",
        0,
        action=fake_git_commit_hash_action)

    fake_run_command.set_fake_result_re(
        r"git checkout --detach (?P<commitish>.+)",
        0,
        action=lambda match, result: set_fake_git_head(fake_run_command,
                                                       match.group("commitish"))),

    def fake_summarize_git_commit_action(match, result):
        index = match.group("index")
        commitish = match.group("commitish")
        result.kwargs["stdout"] = f"{index}: {commitish} description"

    fake_run_command.set_fake_result_re(
        r"git log --max-count=1 '--format=\s*(?P<index>\d+):[^']*' "
        r"(?P<commitish>.+)",
        0,
        action=fake_summarize_git_commit_action)

    gitutils.run_command = fake_run_command

    set_fake_git_head(fake_run_command, "initial")
    expect(gitutils.git_commit_hash("HEAD"), "initial")

    def run_git_next():
        return run_script(git_next)

    expect(call_with_io(run_git_next).stdout, "HEAD is now at child1\n")
    expect(call_with_io(run_git_next).stdout, "HEAD is now at child2\n")
    expect(call_with_io(run_git_next, input="1").stdout.endswith("HEAD is now at child3b\n"))
    expect(call_with_io(run_git_next).stdout, "HEAD is now at child3b1\n")
    expect(call_with_io(run_git_next).stdout, "HEAD is now at merge\n")
    expect(call_with_io(run_git_next, input="0").stdout.endswith("HEAD is now at child4\n"))
    expect(call_with_io(run_git_next).stdout, "HEAD is now at leaf3\n")
    result = call_with_io(run_git_next)
    expect(not result.stdout)
    expect(result.stderr, "git-next: Could not find a child commit for leaf3\n")


if __name__ == "__main__":
    __name__ = os.path.basename(__file__)  # pylint: disable=redefined-builtin

    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        sys.exit(1)
