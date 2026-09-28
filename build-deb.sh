#!/bin/sh
# Builds the binary package into dist/ from the debian/ packaging (the same files
# Launchpad builds the PPA from). Needs: sudo apt install debhelper devscripts
set -e
cd "$(dirname "$0")"
dpkg-buildpackage -b -us -uc --no-sign
mkdir -p dist
# dpkg-buildpackage writes its results next to the source tree
for f in ../conky-spotify-nowplaying_*_all.deb ../conky-spotify-nowplaying_*.buildinfo ../conky-spotify-nowplaying_*.changes; do
    [ -e "$f" ] && mv "$f" dist/
done
dh_clean
ls dist/*.deb
