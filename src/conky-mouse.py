#!/usr/bin/env python3
"""Mouse handling and placement for the conky now-playing widget.

Conky's own Lua mouse hook never receives button presses under GNOME/XWayland, so this
helper subscribes to clicks on conky's window itself:
  - heart: like/unlike the track (or start the Spotify login if not logged in yet)
  - play/pause, previous, next buttons: control playback
  - seek bar: click or drag to change the position in the song
  - minimize: hide the widget until the tray menu or the launcher shows it again
  - close: stop the widget, like the tray menu's Quit
  - anywhere else: drag the widget; the position is saved on release

Hit areas come from nowplaying.py (regions.json, logical px, window-relative), since the
controls move when titles wrap.

The position lives in ~/.config/conky-spotify-nowplaying/position (root-window x y), not in conky.conf:
rewriting conky.conf makes conky reload and flash. Instead this helper keeps the window
at the saved spot, moving it back whenever conky places it elsewhere (startup, reloads).

Minimized is a flag file (~/.cache/conky-spotify-nowplaying/hidden) that the launcher's
show/hide commands also write; while it exists this helper keeps conky's window unmapped.
"""
import ctypes, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.expanduser('~/.config/conky-spotify-nowplaying')
POSITION = os.path.join(CONF, 'position')
CACHE = os.path.expanduser('~/.cache/conky-spotify-nowplaying')
REGIONS = os.path.join(CACHE, 'regions.json')
SEEK_PREVIEW = os.path.join(CACHE, 'seek-preview')
LOG = os.path.join(CACHE, 'mouse.log')
HIDDEN = os.path.join(CACHE, 'hidden')
LAUNCHER = os.environ.get('CSN_LAUNCHER') or 'conky-spotify-nowplaying'
BUTTON_PRESS, BUTTON1_MASK = 4, 1 << 8
BUTTON_PRESS_MASK, BUTTON_RELEASE_MASK = 1 << 2, 1 << 3

x11 = ctypes.CDLL('libX11.so.6')
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XDefaultRootWindow.restype = ctypes.c_ulong
x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x11.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
x11.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
x11.XMapWindow.argtypes = x11.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XFlush.argtypes = [ctypes.c_void_p]
x11.XPending.argtypes = [ctypes.c_void_p]
x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
x11.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
# Windows vanish when conky restarts; don't let the resulting X errors kill the helper.
ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda d, e: 0)
x11.XSetErrorHandler(ERROR_HANDLER)


class XButtonEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('root', ctypes.c_ulong),
                ('subwindow', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('x', ctypes.c_int), ('y', ctypes.c_int), ('x_root', ctypes.c_int), ('y_root', ctypes.c_int),
                ('state', ctypes.c_uint), ('button', ctypes.c_uint), ('same_screen', ctypes.c_int)]


class XEvent(ctypes.Union):
    _fields_ = [('type', ctypes.c_int), ('xbutton', XButtonEvent), ('pad', ctypes.c_long * 24)]


def log(msg):
    with open(LOG, 'a') as f:
        f.write(time.strftime('%T ') + msg + '\n')


def conky_window():
    tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True).stdout
    for line in tree.splitlines():
        if '"ConkySpotifyNowPlaying")' in line:
            return int(line.split()[0], 16)
    return None


def monitors():
    """[(x, y, w, h, primary)] in root-window coordinates."""
    out = subprocess.run(['xrandr', '--listmonitors'], capture_output=True, text=True).stdout
    mons = []
    for m in re.finditer(r'^\s*\d+: \+(\*?)\S+ (\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)', out, re.M):
        mons.append((int(m[4]), int(m[5]), int(m[2]), int(m[3]), m[1] == '*'))
    return mons


def clamp_to_monitor(x, y, w, h):
    """Keep the window fully on the monitor containing its center (or the nearest one)."""
    mons = monitors()
    if not mons:
        return x, y
    cx, cy = x + w // 2, y + h // 2
    def dist(m):
        mx, my, mw, mh, _ = m
        dx = max(mx - cx, 0, cx - (mx + mw))
        dy = max(my - cy, 0, cy - (my + mh))
        return dx * dx + dy * dy
    mx, my, mw, mh, _ = min(mons, key=dist)
    return min(max(x, mx), mx + mw - w), min(max(y, my), my + mh - h)


def geometry(d, win):
    root, x, y = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
    w, h, b, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    x11.XGetGeometry(d, win, ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
                     ctypes.byref(w), ctypes.byref(h), ctypes.byref(b), ctypes.byref(depth))
    return x.value, y.value, w.value, h.value


def pointer(d, root):
    r, c = ctypes.c_ulong(), ctypes.c_ulong()
    rx, ry, wx, wy, mask = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_uint()
    x11.XQueryPointer(d, root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx), ctypes.byref(ry),
                      ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask))
    return rx.value, ry.value, mask.value


def scale():
    out = subprocess.run(['xrdb', '-query'], capture_output=True, text=True).stdout
    m = re.search(r'Xft\.dpi:\s*(\d+)', out)
    return round(int(m.group(1)) / 96) if m else 1


def load_position():
    try:
        x, y = open(POSITION).read().split()
        return int(x), int(y)
    except (OSError, ValueError):
        return None


