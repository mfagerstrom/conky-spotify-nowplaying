# conky-spotify-nowplaying

Every session files itself in the Code tab sidebar under `Blocked`, `Working`,
`Tests Running`, `Self Review`, `Needs Review`, or `Completed` as its task
moves along. The moves are in `.claude/skills/_shared/sidebar-groups.md`.

Everything a session waits on (a Launchpad build, a pull request merge, a
blocking issue) goes through `scripts/catchup.py` with one watcher per session.
How is in `.claude/skills/_shared/run-watch.md`.

Every pull request a session opens gets its own self review before it is
handed over, and ends in the ready signal. The loop is in
`.claude/skills/_shared/self-review.md`, the signal and its gates in
`.claude/skills/_shared/ready-signal.md`, and the static checks every push
passes in `.claude/skills/_shared/static-checks.md`.

Issues are worked end to end with `/implement <number>`: claim, branch, work,
pull request, self review, handoff, and cleanup after the merge.
