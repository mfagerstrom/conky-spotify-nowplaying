# conky-spotify-nowplaying

Every session files itself in the Code tab sidebar under `Blocked`, `Working`,
`Tests Running`, `Needs Review`, or `Completed` as its task moves along. The
moves are in `.claude/skills/_shared/sidebar-groups.md`.

Everything a session waits on (a Launchpad build, a pull request merge, a
blocking issue) goes through `scripts/catchup.py` with one watcher per session.
How is in `.claude/skills/_shared/run-watch.md`.
