# Static checks

Shared by every skill, by [ready-signal.md](ready-signal.md),
[self-review.md](self-review.md) and [sidebar-groups.md](sidebar-groups.md),
and by any commit a session makes outside a skill. Ported in idea from
PlaywrightTesting's `static-checks.md`; the commands are this repository's
own. This file is the only place the list is written out.
A skill that needs it points here rather than restating it.

This repository has no CI, so the checks run locally, on the branch head,
before every push to a pull request, again before the self review starts, and
before `/release` builds.

## The list

```bash
python3 -m py_compile src/*.py bin/conky-spotify-nowplaying scripts/*.py
python3 scripts/catchup_test.py
luac5.3 -p src/draw.lua src/conky.conf
sh -n build-deb.sh release-ppa.sh
desktop-file-validate packaging/conky-spotify-nowplaying.desktop
```

Every command must exit 0 and print nothing, apart from the test summary
`catchup_test.py` prints.

- `desktop-file-validate` hints count as failures to fix. It once caught a
  duplicate main category that every other check passed.
- `luac5.3 -p` only parses; it catches syntax errors, not a bad call or a
  misspelt name. `src/conky.conf` is Lua too (conky reads its config with its
  embedded interpreter), and conky links Lua 5.3, so `luac5.3` reads both
  files with the grammar conky uses. It comes from the `lua5.3` package,
  installed with apt in the user's terminal (`sudo apt install lua5.3`). While
  it is missing, the check has not run: say so in the test plan, and let the
  run below read the Lua errors out of the log instead.

## A build, when packaging could break

A change that touches `debian/` or `packaging/`, or adds, removes or renames a
file under `src/` or `bin/`, also builds the package, since only a build shows
a file `debian/install` names that is not there:

```bash
./build-deb.sh
dpkg-deb --contents dist/conky-spotify-nowplaying_*_all.deb
```

`build-deb.sh` needs `debhelper` and `devscripts`, and cleans up after itself
with `dh_clean`; `dist/` is ignored by git.

## Then a run

The checks cannot see a Lua runtime error or a path that only exists under
`/usr`. A change that touches `src/`, `bin/`, `packaging/` or `debian/` has not
been checked until it has run: from the checkout, as in `/implement` step 5 or
the `run` skill, or built and installed, as in `/release` step 4. Either way
the widget's log is read afterwards:

```bash
grep -iE 'error|traceback|lua' ~/.cache/conky-spotify-nowplaying/run.log
```

The pull request's test plan says which run was done. A change to
documentation, skills or `scripts/` alone owes no run.

## No pre-commit hook

The list is not wired into a git hook. Hooks live in `.git/hooks`, which every
worktree shares and git does not version, so one installed there would run on
every session's commits at once, including the temporary WIP commits
`/new-branch` recommends over a stash. The skills run the list at the points
named above instead.
