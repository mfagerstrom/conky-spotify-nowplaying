#!/usr/bin/env python3
"""Builds the conky now-playing widget's contents.

Four times a second this writes ~/.cache/conky-spotify-nowplaying/widget.txt as conky markup,
which conky.conf renders with ${execpi}. It covers:
  - title/artist, wrapped to the widget's fixed column width (measured with Pango,
    using the same fonts conky draws with)
  - album art, downloaded once per track
  - the controls row: draw.lua draws the buttons and seek bar from draw.txt; click
    regions for conky-mouse.py go to regions.json
  - the heart, minimize and close buttons at the top right (drawn by draw.lua)
  - like state (heart), via spotify_api.py; refreshed on track change and every 10 s
  - lyrics from LRCLIB (lrclib.net) -> lyrics.txt. draw.lua scrolls synced ones smoothly
    along with playback (previous / current / next line); plain ones, for a track LRCLIB
    has no synced lyrics for, are a static block the mouse wheel scrolls. Each answer is
    cached on disk per track (lyrics/), so a track played again is not looked up. The tray
    menu's settings (lyrics_settings.py) turn lyrics off, which collapses their area and
    looks nothing up, or show synced ones statically too.
"""
import functools, hashlib, json, math, os, re, subprocess, threading, time, urllib.error, urllib.parse, urllib.request
import gi
gi.require_version('Pango', '1.0'); gi.require_version('PangoCairo', '1.0'); gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, Pango, PangoCairo
import colorsys

import lyrics_settings
import spotify_api
import widget_size as size_file

CACHE = os.path.expanduser('~/.cache/conky-spotify-nowplaying')
OUT = os.path.join(CACHE, 'widget.txt')
COVER = os.path.join(CACHE, 'cover.jpg')
# Shown for a track with no artwork at all, as while Spotify's DJ talks between songs,
# instead of the last song's cover.
NO_ART_URL = 'file://' + os.path.join(os.path.dirname(os.path.abspath(__file__)), 'no-art.png')
REGIONS = os.path.join(CACHE, 'regions.json')
LOG = os.path.join(CACHE, 'nowplaying.log')
BG = os.path.join(CACHE, 'bg.txt')               # background colour from the album art, for draw.lua
DRAW = os.path.join(CACHE, 'draw.txt')           # geometry + playback clock for draw.lua
LYRICS = os.path.join(CACHE, 'lyrics.txt')       # lyric lines for draw.lua, timed when synced
LYRICS_SCROLL = os.path.join(CACHE, 'lyrics-scroll')   # plain lyrics' wheel offset (conky-mouse.py)
LYRICS_CACHE = os.path.join(CACHE, 'lyrics')     # one JSON file per track looked up on LRCLIB
LYRICS_CACHE_FORMAT = 2                          # 2: plain lyrics are kept, not only synced ones
LYRICS_CACHE_MAX = 2000                          # files kept; the least recently used go first
NO_LYRICS_TTL = 7 * 24 * 3600                    # LRCLIB gains lyrics, so "none" is asked again

# Layout, in conky's logical pixels. conky.conf sets no size or margin: the window is as big
# as this markup, which carries the margins and, through a goto, the width. The user's size
# settings (widget_size.py) set the width, the lyrics' height and the text's size.
MARGIN = 20                                   # around the contents
MIN_HEIGHT = 108                              # the contents' height at least: the artwork's, less BOTTOM_TRIM
COLUMN_X = 154                                # text column, right of the 118 px album art
ART_LEFT, ART_BOTTOM = 22, 140               # artwork edges, measured from a capture
ART_TOP = ART_LEFT                            # the same margin as its left; draw.lua tops NOW PLAYING at it
TOP_ROW = 13                                  # the top of the row NOW PLAYING and the icons sit in
LYRIC_GAP = 10                                # space between controls/artwork and lyrics
LYRIC_SPACING = 3                             # extra space between lyric rows
BOTTOM_TRIM = 10                              # px taken off MARGIN at the bottom
SPACER_FONT = 'Ubuntu Sans 1'
SPACER_HEIGHT = 2                             # what conky adds for that spacer line (measured)
BAR_DROP = 2                                  # seek bar sits this much below the buttons' centre line
TITLE_FONT, ARTIST_FONT, LYRIC_FONT = 'Ubuntu Sans Bold 17', 'Ubuntu Sans 13', 'Ubuntu Sans 11'
LABEL_FONT, HEART_FONT = 'Ubuntu Sans Bold 10', 'DejaVu Sans 15'
MESSAGE_FONT = 'Ubuntu Sans 11'               # "Spotify not playing" and the like
PLAIN_FONT = 'Ubuntu Sans 13'                 # between runs of text; see plain()
CONTROL_ROW_FONT = 'DejaVu Sans 15'            # only sets the controls row's height
# The fonts the text scale applies to; the heart and control fonts only size icons' rows.
# Those beside the artwork, and the times in the controls row, follow it only as far as the
# label, title and artist fit there (see header_scale_for). PLAIN_FONT is ARTIST_FONT, and
# TIME_FONT is set apart from LYRIC_FONT and MESSAGE_FONT by its weight name alone.
TIME_FONT = 'Ubuntu Sans Regular 11'
HEADER_FONTS = {TITLE_FONT, ARTIST_FONT, LABEL_FONT, PLAIN_FONT, TIME_FONT}
TEXT_FONTS = HEADER_FONTS | {LYRIC_FONT, MESSAGE_FONT}
SKIP_SIZE, PLAY_SIZE, CONTROL_GAP = 14, 24, 12
WINDOW_BUTTON_SIZE = 10                       # minimize / close icons, right of the heart
WINDOW_BUTTON_PITCH = 22                      # centre to centre, and each one's hit width
HEART_PITCH = 25                              # heart centre to minimize centre
HEART_WIDTH = 16                              # the heart draw.lua strokes, as wide as HEART_FONT's ♡
LIKE_POLL_SECONDS = 30
LIBRARY_PAGE_SECONDS = 15                     # between Liked Songs pages of a full scan
METADATA_SETTLE = 0.75                        # s to wait after a track change before lookups
NO_ART_WAIT = 3                               # s a track with a length waits for art before the placeholder

