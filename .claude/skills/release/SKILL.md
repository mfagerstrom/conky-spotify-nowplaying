---
name: release
description: >-
  Cut a release of conky-spotify-nowplaying end to end: add the debian/changelog
  entry, run the static checks, build the .deb and smoke-test it installed, commit
  and tag, publish the GitHub release with the .deb attached, upload the signed
  source package to the Launchpad PPA, and confirm Launchpad builds and publishes
  it. Use when asked to release, ship, publish, cut or tag a version -
  "/release", "/release 1.0.2", "ship this", "put out a new version".
---

# Release

A release goes out through three channels that must carry the same version:
the git tag, the GitHub release `.deb`, and the Launchpad PPA
(`ppa:mfagerstrom/conky-spotify-nowplaying`, series `resolute`). The version
lives only in `debian/changelog`; `build-deb.sh` and `release-ppa.sh` read it
from there.

Steps 5-7 publish to places other people see. Confirm the version and the
changelog text with the user before step 5, and don't continue past a failed
step.

The session files itself in the sidebar as the release moves along, per
[sidebar-groups.md](../_shared/sidebar-groups.md): `Working` from the start,
`Needs Review` while it waits on the version confirmation in step 2,
`Tests Running` while Launchpad builds in step 7, and `Completed` at the
report.

## 0. Preconditions

- `git status` is clean and `main` is up to date with `origin/main`
  (`git fetch origin && git status -sb`). Releases are cut from `main`; if the
  work is on a branch, it gets merged first.
- Tools: `dch`, `debuild`, `dpkg-buildpackage`, `dput` (from
  `debhelper devscripts dput`), `gh` logged in, and the signing key
  `5DACA320C75920B4F632EC636F3AA291325FC325` in `gpg --list-secret-keys`.
  Missing tools are installed with apt in the user's terminal (sudo).

## 1. Pick the version

If the user gave one, use it. Otherwise list what changed since the last tag and
propose one:

```bash
git describe --tags --abbrev=0
git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
```

Patch (`1.0.1` -> `1.0.2`) for fixes, minor for new features. Launchpad rejects
a version it has seen before, even for a failed upload, so a version is never
reused - a broken release is fixed with the next number.

## 2. Changelog and README

Write the entry non-interactively (`dch` would otherwise open an editor):

```bash
DEBFULLNAME=mfagerstrom DEBEMAIL=48690419+mfagerstrom@users.noreply.github.com \
  dch -v X.Y.Z -D resolute --force-distribution "First change."
DEBFULLNAME=mfagerstrom DEBEMAIL=48690419+mfagerstrom@users.noreply.github.com \
  dch -a "Next change."
```

One bullet per user-visible change, written for someone installing the package.
Update the version in the README's `.deb` install command to match. Show the
entry to the user and get the version confirmed.

## 3. Static checks

All must pass before building:

```bash
python3 -m py_compile src/*.py bin/conky-spotify-nowplaying scripts/*.py
python3 scripts/catchup_test.py
sh -n build-deb.sh release-ppa.sh
desktop-file-validate packaging/conky-spotify-nowplaying.desktop
dpkg-parsechangelog -S Version        # prints the new X.Y.Z
```

`desktop-file-validate` hints count as failures to fix. There is no Lua
checker installed; `draw.lua` and `conky.conf` errors show up in the smoke test.

## 4. Build and smoke-test the installed package

```bash
./build-deb.sh
dpkg-deb --contents dist/conky-spotify-nowplaying_X.Y.Z_all.deb
dpkg-deb -f dist/conky-spotify-nowplaying_X.Y.Z_all.deb Depends
```

Check the listing: every directory `drwxr-xr-x`, the scripts under
`/usr/share/conky-spotify-nowplaying/` present, and any new file that the code
needs actually listed in `debian/install`.

Then install it for real - running from the checkout does not catch path bugs
that only exist under `/usr` (a checkout-detection bug once made the installed
launcher look in `/usr/src`). Install in the user's terminal, because it needs
sudo:

```bash
sudo apt install ./dist/conky-spotify-nowplaying_X.Y.Z_all.deb
```

Then restart and verify:

```bash
: > ~/.cache/conky-spotify-nowplaying/run.log
conky-spotify-nowplaying restart
pgrep -af 'conky-spotify-nowplaying|share/conky-spotify|^conky '
xwininfo -root -tree | grep ConkySpotifyNowPlaying
grep -iE 'error|traceback|lua' ~/.cache/conky-spotify-nowplaying/run.log
gdbus call --session --dest org.kde.StatusNotifierWatcher \
  --object-path /StatusNotifierWatcher \
  --method org.freedesktop.DBus.Properties.Get \
  org.kde.StatusNotifierWatcher RegisteredStatusNotifierItems
```

Expect the supervisor plus `nowplaying.py`, `conky` and `conky-mouse.py`
running from `/usr/share`, plus `tray.py`. Also expect the window present, no
errors in the log, and `conky_spotify_nowplaying` in the tray list. The log's
`libayatana-appindicator is deprecated` warning is harmless. If Spotify is
playing, capture the window and look at it before going on.

## 5. Commit, tag, push

```bash
git add -A
git commit -m "Release X.Y.Z"      # body: the changelog bullets
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main vX.Y.Z
```

## 6. GitHub release

```bash
gh release create vX.Y.Z dist/conky-spotify-nowplaying_X.Y.Z_all.deb \
  --title "vX.Y.Z" --notes "<changelog bullets, then the apt install line>"
gh release view vX.Y.Z --json assets --jq '.assets[].name'
```

## 7. PPA upload

Run in the user's terminal - GnuPG asks for the key's passphrase there, and
only the user types it:

```bash
./release-ppa.sh
```

It ends with `Successfully uploaded packages.` Then record the version in the
session's ledger and let the one watcher follow Launchpad, per
[run-watch.md](../_shared/run-watch.md#waiting-on-a-launchpad-build).
Accepting the upload takes a few minutes, the build about two, and publishing
another 10-30:

```bash
scripts/catchup.py add-lp <ledger> X.Y.Z "release X.Y.Z"
scripts/catchup.py wait <ledger>        # background Bash call; end the turn
```

`add-lp` prints the move to `Tests Running`, and the `wait` that settles the
row prints the move back to `Working`; make each move as soon as it is read.
Launchpad's emails go to the no-reply maintainer address, so nobody is
notified; the watcher is the only way to know.

- `lp: X.Y.Z published` -> done.
- `lp: X.Y.Z failed` -> read the build log linked in the tally, fix, and
  release the next patch version. Launchpad build machines sometimes fail for
  their own reasons; if the log shows nothing wrong with the package, the user
  can press **Retry** on the build page, and the version is recorded again
  with `add-lp`.
- `lp: X.Y.Z rejected` -> the upload never appeared (usually a reused version
  or a signature problem); check the version and `release-ppa.sh`'s signing
  key.

## 8. Report

Say what shipped: the version, the GitHub release link, the PPA build state
(and whether it's published yet), the smoke-test result, and anything skipped.
