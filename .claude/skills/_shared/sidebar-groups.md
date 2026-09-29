# Sidebar groups

Shared by every skill, by [run-watch.md](run-watch.md),
[self-review.md](self-review.md) and [ready-signal.md](ready-signal.md), and by
any task a session works outside one. The
desktop app's own session states cannot be extended, so the Code tab sidebar
carries custom groups that say where each session stands. This file is the
only place the groups and their moves are written out.

The groups are app-wide, not per repository, and PlaywrightTesting files its
sessions into the same ones. Keep the names exactly as below so both
repositories share one set of groups instead of making near-duplicates.

| Group           | The session is in it while                                         |
| --------------- | ------------------------------------------------------------------ |
| `Blocked`       | its task waits on another session, and it has nothing else to do   |
| `Working`       | it holds a task and is editing, reading, or deciding               |
| `Tests Running` | it is waiting on an external build it started (a Launchpad build)  |
| `Self Review`   | it is reviewing and fixing its own pull request before handoff     |
| `Needs Review`  | it has sent the ready signal, or asked the user a question         |
| `Completed`     | its last pull request merged and it holds no other work            |

## The moves

A session files itself, and only itself, with
`mcp__ccd_sidebar__move_sessions` and `session_ids: ["self"]`. Moving any
other session asks the user first, so it is never done unasked.

- Task starts, or a branch is cut for it with `/new-branch`: `Working`.
- A Launchpad build is recorded with `scripts/catchup.py add-lp`, as in
  `/release` step 7: `Tests Running`. `add-lp` prints
  `sidebar: a Launchpad build is open` when it records it.
- `check` or `wait` shows no Launchpad build still open: back to `Working`.
  Both print `sidebar: no Launchpad builds open` at that point. The move is
  the first thing the session does with that output, before it reads the
  result, because triage of a failed build can run long and the group is
  wrong for all of it.
- Pull request open, its static checks
  ([static-checks.md](static-checks.md)) passing and mergeable: `Self Review`,
  while the session reviews it and fixes what the review finds, per
  [self-review.md](self-review.md). A session whose static checks fail or
  whose pull request conflicts stays in `Working` until that is fixed.
