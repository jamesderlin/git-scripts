"""Common utility classes and functions shared among various Git scripts."""

import argparse
import functools
import math
import os
import shutil
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


class GraphNode:
    """A node in the Git commit graph."""
    def __init__(self, commit_hash):
        self.commit_hash = commit_hash
        self.parents = []
        self.children = []

    def add_children(self, children):
        """Adds a list of `GraphNodes` as children of this one."""
        for child in children:
            child.parents.append(self)
        self.children += children

    def __repr__(self):
        return f"GraphNode('{self.commit_hash}')"


def debug_prompt():
    """Starts an interactive Python prompt."""
    # pylint: disable=import-outside-toplevel
    import code
    import inspect
    previous_frame = inspect.currentframe().f_back
    code.interact(local=dict(**previous_frame.f_globals,
                             **previous_frame.f_locals))


def entrypoint(caller_globals):
    """Returns a decorator for top-level `main` (or equivalent) functions."""
    script_name = os.path.basename(caller_globals["__file__"])

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            caller_globals["__name__"] = script_name
            try:
                return f(*args, **kwargs) or 0
            except AbortError as e:
                if not e.cancelled:
                    print(f"{script_name}: {e}", file=sys.stderr)
                return e.exit_code
            except KeyboardInterrupt:
                return 1
        return wrapper
    return decorator


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
        editor = get_git_config("core", "editor")

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


def terminal_size():
    """
    Returns the terminal size.

    Returns `(math.inf, math.inf)` if stdout is not a TTY.
    """
    if not sys.stdout.isatty():
        return os.terminal_size((math.inf, math.inf))
    return shutil.get_terminal_size()


def ellipsize(s, width):
    """
    Truncates a string to the specified maximum width.

    The maximum width includes the ellipsis added if the string is truncated.

    `width` must be a positive integer.

    Unlike `textwrap.shorten`, leaves whitespace alone.
    """
    assert width > 0
    if len(s) <= width:
        return s

    ellipsis = "..."
    if width < len(ellipsis):
        return s[:width]

    s = s[:(width - len(ellipsis))] + ellipsis
    assert len(s) == width
    return s


def remove_prefix(s, *, prefix, default=None):
    """
    Returns the string with the specified prefix removed.

    Returns `default` if the string does not start with the prefix.
    """
    if s and s.startswith(prefix):
        return s[len(prefix):]
    return default


def prompt_with_choices(preamble, prompt, choices):
    """
    Prompts the user to choose from a list of choices.

    Returns the index of the selected choice.  Raises an `AbortError` if the
    user cancels.
    """
    assert choices

    max_length = terminal_size().columns - 1
    instructions = "\n".join([
        *((preamble,) if preamble else ()),
        *(ellipsize(f"  {i}: {choice}", width=max_length)
          for (i, choice) in enumerate(choices, 1)),
    ])

    print(instructions)

    max_index = len(choices)
    default_prompt = f"[1..{max_index}]: "
    prompt = f"{prompt} {default_prompt}" if prompt else default_prompt

    while True:
        try:
            choice = input(prompt).strip()
        except EOFError:
            print()
            raise AbortError(cancelled=True)

        if not choice:
            continue

        if choice.lower() in ("?", "help"):
            print()
            print(instructions)
            continue

        if choice.lower() in ("q", "quit"):
            raise AbortError(cancelled=True)

        try:
            index = int(choice)
            if 1 <= index <= max_index:
                return index - 1

            print(f"{choice} is not in the range [1..{max_index}].")
        except ValueError:
            print(f"\"{choice}\" is not a valid choice.\n"
                  f"The entered choice must be between "
                  f"1 and {max_index}, inclusive.\n"
                  f"Enter \"help\" to show the choices again "
                  f"or \"quit\" to quit.")
        print()


def get_git_config(section, option, handler=str, default=None):
    """Retrieves a Git configuration option."""
    qualified_option = f"{section}.{option}"
    result = run_command(("git", "config", qualified_option),
                         stdout=subprocess.PIPE,
                         universal_newlines=True)
    if result.returncode == 0:
        return handler(result.stdout.rstrip("\n"))
    if result.returncode == 1:
        return default

    raise AbortError(f"Failed to retrieve config option: {qualified_option}"
                     f"{result.returncode}")


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

    # `git rev-list` normally orders later commits on top.  Parse the output
    # bottom-up to try to preserve parent order to avoid making a separate
    # invocation of `git rev-list --parents --all`.
    for line in reversed(result.stdout.splitlines()):
        (parent_hash, *children_hashes) = line.split()
        parent_node = commit_graph.setdefault(parent_hash,
                                              GraphNode(parent_hash))
        parent_node.add_children(
            [commit_graph.setdefault(child_hash, GraphNode(child_hash))
             for child_hash in children_hashes])
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

    return result.stdout.rstrip("\n")
