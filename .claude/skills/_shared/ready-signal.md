# The ready signal

Shared by any pull request a session opens, inside a skill or outside one.
Ported from PlaywrightTesting's `ready-signal.md`. A session that opened a pull
request ends its report with one line, so the user sees what is waiting on
them without reading back through the report. Its wording and the rules for
withholding it are written out here once.

It is the last line of the report, on its own, in capitals, with nothing else
on it:

```
PULL REQUEST <number> IS READY FOR YOUR REVIEW
```

It goes below everything else the report carries, the open questions
included.

Sending it moves the session to the `Needs Review` sidebar group. The only
other way into that group is a question to the user, per
[sidebar-groups.md](sidebar-groups.md). The line and the move both wait on the
static checks, the mergeability check, and the session's own review of the
pull request, per [self-review.md](self-review.md). The merge moves it to
`Completed` when nothing else is open, or back to `Working` when the session
still holds work, per [sidebar-groups.md](sidebar-groups.md).

The line goes out in the turn the pull request becomes ready, even when the
session is in the middle of something longer. When several are ready, each
gets its own line.

## When it is withheld

The line says the pull request needs nothing further from the session. So it
is withheld while a static check fails, while the pull request conflicts with
its base, and while the self review in [self-review.md](self-review.md) has not
ended. The order is always: static checks and mergeability, self review, ready
signal.

A task that opened no pull request says nothing in its place. There is no
variant of the line for work that did not reach one.

## The static checks come first

This repository has no CI, so the checks run locally, on the branch head,
before every push to a pull request and again before the self review starts.
This is the one list; `/release` step 3 runs it too:

```bash
python3 -m py_compile src/*.py bin/conky-spotify-nowplaying scripts/*.py
python3 scripts/catchup_test.py
sh -n build-deb.sh release-ppa.sh
desktop-file-validate packaging/conky-spotify-nowplaying.desktop
```

`desktop-file-validate` hints count as failures to fix. A change that touches
`src/`, `bin/`, `packaging/` or `debian/` has not been checked until it has
run: use the `run` skill, or build and install the package as in `/release`
step 4, and say in the pull request's test plan which one was done. A change to documentation
or skills alone owes no run.

## The mergeability check comes first

Straight after `gh pr create`, and again when the self review ends, read the
pull request's state without being asked:

```bash
gh pr view <number> --json mergeable,mergeStateStatus
```

`MERGEABLE` means the check is done. `UNKNOWN` means GitHub has not worked it
out yet, which is usual for a few seconds after `gh pr create` or a push:
read it again after a short pause, and treat it as not yet checked until it
says one or the other. `CONFLICTING` means the session resolves the conflicts
now, on its own, before it reports anything:

```bash
git fetch origin
git merge origin/<base>
git diff --name-only --diff-filter=U
```

Read both sides of every hunk before writing the resolution. The resolution is
almost always the union of the two, in the order the surrounding file already
uses, rather than either side winning; one that drops the other side's work is
a silent revert of a merged branch. Never rebase and never force-push: the
branch is pushed, so a merge commit is the correct shape. Rerun the static
checks, push, and read the state again, since the second read is what proves
the resolution took. The report names the file that conflicted and what the
union kept.

## A stacked pull request

A pull request cut from another unmerged branch, per `/new-branch`, targets
that parent branch. This repository does not delete branches on merge, so
GitHub never moves the child to `main` when the parent merges. When the child
is opened, record the parent in the session's ledger with
`scripts/catchup.py add-pr`, unless it is there already, since a parent
another session opened is not. When the watcher prints the parent's
`pr: <number> merged`, retarget the child before anything else, and read its
mergeability again:

```bash
gh api -X PATCH repos/{owner}/{repo}/pulls/<child> -f base=main
```

A child left on its merged parent would merge into a dead branch, and its work
would never reach `main`. The report above a child's ready signal names its
parent, so the user merges the parent first.

## Listening for the merge

Once the line is sent, the session records the pull request with
`scripts/catchup.py add-pr` and leaves the one watcher running, per
[run-watch.md](run-watch.md#waiting-on-a-merge). The merge arrives as a push
from GitHub, and the session acts on it without the user having to report it.
