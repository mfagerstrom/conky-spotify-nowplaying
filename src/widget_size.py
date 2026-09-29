"""The widget's size settings, kept in ~/.config/conky-spotify-nowplaying/size as
'<width> <lyrics height> <text scale>'.

  - width: the width inside the margins, in logical px (dragging a side edge or a corner)
  - lyrics height: the height of the lyrics area, in logical px (dragging the top or bottom
    edge or a corner); the bigger the text, the fewer lines it holds
  - text scale: the size of all the text, 1 being its designed size (the tray menu's Text
    scaling). It changes no size of the widget itself, and dragging does not change it.

conky-mouse.py and tray.py write it; nowplaying.py lays the widget out by it on every
render, and conky sizes its window to that layout, so a change shows without a restart.
"""
import os

PATH = os.path.expanduser('~/.config/conky-spotify-nowplaying/size')
DEFAULTS = (505, 63, 1.0)                  # 63: three lines of lyrics at text scale 1
# The width and height go as far as the monitor allows, which conky-mouse.py works out
# while dragging; their upper limits here only catch a hand-edited file.
LIMITS = ((400, 9999), (16, 99999), (0.7, 2.0))


def clamp(v, low, high):
    return min(max(v, low), high)


def load():
    """(width, lyrics height, text scale), each within its limits; a missing or bad value
    is its default."""
    try:
        with open(PATH) as f:
            fields = f.read().split()
    except OSError:
        fields = []
    settings = []
    for i, (default, limits) in enumerate(zip(DEFAULTS, LIMITS)):
        try:
            v = float(fields[i])
        except (IndexError, ValueError):
            v = default
        settings.append(clamp(v, *limits) if v == v else default)   # v == v: not NaN
    return round(settings[0]), round(settings[1]), round(settings[2], 2)


def save(settings):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = f'{PATH}.{os.getpid()}.tmp'              # the tray and conky-mouse.py both write it
    with open(tmp, 'w') as f:
        f.write('{} {} {:g}\n'.format(*settings))
    os.replace(tmp, PATH)
