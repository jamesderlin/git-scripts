"""Common utility classes and functions shared among various Git scripts."""

import functools
import importlib
import importlib.machinery
import importlib.util
import os
import shlex
import subprocess
import sys

import python_cli_utils.choices_prompt
import python_cli_utils.tty_utils

verbose = False


class AbortError(Exception):
    """
    A simple exception class to abort program execution.

    If `cancelled` is True, no error message should be printed.
    """
    def __init__(self, message=None, *, cancelled=False, exit_code=1):
        super().__init__(message or ("Cancelled."
                                     if cancelled
                                     else "Unknown error"))
        assert exit_code != 0
        self.cancelled = cancelled
        self.exit_code = exit_code


class CommitNotFoundError(AbortError):
    """An exception class thrown by `git_commit_hash`."""
    def __init__(self, commitish, *, exit_code=1):
        super().__init__(f"No commit hash found for \"{commitish}\".")
        self.exit_code = exit_code


def _passthrough_option(option, opt_str, value, parser):
    """
    optparse callback for an option meant to be passed through to another
    command.  Appends the option and its arguments to a list specified by
    `option.dest`.
    """
    if value is None:
        new_value = opt_str
    else:
        if isinstance(value, tuple):
            value = quoted_join(value)

        # If the option has an argument, always use the forms `-oARGUMENT`
        # or `--option=ARGUMENT` instead of separating the argument with
        # a space.  Some `git` commands (e.g. `git diff`) will treat
        # option arguments separated by spaces as ambiguous.
        new_value = (f"{opt_str}={value}"
                     if opt_str.startswith("--")
                     else f"{opt_str}{value}")

    options = getattr(parser.values, option.dest) or []
    options.append(new_value)
    setattr(parser.values, option.dest, options)


def add_passthrough_options(parser_or_group, options_dict, *, dest, help=None):
    """
    Given a dictionary of options meant to be passed through to another
    command, adds those options to an optparse parser or option group.

    The dictionary must map tuples of option names to option argument names
    (metavars) or to `None` if the option takes no argument.
    """
    for (option_names, metavar) in options_dict.items():
        assert isinstance(option_names, tuple)
        parser_or_group.add_option(*option_names,
                                   dest=dest,
                                   metavar=metavar,
                                   type=(None if metavar is None else str),
                                   default=[],
                                   action="callback",
                                   callback=_passthrough_option,
                                   help=help)


def quoted_join(iterable):
    """
    Joins the specified iterable into a single string, shell-quoting each
    element if necessary.
    """
    return " ".join((shlex.quote(i) for i in iterable))


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


def debug_print(message):
    """
    Prints a message if `verbose` mode is enabled.

    `message` may be string or a zero-argument function that returns a string.
    """
    if verbose:
        if message is callable:
            message = message()
        print(message, file=sys.stderr)


def debug_prompt():
    """Starts an interactive Python prompt."""
    # pylint: disable=import-outside-toplevel
    import code
    import inspect
    previous_frame = inspect.currentframe().f_back
    code.interact(local=dict(**previous_frame.f_globals,
                             **previous_frame.f_locals))


def entrypoint(main):
    """
    Decorator for top-level `main` (or equivalent) functions.

    Used to reduce boilerplate.
    """
    @functools.wraps(main)
    def wrapper(*args, **kwargs):
        script_name = os.path.basename(sys.modules[main.__module__].__file__)
        sys.modules[main.__module__].__name__ = script_name

        try:
            return main(*args, **kwargs) or 0
        except AbortError as e:
            if not e.cancelled:
                print(f"{script_name}: {e}", file=sys.stderr)
            return e.exit_code
        except KeyboardInterrupt:
            return 1
        except BrokenPipeError:
            # From <https://docs.python.org/3/library/signal.html#note-on-sigpipe>:
            #
            # Python flushes standard streams on exit; redirect remaining
            # output to devnull to avoid another BrokenPipeError at shutdown.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            return 1  # Python exits with error code 1 on EPIPE.
    return wrapper


