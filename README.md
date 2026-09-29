# conky-spotify-nowplaying

A Spotify "now playing" desktop widget for Linux (GNOME on Wayland), built on Conky.

- Album art, title and artist, with long titles wrapped to the widget's width
- Spotify-style controls: previous / play-pause / next, and a seek bar you can click or drag
- Like button (♥) showing whether the track is in your Liked Songs, click to toggle
- Minimize (–) and close (×) buttons next to the heart
- Smoothly scrolling synced lyrics from [LRCLIB](https://lrclib.net), as many lines as the widget
  is tall, fading out at the top and bottom once five or more show
- A placeholder cover while Spotify's DJ talks between songs (or any track without artwork)
- Background colour taken from the album art, fading between tracks
- Drag the widget anywhere, on any monitor; its position is remembered
- Drag an edge or corner to resize it: the sides set its width, the top and bottom how many
  lines of lyrics it shows. An outline shows the new size until you let go; the text keeps
  its size, and the size is remembered
- Top-bar icon with the current track, Text scaling (70% to 200%, the widget keeps its size),
  Reset widget size, an Always on top toggle, start-at-login toggle, Show / Hide widget and Quit

## Install

Install from the PPA, which also pulls in the dependencies (Conky, playerctl, Python GObject
bindings, fonts) and brings new versions with your other updates:

```sh
sudo add-apt-repository ppa:mfagerstrom/conky-spotify-nowplaying
sudo apt install conky-spotify-nowplaying
```

The PPA builds for Ubuntu 26.04 (`resolute`) only, and that is the only release the widget is
tested on. On another release you can try the latest `.deb` from the
[Releases page](https://github.com/mfagerstrom/conky-spotify-nowplaying/releases) instead; apt
reports any dependency that release lacks, and a `.deb` installed this way does not update itself:

```sh
sudo apt install ./conky-spotify-nowplaying_1.1.0_all.deb
```

Then open **Spotify Now Playing** from the app grid. Opening it again stops the widget, or
brings it back if it is minimized.

The – button at the widget's top right minimizes it and × closes it. While it runs there's an
icon in the top bar: click it to see the current track, toggle **Start at login**,
**Show widget** / **Hide widget**, or **Quit**.

Right-click the app icon for more:

- **Start at login** / **Don't start at login**
- **Log in to Spotify (like button)**

The same things work from a terminal:

```sh
conky-spotify-nowplaying                 # start, show if minimized, or stop if running
conky-spotify-nowplaying start|stop|restart|status
conky-spotify-nowplaying show|hide       # bring back a minimized widget, or minimize it
conky-spotify-nowplaying autostart on|off
conky-spotify-nowplaying login
```

To uninstall: `sudo apt remove conky-spotify-nowplaying`. Your settings stay in
`~/.config/conky-spotify-nowplaying` (delete that folder too for a clean slate), and the
autostart entry, if you turned it on, is `~/.config/autostart/conky-spotify-nowplaying.desktop`.

### Like button (optional)

Playback control, lyrics and album art work without any account setup. For the like button:

1. Create an app at <https://developer.spotify.com/dashboard> with redirect URI
   `http://127.0.0.1:8888/callback` and the **Web API** enabled.
2. Save its Client ID:
   `mkdir -p ~/.config/conky-spotify-nowplaying && echo YOUR_CLIENT_ID > ~/.config/conky-spotify-nowplaying/spotify-client-id`
3. Click the heart on the widget (or run `conky-spotify-nowplaying login`) and approve access in
   the browser.

No client secret is needed (PKCE). The refresh token is stored in
`~/.config/conky-spotify-nowplaying/spotify-token.json` with mode 600.

The heart shows a song as liked when any release of it is saved (single, album version, ...),
matching the Spotify app; unliking from the widget removes every saved release.

Spotify's limits for new developer apps are low. If Spotify rate-limits the app, the widget
stops calling the API until the block lifts (the heart shows the track as not liked, and clicking it
tells you how long); everything else keeps working.

## Development

Run straight from a checkout without installing the package. Its dependencies still have to be
there: install the ones listed under `Depends:` in `debian/control` with apt.

```sh
bin/conky-spotify-nowplaying start
```

Run the unit tests (standard library `unittest`, same dependencies as above; they use
temporary directories and never touch `~/.cache`, `~/.config` or the network):

```sh
python3 -m unittest discover -s tests
```

Build the package into `dist/` (needs `sudo apt install debhelper devscripts`):

```sh
./build-deb.sh
```

Packaging lives in `debian/`; the version comes from `debian/changelog`.

### Releasing

Releases are cut with the `/release` skill in Claude Code, and its steps in
[`.claude/skills/release/SKILL.md`](.claude/skills/release/SKILL.md) are the one written-down
process. In short: a `debian/changelog` entry (the only place the version lives), the
[static checks](.claude/skills/_shared/static-checks.md), `./build-deb.sh` and a smoke test of
the installed package, a commit and tag, a GitHub release with the `.deb` attached, and
`./release-ppa.sh` to upload the signed source package to the PPA (needs `dput` and a GPG key
registered on Launchpad), followed until Launchpad publishes it.

## How it works

| File | Role |
| --- | --- |
| `bin/conky-spotify-nowplaying` | Launcher: start/stop, show/hide, autostart, login; supervises the processes below and restarts them if they exit |
| `src/conky.conf` | Conky window setup; renders the text generated by `nowplaying.py`, and sizes the window to it |
| `src/nowplaying.py` | Reads Spotify via `playerctl` (MPRIS), fetches art/lyrics/like state, lays the widget out at its size settings, and writes the widget text plus geometry for `draw.lua` and click areas for `conky-mouse.py` |
| `src/draw.lua` | Cairo drawing: background, buttons, seek bar and times, lyric scrolling (20 fps) |
| `src/conky-mouse.py` | Handles clicks, dragging and resizing (Conky's own mouse hook gets no button presses under XWayland), keeps the window at its saved position and always on top when set, and unmaps it while minimized |
| `src/widget_size.py` | Reads and writes the size settings: width, lyrics height, text scale |
| `src/spotify_api.py` | Minimal Spotify Web API client (PKCE login, like/unlike, Liked Songs index, rate-limit backoff) |
| `src/tray.py` | Top-bar (AppIndicator) icon: current track, text scaling, always on top, start at login, show/hide, Quit |
| `debian/`, `packaging/` | Debian packaging (also used for the PPA), desktop entry, icons |

Settings live in `~/.config/conky-spotify-nowplaying/` (`position`, `size`, `on-top`,
`spotify-client-id`, `spotify-token.json`) and runtime files/logs in
`~/.cache/conky-spotify-nowplaying/`.

## Notes

- Tested on Ubuntu 26.04, GNOME/Wayland, with 2x display scaling across three monitors.
- The widget is an undecorated window on every workspace. Always on top, it stays above
  other windows; with that unticked, it is raised when clicked and covered like any other.
- Lyrics coverage depends on LRCLIB; only synced lyrics are shown.

## License

[MIT](LICENSE)