_pango = PangoCairo.FontMap.get_default().create_context()
PangoCairo.context_set_resolution(_pango, 96)

# The user's size settings, read on every render, and the text scale beside the artwork.
widget_width, lyrics_height, text_scale = size_file.DEFAULTS
header_scale = text_scale


def sized(font):
    """The font at the text scale, if it is text: 'Ubuntu Sans Bold 17' at 1.5 ->
    'Ubuntu Sans Bold 25.5'."""
    if font not in TEXT_FONTS:
        return font
    *name, pt = font.split()
    return f"{' '.join(name)} {float(pt) * scale_of(font):g}"


def scale_of(font):
    return header_scale if font in HEADER_FONTS else text_scale if font in TEXT_FONTS else 1


def font_description(font):
    return Pango.FontDescription.from_string(sized(font))


def wrap(text, font, width=None, max_lines=3):
    if width is None:
        width = widget_width - COLUMN_X - 4        # the text column
    layout = Pango.Layout.new(_pango)
    layout.set_font_description(font_description(font))
    layout.set_width(round(width * Pango.SCALE))
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
    """'Ubuntu Sans Bold 17' -> 'Ubuntu Sans:bold:size=17', at the text scale"""
    *name, size = sized(pango_font).split()
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
        self.lyrics = None         # {'synced': [(sec, line)]}, {'plain': [line]}, or {} for none
        self.lyrics_retry = None   # time to retry a lyrics fetch that hit a network error
        self.lyrics_key = None     # (track, title, artist, album, length) the lyrics are for / being fetched for
        self.cover_url = None      # art URL last requested
        self.track_since = 0.0     # when the current track id first appeared
        self.lyrics_written = None # version of the lyrics last written to lyrics.txt
        self.lyrics_settings = None   # (shown, scrolling) last logged
        self.heart_hidden = False     # the heart was left out while Spotify rate limits likes
        self.lock = threading.Lock()


state = State()


def art_colour(path):
    """Spotify-style backdrop: the cover's biggest vivid colour (if it covers at least 5%
    of the image, else its dominant colour), darkened so white text stays readable.
    On a mostly grayscale cover, any small splash of colour beats the gray/black, and a
    grayish or near-black dominant colour loses to the cover's main hue once colour fills
    10% of it."""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, False)
    n, stride, px = pb.get_n_channels(), pb.get_rowstride(), pb.get_pixels()
    buckets, accents, total, neutral = {}, {}, 0, 0
    for yy in range(pb.get_height()):
        for xx in range(pb.get_width()):
            i = yy * stride + xx * n
            r, g, b = px[i] / 255, px[i + 1] / 255, px[i + 2] / 255
            h, sat, v = colorsys.rgb_to_hsv(r, g, b)
            key = (int(h * 12), int(sat * 3), int(v * 3))
            tot = buckets.setdefault(key, [0, 0.0, 0.0, 0.0, sat >= 0.35 and v >= 0.3])
            tot[0] += 1; tot[1] += r; tot[2] += g; tot[3] += b
            total += 1
            if sat < 0.15 or v < 0.15:           # gray, or too dark for its hue to mean much
                neutral += 1
            elif sat >= 0.25 and v >= 0.2:       # accent candidates, grouped by hue only
                acc = accents.setdefault(int(h * 12 + 0.5) % 12, [0, 0.0, 0.0, 0.0])
                acc[0] += 1; acc[1] += r; acc[2] += g; acc[3] += b
    accent = max(accents.values(), default=None)
    vivid = [t for t in buckets.values() if t[4]]
    dominant = max(buckets.values())
    _, dsat, dv = colorsys.rgb_to_hsv(*(c / dominant[0] for c in dominant[1:4]))
    if neutral >= 0.85 * total and accent and accent[0] >= 0.004 * total:
        best = accent                            # ~9px floor keeps JPEG noise from winning
    elif vivid and max(vivid)[0] >= 0.05 * total:
        best = max(vivid)
    elif (dsat < 0.25 or dv < 0.2) and sum(a[0] for a in accents.values()) >= 0.10 * total:
        best = accent                            # a dominant colour too faint to be an accent loses
    else:
        best = dominant
    count, r, g, b = best[:4]
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
        log(f'like check skipped (recent click): {track_id}')
        return
    if spotify_api.rate_limited_until():
        return                                   # heart stays unknown (outline) until it lifts
    uri = None
    try:
        uri = spotify_api.current_track_uri()
        liked = spotify_api.is_liked_any(uri) if uri else None
        log(f'like check: {track_id} uri={uri} liked={liked}')
    except Exception as e:
        liked = None
        log(f'like check failed: {track_id} uri={uri} {type(e).__name__}: {e}')
    with state.lock:
        if state.track != track_id:
            return
        state.liked = liked
    spotify_api.write_liked(liked)


