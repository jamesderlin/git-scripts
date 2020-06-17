"""Common utility classes and functions shared among various Git scripts."""

import argparse
import functools
import os
import subprocess
import sys

verbose = False


class AbortError(Exception):
    """
    A simple exception class to abort program execution.

    If `cancelled` is True, no error message should be printed.
    """
    def __init__(self, message=None, cancelled=False, exit_code=1):
        super().__init__(message or ("Cancelled."
                                     if cancelled
                                     else "Unknown error"))
        assert exit_code != 0
        self.cancelled = cancelled
        self.exit_code = exit_code


def entrypoint(caller_globals):
    """Returns a decorator for top-level `main` (or equivalent) functions."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except AbortError as e:
                if not e.cancelled:
                    script_name = caller_globals["__name__"]
                    print(f"{script_name}: {e}", file=sys.stderr)
                return e.exit_code
        return wrapper
    return decorator


class PassThroughOption(argparse.Action):
    """
    Handles an option meant to be passed through to another command.  Appends
    the option and its arguments to a list specified by `dest`.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        # argparse initially adds `self.dest` to `namespace` with its default
        # value, so we can't use `getattr`'s default argument.
        old_values = getattr(namespace, self.dest) or []
        setattr(namespace, self.dest, old_values + [option_string, *values])


def run_command(args, **kwargs):
    """
    A wrapper around `subprocess.run` that prints the executed command-line for
    debugging.  Additionally can print error messages that would normally be
    suppressed.

    The `CompletedProcess` object stores the executed command-line, but
    printing the command-line first can help debug issues where the executed
    process never completes.
    """
    assert args

    if verbose:
        # We must flush to ensure that we print before the executed command
        # prints.
        print(args, file=sys.stderr, flush=True)

        if kwargs.get("stderr") == subprocess.DEVNULL:
            # Unsuppress error messages.
            kwargs["stderr"] = None

        # We don't unsuppress stdout because that is likely to have a greater
        # volume of messages.  Additionally, since unsuppression would be
        # enabled only in debugging scenarios, we're more likely to be
        # interested in error messages.

    # pylint: disable=subprocess-run-check
    return subprocess.run(args, **kwargs)


def run_editor(file_path, line_number=None):
    """
    Open the specified file in an editor at the specified line number, if
    provided.

    The launched editor will be chosen from, in order:

    1. The `GIT_EDITOR` environment variable.
    2. `core.editor` in the Git configuration file.
    3. The `VISUAL` environment variable.
    4. The `EDITOR` environment variable.
    5. Hard-coded paths to common editors.

    The order is consistent with the order described under `git help var`.
    """
    editor = os.environ.get("GIT_EDITOR")
    if not editor:
        result = run_command(("git", "config", "core.editor"),
                             stdout=subprocess.PIPE,
                             universal_newlines=True)
        if result.returncode == 0:
            editor = result.stdout.strip()

    editor = (editor
              or os.environ.get("VISUAL")
              or os.environ.get("EDITOR"))

    if not editor:
        if os.name == "posix":
            editor = "vi"
        elif os.name == "nt":
            # TODO: Check `notepad.exe`'s behavior with `+line` and `--`.
            editor = "notepad.exe"
            line_number = None

    options = []
    if line_number:
        options.append(f"+{line_number}")
    run_command((editor, *options, "--", file_path))


def git_commit_hash(commitish, short=False):
    """
    Normalizes a commit-ish to an actual commit hash to handle things such as
    `:/COMMIT_MESSAGE`.

    Raises an `AbortError` if no commit hash was found.
    """
    assert commitish

    extra_options = []
    if short:
        extra_options.append("--short")
    if not verbose:
        extra_options.append("--quiet")
    result = run_command(("git", "rev-parse", "--verify", *extra_options,
                          commitish),
                         stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,
                         universal_newlines=True)
    if result.returncode != 0:
        raise AbortError(f"No commit hash found for \"{commitish}\".",
                         exit_code=result.returncode)

    return result.stdout.strip()


def summarize_git_commit(commitish, format=None):  # pylint: disable=redefined-builtin
    """
    Returns a string summarizing the specified commit-ish.

    By default, the returned summary will include the commit's short hash and
    the first line of its commit message.

    An optional format string may be specified to control the returned string.
    Format specifiers are the same as those used by `git log`.  If no format
    string is specified, uses `"%h %s"`.
    """
    assert commitish

    format = format or "%h %s"
    result = run_command(("git", "log", "--max-count=1", f"--format={format}",
                          commitish),
                         stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,
                         universal_newlines=True)
    if result.returncode != 0:
        raise AbortError(f"Failed to summarize \"{commitish}\".",
                         exit_code=result.returncode)

    return result.stdout.rstrip()


def is_git_ancestor(parent_commitish, child_commitish):
    """
    Returns whether `parent_commitish` is a parent commit of (or is the same
    as) `child_commitish`.
    """
    assert parent_commitish
    assert child_commitish

    result = run_command(("git", "merge-base", "--is-ancestor",
                          parent_commitish, child_commitish))

    if result.returncode == 0:
        return True

    if result.returncode == 1:
        return False

    raise AbortError(f"Command failed: {result.args} "
                     f"(error: {result.returncode})",
                     exit_code=result.returncode)


def git_commit_graph():
    """
    Returns a dictionary mapping each Git commit hash to a list of commit
    hashes for its immediate children.
    """
    result = run_command(("git", "rev-list", "--children", "--all"),
                         stdout=subprocess.PIPE,
                         check=True,
                         universal_newlines=True)
    commit_graph = {}
    for line in result.stdout.splitlines():
        (parent_hash, *children_hashes) = line.split()
        commit_graph.setdefault(parent_hash, []).extend(children_hashes)
    return commit_graph


def current_git_branch():
    """
    Returns the name of the currently checked out git branch, if any.  Returns
    `"HEAD"` otherwise.
    """
    # Reference: <https://stackoverflow.com/questions/6245570/>
    result = run_command(("git", "rev-parse", "--abbrev-ref", "HEAD"),
                         stdout=subprocess.PIPE,
                         universal_newlines=True)
    if result.returncode != 0:
        raise AbortError("Failed to determine the current git branch.",
                         exit_code=result.returncode)

    return result.stdout.strip()


def git_root():
    """Returns the absolute path to the root of the current Git repository."""
    result = run_command(("git", "rev-parse", "--show-toplevel"),
                         stdout=subprocess.PIPE,
                         universal_newlines=True)
    if result.returncode != 0:
        raise AbortError("Failed to determine the current git repository.",
                         exit_code=result.returncode)

    return result.stdout.strip()
