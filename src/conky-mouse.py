#!/usr/bin/env python3
"""Mouse handling and placement for the conky now-playing widget.

Conky's own Lua mouse hook never receives button presses under GNOME/XWayland, so this
helper subscribes to clicks on conky's window itself:
  - heart: like/unlike the track (or start the Spotify login if not logged in yet)
  - play/pause, previous, next buttons: control playback
  - seek bar: click or drag to change the position in the song
  - minimize: hide the widget until the tray menu or the launcher shows it again
  - close: stop the widget, like the tray menu's Quit
  - an edge or corner: resize the widget. A side edge changes its width, the top or bottom
    edge the height of the lyrics, and a corner both; the text keeps its size. An outline
    shows the new size while the button is held; on release it is saved (widget_size.py)
  - anywhere else: drag the widget; the position is saved on release
  - the mouse wheel over plain lyrics (static, with no timing to follow): scroll them a
    line at a time. draw.lua reads the offset from lyrics-scroll. The wheel does nothing
    anywhere else.

Hit areas come from nowplaying.py (regions.json, logical px, window-relative), since the
controls move when titles wrap.

The position lives in ~/.config/conky-spotify-nowplaying/position (root-window x y), not in conky.conf:
rewriting conky.conf makes conky reload and flash. Instead this helper keeps the window
at the saved spot, moving it back whenever conky places it elsewhere (startup, reloads).

A resize only writes the size settings, once, on release: nowplaying.py lays the widget out
again and conky's window takes the new size, without a reload or a restart. Conky's window
shows a blank frame when it resizes, so a copy of the widget's pixels covers it until conky
has drawn at the new size.

Always on top is the window manager's state for conky's regular window, set and cleared
here as ~/.config/conky-spotify-nowplaying/on-top (the tray menu's toggle) changes, so conky
never restarts for it.

Minimized is a flag file (~/.cache/conky-spotify-nowplaying/hidden) that the launcher's
show/hide commands also write; while it exists this helper keeps conky's window unmapped.
"""
import ctypes, json, math, os, re, subprocess, sys, time

import widget_size

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.expanduser('~/.config/conky-spotify-nowplaying')
POSITION = os.path.join(CONF, 'position')
CACHE = os.path.expanduser('~/.cache/conky-spotify-nowplaying')
REGIONS = os.path.join(CACHE, 'regions.json')
SEEK_PREVIEW = os.path.join(CACHE, 'seek-preview')
LYRICS_SCROLL = os.path.join(CACHE, 'lyrics-scroll')   # '<lyrics version> <line offset>', for draw.lua
LOG = os.path.join(CACHE, 'mouse.log')
HIDDEN = os.path.join(CACHE, 'hidden')
ON_TOP = os.path.join(CONF, 'on-top')             # 'off': not always on top
LAUNCHER = os.environ.get('CSN_LAUNCHER') or 'conky-spotify-nowplaying'
BUTTON_PRESS, MOTION_NOTIFY, BUTTON1_MASK = 4, 6, 1 << 8
WHEEL_UP, WHEEL_DOWN = 4, 5                       # X reports the wheel as button presses
BUTTON_PRESS_MASK, BUTTON_RELEASE_MASK, POINTER_MOTION_MASK = 1 << 2, 1 << 3, 1 << 6
CLIENT_MESSAGE, SUBSTRUCTURE_NOTIFY_MASK, SUBSTRUCTURE_REDIRECT_MASK = 33, 1 << 19, 1 << 20
CW_BACK_PIXMAP, CW_BACK_PIXEL, CW_BORDER_PIXEL = 1 << 0, 1 << 1, 1 << 3
CW_OVERRIDE_REDIRECT, CW_COLORMAP = 1 << 9, 1 << 13
US_POSITION, P_POSITION = 1 << 0, 1 << 2          # XSizeHints flags
COVER_HOLD = 0.1                                  # s: conky draws every 0.05 s
OUTLINE, OUTLINE_WIDTH = 0x1db954, 2              # conky.conf's color1; logical px
EDGE = 6                                          # logical px along the border that resize
CORNER = 24                                       # how far from a corner both of its edges resize
# X cursor font shapes (X11/cursorfont.h), by the edges a press there resizes.
CURSORS = {'l': 70, 'r': 96, 't': 138, 'b': 16, 'lt': 134, 'rt': 136, 'lb': 12, 'rb': 14}

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
x11.XCreateFontCursor.restype = ctypes.c_ulong
x11.XCreateFontCursor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
x11.XDefineCursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
x11.XUndefineCursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
x11.XCreatePixmap.restype = ctypes.c_ulong
x11.XCreatePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
x11.XFreePixmap.argtypes = x11.XDestroyWindow.argtypes = x11.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x11.XCreateGC.restype = ctypes.c_void_p
x11.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
x11.XFreeGC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
x11.XCopyArea.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p] + [ctypes.c_int] * 2 + \
    [ctypes.c_uint] * 2 + [ctypes.c_int] * 2
