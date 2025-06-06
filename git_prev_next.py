#!/usr/bin/env python3

"""Navigates to a parent or child commit."""

import argparse
import enum

import gitutils


class Mode(enum.Enum):
    """Whether we're being run via `git-prev` or via `git-next`."""
    PREV = enum.auto()
    NEXT = enum.auto()


def prompt_for_hash(mode, commit_hashes):
    """
    Prompts the user to choose among several commits.

    Returns the selected commit hash.
    """
    if mode == Mode.PREV:
        instructions = "There are multiple parents:"
        prompt = "Enter the parent index"
    else:
        instructions = "There are multiple children:"
        prompt = "Enter the child index"

    commit_descriptions = [gitutils.summarize_git_commit(commit_hash, "%h %s")
                           for commit_hash in commit_hashes]

    selected_index \
        = gitutils.prompt_with_numbered_choices(commit_descriptions,
                                                preamble=instructions,
                                                prompt=prompt)
    if selected_index is None:
        raise gitutils.AbortError(cancelled=True)
    return commit_hashes[selected_index]


def main(mode, description, argv):
    command_name = gitutils.git_extension_command_name()
    ap = argparse.ArgumentParser(description=description.strip(), add_help=False)
    ap.add_argument("-h", "--help", action="help",
                    help="Show this help message and exit.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print verbose debugging messages.")
    ap.add_argument("-a", "--attach", action="store_true", default=None,
                    help=f"Automatically attach to a local branch if "
                         f"possible.  This can be enabled by default by "
                         f"setting the `{command_name}.attach` configuration "
                         f"variable to `true`.")

    args = ap.parse_args(argv[1:])

    gitutils.verbose = args.verbose
    attach = gitutils.get_option(args, "attach", handler=bool, default=False)

    # Called for the side-effect of verifying that we're in a git repository.
    gitutils.git_root()

    commit_graph = gitutils.git_commit_graph()
    head_hash = gitutils.git_commit_hash("HEAD")
    head_node = commit_graph.get(head_hash)

    target_nodes = (head_node.parents
                    if mode == Mode.PREV
                    else head_node.children)
    target_hashes = [node.commit_hash for node in target_nodes]
    if not target_hashes:
        head_hash_short = gitutils.git_commit_hash(head_hash, short=True)
        message = (f"Could not find a parent commit for {head_hash_short}"
                   if mode == Mode.PREV
                   else f"Could not find a child commit for {head_hash_short}")
        raise gitutils.AbortError(message)

    if len(target_hashes) == 1:
        selected_hash = target_hashes[0]
    else:
        selected_hash = prompt_for_hash(mode, target_hashes)

    selected_branch = None
    if attach:
        local_branches = gitutils.git_names_for(selected_hash)
        if len(local_branches) > 0:
            selected_branch = gitutils.prompt_for_branch(local_branches,
                                                         selected_hash)

    command = ["git", "checkout"]
    if selected_branch:
        command.append(selected_branch)
    else:
        command += ["--detach", selected_hash]
    return gitutils.run_command(command).returncode
