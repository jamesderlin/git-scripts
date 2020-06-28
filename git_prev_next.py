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
    """Prompts the user to choose among several commits."""
    if mode == Mode.PREV:
        instructions = "There are multiple parents:"
        prompt = "Enter the parent index"
    else:
        instructions = "There are multiple children:"
        prompt = "Enter the child index"

    commit_descriptions = [gitutils.summarize_git_commit(commit_hash, "%h %s")
                           for commit_hash in commit_hashes]

    selected_index = gitutils.prompt_with_choices(commit_descriptions,
                                                  preamble=instructions,
                                                  prompt=prompt)
    return commit_hashes[selected_index]


def main(mode, description, argv):
    ap = argparse.ArgumentParser(description=description.strip(), add_help=False)
    ap.add_argument("-h", "--help", action="help",
                    help="Show this help message and exit.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print verbose debugging messages.")

    args = ap.parse_args(argv[1:])

    gitutils.verbose = args.verbose

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

    command = ("git", "checkout", "--detach", selected_hash)
    return gitutils.run_command(command).returncode
