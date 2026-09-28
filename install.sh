#!/bin/sh
# Installs the widget for the current user:
#   - installs runtime dependencies with apt
#   - symlinks the source files into ~/.config/conky (so edits in this repo take effect)
#   - installs the GNOME autostart entry
# Private/machine-specific state (position, Spotify client ID and token) stays in
# ~/.config/conky and is never touched by this script.
set -e
REPO=$(cd "$(dirname "$0")" && pwd)
CONF="$HOME/.config/conky"
FILES="conky.conf draw.lua nowplaying.py conky-mouse.py spotify_api.py run.sh"

sudo apt install -y conky-all playerctl python3-gi gir1.2-pango-1.0 gir1.2-gdkpixbuf-2.0 \
    x11-utils x11-xserver-utils xdg-utils fonts-ubuntu fonts-dejavu-core

mkdir -p "$CONF" "$HOME/.config/autostart"
for f in $FILES; do
    if [ -e "$CONF/$f" ] && [ ! -L "$CONF/$f" ]; then
        mv "$CONF/$f" "$CONF/$f.bak"
        echo "backed up existing $CONF/$f -> $f.bak"
    fi
    ln -sfn "$REPO/$f" "$CONF/$f"
done
chmod +x "$REPO/nowplaying.py" "$REPO/conky-mouse.py" "$REPO/spotify_api.py" "$REPO/run.sh"
cp "$REPO/conky-spotify-nowplaying.desktop" "$HOME/.config/autostart/"

echo
echo "Installed. It starts automatically at login; to start it now:"
echo "  setsid $CONF/run.sh >/dev/null 2>&1 &"
if [ ! -f "$CONF/spotify-client-id" ]; then
    echo
    echo "For the like button: create an app at https://developer.spotify.com/dashboard"
    echo "(redirect URI http://127.0.0.1:8888/callback, Web API), then save its Client ID:"
    echo "  echo YOUR_CLIENT_ID > $CONF/spotify-client-id"
    echo "and click the heart on the widget to log in."
fi
