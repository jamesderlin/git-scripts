"""Common utility classes and functions shared among various Git scripts."""

import argparse
import functools
import importlib
import importlib.machinery
import importlib.util
import math
import os
import shlex
import shutil
import subprocess
import sys

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


class PassThroughOption(argparse.Action):
    """
    Handles an option meant to be passed through to another command.  Appends
    the option and its arguments to a list specified by `dest`.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        if not values:
            new_value = option_string
        else:
            values_string = shlex.quote(" ".join((shlex.quote(token)
                                                  for token in values)))

            # If the option has an argument, always use the forms `-oARGUMENT`
            # or `--option=ARGUMENT` instead of separating the argument with
            # a space.   Some `git` commands (e.g. `git diff`) will treat
            # option arguments separated by spaces as ambiguous.
            new_value = (("{option_string}={values_string}"
                          if option_string.startswith("--")
                          else "{option_string}{values_string}")
                         .format(option_string=option_string,
                                 values_string=values_string))

        # argparse initially adds `self.dest` to `namespace` with its default
        # value, so we can't use `getattr`'s default argument.
        old_values = getattr(namespace, self.dest) or []
        old_values.append(new_value)
        setattr(namespace, self.dest, old_values)


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
    Truncates a string to the specified maximum width (in code points).

    The maximum width includes the added ellipsis if the string is truncated.

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


def prompt_with_choices(prompt, choices, *, default=None,
                        invalid_handler=None):
    """
    Prompts the user to choose from a fixed list of choices.

    `prompt` specifies the string to print when prompting for input.

    `choices` must be an iterable where each element is either a single string
    or a tuple of strings.  Each tuple consists of acceptable inputs for a
    single choice.

    Returns a string indicating the selected choice.  If the choice corresponds
    to a tuple, the first element of the chosen tuple is considered canonical
    and will be returned.

    Returns `None` if the user enters EOF to exit the prompt or if `choices` is
    empty.

    Choice selection is case-insensitive but case-preserving.  That is, an
    available choice of `"Yes"` will match against input of `"YES"`, and
    `"Yes"` will be returned.  Leading and trailing whitespace is ignored.

    `default` specifies the default value to return if the user accepts the
    prompt with empty input (an empty string or a string consisting entirely of
    whitespace). `default` must be either be `None` or a string in `choices`.
    If `None`, the user will be required to provide valid, non-empty input (or
    enter EOF) to exit the prompt.

    If the user enters an invalid choice, `invalid_handler` will be called with
    the invalid input and should return an appropriate error message.  If
    `invalid_handler` is `None`, a default error message will be generated.
    `invalid_handler` can be used to provide more details about what legal
    inputs are.

    Example:
    ```python
    response = prompt_with_choices("Continue? (y/N) ",
                                   [("y", "yes"), ("n", "no")],
                                   default="n")
    if response is None:
        raise AbortError(cancelled=True)
    if response == "y":
        # ...
    else:
        assert response == "n"
        # ...
    ```
    """
    def normalize_choice_str(s):
        """
        Helper function to transform a choice string to a standard
        representation for easier comparison later.
        """
        assert isinstance(s, str)
        return s.strip().casefold()

    if not choices:
        return None

    # Transform top-level elements that are single strings to be one-element
    # tuples.
    choices = [(choice,)
               if isinstance(choice, str)
               else choice
               for choice in choices]

    choices_table = {
        normalize_choice_str(choice_str): choice_tuple[0]
        for choice_tuple in choices
        for choice_str in choice_tuple
    }

    if default is not None:
        default = normalize_choice_str(default)
        assert default in choices_table

    def default_invalid_handler(response):
        return f"\"{response}\" is not a valid choice."

    invalid_handler = invalid_handler or default_invalid_handler

    while True:
        try:
            raw_response = input(prompt)
            normalized_response = normalize_choice_str(raw_response)
        except EOFError:
            print()
            return None

        if not normalized_response:
            if default is None:
                continue
            return choices_table[default]

        try:
            return choices_table[normalized_response]
        except KeyError:
            print(invalid_handler(raw_response))

        print()


def prompt_with_numbered_choices(choices,
                                 *,
                                 default_index=None,
                                 preamble="",
                                 prompt=None):
    """
    Prompts the user to choose from a potentially variable list of choices.
    `prompt_with_numbered_choices` is more suitable than `prompt_with_choices`
    for cases where the choices are not known until runtime.

    Returns the (zero-based) index of the selected choice.  If there is only
    one choice, returns 0 without prompting.

    Returns `None` if the user cancels, quits the prompt, or if `choices` is
    empty.

    `default_index` specifies the (zero-based) index of the choice to use as
    the default if the user accepts the prompt with empty input (an empty
    string or a string consisting entirely of whitespace).

    `prompt` specifies the string to print whenever the user is prompted for
    input.  If `None`, a default prompt strng will be generated automatically.

    `preamble` specifies a string to print before the list of choices is
    printed. `preamble` and the list of choices are printed before the initial
    prompt and whenever the user explicitly requests them by entering `"help"`.
    """
    if not choices:
        return None

    max_choice = len(choices)
    if max_choice == 1:
        # If there's only one choice, don't bother prompting.
        return 0

    assert (default_index is None) or (0 <= default_index < max_choice)

    max_length = terminal_size().columns - 1
    instructions = "\n".join([
        *((preamble,) if preamble else ()),
        *(ellipsize(f"  {i}: {choice}", width=max_length)
          for (i, choice) in enumerate(choices, 1)),
    ])

    print(instructions)

    default_hint = "" if default_index is None else f"[{default_index + 1}] "
    default_prompt = (f"[1, 2]: {default_hint}"
                      if max_choice == 2
                      else f"[1..{max_choice}]: {default_hint}")
    prompt = f"{prompt} {default_prompt}" if prompt else default_prompt

    allowed_inputs = ([str(i + 1) for i in range(max_choice)]
                      + [("?", "h", "help"), ("q", "quit")])

    def invalid_handler(response):
        return (f"\"{response}\" is not a valid choice.\n"
                f"The entered choice must be between 1 and {max_choice}, "
                f"inclusive.\n"
                f"Enter \"help\" to show the choices again or \"quit\" to "
                f"quit.")

    while True:
        response = prompt_with_choices(prompt, allowed_inputs,
                                       default=(None
                                                if default_index is None
                                                else str(default_index + 1)),
                                       invalid_handler=invalid_handler)
        if response is None or response == "q":
            return None

        if response == "?":
            print()
            print(instructions)
            continue

        choice = int(response)
        assert 1 <= choice <= max_choice
        return choice - 1


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


def get_option(args, variable_name, *, handler=None, default=None):
    """
    Retrieves a command-line option, falling back to a Git configuration option
    with the same name.

    Callers *must* set the default value for the command-line option to `None`.
    """
    value = getattr(args, variable_name)
    if value is not None:
        return value

    return get_git_config(git_extension_command_name(), variable_name,
                          handler=handler, default=default)


def git_extension_command_name(extension_name=None):
    """
    Returns the command name for a Git extension.

    For example, for an extension named `git-foo`, returns "foo".

    If `extension_name` is not specified, uses the name of the current script.
    If a command name cannot be determined, returns the extension name.
    """
    extension_name = extension_name or os.path.basename(sys.argv[0])
    return remove_prefix(extension_name, prefix="git-", default=extension_name)


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
