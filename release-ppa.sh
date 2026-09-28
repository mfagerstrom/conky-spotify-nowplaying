#!/bin/sh
# Builds a signed source package and uploads it to the Launchpad PPA.
#   1. add a debian/changelog entry first:  dch -v X.Y.Z -D resolute "What changed"
#   2. ./release-ppa.sh            (asks for your GPG passphrase, then uploads)
# Launchpad then builds the binary package; watch it on the PPA page.
set -e
cd "$(dirname "$0")"
PPA=${PPA:-ppa:mfagerstrom/conky-spotify-nowplaying}
debuild -S -d ${GPG_KEY:+-k"$GPG_KEY"}
VERSION=$(dpkg-parsechangelog -S Version)
CHANGES="../conky-spotify-nowplaying_${VERSION}_source.changes"
echo "Uploading $CHANGES to $PPA"
dput "$PPA" "$CHANGES"