x11.XCreateWindow.restype = ctypes.c_ulong
x11.XMoveResizeWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_uint, ctypes.c_uint]
x11.XCreateWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p,
                              ctypes.c_ulong, ctypes.c_void_p]
x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
x11.XInternAtom.restype = ctypes.c_ulong
x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
x11.XGetWMNormalHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
x11.XSetWMNormalHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
# Windows vanish when conky restarts; don't let the resulting X errors kill the helper.
ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda d, e: 0)
x11.XSetErrorHandler(ERROR_HANDLER)


class XButtonEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('root', ctypes.c_ulong),
                ('subwindow', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('x', ctypes.c_int), ('y', ctypes.c_int), ('x_root', ctypes.c_int), ('y_root', ctypes.c_int),
                ('state', ctypes.c_uint), ('button', ctypes.c_uint), ('same_screen', ctypes.c_int)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                ('format', ctypes.c_int), ('data', ctypes.c_long * 5)]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int),
                ('border_width', ctypes.c_int), ('depth', ctypes.c_int), ('visual', ctypes.c_void_p),
                ('root', ctypes.c_ulong), ('class_', ctypes.c_int), ('bit_gravity', ctypes.c_int),
                ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int),
                ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong),
                ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong), ('map_installed', ctypes.c_int),
                ('map_state', ctypes.c_int), ('all_event_masks', ctypes.c_long),
                ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p)]


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [('background_pixmap', ctypes.c_ulong), ('background_pixel', ctypes.c_ulong),
                ('border_pixmap', ctypes.c_ulong), ('border_pixel', ctypes.c_ulong),
                ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int),
                ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
                ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int),
                ('event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
                ('override_redirect', ctypes.c_int), ('colormap', ctypes.c_ulong),
                ('cursor', ctypes.c_ulong)]


class XSizeHints(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_long)] + [(n, ctypes.c_int) for n in (
        'x', 'y', 'width', 'height', 'min_width', 'min_height', 'max_width', 'max_height',
        'width_inc', 'height_inc', 'min_aspect_x', 'min_aspect_y', 'max_aspect_x', 'max_aspect_y',
        'base_width', 'base_height', 'win_gravity')]


class XEvent(ctypes.Union):
    # A motion event has the same layout as a button event up to its x and y.
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


def monitor_for(x, y, w, h):
    """(x, y, w, h) of the monitor containing the window's center (or the nearest one), or
    None when xrandr lists none."""
    mons = monitors()
    if not mons:
        return None
    cx, cy = x + w // 2, y + h // 2
    def dist(m):
        mx, my, mw, mh, _ = m
        dx = max(mx - cx, 0, cx - (mx + mw))
        dy = max(my - cy, 0, cy - (my + mh))
        return dx * dx + dy * dy
    return min(mons, key=dist)[:4]


def clamp_to_monitor(x, y, w, h):
    """Keep the window fully on the monitor containing its center (or the nearest one)."""
    mon = monitor_for(x, y, w, h)
    if not mon:
        return x, y
    mx, my, mw, mh = mon
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


def resize_edges(x, y, w, h, s):
    """The edges a press at window-relative (x, y) drags: '' for none (a move), else 'l' or
    'r' and/or 't' or 'b', as in 'rb' for the bottom right corner."""
    edge, corner = EDGE * s, CORNER * s
    horiz = 'l' if x < edge else 'r' if x >= w - edge else ''
    vert = 't' if y < edge else 'b' if y >= h - edge else ''
    if horiz and not vert:                     # along an edge but close to a corner: both
        vert = 't' if y < corner else 'b' if y >= h - corner else ''
    elif vert and not horiz:
        horiz = 'l' if x < corner else 'r' if x >= w - corner else ''
    return horiz + vert


def dragged(settings, edges, dx, dy, room_x, room_y, s):
    """The size settings once the pointer has dragged `edges` by (dx, dy) window px: the side
    edges change the width, the top and bottom the lyrics' height, a corner both. The window
    grows by at most (room_x, room_y), what its monitor has left; s is the display scale."""
    width, height, text = settings
    limits = widget_size.LIMITS
    if 'l' in edges or 'r' in edges:
        out = min(dx if 'r' in edges else -dx, room_x)     # how far the edge moved outwards
        width = widget_size.clamp(width + math.floor(out / s), *limits[0])
    if 't' in edges or 'b' in edges:
        out = min(dy if 'b' in edges else -dy, room_y)
        height = widget_size.clamp(height + math.floor(out / s), *limits[1])
    return width, height, text