def import_file(file_path, module_name=None):
    """
    Imports and returns a Python module from a file path.

    Unlike normal `import`, allows importing from files that have `-`
    characters in their names and that do not end with a `.py` extension.

    If `module_name` is not specified, the module name will be derived from
    the filename, replacing any `-` characters with `_`s.
    """
    # Derived from: <https://stackoverflow.com/a/56090741/>.
    if not module_name:
        (stem, _extension) = os.path.splitext(os.path.basename(file_path))
        module_name = stem.replace("-", "_")

    file_path = os.path.abspath(file_path)
    loader = importlib.machinery.SourceFileLoader(module_name, file_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sys.modules[module_name] = module

    # Add the module path in case it imports other local modules.
    module_dir = os.path.dirname(file_path)
    if module_dir not in sys.path:
        sys.path.insert(1, module_dir)

    return module


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
    3. The editor chosen by `spawn_editor.edit_file`.

    The order is consistent with the order described under `git help var`.
    """
    import spawneditor  # pylint: disable=import-outside-toplevel

    editor = os.environ.get("GIT_EDITOR")
    if not editor:
        editor = get_git_config("core", "editor")

    try:
        spawneditor.edit_file(file_path, line_number=line_number, editor=editor)
    except spawneditor.UnsupportedPlatformError as e:
        raise AbortError("Unable to determine what text editor to use.  "
                         "See the GIT_EDITOR section from `git help var`.") \
              from e


def remove_prefix(s, *, prefix, default=None):
    """
    Returns the string with the specified prefix removed.

    Returns `default` if the string does not start with the prefix.
    """
    if s and s.startswith(prefix):
        return s[len(prefix):]
    return default


prompt_with_choices = python_cli_utils.choices_prompt


def prompt_with_numbered_choices(choices,
                                 *,
                                 default_index=None,
                                 preamble="",
                                 prompt=None):
    """An wrapper around `python_cli_utils.numbered_choices_prompt."""
    max_length = python_cli_utils.terminal_size().columns - 1

    def item_formatter(s):
        return python_cli_utils.ellipsize(s, width=max_length)

    return python_cli_utils.numbered_choices_prompt(
        choices,
        default_index=default_index,
        preamble=preamble,
        prompt=prompt,
        item_formatter=item_formatter,
    )


def prompt_for_branch(local_branches, commitish):
    """
    Prompts the user to choose among several branch names.

    Returns the selected branch name.
    """
    if len(local_branches) == 1:
        # If there's only one choice, don't bother prompting.
        return local_branches[0]

    short_hash = git_commit_hash(commitish, short=True)
    selected_index \
        = prompt_with_numbered_choices(
            local_branches,
            preamble=f"{short_hash} has multiple local branches associated "
                     f"with it.",
            prompt="Enter the branch index")
    if selected_index is None:
        raise AbortError(cancelled=True)
    return local_branches[selected_index]


def get_git_config(section, variable_name, handler=None, default=None):
    """
    Retrieves a Git configuration option.

    If `handler` is not supplied, the value of the configuration option will be
    returned as a string.

    If the configuration option is not present, `default` will be returned.
    """
    qualified_name = f"{section}.{variable_name}"
    options = []
    if handler is bool:
        options.append("--type=bool")
    result = run_command(("git", "config", *options, qualified_name),
                         stdout=subprocess.PIPE,
                         universal_newlines=True)
    if result.returncode == 0:
        value_string = result.stdout.rstrip("\n")
        if not handler:
            return value_string
        if handler is bool:
            return value_string == "true"
        return handler(value_string)
    if result.returncode == 1:
        return default

    raise AbortError(f"Failed to retrieve config option: {qualified_name}"
                     f"{result.returncode}")


def get_option(opts, variable_name, *, handler=None, default=None):
    """
    Retrieves a command-line option, falling back to a Git configuration option
    with the same name.

    Callers *must* set the default value for the command-line option to `None`.
    """
    value = getattr(opts, variable_name)
    if value is not None:
        return value

    return get_git_config(git_extension_command_name(), variable_name,
                          handler=handler, default=default)


def git_extension_command_name(extension_name=None):
    """
    Returns the command name for a Git extension.

    For example, for an extension named `git-foo`, returns "foo".

    If `extension_name` is not specified, uses the name of the current script,
    determined from `sys.argv[0]`.

    If a command name cannot be determined, returns the extension name.
    """
    extension_name = extension_name or os.path.basename(sys.argv[0])
    return remove_prefix(extension_name, prefix="git-", default=extension_name)


def git_commit_hash(commitish, short=False):
    """
    Normalizes a commit-ish to an actual commit hash to handle things such as
    `:/COMMIT_MESSAGE`.

    Raises an `CommitNotFoundError` if no commit hash was found.
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
        raise CommitNotFoundError(commitish, exit_code=result.returncode)

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


def git_status(*paths, untracked_files="no"):
    """
    Returns a dictionary mapping (new) file paths to status results.

    Each status result will be another dictionary with keys:

        "code": The two character status code.
        "original_file_path": For renames or copies, the original file path.

    All returned file paths will be relative to the current directory.

    See the "Short Format" documentation from `git help status` for a guide to
    possible codes.
    """
    root = git_root()

    result = run_command(("git", "status", "-z",
                          f"--untracked-files={untracked_files}",
                          "--", *paths),
                         stdout=subprocess.PIPE,
                         encoding="utf-8",
                         check=True)
    tokens = result.stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()

    status_dict = {}

    tokens_iter = iter(tokens)
    try:
        while True:
            token = next(tokens_iter)

            if len(token) <= 2 or token[2] != " ":
                raise AbortError(f"Unexpected token: {token}")
            code = token[0:2]

            file_info = {"code": code}

            # `git status --porcelain` returns paths relative to the root of
            # the current git repository, not relative to the current working
            # directory.
            file_path = token[3:]
            file_path = os.path.relpath(os.path.join(root, file_path))

            if ("R" in code) or ("C" in code):
                original_file_path = next(tokens_iter)
                original_file_path = os.path.relpath(
                    os.path.join(root, original_file_path))
                file_info["original_file_path"] = original_file_path
            status_dict[file_path] = file_info

    except StopIteration:
        pass

    return status_dict


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


def git_names_for(commitish, local_branches=True, remote_branches=False,
                  tags=False):
    """Returns a list of named references for the specified commit-ish."""
    # TODO: Add option to return `HEAD`?
    result = run_command(("git", "for-each-ref", f"--points-at={commitish}"),
                         stdout=subprocess.PIPE,
                         universal_newlines=True,
                         check=True)
    lines = result.stdout.splitlines()

    prefixes = []
    if local_branches:
        prefixes.append("refs/heads/")
    if remote_branches:
        prefixes.append("refs/remotes/")
    if tags:
        prefixes.append("refs/tags/")
    if not prefixes:
        return []

    names = []
    for line in lines:
        (_commit_hash, ref_type, name) = line.split(maxsplit=2)
        if ref_type != "commit":
            continue

        for prefix in prefixes:
            commitish = remove_prefix(name, prefix=prefix)
            if commitish:
                names.append(commitish)
                break

    return names


def git_root():
    """Returns the absolute path to the root of the current Git repository."""
    result = run_command(("git", "rev-parse", "--show-toplevel"),
                         stdout=subprocess.PIPE,
                         universal_newlines=True)
    if result.returncode != 0:
        raise AbortError("Failed to determine the current git repository.",
                         exit_code=result.returncode)

    return result.stdout.rstrip("\n")
