#!/bin/sh
# Builds the binary package into dist/ from the debian/ packaging (the same files
# Launchpad builds the PPA from). Needs: sudo apt install debhelper devscripts
set -e
cd "$(dirname "$0")"
# dpkg-buildpackage writes into the parent directory, which every git worktree of
# this repository shares, so builds hold a lock on it and take turns
if [ -z "$BUILD_DEB_LOCKED" ]; then
    BUILD_DEB_LOCKED=1 exec flock .. "./$(basename "$0")" "$@"
fi
dpkg-buildpackage -b -us -uc --no-sign
mkdir -p dist
# dpkg-buildpackage writes its results next to the source tree
for f in ../conky-spotify-nowplaying_*_all.deb ../conky-spotify-nowplaying_*.buildinfo ../conky-spotify-nowplaying_*.changes; do
    [ -e "$f" ] && mv "$f" dist/
done
dh_clean
ls dist/*.deb
