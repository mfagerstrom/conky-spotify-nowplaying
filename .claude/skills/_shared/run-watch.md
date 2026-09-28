# Waiting on something

Shared by `/release`, by any pull request a session opens, and by
[sidebar-groups.md](sidebar-groups.md). This file is the only place the
catch-up script, the one-watcher rule and the three minute floor are written
out. A skill that needs any of them points here rather than restating them.

Ported from PlaywrightTesting's `run-watch.md`, less the GitHub Actions run
tallies: this repository has no workflows. What a session waits on here is a
Launchpad build, a pull request merge, or a blocking issue.

## Record it in a ledger

Everything a session waits on goes through `scripts/catchup.py`. Nothing else
reads it: no `gh pr checks --watch`, no loop around `curl` on Launchpad, no
`sleep` between reads. Keep one ledger per session, in the session's
scratchpad directory, and record each row as the wait starts:

```bash
scripts/catchup.py add-lp    <ledger> <version>      "<short label>"
scripts/catchup.py add-pr    <ledger> <pr-number>    "<short label>"
scripts/catchup.py add-issue <ledger> <issue-number> "<short label>"
```

Each one reads its row once, prints a `waiting:` line with the link a reader
follows, and settles the row at once if it is already finished. Recording the
same key again replaces its row. Where several rows are open, the `waiting:`
lines are what the session lists, so the whole open set reads in one place.

To read the Launchpad rows once, without waiting:

```bash
scripts/catchup.py check <ledger>
```

`check` skips pull request and issue rows; those settle through `wait`.

## One watcher per session

When the session has nothing to do but wait, it runs exactly one watcher, and
that watcher is the script:

```bash
scripts/catchup.py wait <ledger>
```

Run it as a background Bash call and end the turn. It exits as soon as any row
finishes, after printing that row's result, and ends with

```
wait: <n> finished; <m> still open; act on the result, then run wait again
```

That exit is the session's cue to act on the finished row straight away. Once
it has acted, the session starts `wait` again on the same ledger whenever rows
are still open, and ends the turn. A row recorded while `wait` is running goes
into the ledger with an `add-` command, and the same watcher picks it up.
Never start a second one.

`wait --all` exits only when every row is done. Use it only when no single
result could change what the session does next.

## The sidebar lines

The script names each sidebar move in its output, so none of them depends on
the session remembering it:

```
sidebar: a Launchpad build is open; move this session to Tests Running
sidebar: no Launchpad builds open; move this session from Tests Running to Working
sidebar: a pull request closed; move this session to Working if it still holds work ...
sidebar: a blocking issue closed; move this session from Blocked to Working, then start the deferred task again from its first step
```

A `sidebar:` line is acted on as soon as it is read, before anything else the
output asks for, per [sidebar-groups.md](sidebar-groups.md). A session that
reads a failed build and goes straight into its log without moving sits under
`Tests Running` for the whole triage, which tells the user a build is still out
when none is.

## Waiting on a Launchpad build

`/release` records the version it uploaded as soon as `release-ppa.sh` prints
`Successfully uploaded packages.`:

```bash
scripts/catchup.py add-lp <ledger> X.Y.Z "release X.Y.Z"
```

The row reads Launchpad's public API, which needs no sign-in and does not count
against the GitHub rate limit. It settles as one of these:

- `lp: X.Y.Z published`: the source is `Published` and every build
  `Successfully built`. Done.
- `lp: X.Y.Z failed`: a build ended in a state that will not recover on its own
  (`Failed to build`, `Dependency wait`, `Failed to upload`, and so on). The
  tally carries the build log link to read.
- `lp: X.Y.Z gone`: the source was superseded or deleted.
- `lp: X.Y.Z rejected`: the version never showed up in the archive within 45
  minutes of being recorded. Launchpad mails its rejections to the no-reply
  maintainer address, so this is the only sign: usually a reused version or a
  signature problem.

While it is open, the row's state says where it is (`not in the archive yet`,
`source Pending; Currently building`, and so on), and `check` prints it.

## Waiting on a merge

A session that has sent the ready signal, per
[ready-signal.md](ready-signal.md), records the pull request and lets the
watcher wait for the merge:

```bash
scripts/catchup.py add-pr <ledger> <pr-number> "<short label>"
```

When it merges, `wait` prints

```
done: <label> - <pull request url>
  merged <sha> at <time>
pr: <number> merged
```

and exits. That exit is the cue to report the merge and pick the sidebar group
straight away, without waiting for the user to mention it. `closed without
merging` means the user closed it: report that and do nothing else to the
branch.

## Waiting on a blocking issue

A session that defers its task to another session, per
[sidebar-groups.md](sidebar-groups.md#blocked-by-another-session), records the
blocker's issue and lets the watcher wait for it to close:

```bash
scripts/catchup.py add-issue <ledger> <issue-number> "<short label>"
```

When it closes, `wait` prints `issue: <number> closed` and the `sidebar:` line
out of `Blocked`, and exits. That exit is the cue to move to `Working` and
start the deferred task over from the skill's first step.

## Push notices

`wait` learns a pull request merged or an issue closed from GitHub's
`pull_request` and `issues` events, so the session wakes within seconds. The
events come through `gh webhook forward`, from the `cli/gh-webhook` extension.
The script never installs the extension, since this machine installs software
through apt only. When it is missing, `wait` says so and reads on the timer.

GitHub allows one forwarder hook per repository, so every `wait` on the
machine shares one forwarder. The first `wait` to start takes a lock under
`~/.cache/catchup/`, runs the forwarder, and writes each close to a shared
events file as one short line. Every `wait` reads that file every five seconds,
which costs no API calls. When the holder exits, the next `wait` takes over the
lock within a few seconds and starts its own forwarder.

Before starting a forwarder, the holder stops any forwarder process that
outlived its waiter, and deletes a hook one on this machine left behind or one
GitHub has marked inactive. A hook made on another machine is left alone: the
forwarder here is refused, retries after a delay that doubles from one minute
to ten, and `wait` reads on the timer meanwhile. Every five minutes the holder
confirms its hook is still on the repository and restarts the forwarder when it
is gone. A `webhook-forwarder.github.com` hook in the repository settings while
no `wait` runs on any machine is safe to delete.

A close sent while no forwarder is listening never arrives, so reads cover the
gaps. When a forwarder connects, every `wait` reads each open pull request and
issue once. While no forwarder is live, every timer check reads them too. Issue
rows are read on every timer check even while it is live, since a blocked
session can wait for days.

## The three minute floor

Three minutes is the floor for timer checks. `--interval` accepts a longer
value and silently raises a shorter one. While a Launchpad row is open, or the
push is down, `wait` checks every three minutes. While the push is live and
only pull request and issue rows are open, it checks every fifteen minutes,
just to catch a lost notice.

Accepting an upload takes a few minutes, the build about two, and publishing
another ten to thirty, so a Launchpad row is usually open for twenty to forty
minutes. That is normal and needs no check of its own.
