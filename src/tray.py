#!/usr/bin/env python3
"""Top-bar (AppIndicator) icon for the widget: shows the current track, toggles
start-at-login, minimizes and restores the widget, and quits it. Started by the launcher's supervisor."""
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

NAME = 'conky-spotify-nowplaying'
HERE = os.path.dirname(os.path.abspath(__file__))
AUTOSTART = os.path.expanduser(f'~/.config/autostart/{NAME}.desktop')
HIDDEN = os.path.expanduser(f'~/.cache/{NAME}/hidden')   # the widget's minimize button writes it
LAUNCHER = os.environ.get('CSN_LAUNCHER') or NAME


def launcher(*args):
    subprocess.Popen([LAUNCHER, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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

    autostart = Gtk.CheckMenuItem(label='Start at login')
    autostart.set_active(os.path.exists(AUTOSTART))
    autostart.connect('toggled', lambda item: launcher('autostart', 'on' if item.get_active() else 'off'))
    menu.append(autostart)
    menu.append(Gtk.SeparatorMenuItem())

    def visibility_label():
        return 'Show widget' if os.path.exists(HIDDEN) else 'Hide widget'

    visibility = Gtk.MenuItem(label=visibility_label())
    visibility.connect('activate', lambda _: launcher('show' if os.path.exists(HIDDEN) else 'hide'))
    menu.append(visibility)

    quit_item = Gtk.MenuItem(label='Quit')
    quit_item.connect('activate', lambda _: launcher('stop'))
    menu.append(quit_item)
    menu.show_all()
    indicator.set_menu(menu)

    def refresh():
        track.set_label(now_playing())
        visibility.set_label(visibility_label())
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