- Self review ended and ready signal sent, or the turn ends on a question only
  the user can answer (a `/release` version confirmation, say):
  `Needs Review`. The ready signal waits on the self review ending, per
  [ready-signal.md](ready-signal.md). This holds for every pull request the
  session opens, including one opened on the side of a longer task. The
  session records the pull request with `scripts/catchup.py add-pr` and leaves
  the one `wait` running, per [run-watch.md](run-watch.md#waiting-on-a-merge),
  so the merge reaches it without the user reporting it.
- The user answers a question the self review put: back to `Self Review`.
- The user answers anything else, or review comments come in to act on:
  `Working`. The commits that answer them go back through the self review
  loop in `Self Review` before the signal goes out again, per
  [self-review.md](self-review.md#commits-after-the-handoff).
- `pr: <number> merged` or `closed without merging`: pick the group by what
  the session still holds. Any of these means `Working`:

  - an open row in the ledger, a Launchpad build or another pull request;
  - another open pull request of its own that still needs work;
  - a background task or subagent still running;
  - anything the user asked for in this session that is not finished yet.

  A session whose other pull request is waiting on the user goes to
  `Needs Review`, and one whose other pull request is still in self review
  goes to `Self Review`. Only when nothing is left does it go to `Completed`.
  `wait` prints a `sidebar:` line with every closed pull request as the
  reminder, and the move comes before anything else the session does with
  that output.
- A task that opened no pull request, a finished `/release` for example, goes
  to `Completed` when it ends with nothing left to wait on.
- Task held by another session: `Blocked`. See the next section.
- `issue: <number> closed`: `Working`, then the deferred task starts over.
  See the next section.

## Blocked by another session

A session is blocked when the work it was asked for is held by another
session: the issue already has a branch or pull request from someone else, or
a change it needs sits in a pull request that has not merged.

1. Report who holds the work and what it is waiting on.
2. Move to `Blocked`.
3. Record the blocker in the session's ledger, and make sure one `wait` is
   running on it, per
   [run-watch.md](run-watch.md#waiting-on-a-blocking-issue):

   ```bash
   scripts/catchup.py add-issue <ledger> <blocking issue> "<short label>"
   ```

   The blocking issue is the one whose close means the way is clear. When the
   blocker is a pull request with no issue behind it, record the pull request
   with `add-pr` instead, and read its `pr: <number> merged` the same way.
4. End the turn. The watcher is the only thing running.

When `wait` prints `issue: <number> closed` and
`sidebar: a blocking issue closed`, the session moves to `Working` before
anything else, then starts the deferred task over from the skill's first
step, because the blocker may have already done some or all of it. When the
deferred task's own issue is now closed, the holder finished it: report that
and go to `Completed`. A blocker closed as not planned frees the work the same
way; the session says so in its report.

A blocker whose hold ends without a close or a merge cannot be watched this
way. The session moves to `Blocked`, records no row, and leaves `Blocked` when
the user invokes it again.

A session with anything of its own still open files by that instead, and
`Blocked` waits until it is the last thing left:

- a Launchpad build still open: `Tests Running`
- a pull request of its own still in self review: `Self Review`
- a pull request waiting on the user, or a question to the user: `Needs Review`
- work it can still do while it waits: `Working`

## Rules

- Look the group up by name with `mcp__ccd_sidebar__list_groups` before each
  move, and create a missing one with `mcp__ccd_sidebar__create_group` under
  the exact name above. Ids are never written into a skill or a script; the
  Stop hook below keeps its own cache of them.
- Custom groups only show when the sidebar is grouped by `custom`. The session
  never changes the view itself.
- A subagent does not move sessions. The session that spawned it moves itself.
- A sidebar tool that fails or is not loaded is reported in one line and
  skipped. The groups are a convenience, never a gate on any step. The Stop
  hook below sends a turn back once at most, so a move that cannot be made
  does not hold the session.

## The Stop hook

`scripts/enforce_sidebar_move.py`, run from `.claude/settings.json`, holds a
turn open until the session is filed where the turn's latest milestone puts
it. It reads the transcript since the last prompt the user typed, and counts
a milestone only when its command's output shows it ran:

| Milestone                                                  | Group                                   |
| ---------------------------------------------------------- | --------------------------------------- |
| `gh issue edit --add-label "In Progress"` printed the URL  | `Working` or `Needs Review`             |
| `gh pr create` printed the pull request URL                | `Needs Review`                          |
| `catchup.py add-issue` printed `waiting for close:`        | `Blocked` or `Needs Review`             |
| `catchup.py add-lp` printed `waiting:`                     | `Tests Running`                         |
| `sidebar: no Launchpad builds open` from `check` or `wait` | `Working`, `Needs Review` or `Completed` |
| `pr: <n> merged` or `closed` from `wait`                   | `Completed` or `Working`                |

With another pull request still open in the ledger, a close or a settled
build calls for `Working` or `Needs Review` instead of `Completed`, and a
Launchpad build still open adds `Tests Running`. A turn with no milestone
that ends with a build open in the ledger calls for `Tests Running`, and one
with a pull request open calls for `Needs Review`. A turn that ends under
`Self Review` stopped inside the loop, which only ends a turn on a question,
filed under `Needs Review`.

A wrong or missing group blocks the stop with a reason naming the group. A
stop that a block already sent back goes through, so the hook never loops.
Group ids resolve to names from `list_groups` results in the transcript and
from `~/.cache/claude-sidebar/groups.json`, which a `PostToolUse` hook on
`list_groups` keeps. Ids are app-wide, so the cache is shared by every
repository.
