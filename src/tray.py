#!/usr/bin/env python3
"""Top-bar (AppIndicator) icon for the widget: shows the current track, sets the text
scaling, turns lyrics on or off and picks how they scroll, toggles always-on-top and
start-at-login, minimizes and restores the widget, and quits it. Started by the launcher's
supervisor."""
import ctypes, os, signal, subprocess, sys

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    print('tray: AyatanaAppIndicator3 not available (install gir1.2-ayatanaappindicator3-0.1)',
          file=sys.stderr)
    sys.exit(78)   # tells the supervisor not to keep restarting us
from gi.repository import Gdk, GLib, Gtk

import lyrics_settings
import widget_size

NAME = 'conky-spotify-nowplaying'
HERE = os.path.dirname(os.path.abspath(__file__))
AUTOSTART = os.path.expanduser(f'~/.config/autostart/{NAME}.desktop')
HIDDEN = os.path.expanduser(f'~/.cache/{NAME}/hidden')   # the widget's minimize button writes it
ON_TOP = os.path.expanduser(f'~/.config/{NAME}/on-top')  # 'off': not always on top; conky-mouse.py applies it
LAUNCHER = os.environ.get('CSN_LAUNCHER') or NAME
SCALES = (0.7, 0.85, 1.0, 1.25, 1.5, 1.75, 2.0)          # the Text scaling menu's presets


