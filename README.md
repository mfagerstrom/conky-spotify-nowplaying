# conky-spotify-nowplaying

A Spotify "now playing" desktop widget for Linux (GNOME on Wayland), built on Conky.

- Album art, title and artist, with long titles wrapped to the widget's width
- Spotify-style controls: previous / play-pause / next, and a seek bar you can click or drag
- Like button (♥) showing whether the track is in your Liked Songs, click to toggle
- Minimize (–) and close (×) buttons next to the heart
- Smoothly scrolling synced lyrics from [LRCLIB](https://lrclib.net), as many lines as the widget
  is tall, fading out at the top and bottom once five or more show
- Plain lyrics for a track LRCLIB has no synced lyrics for: a static block from the first line,
  every line alike, that you scroll with the mouse wheel over it
- A placeholder cover while Spotify's DJ talks between songs (or any track without artwork)
- Background colour taken from the album art as it is, fading between tracks; the text and
  controls turn dark on a light background. On a mostly dark cover, a bright colour such as
  a band of light gives way to a deeper shade of it that the cover also has. When neither
  white nor dark text would be easy to read on the cover's main colour, the next colour on
  the cover is used instead, never a lightened or darkened one; a cover with no such colour
  gets Spotify's dark gray (#181818)
- Drag the widget anywhere, on any monitor; its position is remembered. Plugging in or
  unplugging a monitor keeps it where it was on its own monitor, at the same size, or moves
  it onto the nearest one while its own is unplugged
- Drag an edge or corner to resize it: the sides set its width, the top and bottom how many
  lines of lyrics it shows. An outline shows the new size until you let go; the text keeps
  its size, and the size is remembered
- Top-bar icon with the current track, Text scaling (70% to 200%; the widget keeps its size,
  unless larger text wraps a title onto another line), Reset widget size, a Lyrics toggle and
  Lyrics scrolling (Autoscroll or Static), an Always on top toggle, start-at-login toggle,
  Show / Hide widget and Quit

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
sudo apt install ./conky-spotify-nowplaying_1.5.0_all.deb
```

Then open **Spotify Now Playing** from the app grid. Opening it again stops the widget, or
brings it back if it is minimized.

The – button at the widget's top right minimizes it and × closes it. While it runs there's an
icon in the top bar: click it to see the current track, toggle **Start at login**,
**Show widget** / **Hide widget**, or **Quit**.

Two of its settings are about lyrics, and both are remembered:

- **Lyrics** (on by default): untick it and the widget shows only the track, with no lyrics
  area and no lyrics looked up. Tick it again and the lyrics area comes back at the height you
  gave it, with the playing track's lyrics.
- **Lyrics scrolling**: **Autoscroll** (the default) scrolls synced lyrics along with the song,
  the current line bold in the middle. **Static** shows them like plain lyrics instead: every
  line alike from the first one, scrolled with the mouse wheel, back at the top on the next
  track. Plain lyrics are always static, having no timing to follow. The choice is greyed out
  while Lyrics is off.

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

Playback control, lyrics and album art work without any account setup.

The heart reads whether a song is liked from the Spotify desktop app's own copy of your Liked
Songs, which it keeps in `~/.cache/spotify/Users/<name>-user/primary.ldb`. The widget only reads
those files, while the app runs or not, and sends Spotify nothing to do it, so the heart follows
a like or unlike made in the app within about five seconds, needs no login and works while
Spotify rate-limits the widget. It looks for the app's cache where the Spotify apt package,
snap and flatpak keep it. Without those files (Spotify used only on another device, say), or
when they cannot be read, the heart asks the Spotify Web API instead, every two minutes, which needs the login below.

Clicking the heart likes or unlikes the song through the Web API, which needs the login:

1. Create an app at <https://developer.spotify.com/dashboard> with redirect URI
   `http://127.0.0.1:8888/callback` and the **Web API** enabled.
2. Save its Client ID:
   `mkdir -p ~/.config/conky-spotify-nowplaying && echo YOUR_CLIENT_ID > ~/.config/conky-spotify-nowplaying/spotify-client-id`
3. Click the heart on the widget (or run `conky-spotify-nowplaying login`) and approve access in
   the browser.

No client secret is needed (PKCE). The refresh token is stored in
`~/.config/conky-spotify-nowplaying/spotify-token.json` with mode 600.

The heart shows a song as liked when any release of it is saved (single, album version, ...),
matching the Spotify app; unliking from the widget removes every saved release. Like the app,
it does not count a copy saved from a duplicate listing of the same album (Spotify sometimes
lists one album twice, under the same barcode).
To know which songs you have saved under another release, the widget keeps an index of your
Liked Songs in `~/.cache/conky-spotify-nowplaying/library.json`, and counts a release in it
only while the app still has it saved. Finding those other releases uses the Web API, so it
needs the login and pauses while Spotify rate-limits the widget; the heart then shows whether
the app has this exact release saved. After you log in the widget reads
the whole library slowly in the background, one page of 50 songs every 15 seconds (about 25
minutes for 5,000 songs), and does the same again once a day. In between it fetches your
newest likes every five minutes, or, while it can read the app's Liked Songs, only when the
app has a song the index lacks. Until the index is complete,
a song liked only as a different release may show as not liked. The index saves its place
after every page, so quitting the widget or hitting a rate limit resumes the read rather
than restarting it.

Spotify's limits for new developer apps are low, and a request sent while rate-limited can
extend the block, so the widget keeps its traffic small. With the app's files readable, steady
play costs about one request per song the app has not saved (to look for other releases of it,
remembered across restarts in `lookups.json`), plus the daily library read. Every request is
logged with its status to `~/.cache/conky-spotify-nowplaying/nowplaying.log` as an `api:` line.
Only one copy of the widget runs against that cache: a second one waits until the first exits.
If Spotify rate-limits the app, anywhere, the widget stops calling Spotify until the block lifts. The heart keeps showing what the Spotify app has
saved, and a click on it only says when likes can be changed again. Without the app's files
to read, the heart is hidden meanwhile, since it can neither read nor change likes;
everything else keeps working, and minimize and close stay where they are. It comes back,
with the track's like state checked again, once the block lifts.

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
| `src/draw.lua` | Cairo drawing: background, buttons, seek bar and times, lyrics (20 fps) |
| `src/conky-mouse.py` | Handles clicks, the mouse wheel over static lyrics, dragging and resizing (Conky's own mouse hook gets no button presses under XWayland), keeps the window at its saved position and always on top when set, and unmaps it while minimized |
| `src/widget_size.py` | Reads and writes the size settings: width, lyrics height, text scale |
| `src/lyrics_settings.py` | Reads and writes the lyrics settings: on or off, autoscroll or static |
| `src/spotify_api.py` | Minimal Spotify Web API client (PKCE login, like/unlike, Liked Songs index, rate-limit backoff) |
| `src/spotify_local.py` | Reads the Spotify desktop app's Liked Songs from its LevelDB on disk, read-only, for the heart |
| `src/tray.py` | Top-bar (AppIndicator) icon: current track, text scaling, lyrics on/off and scrolling, always on top, start at login, show/hide, Quit |
| `debian/`, `packaging/` | Debian packaging (also used for the PPA), desktop entry, icons |

Settings live in `~/.config/conky-spotify-nowplaying/` (`position`, `size`, `on-top`,
`lyrics`, `lyrics-scrolling`, `spotify-client-id`, `spotify-token.json`) and runtime files/logs in
`~/.cache/conky-spotify-nowplaying/`.

## Notes

- Tested on Ubuntu 26.04, GNOME/Wayland, with 2x display scaling across three monitors.
- The widget is an undecorated window on every workspace. Always on top, it stays above
  other windows; with that unticked, it is raised when clicked and covered like any other.
- Lyrics coverage depends on LRCLIB. Synced lyrics are always preferred, and scroll along
  with the song, the current line bold in the middle. A track with only plain (untimed)
  lyrics shows them from the top without following the song: scroll them with the mouse
  wheel over the lyrics, a line per step, between the first and the last line. They start
  at the top again on the next track. Lyrics scrolling set to Static shows synced lyrics
  the same way. Each track's lyrics are
  cached in `~/.cache/conky-spotify-nowplaying/lyrics/` (up to 2000 tracks, the least
  recently played dropped first), so a track played again, or after a restart, shows them
  without asking LRCLIB. A track LRCLIB has no lyrics for is asked about again after
  a week; a failed lookup is never cached. Delete that folder to fetch everything afresh.

## License

[MIT](LICENSE)