def without_idle_edges(edges, regions):
    """With no lyrics shown the height has nothing to change: the top and bottom edges move
    the widget like anywhere else, and a corner only changes the width."""
    return edges if regions.get('lyrics') else edges.replace('t', '').replace('b', '')


def set_cursor(d, win, edges, cache={}):
    if edges:
        if edges not in cache:
            cache[edges] = x11.XCreateFontCursor(d, CURSORS[edges])
        x11.XDefineCursor(d, win, cache[edges])
    else:
        x11.XUndefineCursor(d, win)
    x11.XFlush(d)


def cover(d, root, win, rect, src):
    """An unmanaged window at `rect` (root x, y, w, h) showing `win`'s pixels from `src`
    (window x, y), transparency and all: it hides the blank frame conky's window shows while
    it resizes. Taken down with uncover()."""
    x, y, w, h = rect
    attrs = XWindowAttributes()
    x11.XGetWindowAttributes(d, win, ctypes.byref(attrs))
    pixmap = x11.XCreatePixmap(d, win, w, h, attrs.depth)
    gc = x11.XCreateGC(d, pixmap, 0, None)
    x11.XCopyArea(d, win, pixmap, gc, src[0], src[1], w, h, 0, 0)
    x11.XFreeGC(d, gc)
    # A 32-bit visual needs its own colormap and a border pixel, or the window is refused.
    new = XSetWindowAttributes(background_pixmap=pixmap, border_pixel=0, override_redirect=1,
                               colormap=attrs.colormap)
    shade = x11.XCreateWindow(d, root, x, y, w, h, 0, attrs.depth, 1, attrs.visual,
                              CW_BACK_PIXMAP | CW_BORDER_PIXEL | CW_OVERRIDE_REDIRECT | CW_COLORMAP,
                              ctypes.byref(new))
    x11.XFreePixmap(d, pixmap)                  # the window keeps its own reference
    x11.XMapRaised(d, shade)
    x11.XSync(d, False)
    return shade


def uncover(d, shade):
    if shade:
        x11.XDestroyWindow(d, shade)
        x11.XFlush(d)


def outline(d, root):
    """Four thin unmanaged windows, not mapped yet, that frame a rectangle between them."""
    attrs = XSetWindowAttributes(background_pixel=OUTLINE, override_redirect=1)
    return [x11.XCreateWindow(d, root, 0, 0, 1, 1, 0, 0, 1, None,
                              CW_BACK_PIXEL | CW_OVERRIDE_REDIRECT, ctypes.byref(attrs))
            for _ in range(4)]


def place_outline(d, frame, x, y, w, h, t):
    for part, (px, py, pw, ph) in zip(frame, ((x, y, w, t), (x, y + h - t, w, t),
                                              (x, y, t, h), (x + w - t, y, t, h))):
        x11.XMoveResizeWindow(d, part, px, py, max(pw, 1), max(ph, 1))
        x11.XMapRaised(d, part)
    x11.XFlush(d)


def resized(start, edges, old, new, s, lyrics):
    """The window's geometry at size settings `new`, from `start` (x, y, w, h) at `old`:
    the edges across from the dragged ones stay put. Without lyrics the height stays."""
    x, y, w, h = start
    nw = w + round((new[0] - old[0]) * s)
    nh = h + (round((new[1] - old[1]) * s) if lyrics else 0)
    return x + (w - nw if 'l' in edges else 0), y + (h - nh if 't' in edges else 0), nw, nh


def near(a, b):
    return abs(a[0] - b[0]) <= 8 and abs(a[1] - b[1]) <= 8   # conky rounds, and adds its border


