#!/bin/sh
# Starts the widget: the contents generator, conky, and the click/placement helper.
# The two Python helpers are restarted automatically if they ever exit.
cd "$HOME/.config/conky" || exit 1
(while true; do ./nowplaying.py; sleep 2; done) &
conky -c conky.conf &
while true; do ./conky-mouse.py; sleep 2; done