def save_position(x, y):
    os.makedirs(CONF, exist_ok=True)
    with open(POSITION, 'w') as f:
        f.write(f'{x} {y}\n')


def drag(d, root, win):
    wx, wy, w, h = geometry(d, win)
    px, py, mask = pointer(d, root)
    log(f'drag start: window {wx},{wy} pointer {px},{py}')
    last_logged = 0.0
    while mask & BUTTON1_MASK:
        nx, ny, mask = pointer(d, root)
        x11.XMoveWindow(d, win, wx + nx - px, wy + ny - py)
        x11.XFlush(d)
        if time.monotonic() - last_logged > 0.25:
            last_logged = time.monotonic()
            log(f'  pointer {nx},{ny} mask {mask:#x}')
        time.sleep(0.01)
    x, y, _, _ = geometry(d, win)
    cx, cy = clamp_to_monitor(x, y, w, h)
    log(f'drag end: window {x},{y} -> saved {cx},{cy}')
    return cx, cy


def load_regions():
    try:
        return json.load(open(REGIONS))
    except (OSError, ValueError):
        return {}


def seek(d, root, win, regions, s):
    """Follow the pointer along the bar (draw.lua shows the preview), seek on release."""
    bx0, bx1 = regions['bar']
    duration = regions['duration']
    wx, _, _, _ = geometry(d, win)
    fraction, mask = 0.0, BUTTON1_MASK
    while mask & BUTTON1_MASK:
        px, _, mask = pointer(d, root)
        fraction = min(max((px - wx - bx0 * s) / ((bx1 - bx0) * s), 0.0), 1.0)
        with open(SEEK_PREVIEW + '.tmp', 'w') as f:
            f.write(f'{fraction:.4f}')
        os.replace(SEEK_PREVIEW + '.tmp', SEEK_PREVIEW)
        time.sleep(0.02)
    subprocess.run(['playerctl', '-p', 'spotify', 'position', f'{fraction * duration:.2f}'])
    log(f'seek: {fraction:.3f} of {duration:.0f}s')
    time.sleep(0.4)  # keep the preview until nowplaying.py reports the new position
    try:
        os.remove(SEEK_PREVIEW)
    except OSError:
        pass


def main():
    try:
        os.remove(SEEK_PREVIEW)  # left behind if a previous run was killed mid-seek
    except OSError:
        pass
    d = ctypes.c_void_p(x11.XOpenDisplay(None))
    root = x11.XDefaultRootWindow(d)
    win, last_check, hidden = None, 0.0, None   # None: not known, e.g. after a restart
    ev = XEvent()
    while True:
        now = time.monotonic()
        # Conky rebuilds its window on reloads, often under the same id, which silently drops
        # our selection and resets its position -- so re-select and re-place every second.
        if now - last_check > 1:
            last_check = now
            current = conky_window()
            if current:
                if current != win:
                    log(f'attached to 0x{current:x}')
                win = current
                x11.XSelectInput(d, win, BUTTON_PRESS_MASK | BUTTON_RELEASE_MASK)
                saved = load_position()
                if saved:
                    x, y, w, h = geometry(d, win)
                    target = clamp_to_monitor(*saved, w, h)
                    if (x, y) != target:
                        x11.XMoveWindow(d, win, *target)
                # Unmapped again every second while hidden, since a conky reload maps it.
                hide = os.path.exists(HIDDEN)
                if hide:
                    x11.XUnmapWindow(d, win)
                elif hidden is not False:              # a no-op when already mapped
                    x11.XMapWindow(d, win)
                    if hidden:
                        log('shown')
                hidden = hide
                x11.XFlush(d)
        while x11.XPending(d):
            x11.XNextEvent(d, ctypes.byref(ev))
            b = ev.xbutton
            if ev.type == BUTTON_PRESS and b.button == 1 and b.window == win:
                s = scale()
                regions = load_regions()
                def hit(name):
                    r = regions.get(name)
                    return r and r[0] * s <= b.x <= r[2] * s and r[1] * s <= b.y <= r[3] * s
                if hit('heart'):
                    logged_in = os.path.exists(os.path.join(CONF, 'spotify-token.json'))
                    action = 'toggle' if logged_in else 'login'
                    subprocess.Popen([sys.executable, os.path.join(HERE, 'spotify_api.py'), action],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log(f'heart: {action}')
                elif hit('play'):
                    subprocess.Popen(['playerctl', '-p', 'spotify', 'play-pause'])
                    log('play-pause')
                elif hit('prev'):
                    subprocess.Popen(['playerctl', '-p', 'spotify', 'previous'])
                    log('previous')
                elif hit('next'):
                    subprocess.Popen(['playerctl', '-p', 'spotify', 'next'])
                    log('next')
                elif hit('minimize'):
                    open(HIDDEN, 'w').close()
                    x11.XUnmapWindow(d, win)
                    x11.XFlush(d)
                    hidden = True
                    log('minimize')
                elif hit('close'):
                    subprocess.Popen([LAUNCHER, 'stop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log('close')
                elif hit('seek') and regions.get('duration'):
                    seek(d, root, win, regions, s)
                else:
                    save_position(*drag(d, root, win))
        time.sleep(0.02)


if __name__ == '__main__':
    main()