def resize(d, root, win, edges, s, regions):
    """Outline the new size while the pointer drags `edges`; on release, save it and hold a
    copy of the widget over conky's window while it takes the new size. Returns the window's
    new position, or None if nothing changed."""
    old = new = widget_size.load()
    start = wx, wy, w, h = geometry(d, win)
    mon = monitor_for(wx, wy, w, h)
    if mon:                                     # the room on the dragged side, to the monitor's edge
        mx, my, mw, mh = mon
        room_x = max(wx - mx if 'l' in edges else mx + mw - wx - w, 0)
        room_y = max(wy - my if 't' in edges else my + mh - wy - h, 0)
    else:
        room_x = room_y = math.inf
    px, py, mask = pointer(d, root)
    log(f'resize start: {edges} at {old}, window {wx},{wy} {w}x{h}')
    frame = outline(d, root)
    g = start
    try:
        while mask & BUTTON1_MASK:
            nx, ny, mask = pointer(d, root)
            new = dragged(old, edges, nx - px, ny - py, room_x, room_y, s)
            g = resized(start, edges, old, new, s, regions.get('lyrics'))
            if (nx, ny) != (px, py):
                place_outline(d, frame, *g, round(OUTLINE_WIDTH * s))
            time.sleep(0.01)
    finally:
        for part in frame:
            x11.XDestroyWindow(d, part)
        x11.XFlush(d)
    if new == old:
        log('resize end: unchanged')
        return None
    shade = cover(d, root, win, start, (0, 0))
    try:
        widget_size.save(new)
        # nowplaying.py lays the widget out again and conky resizes its window, from its top
        # left corner; move it into place, then keep the copy up while conky draws there.
        deadline, shown = time.monotonic() + 1, None
        while time.monotonic() < deadline and (shown is None or time.monotonic() < shown):
            x, y, cw, ch = geometry(d, win)
            if near((cw, ch), g[2:]):
                if (x, y) != g[:2]:
                    x11.XMoveWindow(d, win, *g[:2])
                    x11.XFlush(d)
                elif shown is None:
                    shown = time.monotonic() + COVER_HOLD
            time.sleep(0.01)
    finally:
        uncover(d, shade)
    x, y = clamp_to_monitor(*g)
    log(f'resize end: {new}, window {g[2]}x{g[3]} -> saved {x},{y}')
    return x, y


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


def load_on_top():
    try:
        with open(ON_TOP) as f:
            return f.read().strip() != 'off'
    except OSError:
        return True


def hint_position(d, win, x, y):
    """Mark (x, y) as the window's own position in WM_NORMAL_HINTS, keeping any size hints
    already there. Unmapping withdraws the window, and on the next map the window manager
    places it anew (mutter: the middle of some monitor) unless the hints carry a
    position; without them the widget would show there until moved back."""
    hints, supplied = XSizeHints(), ctypes.c_long()
    if not x11.XGetWMNormalHints(d, win, ctypes.byref(hints), ctypes.byref(supplied)):
        hints = XSizeHints()
    hints.flags |= US_POSITION | P_POSITION
    hints.x, hints.y = x, y
    x11.XSetWMNormalHints(d, win, ctypes.byref(hints))


def set_above(d, root, win, above):
    """Ask the window manager to keep the window above others, or not (EWMH _NET_WM_STATE)."""
    ev = XClientMessageEvent(type=CLIENT_MESSAGE, window=win, format=32,
                             message_type=x11.XInternAtom(d, b'_NET_WM_STATE', False))
    ev.data[:] = [1 if above else 0, x11.XInternAtom(d, b'_NET_WM_STATE_ABOVE', False), 0, 1, 0]
    x11.XSendEvent(d, root, False, SUBSTRUCTURE_REDIRECT_MASK | SUBSTRUCTURE_NOTIFY_MASK, ctypes.byref(ev))
    x11.XFlush(d)


def load_regions(cache={}):
    """regions.json, read again only when nowplaying.py has rewritten it: pointer motion
    along the border asks for it many times a second."""
    try:
        mtime = os.stat(REGIONS).st_mtime_ns
        if cache.get('mtime') != mtime:
            with open(REGIONS) as f:
                cache.update(mtime=mtime, regions=json.load(f))
        return cache['regions']
    except (OSError, ValueError):
        return {}


def region_at(regions, s, x, y):
    """The control under window-relative (x, y), if any."""
    for name in ('heart', 'play', 'prev', 'next', 'minimize', 'close', 'seek'):
        r = regions.get(name)
        if r and r[0] * s <= x <= r[2] * s and r[1] * s <= y <= r[3] * s:
            return name
    return None


