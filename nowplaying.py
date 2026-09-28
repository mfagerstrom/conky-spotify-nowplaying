#!/usr/bin/env python3
"""Builds the conky now-playing widget's contents.

Four times a second this writes ~/.cache/conky-nowplaying/widget.txt as conky markup,
which conky.conf renders with ${execpi}. It covers:
  - title/artist, wrapped to the widget's fixed column width (measured with Pango,
    using the same fonts conky draws with)
  - album art, downloaded once per track
  - the controls row: draw.lua draws the buttons and seek bar from draw.txt; click
    regions for conky-mouse.py go to regions.json
  - like state (heart), via spotify_api.py; refreshed on track change and every 10 s
  - lyrics from LRCLIB (lrclib.net) -> lyrics.txt; draw.lua scrolls them smoothly (previous /
    current / next line). Unsynced lyrics are spread evenly over the song and shown dimmed.
"""
import json, os, re, subprocess, threading, time, urllib.error, urllib.parse, urllib.request
import gi
gi.require_version('Pango', '1.0'); gi.require_version('PangoCairo', '1.0'); gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Pango, PangoCairo
import colorsys

import spotify_api

CACHE = os.path.expanduser('~/.cache/conky-nowplaying')
OUT = os.path.join(CACHE, 'widget.txt')
COVER = os.path.join(CACHE, 'cover.jpg')
REGIONS = os.path.join(CACHE, 'regions.json')
BG = os.path.join(CACHE, 'bg.txt')               # background colour from the album art, for draw.lua
DRAW = os.path.join(CACHE, 'draw.txt')           # geometry + playback clock for draw.lua
LYRICS = os.path.join(CACHE, 'lyrics.txt')       # timed lyric lines for draw.lua

# Layout, in conky's logical pixels. Must match conky.conf (minimum/maximum_width = 505).
TEXT_WIDTH = 505
MARGIN = 20                                   # border_inner_margin
COLUMN_X = 154                                # text column, right of the 118 px album art
ART_LEFT, ART_BOTTOM = 22, 140               # artwork edges, measured from a capture
LABEL_LIFT = 7                                # lifts NOW PLAYING to the artwork's top edge
LYRIC_GAP = 10                                # space between controls/artwork and lyrics
BOTTOM_TRIM = 10                              # px taken off border_inner_margin at the bottom
                                              # (conky.conf's minimum_height is reduced to match)
SPACER_FONT = 'Ubuntu Sans 1'
SPACER_HEIGHT = 28                            # what conky actually adds for that spacer line (measured)
TIME_DROP = 1                                 # timestamps sit this much below the bar's centre line
COLUMN_WIDTH = TEXT_WIDTH - COLUMN_X - 4
TITLE_FONT, ARTIST_FONT, LYRIC_FONT = 'Ubuntu Sans Bold 17', 'Ubuntu Sans 13', 'Ubuntu Sans 11'
LABEL_FONT, HEART_FONT = 'Ubuntu Sans Bold 10', 'DejaVu Sans 15'
TIME_FONT = 'Ubuntu Sans 11'
CONTROL_ROW_FONT = 'DejaVu Sans 15'            # only sets the controls row's height
SKIP_SIZE, PLAY_SIZE, CONTROL_GAP = 14, 24, 12
LYRIC_ROWS = 3
LIKE_POLL_SECONDS = 10

_pango = PangoCairo.FontMap.get_default().create_context()
PangoCairo.context_set_resolution(_pango, 96)


