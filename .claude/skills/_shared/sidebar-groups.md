# Sidebar groups

Shared by every skill, and by any task a session works outside one. The
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
| `Needs Review`  | it has a pull request waiting on the user, or asked the user a question |
| `Completed`     | its last pull request merged and it holds no other work            |

PlaywrightTesting also has a `Self Review` group. This repository has no self
review step, so its sessions never use it.

## The moves

A session files itself, and only itself, with
`mcp__ccd_sidebar__move_sessions` and `session_ids: ["self"]`. Moving any
other session asks the user first, so it is never done unasked.

- Task starts, or a branch is cut for it with `/new-branch`: `Working`.
- An external build it has to wait on starts, such as the Launchpad build in
  `/release` step 7: `Tests Running`. Back to `Working` once the build
  settles, before reading its result, because triage can run long and the
  group is wrong for all of it.
- Pull request opened and handed to the user, or the turn ends on a question
  only the user can answer (a `/release` version confirmation, say):
  `Needs Review`. This holds for every pull request the session opens,
  including one opened on the side of a longer task.
- The user answers, or review comments come in to act on: `Working`.
- Pull request merged or closed: pick the group by what the session still
  holds. Any of these means `Working`:

  - another open pull request of its own that still needs work;
  - a background task or subagent still running;
  - anything the user asked for in this session that is not finished yet.

  A session whose other pull request is waiting on the user goes to
  `Needs Review`. Only when nothing is left does it go to `Completed`.
- A task that opened no pull request, a finished `/release` for example, goes
  to `Completed` when it ends with nothing left to wait on.
- Task held by another session: `Blocked`. See the next section.

## Blocked by another session

A session is blocked when the work it was asked for is held by another
session: the issue already has a branch or pull request from someone else, or
a change it needs sits in a pull request that has not merged.

1. Report who holds the work and what it is waiting on.
2. Move to `Blocked`.
3. End the turn.

It leaves `Blocked` for `Working` when the user invokes it again, and starts
the task over from the skill's first step, because the blocker may have
already done some or all of it. When the blocker finished the task, report
that and go to `Completed`.

A session with anything of its own still open files by that instead, and
`Blocked` waits until it is the last thing left:

- an external build still running: `Tests Running`
- a pull request waiting on the user, or a question to the user: `Needs Review`
- work it can still do while it waits: `Working`

## Rules

- Look the group up by name with `mcp__ccd_sidebar__list_groups` before each
  move, and create a missing one with `mcp__ccd_sidebar__create_group` under
  the exact name above. Ids are not stored anywhere.
- Custom groups only show when the sidebar is grouped by `custom`. The session
  never changes the view itself.
- A subagent does not move sessions. The session that spawned it moves itself.
- A sidebar tool that fails or is not loaded is reported in one line and
  skipped. The groups are a convenience, never a gate on any step.