def scroll_lyrics(regions, s, x, y, down):
    """Moves static lyrics under window-relative (x, y) a line down or up, between their
    first line at the top and their last at the bottom. Returns the new offset, or None
    when (x, y) is not over lyrics the wheel scrolls."""
    area, static = regions.get('lyrics'), regions.get('lyrics_scroll')
    if not (area and static and area[0] * s <= x <= area[2] * s and area[1] * s <= y <= area[3] * s):
        return None
    version, last = static
    try:
        with open(LYRICS_SCROLL) as f:
            shown, offset = f.read().split()
        offset = int(offset) if shown == version else 0     # other lyrics: from the top
    except (OSError, ValueError):
        offset = 0
    offset = min(max(offset + (1 if down else -1), 0), last)
    with open(LYRICS_SCROLL + '.tmp', 'w') as f:
        f.write(f'{version} {offset}')
    os.replace(LYRICS_SCROLL + '.tmp', LYRICS_SCROLL)
    return offset


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
    cursor = None                                # the edges whose cursor win shows; None: not set
    above, last_on_top = None, 0.0               # the always-on-top state win has; None: not set
    logged_on_top = None
    s = scale()                                  # refreshed on each click; motion uses the last
    ev = XEvent()
    while True:
        now = time.monotonic()
        # Conky rebuilds its window on reloads, often under the same id, which silently drops
        # our selection, its position and its always-on-top state -- so re-select, re-place and
        # re-apply every second.
        if now - last_check > 1:
            last_check = now
            current = conky_window()
            if current:
                if current != win:
                    log(f'attached to 0x{current:x}')
                    cursor = None
                win = current
                above = None                           # sent again below; a no-op if it held
                x11.XSelectInput(d, win, BUTTON_PRESS_MASK | BUTTON_RELEASE_MASK | POINTER_MOTION_MASK)
                saved = load_position()
                x, y, w, h = geometry(d, win)
                target = clamp_to_monitor(*saved, w, h) if saved else (x, y)
                if (x, y) != target:
                    x11.XMoveWindow(d, win, *target)
                # Unmapped again every second while hidden, since a conky reload maps it.
                hide = os.path.exists(HIDDEN)
                if hide:
                    x11.XUnmapWindow(d, win)
                elif hidden is not False:              # a no-op when already mapped
                    hint_position(d, win, *target)
                    x11.XMapWindow(d, win)
                    above = None                       # the window manager forgets it when unmapped
                    if hidden:
                        log('shown')
                hidden = hide
                x11.XFlush(d)
        # The tray's toggle takes effect within a tenth of a second.
        if win and not hidden and now - last_on_top > 0.1:
            last_on_top = now
            want = load_on_top()
            if want != above:
                set_above(d, root, win, want)
                above = want
                if want != logged_on_top:
                    log(f'always on top: {want}')
                    logged_on_top = want
        while x11.XPending(d):
            x11.XNextEvent(d, ctypes.byref(ev))
            b = ev.xbutton
            if ev.type == MOTION_NOTIFY and b.window == win:
                _, _, w, h = geometry(d, win)
                edges = resize_edges(b.x, b.y, w, h, s)
                if edges:
                    regions = load_regions()
                    if region_at(regions, s, b.x, b.y):
                        edges = ''               # a control reaching the border wins
                    else:
                        edges = without_idle_edges(edges, regions)
                if edges != cursor:
                    set_cursor(d, win, edges)
                    cursor = edges
            elif ev.type == BUTTON_PRESS and b.button in (WHEEL_UP, WHEEL_DOWN) and b.window == win:
                scroll_lyrics(load_regions(), s, b.x, b.y, b.button == WHEEL_DOWN)
            elif ev.type == BUTTON_PRESS and b.button == 1 and b.window == win:
                s = scale()
                regions = load_regions()
                hit = region_at(regions, s, b.x, b.y)
                _, _, w, h = geometry(d, win)
                edges = resize_edges(b.x, b.y, w, h, s)
                edges = without_idle_edges(edges, regions)
                if hit == 'heart':
                    logged_in = os.path.exists(os.path.join(CONF, 'spotify-token.json'))
                    action = 'toggle' if logged_in else 'login'
                    subprocess.Popen([sys.executable, os.path.join(HERE, 'spotify_api.py'), action],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log(f'heart: {action}')
                elif hit == 'play':
                    subprocess.Popen(['playerctl', '-p', 'spotify', 'play-pause'])
                    log('play-pause')
                elif hit == 'prev':
                    subprocess.Popen(['playerctl', '-p', 'spotify', 'previous'])
                    log('previous')
                elif hit == 'next':
                    subprocess.Popen(['playerctl', '-p', 'spotify', 'next'])
                    log('next')
                elif hit == 'minimize':
                    open(HIDDEN, 'w').close()
                    x11.XUnmapWindow(d, win)
                    x11.XFlush(d)
                    hidden = True
                    log('minimize')
                elif hit == 'close':
                    subprocess.Popen([LAUNCHER, 'stop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    log('close')
                elif hit == 'seek' and regions.get('duration'):
                    seek(d, root, win, regions, s)
                elif edges:
                    placed = resize(d, root, win, edges, s, regions)
                    if placed:
                        save_position(*placed)
                else:
                    save_position(*drag(d, root, win))
        time.sleep(0.02)


if __name__ == '__main__':
    main()