def wrap(text, font, width=COLUMN_WIDTH, max_lines=3):
    layout = Pango.Layout.new(_pango)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_width(width * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_text(text, -1)
    raw = text.encode()
    lines = [raw[l.start_index:l.start_index + l.length].decode().strip() for l in layout.get_lines_readonly()]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + '…'
    return lines


def esc(text):
    return text.replace('$', '$$')


def conky_font(pango_font):
    """'Ubuntu Sans Bold 17' -> 'Ubuntu Sans:bold:size=17'"""
    *name, size = pango_font.split()
    bold = 'Bold' in name
    name = ' '.join(n for n in name if n != 'Bold')
    return f"{name}{':bold' if bold else ''}:size={size}"


def playerctl(*args):
    return subprocess.run(['playerctl', '-p', 'spotify', *args], capture_output=True, text=True).stdout.strip()


class State:
    def __init__(self):
        self.track = None          # mpris:trackid of the track the extras below belong to
        self.liked = None          # True / False / None (unknown or not logged in)
        self.liked_checked = 0.0
        self.lyrics = None         # {'synced': [(sec, line)], 'plain': [line]} or {} if none
        self.lyrics_retry = None   # time to retry a lyrics fetch that hit a network error
        self.lyrics_written = None # version of the lyrics last written to lyrics.txt
        self.lock = threading.Lock()


state = State()


def art_colour(path):
    """Spotify-style backdrop: the cover's biggest vivid colour (if it covers at least 5%
    of the image, else its dominant colour), darkened so white text stays readable."""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, False)
    n, stride, px = pb.get_n_channels(), pb.get_rowstride(), pb.get_pixels()
    buckets, total = {}, 0
    for yy in range(pb.get_height()):
        for xx in range(pb.get_width()):
            i = yy * stride + xx * n
            r, g, b = px[i] / 255, px[i + 1] / 255, px[i + 2] / 255
            h, sat, v = colorsys.rgb_to_hsv(r, g, b)
            key = (int(h * 12), int(sat * 3), int(v * 3))
            tot = buckets.setdefault(key, [0, 0.0, 0.0, 0.0, sat >= 0.35 and v >= 0.3])
            tot[0] += 1; tot[1] += r; tot[2] += g; tot[3] += b
            total += 1
    vivid = [t for t in buckets.values() if t[4]]
    best = max(vivid) if vivid and max(vivid)[0] >= 0.05 * total else max(buckets.values())
    count, r, g, b, _ = best
    h, sat, v = colorsys.rgb_to_hsv(r / count, g / count, b / count)
    return colorsys.hsv_to_rgb(h, min(sat, 0.65), min(max(v, 0.25), 0.38))


def update_bg():
    try:
        r, g, b = art_colour(COVER)
    except Exception:
        r, g, b = 0.094, 0.094, 0.094   # Spotify's #181818
    write_atomic(BG, f'{r:.3f} {g:.3f} {b:.3f}\n')


def fetch_cover(url):
    if not url or url == _read(os.path.join(CACHE, 'cover.url')):
        if not os.path.exists(BG):
            update_bg()
        return
    tmp = COVER + f'.{os.getpid()}.tmp'
    try:
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, COVER)
        with open(os.path.join(CACHE, 'cover.url'), 'w') as f:
            f.write(url)
    except Exception:
        return
    update_bg()


def fetch_liked(track_id):
    # Right after a click Spotify can still report the old state; trust the click.
    if spotify_api.recently_toggled():
        return
    try:
        uri = spotify_api.current_track_uri()
        liked = spotify_api.is_liked(uri) if uri else None
    except Exception:
        liked = None
    with state.lock:
        if state.track != track_id:
            return
        state.liked = liked
    spotify_api.write_liked(liked)


