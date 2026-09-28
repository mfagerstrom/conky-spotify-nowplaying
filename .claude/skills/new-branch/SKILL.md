---
name: new-branch
description: Start a working branch from a freshly fetched origin/main. Use whenever new work needs a branch - "start a branch", "branch for issue N", "let's work on X" - and before the first edit of any task that does not already have one.
---

# New Branch

Every branch starts from current `origin/main`, never from whatever happens to
be checked out. Branching off the previous task's branch silently drags its
commits into the next pull request.

Cutting the branch starts the task, so move the session to the `Working`
sidebar group, per
[sidebar-groups.md](../_shared/sidebar-groups.md).

## 1. Protect existing work

Run `git status` first.

- Clean -> continue.
- Uncommitted work that belongs to the *current* branch -> commit it there
  before switching.
- Uncommitted work that belongs to the *new* branch -> leave it in the working
  tree; `git checkout -b` carries it across.

Never bare `git stash` / `git stash pop`. The stash stack is shared with the
main checkout and every other worktree, so another session's entry can be
popped. Prefer a temporary WIP commit. If a stash is unavoidable, use
`git stash push -u -m "<unique-tag>"`, capture the SHA from
`git stash list --format='%H %gs'`, and restore with `git stash apply <sha>`.

## 2. Branch from origin/main

```bash
git fetch origin main
git checkout -b <type>/issue-<N>-<slug> origin/main
```

Fetch-then-branch rather than `git checkout main && git pull`: this repo uses
worktrees, and `main` cannot be checked out in two of them at once. Branching
directly from the `origin/main` ref works in every worktree and never needs
`main` checked out locally.

`<type>` is one of `fix`, `feat`, `chore`, `docs`.
Include the issue number when one exists - `fix/issue-12-lyrics-skip-race`.

## 3. Verify the base

```bash
git log --oneline origin/main..HEAD
```

Must print nothing. Any commit listed here means the branch was cut from the
wrong base and those commits will land in the pull request. Fix it before
editing:

```bash
git checkout -b <name>-v2 origin/main   # re-cut, then move work over
```

## Overriding the base

Only branch from something other than `origin/main` when the user explicitly
asks, or when the work genuinely stacks on an unmerged branch. Say which base
was used and why. When stacking intentionally, target the pull request at that
parent branch (`gh pr create --base <parent>`) so the diff stays scoped.
