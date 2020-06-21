#!/usr/bin/env python3

"""Navigates to a child commit."""

import argparse
import os
import sys

import gitutils


@gitutils.entrypoint(globals())
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.strip(), add_help=False)
    ap.add_argument("-h", "--help", action="help",
                    help="Show this help message and exit.")
    ap.add_argument("--verbose", action="store_true",
                    help="Print verbose debugging messages.")

    args = ap.parse_args(argv[1:])

    gitutils.verbose = args.verbose

    commit_graph = gitutils.git_commit_graph()
    head_hash = gitutils.git_commit_hash("HEAD")

    children_hashes = [child.commit_hash
                       for child in commit_graph.get(head_hash).children]
    if not children_hashes:
        head_hash_short = gitutils.git_commit_hash(head_hash, short=True)
        raise gitutils.AbortError(f"Could not find a child commit for "
                                  f"{head_hash_short}")

    if len(children_hashes) == 1:
        selected_child_hash = children_hashes[0]
    else:
        selected_child_hash = prompt_for_child(children_hashes)

    return gitutils.run_command(
        ("git", "checkout", "--detach", selected_child_hash),
        bufsize=1).returncode


if __name__ == "__main__":
    __name__ = os.path.basename(__file__)  # pylint: disable=redefined-builtin

    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        sys.exit(1)