def _lrclib(path, **params):
    req = urllib.request.Request(f'https://lrclib.net/api/{path}?' + urllib.parse.urlencode(params),
                                 headers={'User-Agent': 'conky-nowplaying (personal desktop widget)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_lyrics(track_id, artist, title, album, duration):
    """Exact match first (LRCLIB wants whole seconds), then the closest-length search hit.
    Network errors leave state.lyrics as None so the next render retries."""
    try:
        record = _lrclib('get', artist_name=artist, track_name=title, album_name=album,
                         duration=int(duration))
        if not record or not (record.get('syncedLyrics') or record.get('plainLyrics')):
            # Prefer a version within 3 s of Spotify's length; otherwise accept one within
            # 20 s (a different edit of the song -- lines may drift a little, but lyrics
            # that drift beat no lyrics).
            hits = [h for h in (_lrclib('search', track_name=title, artist_name=artist) or [])
                    if (h.get('syncedLyrics') or h.get('plainLyrics'))]
            for tolerance in (3, 20):
                close = [h for h in hits if abs((h.get('duration') or 0) - duration) <= tolerance]
                if close:
                    close.sort(key=lambda h: (not h.get('syncedLyrics'), abs(h['duration'] - duration)))
                    record = close[0]
                    break
            else:
                record = None
    except Exception:
        with state.lock:
            if state.track == track_id:
                state.lyrics_retry = time.time() + 30
        return
    lyrics = {}
    if record and record.get('syncedLyrics'):
        synced = []
        for line in record['syncedLyrics'].splitlines():
            m = re.match(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)', line)
            if m:
                synced.append((int(m[1]) * 60 + float(m[2]), m[3].strip()))
        lyrics['synced'] = synced
    elif record and record.get('plainLyrics'):
        lyrics['plain'] = [l.strip() for l in record['plainLyrics'].splitlines() if l.strip()]
    with state.lock:
        if state.track == track_id:
            state.lyrics = lyrics


def _read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return ''


def fmt_time(sec):
    sec = int(sec)
    return f'{sec // 60}:{sec % 60:02d}'


def text_width(text, font):
    layout = Pango.Layout.new(_pango)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_text(text, -1)
    return layout.get_pixel_size()[0]


def scale():
    out = subprocess.run(['xrdb', '-query'], capture_output=True, text=True).stdout
    m = re.search(r'Xft\.dpi:\s*(\d+)', out)
    return round(int(m.group(1)) / 96) if m else 1


SCALE = scale()


def write_atomic(path, text):
    with open(path + '.tmp', 'w') as f:
        f.write(text)
    os.replace(path + '.tmp', path)


def font_ascent(font):
    return _pango.get_metrics(Pango.FontDescription.from_string(font), None).get_ascent() / Pango.SCALE


def ink_extents(text, font):
    layout = Pango.Layout.new(_pango)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_text(text, -1)
    return layout.get_pixel_extents()[0]


def line_height(*fonts):
    """Conky's height for a line: the tallest font's ascent + descent."""
    heights = []
    for font in fonts:
        m = _pango.get_metrics(Pango.FontDescription.from_string(font), None)
        heights.append((m.get_ascent() + m.get_descent()) / Pango.SCALE)
    return max(heights)


def write_regions(regions):
    """Clickable areas (window-relative, logical px) for conky-mouse.py."""
    write_atomic(REGIONS, json.dumps(regions))


def render():
    status = playerctl('status')
    if status not in ('Playing', 'Paused'):
        write_regions({})
        write_atomic(DRAW, '')
        return "${color}${font Ubuntu Sans:size=11}Spotify not playing${font}\n"

    meta = playerctl('metadata', '--format',
                     '{{mpris:trackid}}\t{{title}}\t{{artist}}\t{{album}}\t{{mpris:artUrl}}\t{{position}}\t{{mpris:length}}')
    parts = meta.split('\t')
    if len(parts) != 7:
        return "${color}Loading…\n"
    track_id, title, artist, album, art, pos_us, len_us = parts
    position = int(pos_us or 0) / 1e6
    duration = int(len_us or 0) / 1e6

    now = time.time()
    with state.lock:
        new_track = track_id != state.track
        if new_track:
            state.track, state.liked, state.lyrics, state.liked_checked = track_id, None, None, now
            state.lyrics_retry = None
        retry_lyrics = state.lyrics_retry is not None and now >= state.lyrics_retry
        if retry_lyrics:
            state.lyrics_retry = None
        poll_like = new_track or now - state.liked_checked > LIKE_POLL_SECONDS
        if poll_like:
            state.liked_checked = now
    if new_track:
        spotify_api.write_liked(None)  # don't show the previous track's heart
        threading.Thread(target=fetch_cover, args=(art,), daemon=True).start()
    if new_track or retry_lyrics:
        threading.Thread(target=fetch_lyrics, args=(track_id, artist, title, album, duration), daemon=True).start()
    if poll_like:
        threading.Thread(target=fetch_liked, args=(track_id,), daemon=True).start()

    # A click on the heart writes the new state to the liked file immediately.
    liked_file = _read(spotify_api.LIKED_FILE)
    liked = {'1': True, '0': False}.get(liked_file, state.liked)
    heart = '${color1}♥' if liked else ('${color}♡' if liked is False else '${color3}♡')

    g = f'${{goto {COLUMN_X}}}'
    # Running top of the current line, to place what draw.lua draws. The first line is
    # lifted so the label's cap height lines up with the top of the artwork (measured).
    y = MARGIN - LABEL_LIFT
    out = [f"${{image {COVER} -p 0,0 -s 118x118 -n}}${{voffset -{LABEL_LIFT}}}"
           f"{g}${{color1}}${{font {conky_font(LABEL_FONT)}}}NOW PLAYING${{font}}"
           f"${{alignr}}${{font {conky_font(HEART_FONT)}}}{heart}${{font}}"]
    y += line_height(LABEL_FONT, HEART_FONT)
    for line in wrap(title, TITLE_FONT):
        out.append(f"{g}${{color2}}${{font {conky_font(TITLE_FONT)}}}{esc(line)}${{font}}")
        y += line_height(TITLE_FONT)
    for line in wrap(artist, ARTIST_FONT, max_lines=2):
        out.append(f"{g}${{color}}${{font {conky_font(ARTIST_FONT)}}}{esc(line)}${{font}}")
        y += line_height(ARTIST_FONT)
    # Controls row, Spotify-style: ⏮ ▶ ⏭ (drawn by draw.lua), elapsed, seek bar, total.
    row = line_height(CONTROL_ROW_FONT, TIME_FONT)
    elapsed, total = fmt_time(position), fmt_time(duration)
    prev_cx = COLUMN_X + SKIP_SIZE / 2
    play_cx = prev_cx + SKIP_SIZE / 2 + CONTROL_GAP + PLAY_SIZE / 2
    next_cx = play_cx + PLAY_SIZE / 2 + CONTROL_GAP + SKIP_SIZE / 2
    time_x = round(next_cx + SKIP_SIZE / 2 + CONTROL_GAP + 4)
    # Centre the bar and buttons on the timestamps' digits. Conky draws the row's baseline
    # at the bottom of the row (measured from a capture); the digits' ink ends there.
    ink = ink_extents(total, TIME_FONT)
    ink_bottom = ink.y + ink.height - font_ascent(TIME_FONT)   # relative to the baseline, ~0
    mid_offset = row + ink_bottom - ink.height / 2 + 0.75     # + stroke rounding, measured
    # Push the row down so the play button's bottom meets the artwork's bottom, when the
    # title/artist leave room for that.
    push = max(0, round(ART_BOTTOM - PLAY_SIZE / 2 - (y + mid_offset)))
    y += push
    mid_y = y + mid_offset
    out.append(f"${{voffset {push}}}{g}${{font {conky_font(CONTROL_ROW_FONT)}}} ${{font}}"   # reserves the row height
               f"${{goto {time_x}}}${{voffset {TIME_DROP}}}${{color}}${{font {conky_font(TIME_FONT)}}}{elapsed}"
               f"${{alignr}}{total}${{font}}${{voffset -{TIME_DROP}}}")
    time_w = text_width(total, TIME_FONT)      # elapsed never has more digits than total
    bar_x0 = time_x + time_w + 10
    bar_x1 = MARGIN + TEXT_WIDTH - time_w - 10
    fraction = min(max(position / duration, 0), 1) if duration else 0
    y += row

    draw = [f'scale {SCALE}',
            f'bar {bar_x0} {bar_x1} {mid_y} {fraction:.4f} {duration:.3f}',
            f'controls {prev_cx} {play_cx} {next_cx} {mid_y} {SKIP_SIZE} {PLAY_SIZE} {int(status == "Playing")}',
            f'clock {time.monotonic():.3f} {position:.3f} {int(status == "Playing")}']
    lyr_version = write_lyrics(track_id, duration)
    if lyr_version:
        # Full width under the artwork and controls: reserve LYRIC_ROWS rows there, and
        # draw.lua draws and scrolls the lyrics inside them.
        lyric_row = line_height(LYRIC_FONT)
        top = max(mid_y + PLAY_SIZE / 2, ART_BOTTOM) + LYRIC_GAP
        gap = round(top - y)
        # One tiny spacer line, pushed down so the text ends where the lyric rows end
        # (minus the bottom trim); blank lyric-font lines leave extra slack instead.
        push = round(gap + LYRIC_ROWS * lyric_row - SPACER_HEIGHT - BOTTOM_TRIM)
        out.append(f"${{voffset {push}}}${{font {conky_font(SPACER_FONT)}}} ${{font}}")
        draw.append(f'lyrics {ART_LEFT} {MARGIN + TEXT_WIDTH} {y + gap} {lyric_row} {lyr_version}')
    write_atomic(DRAW, '\n'.join(draw) + '\n')

    half = PLAY_SIZE / 2 + 4
    heart_size = line_height(HEART_FONT)
    write_regions({
        # a little padding around each target makes them easier to hit
        'prev': [prev_cx - SKIP_SIZE / 2 - 5, mid_y - half, prev_cx + SKIP_SIZE / 2 + 5, mid_y + half],
        'play': [play_cx - half, mid_y - half, play_cx + half, mid_y + half],
        'next': [next_cx - SKIP_SIZE / 2 - 5, mid_y - half, next_cx + SKIP_SIZE / 2 + 5, mid_y + half],
        'seek': [bar_x0 - 6, mid_y - 12, bar_x1 + 6, mid_y + 12],
        'heart': [MARGIN + TEXT_WIDTH - heart_size - 8, MARGIN - 8, MARGIN + TEXT_WIDTH + 12, MARGIN + heart_size + 6],
        'bar': [bar_x0, bar_x1],
        'duration': duration,
    })
    return '\n'.join(out)   # no trailing newline: it would add an empty line at the bottom


def write_lyrics(track_id, duration):
    """Writes lyrics.txt for draw.lua when the track's lyrics change; returns a version
    string (changes with the lyrics) or None when there are no lyrics to show."""
    lyr = state.lyrics or {}
    if lyr.get('synced'):
        kind, lines = 'synced', [(t, l) for t, l in lyr['synced']]
        if lines and lines[0][0] > 0:
            lines.insert(0, (0.0, ''))           # before the first line: show the intro as ♪
    elif lyr.get('plain') and duration:
        n = len(lyr['plain'])                    # unsynced: spread lines evenly over the song
        kind, lines = 'plain', [(i * duration / n, l) for i, l in enumerate(lyr['plain'])]
    else:
        return None
    version = f'{kind}-{abs(hash((track_id, len(lines)))) % 10**8}'
    if state.lyrics_written != version:
        body = '\n'.join(f'{t:.2f}\t{l or "♪"}' for t, l in lines)
        write_atomic(LYRICS, f'{kind}\n{body}\n')
        state.lyrics_written = version
    return version


def main():
    os.makedirs(CACHE, exist_ok=True)
    while True:
        try:
            text = render()
        except Exception as e:
            text = f"${{color}}widget error: {esc(str(e))[:60]}\n"
        with open(OUT + '.tmp', 'w') as f:
            f.write(text)
        os.replace(OUT + '.tmp', OUT)
        time.sleep(0.25)


if __name__ == '__main__':
    main()
