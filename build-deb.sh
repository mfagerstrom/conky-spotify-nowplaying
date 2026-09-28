#!/bin/sh
# Builds dist/conky-spotify-nowplaying_<VERSION>_all.deb from this checkout.
set -e
umask 022   # package files/dirs must not inherit a group-writable umask
cd "$(dirname "$0")"
PKG=conky-spotify-nowplaying
VERSION=$(cat VERSION)
STAGE=$(mktemp -d)
trap 'rm -rf "${STAGE:?}"' EXIT
chmod 755 "$STAGE"   # mktemp makes it 0700, which would end up as the package root

install -Dm755 "bin/$PKG" "$STAGE/usr/bin/$PKG"
install -d "$STAGE/usr/share/$PKG"
install -m644 src/conky.conf src/draw.lua "$STAGE/usr/share/$PKG/"
install -m755 src/nowplaying.py src/conky-mouse.py src/spotify_api.py src/tray.py "$STAGE/usr/share/$PKG/"
install -Dm644 "packaging/$PKG.desktop" "$STAGE/usr/share/applications/$PKG.desktop"
install -Dm644 "packaging/$PKG.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/$PKG.svg"
install -Dm644 "packaging/icons/$PKG-symbolic.svg" "$STAGE/usr/share/icons/hicolor/symbolic/apps/$PKG-symbolic.svg"
install -Dm644 README.md "$STAGE/usr/share/doc/$PKG/README.md"

SIZE=$(du -sk "$STAGE/usr" | cut -f1)
install -d "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/; s/@SIZE@/$SIZE/" packaging/control > "$STAGE/DEBIAN/control"

mkdir -p dist
dpkg-deb --build --root-owner-group "$STAGE" "dist/${PKG}_${VERSION}_all.deb"
