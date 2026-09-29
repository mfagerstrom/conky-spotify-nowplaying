#!/usr/bin/env python3
"""Top-bar (AppIndicator) icon for the widget: shows the current track, sets the widget's
text scaling, toggles always-on-top and start-at-login, minimizes and restores the widget, and quits it. Started by the
launcher's supervisor."""
import os, signal, subprocess, sys

import gi
gi.require_version('Gtk', '3.0')
try:
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    print('tray: AyatanaAppIndicator3 not available (install gir1.2-ayatanaappindicator3-0.1)',
          file=sys.stderr)
    sys.exit(78)   # tells the supervisor not to keep restarting us
from gi.repository import GLib, Gtk

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


def main():
    indicator = AppIndicator.Indicator.new(NAME, f'{NAME}-symbolic',
                                           AppIndicator.IndicatorCategory.APPLICATION_STATUS)
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