def launcher(*args):
    subprocess.Popen([LAUNCHER, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def on_top():
    try:
        with open(ON_TOP) as f:
            return f.read().strip() != 'off'
    except OSError:
        return True


def set_on_top(on):
    os.makedirs(os.path.dirname(ON_TOP), exist_ok=True)
    with open(ON_TOP, 'w') as f:
        f.write('on\n' if on else 'off\n')


def now_playing():
    out = subprocess.run(['playerctl', '-p', 'spotify', 'metadata', '--format', '{{artist}} – {{title}}'],
                         capture_output=True, text=True).stdout.strip()
    return out or 'Spotify not playing'


class Indicator(AppIndicator.Indicator):
    """The library's indicator, minus its GtkStatusIcon fallback off X11 (see no_fallback)."""


def no_fallback():
    """When the panel's StatusNotifierWatcher is missing or slow at start, the library falls
    back to a GtkStatusIcon until it answers. Off X11 that icon has no tray to live in: it
    shows nothing and GTK logs a gtk_widget_get_scale_factor critical for it. Clear the
    class's fallback hook, which the library skips when unset; the item still registers
    once the watcher answers. The hook is AppIndicatorClass's 26th pointer: GObjectClass
    is 17 pointer-sized fields, then eight signal slots come before it, and seven more
    slots follow. A class of any other size is left alone rather than written blind."""
    class TypeQuery(ctypes.Structure):
        _fields_ = [('type', ctypes.c_size_t), ('type_name', ctypes.c_char_p),
                    ('class_size', ctypes.c_uint), ('instance_size', ctypes.c_uint)]

    gobject = ctypes.CDLL('libgobject-2.0.so.0')
    gobject.g_type_from_name.restype = ctypes.c_size_t
    gobject.g_type_from_name.argtypes = [ctypes.c_char_p]
    gobject.g_type_query.argtypes = [ctypes.c_size_t, ctypes.POINTER(TypeQuery)]
    gobject.g_type_class_ref.restype = ctypes.c_void_p
    gobject.g_type_class_ref.argtypes = [ctypes.c_size_t]
    gtype = gobject.g_type_from_name(Indicator.__gtype__.name.encode())
    query = TypeQuery()
    gobject.g_type_query(gtype, ctypes.byref(query))
    pointer = ctypes.sizeof(ctypes.c_void_p)
    if query.class_size != 33 * pointer:
        print(f'tray: unexpected AppIndicator class size {query.class_size}; keeping its fallback',
              file=sys.stderr)
        return
    klass = gobject.g_type_class_ref(gtype)
    ctypes.c_void_p.from_address(klass + 25 * pointer).value = None


def main():
    if not Gdk.Display.get_default().__gtype__.name.startswith('GdkX11'):
        no_fallback()
    indicator = Indicator(id=NAME, icon_name=f'{NAME}-symbolic', category='ApplicationStatus')
    # Running from a checkout the icon isn't installed; point at the repo's copy.
    repo_icons = os.path.join(HERE, '..', 'packaging', 'icons')
    if os.path.isdir(repo_icons):
        indicator.set_icon_theme_path(os.path.realpath(repo_icons))
    indicator.set_title('Spotify Now Playing')
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    track = Gtk.MenuItem(label=now_playing())
    track.set_sensitive(False)
    menu.append(track)
    menu.append(Gtk.SeparatorMenuItem())

    # Text scaling: preset sizes for the text, the current one checked; the widget keeps its
    # size. Reset widget size puts the width and the lyrics' height back, keeping the text.
    size_menu = Gtk.Menu()
    scale_items = {}
    syncing = False

    def sync_sizes():
        nonlocal syncing
        syncing = True                          # set_active emits activate, as a click does
        current = widget_size.load()[2]
        for scale, item in scale_items.items():
            item.set_active(abs(scale - current) < 1e-6)
        syncing = False

    def pick_scale(scale):
        if syncing:
            return
        width, height, _ = widget_size.load()
        widget_size.save((width, height, scale))
        sync_sizes()                            # a click on the checked one unchecked it

    for scale in SCALES:
        item = Gtk.CheckMenuItem(label=f'{scale:.0%}')
        item.set_draw_as_radio(True)
        item.connect('activate', lambda _, scale=scale: pick_scale(scale))
        scale_items[scale] = item
        size_menu.append(item)
    sync_sizes()
    size = Gtk.MenuItem(label='Text scaling')
    size.set_submenu(size_menu)
    menu.append(size)
    reset = Gtk.MenuItem(label='Reset widget size')
    reset.connect('activate', lambda _: widget_size.save(widget_size.DEFAULTS[:2] + widget_size.load()[2:]))
    menu.append(reset)
    menu.append(Gtk.SeparatorMenuItem())

    # Lyrics on or off, and Lyrics scrolling: Autoscroll or Static, the current one checked,
    # greyed out while lyrics are off.
    lyrics = Gtk.CheckMenuItem(label='Lyrics')
    scrolling_menu = Gtk.Menu()
    scrolling = Gtk.MenuItem(label='Lyrics scrolling')
    scrolling_items = {}
    syncing_lyrics = False

    def sync_lyrics():
        nonlocal syncing_lyrics
        syncing_lyrics = True                   # set_active emits the same signals a click does
        shown, current = lyrics_settings.shown(), lyrics_settings.scrolling()
        if lyrics.get_active() != shown:
            lyrics.set_active(shown)
        for mode, item in scrolling_items.items():
            item.set_active(mode == current)
        scrolling.set_sensitive(shown)
        syncing_lyrics = False

    def toggle_lyrics(item):
        if not syncing_lyrics:
            lyrics_settings.set_shown(item.get_active())
            sync_lyrics()

    def pick_scrolling(mode):
        if not syncing_lyrics:
            lyrics_settings.set_scrolling(mode)
            sync_lyrics()                       # a click on the checked one unchecked it

    for mode, label in ((lyrics_settings.AUTOSCROLL, 'Autoscroll'), (lyrics_settings.STATIC, 'Static')):
        item = Gtk.CheckMenuItem(label=label)
        item.set_draw_as_radio(True)
        item.connect('activate', lambda _, mode=mode: pick_scrolling(mode))
        scrolling_items[mode] = item
        scrolling_menu.append(item)
    lyrics.connect('toggled', toggle_lyrics)
    sync_lyrics()
    scrolling.set_submenu(scrolling_menu)
    menu.append(lyrics)
    menu.append(scrolling)
    menu.append(Gtk.SeparatorMenuItem())

    always_on_top = Gtk.CheckMenuItem(label='Always on top')
    always_on_top.set_active(on_top())
    always_on_top.connect('toggled', lambda item: set_on_top(item.get_active()))
    menu.append(always_on_top)

    autostart = Gtk.CheckMenuItem(label='Start at login')
    autostart.set_active(os.path.exists(AUTOSTART))
    autostart.connect('toggled', lambda item: launcher('autostart', 'on' if item.get_active() else 'off'))
    menu.append(autostart)
    menu.append(Gtk.SeparatorMenuItem())

    def visibility_label():
        return 'Show widget' if os.path.exists(HIDDEN) else 'Hide widget'

    visibility = Gtk.MenuItem(label=visibility_label())
    # Do what the label says: it can be up to one refresh behind the flag file.
    visibility.connect('activate', lambda item: launcher('show' if item.get_label() == 'Show widget' else 'hide'))
    menu.append(visibility)

    quit_item = Gtk.MenuItem(label='Quit')
    quit_item.connect('activate', lambda _: launcher('stop'))
    menu.append(quit_item)
    menu.show_all()
    indicator.set_menu(menu)

    def refresh():
        track.set_label(now_playing())
        visibility.set_label(visibility_label())
        sync_sizes()                            # resized by dragging meanwhile
        sync_lyrics()                           # changed outside the menu
        if always_on_top.get_active() != on_top():
            always_on_top.set_active(on_top())  # changed outside the menu; writes the same back
        if autostart.get_active() != os.path.exists(AUTOSTART):
            autostart.set_active(os.path.exists(AUTOSTART))   # changed from the app menu/terminal
        return True

    GLib.timeout_add_seconds(3, refresh)
    try:                                        # newer PyGObject moved this into GLibUnix
        gi.require_version('GLibUnix', '2.0')
        from gi.repository import GLibUnix
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
    except (ValueError, ImportError):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
    Gtk.main()


if __name__ == '__main__':
    main()
