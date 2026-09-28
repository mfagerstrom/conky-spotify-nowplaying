#!/usr/bin/env python3
"""Save the running widget's window as a PNG.

usage: scripts/capture-widget.py <out.png>

The widget is an X window (Conky runs under XWayland), so its pixels can be read
straight from the X server through GDK without a screenshot portal or any tool
outside the widget's own dependencies. Only the widget window is captured, never
the rest of the screen.
"""
import os
import re
import subprocess
import sys

os.environ['GDK_BACKEND'] = 'x11'   # must be set before Gdk is imported

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gdk, GdkX11  # noqa: E402

WM_CLASS = 'ConkySpotifyNowPlaying'   # own_window_class in src/conky.conf


def find_window():
    run = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f'xwininfo failed: {run.stderr.strip()}')
    tree = run.stdout
    m = re.search(rf'^\s*(0x[0-9a-f]+) .*"{WM_CLASS}"', tree, re.MULTILINE)
    return int(m.group(1), 16) if m else None


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__.strip().splitlines()[2])
    xid = find_window()
    if xid is None:
        sys.exit(f'no {WM_CLASS} window; is the widget running?')
    display = Gdk.Display.get_default()
    if display is None:
        sys.exit('no X display; run this from the desktop session')
    window = GdkX11.X11Window.foreign_new_for_display(display, xid)
    pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, window.get_width(), window.get_height())
    if pixbuf is None:
        sys.exit(f'could not read window {xid:#x}; is it on screen?')
    pixbuf.savev(argv[1], 'png', [], [])
    print(f'{argv[1]}: {pixbuf.get_width()}x{pixbuf.get_height()}')


if __name__ == '__main__':
    main(sys.argv)