def _lrclib(path, **params):
    req = urllib.request.Request(f'https://lrclib.net/api/{path}?' + urllib.parse.urlencode(params),
                                 headers={'User-Agent': 'conky-spotify-nowplaying (https://github.com/mfagerstrom/conky-spotify-nowplaying)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def log(msg):
    if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
        os.replace(LOG, LOG + '.1')              # keep the log small: one rotated copy
    with open(LOG, 'a') as f:
        f.write(time.strftime('%T ') + msg + '\n')


def lyrics_cache_path(key):
    """The cache file for a lookup. It is named by what LRCLIB is asked (title, artist,
    album, whole seconds), not the Spotify id, so a track whose metadata changes after
    the lookup is looked up again rather than served the old answer."""
    _track_id, title, artist, album, duration = key
    name = hashlib.sha1(json.dumps([title, artist, album, int(duration)]).encode()).hexdigest()
    return os.path.join(LYRICS_CACHE, name + '.json')


def read_cached_lyrics(key):
    """The cached lyrics for key as fetch_lyrics would set them, or None when there is no
    usable entry: none at all, unreadable, or a "no lyrics" answer that has expired or
    predates LYRICS_CACHE_FORMAT (an entry without a format kept only synced lyrics, so
    its "none" may hide plain ones)."""
    path = lyrics_cache_path(key)
    try:
        with open(path) as f:
            entry = json.load(f)
        synced = [(float(sec), str(line)) for sec, line in entry['synced']]
        plain = [str(line) for line in entry.get('plain', [])]
        if not synced and not plain and (entry.get('format', 1) < LYRICS_CACHE_FORMAT
                                         or not 0 <= time.time() - entry['fetched'] <= NO_LYRICS_TTL):
            return None                          # expired, or dated in the future by a clock change
        os.utime(path)                           # mark it used, for pruning
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {'synced': synced} if synced else {'plain': plain} if plain else {}


def cache_lyrics(key, lyrics):
    """Saves a successful lookup, then prunes the least recently used entries past
    LYRICS_CACHE_MAX. A failure here costs only the cache, never the lyrics on screen."""
    try:
        os.makedirs(LYRICS_CACHE, exist_ok=True)
        write_atomic(lyrics_cache_path(key),
                     json.dumps({'format': LYRICS_CACHE_FORMAT, 'fetched': time.time(),
                                 'synced': lyrics.get('synced', []), 'plain': lyrics.get('plain', [])}))
    except OSError as e:
        log(f'lyrics cache write failed: {type(e).__name__}: {e}')
        return
    try:
        names = [n for n in os.listdir(LYRICS_CACHE) if n.endswith('.json')]
    except OSError:                              # the folder deleted since the write
        return
    if len(names) <= LYRICS_CACHE_MAX:
        return
    files = []
    for name in names:
        try:
            files.append((os.path.getmtime(os.path.join(LYRICS_CACHE, name)), name))
        except OSError:                          # pruned by another fetch meanwhile
            pass
    for _, name in sorted(files)[:max(0, len(files) - LYRICS_CACHE_MAX)]:
        try:
            os.remove(os.path.join(LYRICS_CACHE, name))
        except OSError:
            pass


def closest(hits, duration):
    """The search hit nearest Spotify's length: within 3 s if there is one, else within
    20 s (a different edit of the song -- synced lines may drift a little, but lyrics that
    drift beat no lyrics), else None."""
    for tolerance in (3, 20):
        close = [h for h in hits if abs((h.get('duration') or 0) - duration) <= tolerance]
        if close:
            return min(close, key=lambda h: abs(h['duration'] - duration))
    return None


def plain_lines(text):
    """Plain lyrics as lines, blank ones kept between verses: one at most, none at the ends."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def lyrics_summary(lyrics):
    """'12 synced lines', '30 plain lines' or 'none', for the log."""
    kind = 'synced' if lyrics.get('synced') else 'plain' if lyrics.get('plain') else None
    return f'{len(lyrics[kind])} {kind} lines' if kind else 'none'


def retry_lyrics(key):
    """Has the next render look the lyrics up again in 30 s, if the track still plays."""
    with state.lock:
        if state.lyrics_key == key:
            state.lyrics_retry = time.time() + 30


def fetch_lyrics(key):
    """The track's lyrics, from the cache when it has them. Otherwise LRCLIB: the exact
    match (it wants whole seconds), then the closest-length search hit with synced lyrics.
    Synced lyrics always win; plain ones are kept only when no synced version is close
    enough: the exact match's, else the closest search hit's. The answer is cached, "none"
    included. An exact match that fails (a server error, a timeout) is skipped for the
    search. Network errors that leave nothing found (the search failing, or finding nothing
    after the exact match failed) leave state.lyrics as None so the next render retries 30 s
    on, and are not cached."""
    track_id, title, artist, album, duration = key
    lyrics = read_cached_lyrics(key)
    if lyrics is not None:
        log(f"lyrics: {lyrics_summary(lyrics)} for {artist} - {title} ({duration:.0f} s), from the cache")
        with state.lock:
            if state.lyrics_key == key:
                state.lyrics = lyrics
        return
    try:
        record = _lrclib('get', artist_name=artist, track_name=title, album_name=album,
                         duration=int(duration))
        get_failed = False
    except Exception as e:                           # a server error or timeout: the search often still answers
        log(f'lyrics: exact match failed ({type(e).__name__}), using the search: {artist} - {title}')
        record, get_failed = None, True
    try:
        if not (record or {}).get('syncedLyrics'):
            hits = _lrclib('search', track_name=title, artist_name=artist) or []
            synced = closest([h for h in hits if h.get('syncedLyrics')], duration)
            if synced or not (record or {}).get('plainLyrics'):
                record = synced or closest([h for h in hits if h.get('plainLyrics')], duration)
    except Exception as e:
        log(f'lyrics lookup failed ({type(e).__name__}), retrying in 30 s: {artist} - {title}')
        retry_lyrics(key)
        return
    lyrics, record = {}, record or {}
    synced = []
    for line in (record.get('syncedLyrics') or '').splitlines():
        m = re.match(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)', line)
        if m:
            synced.append((int(m[1]) * 60 + float(m[2]), m[3].strip()))
    if synced:
        lyrics['synced'] = synced
    elif record.get('plainLyrics'):                  # also when no synced line had a timestamp
        lyrics['plain'] = plain_lines(record['plainLyrics'])
    if get_failed and not lyrics:
        # Only the exact match can rule the lyrics out, so "none" waits for it.
        log(f'lyrics: none from the search, retrying in 30 s: {artist} - {title}')
        retry_lyrics(key)
        return
    log(f'lyrics: {lyrics_summary(lyrics)} for {artist} - {title} ({duration:.0f} s)')
    cache_lyrics(key, lyrics)
    with state.lock:
        if state.lyrics_key == key:
            state.lyrics = lyrics


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def fmt_time(sec):
    sec = int(sec)
    return f'{sec // 60}:{sec % 60:02d}'


def text_width(text, font):
    layout = Pango.Layout.new(_pango)
    layout.set_font_description(font_description(font))
    layout.set_text(text, -1)
    return layout.get_pixel_size()[0]


def scale():
    out = subprocess.run(['xrdb', '-query'], capture_output=True, text=True).stdout
    m = re.search(r'Xft\.dpi:\s*(\d+)', out)
    return round(int(m.group(1)) / 96) if m else 1


SCALE = scale()


def write_atomic(path, text):
    # Threads can write one file at once (two fetches of a track's lyrics, or of its
    # cover's colour), so each writes its own temporary file.
    tmp = f'{path}.{threading.get_ident()}.tmp'
    try:
        with open(tmp, 'w') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def font_ascent(font):
    return _pango.get_metrics(font_description(font), None).get_ascent() / Pango.SCALE


def ink_extents(text, font):
    """(top, height) of the text's ink, from the top of its line"""
    layout = Pango.Layout.new(_pango)
    layout.set_font_description(font_description(font))
    layout.set_text(text, -1)
    ink = layout.get_pixel_extents()[0]
    return ink.y, ink.height


def line_height(*fonts):
    """Conky's height for a line: the tallest font's ascent + descent."""
    heights = []
    for font in fonts:
        m = _pango.get_metrics(font_description(font), None)
        heights.append((m.get_ascent() + m.get_descent()) / Pango.SCALE)
    return max(heights)


def write_regions(regions):
    """Clickable areas (window-relative, logical px) for conky-mouse.py."""
    write_atomic(REGIONS, json.dumps(regions))


def controls_mid(total):
    """How far below the controls row's top its buttons and bar sit: on the line through the
    middle of 11 pt digits, the way they were placed when conky drew the times (it draws a
    row's baseline at the row's bottom, measured from a capture). Measured at the text scale
    and scaled back, so the row does not move with the text."""
    ink_y, ink_h = ink_extents(total, TIME_FONT)
    k = scale_of(TIME_FONT)
    ink_bottom = (ink_y + ink_h - font_ascent(TIME_FONT)) / k      # below the baseline, ~0
    return line_height(CONTROL_ROW_FONT) + ink_bottom - ink_h / k / 2 + 0.75   # + stroke rounding


@functools.cache                                  # fonts, constants and the text scale only
def header_scale_for(scale):
    """The text scale for the label, title and artist: `scale`, or as much of it as still
    fits the label row and a line each of title and artist above the controls, in steps of
    0.05, so a larger text scale alone doesn't push the controls below the artwork."""
    global header_scale
    fits = ART_BOTTOM - PLAY_SIZE / 2 - controls_mid('0:00') - TOP_ROW
    header_scale = scale
    while header_scale > 1 and (line_height(LABEL_FONT, HEART_FONT, PLAIN_FONT)
                                + line_height(TITLE_FONT) + line_height(ARTIST_FONT)) > fits:
        header_scale = round(header_scale - 0.05, 2)
    return header_scale


@functools.cache                                  # fonts, constants and the widget settings only
def top_buttons(_width, _header_scale):
    """The heart, minimize and close on the top row: their centres (x), the shared centre
    line (y), and the click regions for minimize and close."""
    close_cx = MARGIN + widget_width - WINDOW_BUTTON_SIZE / 2
    min_cx = close_cx - WINDOW_BUTTON_PITCH
    heart_cx = min_cx - HEART_PITCH
    # Centred on HEART_FONT's ♡ ink in the top row, whose height that font sets; conky puts
    # the row's baseline at its bottom (measured).
    ink_y, ink_h = ink_extents('♡', HEART_FONT)
    cy = (TOP_ROW + line_height(LABEL_FONT, HEART_FONT) - font_ascent(HEART_FONT)
          + ink_y + ink_h / 2)
    half = WINDOW_BUTTON_PITCH / 2
    regions = {
        'minimize': [min_cx - half, cy - half, min_cx + half, cy + half],
        'close': [close_cx - half, cy - half, close_cx + half + 12, cy + half],
    }
    return heart_cx, min_cx, close_cx, cy, regions


def plain():
    """Ends a run of text in a font of its own. Not a bare ${font}: that would go back to
    conky.conf's default, which is tiny because the config can't follow the text scale,
    and conky counts that font into the line's height and baseline. The offsets measured
    above were measured with this font there."""
    return f"${{font {conky_font(PLAIN_FONT)}}}"


# Vertical gaps are tiny lines of their own, each ending in a ${voffset}: conky counts a
# voffset into its window's height only after a line's fonts, and one ending a line of text
# draws that text lower too.

def gap(space, end='', after_text=True):
    """A tiny line taking `space` of height in all; `end` goes before its voffset. A line
    starts in the font the one before it ended in, which conky counts into its height: a
    line of text ends in plain()'s; the first line starts in conky.conf's tiny default, and
    the controls row ends in the tiny spacer font."""
    starts = line_height(PLAIN_FONT) if after_text else SPACER_HEIGHT
    return f"${{font {conky_font(SPACER_FONT)}}} {end}${{voffset {round(space - starts)}}}"


def first_line(next_top, before=''):
    """The widget's top line: it makes conky's window the widget's width (a goto only counts
    on a line with another after it) and starts the next line at next_top."""
    return before + gap(next_top, f"${{goto {2 * MARGIN + widget_width}}}", after_text=False)


def last_gap(y, bottom):
    """The last line, ending the window at the widget's bottom."""
    return gap(bottom - y, after_text=False)


def draw_basics():
    """draw.txt's lines for every screen: the scales (display; lyrics' text; the header's
    and times' text) and the window buttons."""
    _, min_cx, close_cx, top_cy, _ = top_buttons(widget_width, header_scale)
    return [f'scale {SCALE} {text_scale:g} {header_scale:g}',
            f'window {min_cx} {close_cx} {top_cy} {WINDOW_BUTTON_SIZE}']


def message(text):
    """A line of text where the track would be, the widget keeping its size and margins.
    draw.txt keeps only the scale and the window buttons, so nothing of the last track is
    drawn over it."""
    write_atomic(DRAW, '\n'.join(draw_basics()) + '\n')
    return '\n'.join((first_line(MARGIN),
                      f"${{goto {MARGIN}}}${{color}}${{font {conky_font(MESSAGE_FONT)}}}{esc(text)}{plain()}",
                      last_gap(MARGIN + line_height(MESSAGE_FONT, PLAIN_FONT), 2 * MARGIN + MIN_HEIGHT)))


def render():
    global widget_width, lyrics_height, text_scale, header_scale
    widget_width, lyrics_height, text_scale = size_file.load()
    header_scale = header_scale_for(text_scale)
    status = playerctl('status')
    heart_cx, min_cx, _, top_cy, top_regions = top_buttons(widget_width, header_scale)
    if status not in ('Playing', 'Paused'):
        write_regions(top_regions)
        return message('Spotify not playing')

    meta = playerctl('metadata', '--format',
                     '{{mpris:trackid}}\t{{title}}\t{{artist}}\t{{album}}\t{{mpris:artUrl}}\t{{position}}\t{{mpris:length}}')
    parts = meta.split('\t')
    if len(parts) != 7:
        return message('Loading…')
    track_id, title, artist, album, art, pos_us, len_us = parts
    position = int(pos_us or 0) / 1e6
    duration = int(len_us or 0) / 1e6

    now = time.time()
    lyrics_on, lyrics_scrolling = lyrics_settings.shown(), lyrics_settings.scrolling()
    if (lyrics_on, lyrics_scrolling) != state.lyrics_settings:
        state.lyrics_settings = (lyrics_on, lyrics_scrolling)
        log(f'lyrics: {lyrics_scrolling}' if lyrics_on else 'lyrics: off, not looked up')
    # When skipping, Spotify announces the new track id a moment before the rest of the
    # metadata (title, length, art) catches up. Only look up lyrics/art once the metadata
    # has had a moment to settle, and look them up again if it changes afterwards.
    lyrics_key = (track_id, title, artist, album, round(duration))
    fetch_lyrics_now = fetch_cover_now = False
    # While Spotify rate limits the widget, the like state cannot be read and a click only
    # posts a notification, so the heart is left out. Logged out, it stays: a click logs in.
    heart_hidden = bool(spotify_api.rate_limited_until()) and os.path.exists(spotify_api.TOKEN_FILE)
    with state.lock:
        new_track = track_id != state.track
        if new_track:
            state.track, state.liked, state.lyrics, state.liked_checked = track_id, None, None, now
            state.lyrics_retry, state.track_since = None, now
        settled = now - state.track_since >= METADATA_SETTLE and title and duration > 0
        if state.lyrics_retry is not None and now >= state.lyrics_retry:
            state.lyrics_retry, state.lyrics_key = None, None
        # Turned off, lyrics are not looked up, and turned on again they are for the track
        # playing, unless they were already fetched for it.
        if settled and lyrics_on and state.lyrics_key != lyrics_key:
            state.lyrics_key, state.lyrics = lyrics_key, None
            fetch_lyrics_now = True
        # Spotify's DJ talks in clips with no length, which get it at once; a song whose
        # art is only late waits a while longer.
        no_art = not art and now - state.track_since >= (NO_ART_WAIT if duration else METADATA_SETTLE)
        if no_art:
            art = NO_ART_URL
        if (settled or no_art) and art and art != state.cover_url:
            state.cover_url = art
            fetch_cover_now = True
            if no_art:
                log(f'no artwork: {title!r} by {artist!r} ({track_id}), showing the placeholder')
        # The limit lifting starts a like check at once, rather than on the next poll; the
        # heart is drawn again from this render, and fills in when the check answers.
        poll_like = (new_track or now - state.liked_checked > LIKE_POLL_SECONDS
                     or (state.heart_hidden and not heart_hidden))
        if state.heart_hidden != heart_hidden:
            state.heart_hidden = heart_hidden
            log('heart: hidden while rate limited' if heart_hidden else 'heart: shown, rate limit lifted')
        if poll_like:
            state.liked_checked = now
    if new_track:
        spotify_api.write_liked(None)  # don't show the previous track's heart
    if fetch_cover_now:
        threading.Thread(target=fetch_cover, args=(art,), daemon=True).start()
    if fetch_lyrics_now:
        threading.Thread(target=fetch_lyrics, args=(lyrics_key,), daemon=True).start()
    if poll_like:
        threading.Thread(target=fetch_liked, args=(track_id,), daemon=True).start()

    # A click on the heart writes the new state to the liked file immediately.
    liked_file = _read(spotify_api.LIKED_FILE)
    liked = {'1': True, '0': False}.get(liked_file, state.liked)
    heart_state = int(bool(liked))            # unknown draws like not liked: the outline
    heart_size = line_height(HEART_FONT)
    heart_box = [heart_cx - heart_size / 2 - 8, MARGIN - 8, min_cx - WINDOW_BUTTON_PITCH / 2,
                 MARGIN + heart_size + 6]

    total = fmt_time(duration)                # measured for the bar; draw.lua draws both times
    mid_offset = controls_mid(total)
    row = line_height(CONTROL_ROW_FONT)
    g = f'${{goto {COLUMN_X}}}'
    # Running top of the current line, to place what draw.lua draws.
    y = TOP_ROW
    out = [first_line(y, f"${{image {COVER} -p {MARGIN},{MARGIN} -s 118x118 -n}}"),
           # Only reserved: draw.lua draws NOW PLAYING, its capitals level with the artwork's top.
           f"{g}${{font {conky_font(LABEL_FONT)}}} ${{font {conky_font(HEART_FONT)}}} {plain()}"]
    y += line_height(LABEL_FONT, HEART_FONT, PLAIN_FONT)   # plain() ends it, and counts once text grows
    # Controls row, Spotify-style: ⏮ ▶ ⏭ (drawn by draw.lua), elapsed, seek bar, total.
    prev_cx = COLUMN_X + SKIP_SIZE / 2
    play_cx = prev_cx + SKIP_SIZE / 2 + CONTROL_GAP + PLAY_SIZE / 2
    next_cx = play_cx + PLAY_SIZE / 2 + CONTROL_GAP + SKIP_SIZE / 2
    time_x = next_cx + SKIP_SIZE / 2 + CONTROL_GAP + 4
    for line in wrap(title, TITLE_FONT):
        out.append(f"{g}${{color2}}${{font {conky_font(TITLE_FONT)}}}{esc(line)}{plain()}")
        y += line_height(TITLE_FONT)
    for line in wrap(artist, ARTIST_FONT, max_lines=2):
        out.append(f"{g}${{color}}${{font {conky_font(ARTIST_FONT)}}}{esc(line)}{plain()}")
        y += line_height(ARTIST_FONT)
    # The play button's bottom meets the artwork's bottom, unless a title or artist wrapped
    # onto more lines than fit beside it: then the row, and the widget with it, moves down.
    # A line each of title and artist always fits (see header_scale_for), so the text scale
    # moves it only when larger text makes the title or artist wrap.
    mid_y = max(ART_BOTTOM - PLAY_SIZE / 2, y + mid_offset)
    out.append(gap(mid_y - mid_offset - y))
    y = mid_y - mid_offset
    time_w = text_width(total, TIME_FONT)      # elapsed never has more digits than total
    # The row is only reserved here; draw.lua draws the times, centred on the bar like the
    # buttons. It ends in the tiny font rather than plain(), whose size follows the text.
    out.append(f"{g}${{font {conky_font(CONTROL_ROW_FONT)}}} ${{font {conky_font(SPACER_FONT)}}}")
    bar_x0 = time_x + time_w + 10
    bar_x1 = MARGIN + widget_width - time_w - 10
    fraction = min(max(position / duration, 0), 1) if duration else 0
    y += row
    lyr = write_lyrics(track_id, lyrics_on, lyrics_scrolling == lyrics_settings.STATIC)
    lyrics_top = max(mid_y + PLAY_SIZE / 2, ART_BOTTOM) + LYRIC_GAP
    if lyr:
        bottom = lyrics_top + lyrics_height - BOTTOM_TRIM + MARGIN
    else:
        bottom = max(y, MARGIN + MIN_HEIGHT) + MARGIN

    draw = [*draw_basics(),
            f'bar {bar_x0} {bar_x1} {mid_y + BAR_DROP} {fraction:.4f} {duration:.3f}',
            f'controls {prev_cx} {play_cx} {next_cx} {mid_y} {SKIP_SIZE} {PLAY_SIZE} {int(status == "Playing")}',
            f'times {time_x} {MARGIN + widget_width} {mid_y + BAR_DROP}',
            f'clock {time.monotonic():.3f} {position:.3f} {int(status == "Playing")}',
            f'label {COLUMN_X} {ART_TOP}']
    if not heart_hidden:
        draw.append(f'heart {heart_cx} {top_cy} {HEART_WIDTH} {heart_state} ' + ' '.join(map(str, heart_box)))
    lyrics_area = lyrics_scroll = None
    if lyr:
        # Full width under the artwork and controls: reserve lyrics_height there, and
        # draw.lua draws and scrolls as many lines as fit inside it.
        version, count, static = lyr
        lyric_row = line_height(LYRIC_FONT) + LYRIC_SPACING * text_scale
        draw.append(f'lyrics {ART_LEFT} {MARGIN + widget_width} {lyrics_top} {lyric_row} '
                    f'{version} {lyrics_height}')
        lyrics_area = [ART_LEFT, lyrics_top, MARGIN + widget_width, lyrics_top + lyrics_height]
        if static:
            # The wheel's range, in lines: from the first line at the top to the last one
            # at the bottom. draw.lua works the same limit out.
            lyrics_scroll = [version, max(0, math.ceil(count - lyrics_height / lyric_row - 1e-3))]
    out.append(last_gap(y, bottom))            # the lyrics and the bottom margin
    write_atomic(DRAW, '\n'.join(draw) + '\n')

    half = PLAY_SIZE / 2 + 4
    write_regions({
        'lyrics': lyrics_area,                  # the top and bottom edges resize only these
        'lyrics_scroll': lyrics_scroll,         # [version, last offset] for the wheel; None: no wheel
        **top_regions,
        # a little padding around each target makes them easier to hit
        'prev': [prev_cx - SKIP_SIZE / 2 - 5, mid_y - half, prev_cx + SKIP_SIZE / 2 + 5, mid_y + half],
        'play': [play_cx - half, mid_y - half, play_cx + half, mid_y + half],
        'next': [next_cx - SKIP_SIZE / 2 - 5, mid_y - half, next_cx + SKIP_SIZE / 2 + 5, mid_y + half],
        'seek': [bar_x0 - 6, mid_y + BAR_DROP - 12, bar_x1 + 6, mid_y + BAR_DROP + 12],
        'heart': None if heart_hidden else heart_box,
        'bar': [bar_x0, bar_x1],
        'duration': duration,
    })
    return '\n'.join(out)   # no trailing newline: it would add an empty line at the bottom


def write_lyrics(track_id, shown=True, static=False):
    """Writes lyrics.txt for draw.lua when the track's lyrics change, and returns
    (version, lines, static): a version string that changes with the lyrics, their number
    of lines, and whether draw.lua shows them as a static block rather than following
    playback. None when there are no lyrics to show, or `shown` is off.

    Synced lyrics go under a 'synced' header as '<seconds>\\t<line>'. Plain ones, and
    synced ones when `static`, go under 'plain' as '\\t<line>'. Writing new lyrics, or
    the same ones shown another way, puts a static block back at its top."""
    lyrics = (state.lyrics or {}) if shown else {}
    synced = list(lyrics.get('synced') or [])
    if synced and not static:
        lines = synced
        if lines[0][0] > 0:
            lines.insert(0, (0.0, ''))           # before the first line: show the intro as ♫
        mode, body = 'synced', '\n'.join(f'{t:.2f}\t{l or "♫"}' for t, l in lines)   # instrumental: ♫
    else:
        # untimed, the blank lines of instrumental breaks read as the gaps between verses
        lines = plain_lines('\n'.join(l for _, l in synced)) if synced else lyrics.get('plain') or []
        mode, body = 'static' if synced else 'plain', '\n'.join(f'\t{l}' for l in lines)
    if not lines:
        state.lyrics_written = None              # the same lyrics coming back start at the top
        return None
    version = f'{abs(hash((track_id, mode, len(lines)))) % 10**8}'
    if state.lyrics_written != version:
        write_atomic(LYRICS, f"{'synced' if mode == 'synced' else 'plain'}\n{body}\n")
        try:
            os.remove(LYRICS_SCROLL)
        except OSError:
            pass
        state.lyrics_written = version
    return version, len(lines), mode != 'synced'


def library_loop():
    """Keeps spotify_api's Liked Songs index fresh (full scan when missing / daily, newest
    likes every minute), then re-checks the heart so it reflects likes made in the app.

    A full scan fetches one 50-track page every LIBRARY_PAGE_SECONDS, so it stays far below
    Spotify's rate limit alongside the heart checks; a 5,000-song library takes about 25
    minutes. A 429 pauses it without sending anything until the backoff has passed."""
    paused = False
    while True:
        scanning = False
        until = spotify_api.rate_limited_until()
        if until:
            offset = spotify_api.scan_offset()
            if offset is not None and not paused:
                log(f'library scan paused at offset {offset} until '
                    + time.strftime('%T', time.localtime(until)))
            paused = offset is not None
        elif os.path.exists(spotify_api.TOKEN_FILE):
            paused = False
            try:
                started = time.time()
                lib = spotify_api.refresh_library()
                scanning = 'scan' in lib
                if scanning:
                    log(f"library scan: {lib['scan']['offset']} of {lib['total']} indexed, "
                        f"next page in {LIBRARY_PAGE_SECONDS} s")
                else:
                    if lib['scanned'] >= started:
                        log(f"library scan done: {lib['total']} songs, {len(lib['keys'])} titles")
                    with state.lock:
                        state.liked_checked = 0
            except Exception as e:
                log(f'library refresh failed: {type(e).__name__}: {e}')
        time.sleep(LIBRARY_PAGE_SECONDS if scanning else 60)


def main():
    os.makedirs(CACHE, exist_ok=True)
    threading.Thread(target=library_loop, daemon=True).start()
    while True:
        try:
            text = render()
        except Exception as e:
            try:
                text = message(f'widget error: {str(e)[:60]}')
            except Exception:                   # the layout itself is what failed
                text = f"${{color}}${{font Ubuntu Sans:size=11}}widget error: {esc(str(e))[:60]}\n"

        with open(OUT + '.tmp', 'w') as f:
            f.write(text)
        os.replace(OUT + '.tmp', OUT)
        # Every 0.25 s, or at once when the size settings change.
        settings = size_file.load()
        for _ in range(5):
            time.sleep(0.05)
            if size_file.load() != settings:
                break


if __name__ == '__main__':
    main()
