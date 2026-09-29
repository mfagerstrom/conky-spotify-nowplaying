"""The tray menu's lyrics settings, one file each in ~/.config/conky-spotify-nowplaying/:

  - lyrics: 'off' collapses the lyrics area and stops looking lyrics up on LRCLIB;
    anything else, or no file, shows them
  - lyrics-scrolling: 'static' shows synced lyrics like plain ones, a block from the first
    line that the mouse wheel scrolls; anything else, or no file, scrolls them along with
    playback (plain lyrics are static either way)

tray.py writes them; nowplaying.py reads them on every render, so a change shows without a
restart.
"""
import os

CONF = os.path.expanduser('~/.config/conky-spotify-nowplaying')
SHOWN = os.path.join(CONF, 'lyrics')
SCROLLING = os.path.join(CONF, 'lyrics-scrolling')
AUTOSCROLL, STATIC = 'autoscroll', 'static'


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        f.write(value + '\n')
    os.replace(tmp, path)


def shown():
    return _read(SHOWN) != 'off'


def set_shown(on):
    _write(SHOWN, 'on' if on else 'off')


def scrolling():
    """AUTOSCROLL or STATIC."""
    return STATIC if _read(SCROLLING) == STATIC else AUTOSCROLL


def set_scrolling(mode):
    _write(SCROLLING, mode)
