#!/bin/sh
# Builds a signed source package and uploads it to the Launchpad PPA.
#   1. add a debian/changelog entry first:  dch -v X.Y.Z -D resolute "What changed"
#   2. ./release-ppa.sh            (asks for your GPG passphrase, then uploads)
# Launchpad then builds the binary package; watch it on the PPA page.
set -e
cd "$(dirname "$0")"
PPA=${PPA:-ppa:mfagerstrom/conky-spotify-nowplaying}
# Signing key registered on Launchpad (the maintainer address in debian/ is a no-reply one,
# so debsign can't pick the key from it).
GPG_KEY=${GPG_KEY:-5DACA320C75920B4F632EC636F3AA291325FC325}
debuild -S -d -k"$GPG_KEY"
VERSION=$(dpkg-parsechangelog -S Version)
CHANGES="../conky-spotify-nowplaying_${VERSION}_source.changes"
echo "Uploading $CHANGES to $PPA"
dput "$PPA" "$CHANGES"
