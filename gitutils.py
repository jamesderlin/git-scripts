import argparse
import subprocess
import sys

verbose = False


class AbortError(Exception):
    """A simple exception class to abort program execution."""
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


class PassThroughOption(argparse.Action):
    """Handles an option meant to be passed through to another command.  Appends
    the option and its arguments to a list specified by `dest`.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        # argparse initially adds `self.dest` to `namespace` with its default
        # value, so we can't use `getattr`'s default argument.
        old_values = getattr(namespace, self.dest) or []
        setattr(namespace, self.dest, old_values + [option_string, *values])


def run_command(args, **kwargs):
    """A wrapper around `subprocess.run` that prints the executed command-line
    for debugging.  Additionally can print error messages that would normally
    be suppressed.

    The `CompletedProcess` object stores the executed command-line, but printing
    the command-line first can help debug issues where the executed process
    never completes.
    """
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

    return subprocess.run(args, **kwargs)


def git_commit_hash(commitish):
    """Normalizes a commit-ish to an actual commit hash to handle things such
    as `:/COMMIT_MESSAGE`.

    Raises an `AbortError` if no commit hash was found.
    """
    command = ("git", "rev-parse", "--verify", commitish)
    result = run_command(command,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,
                         universal_newlines=True)
    if result.returncode != 0:
        raise AbortError(f"No commit hash found for \"{commitish}\"")

    return result.stdout.strip()


def git_is_ancestor(parent_commitish, child_commitish):
    """Returns whether `parent_commitish` is a parent commit of (or is the same
    as) `child_commitish`.
    """
    result = run_command(("git", "merge-base", "--is-ancestor",
                          parent_commitish, child_commitish))
    return result.returncode == 0
