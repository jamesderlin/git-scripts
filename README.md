# git-scripts

A collection of various custom Git commands that I've written.

## Commands

### `git-attach`

Attaches a detached Git head to a local named branch if possible. (Undoes
`git-detach`.)

### `git-detach`

Creates a detached head.

### `git-diff1`

Shows the diff for a single commit.

### `git-have-commit`

Reports whether one Git commit is an ancestor of another.

### `git-next`

Navigates to a child commit.  Similar to `hg next`.  Interactively prompts if
there are multiple children.

### `git-prev`

Navigates to a parent commit.  Similar to `hg prev`.  Interactively prompts if
there are multiple parents.

### `git-reparent`

A wrapper around `git rebase` that attempts to use a friendlier command-line
syntax: `git reparent --dest=COMMIT START::END`

### `git-resolve`

Interactively manages and resolves merge conflicts.  Unlike `git mergetool`,
opens conflicting files in a text editor instead of in a graphical merge tool.
Additionally will never automatically add files that still contain conflict
markers.

## Installation

```shell
git clone --recurse-submodules https://github.com/jamesderlin/git-scripts.git
```

`git-scripts` depends on submodules, so `--recurse-submodules` is necessary
when using `git clone`.  Setting `git config submodule.recurse true` is also
recommended so that submodules are automatically and appropriately updated when
the parent repository is updated.

Then either add the repository directory to your `PATH` environment variable or
create symlinks in a directory already in `PATH`.  For example, on a POSIX
system:

```shell
ln -s git-* ~/bin/
```

Using symlinks allows easily enabling only specific commands.

---

Copyright © 2015-2021 James D. Lin.
