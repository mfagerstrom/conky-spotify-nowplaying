"""Tests for nowplaying.py's pure logic: art colour, wrapping, formatting, files, lyrics,
and the click regions render() writes."""
import colorsys
import json
import os
import time
import unittest
import urllib.error
from unittest import mock

import support

nowplaying = support.load_nowplaying()

from gi.repository import GdkPixbuf, GLib  # noqa: E402  (after nowplaying pins the versions)

SIZE = 48   # art_colour samples the cover at 48x48; covers this size are read as drawn


class ArtColourTest(support.TempDirTest):

    def cover(self, pixel):
        """Saves a SIZE x SIZE PNG whose pixel (x, y) is pixel(x, y) -> (r, g, b), 0-255."""
        data = bytes(c for y in range(SIZE) for x in range(SIZE) for c in pixel(x, y))
        pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                             False, 8, SIZE, SIZE, SIZE * 3)
        path = os.path.join(self.dir, f'cover-{len(os.listdir(self.dir))}.png')
        pb.savev(path, 'png', [], [])
        return path

    def hsv(self, path):
        return colorsys.rgb_to_hsv(*nowplaying.art_colour(path))

    def assertHue(self, path, rgb):
        h, s, _ = self.hsv(path)
        want = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0]
        self.assertGreater(s, 0.3)
        self.assertAlmostEqual(h, want, delta=0.02)

    def test_grayscale_cover_with_small_accent_takes_the_accent(self):
        accent = (40, 90, 220)
        path = self.cover(lambda x, y: accent if x < 4 and y < 4 else (128, 128, 128))
        self.assertHue(path, accent)

    def test_grayscale_cover_with_accent_on_dark_background(self):
        accent = (220, 40, 40)
        path = self.cover(lambda x, y: accent if 20 <= x < 24 and 20 <= y < 24 else (10, 10, 10))
        self.assertHue(path, accent)

    def test_accent_below_the_noise_floor_is_ignored(self):
        path = self.cover(lambda x, y: (40, 90, 220) if x < 2 and y < 2 else (128, 128, 128))
        _, s, _ = self.hsv(path)
        self.assertAlmostEqual(s, 0, places=6)

    def test_pure_grayscale_cover_is_neutral(self):
        path = self.cover(lambda x, y: (x * 5,) * 3)
        r, g, b = nowplaying.art_colour(path)
        self.assertAlmostEqual(r, g, places=6)
        self.assertAlmostEqual(g, b, places=6)

    def test_colourful_cover_takes_the_dominant_vivid_colour(self):
        green, magenta = (40, 180, 60), (200, 40, 180)
        path = self.cover(lambda x, y: green if y < 34 else magenta)
        self.assertHue(path, green)

    def test_small_vivid_patch_beats_a_dull_dominant_colour(self):
        # A dull brown cover (not grayscale) with a vivid orange block over 5% of it.
        orange = (240, 130, 20)
        path = self.cover(lambda x, y: orange if x < 12 and y < 12 else (90, 75, 60))
        self.assertHue(path, orange)

    def test_colour_split_across_hues_beats_a_gray_dominant_colour(self):
        # Half pale gray-blue sky, a dull orange-brown band (not vivid), a teal stripe
        # under the 5% vivid floor and dark gray below: too little gray for the accent
        # rule, no vivid bucket big enough, but a fifth of the cover is coloured.
        sky, brown, teal = (195, 212, 224), (92, 77, 64), (66, 110, 120)
        rows = [sky] * 24 + [brown] * 8 + [teal] * 2 + [(30, 30, 30)] * 14
        self.assertHue(self.cover(lambda x, y: rows[y]), brown)

    def test_colour_beats_a_faintly_tinted_dominant_colour(self):
        # The same cover with a bluer sky (saturation 0.2): still too faint to be a colour.
        sky, brown, teal = (180, 205, 225), (92, 77, 64), (66, 110, 120)
        rows = [sky] * 24 + [brown] * 8 + [teal] * 2 + [(30, 30, 30)] * 14
        self.assertHue(self.cover(lambda x, y: rows[y]), brown)

    def test_gray_dominant_colour_stays_when_colour_is_scarce(self):
        # 80% gray, a faint tint that is not an accent, and colour on only 8% of it.
        rows = [(128, 128, 128)] * 38 + [(160, 145, 125)] * 6 + [(100, 110, 140)] * 4
        _, s, _ = self.hsv(self.cover(lambda x, y: rows[y]))
        self.assertAlmostEqual(s, 0, places=6)

    def test_output_is_always_clamped(self):
        covers = {
            'white': lambda x, y: (255, 255, 255),
            'black': lambda x, y: (0, 0, 0),
            'bright yellow': lambda x, y: (255, 240, 0),
            'saturated red': lambda x, y: (255, 0, 0),
            'dark navy': lambda x, y: (5, 10, 60),
            'gradient': lambda x, y: (x * 5, y * 5, 255 - x * 5),
        }
        for name, pixel in covers.items():
            with self.subTest(name):
                _, s, v = self.hsv(self.cover(pixel))
                self.assertLessEqual(s, 0.65 + 1e-9)
                self.assertGreaterEqual(v, 0.25 - 1e-9)
                self.assertLessEqual(v, 0.38 + 1e-9)

    def test_update_bg_falls_back_to_spotify_gray_without_a_cover(self):
        self.redirect(nowplaying, COVER='missing.jpg', BG='bg.txt')
        nowplaying.update_bg()
        self.assertEqual(nowplaying._read(nowplaying.BG), '0.094 0.094 0.094')


class WrapTest(unittest.TestCase):
    FONT = nowplaying.TITLE_FONT

    def test_short_text_is_one_line(self):
        self.assertEqual(nowplaying.wrap('Hello', self.FONT), ['Hello'])

    def test_wrapped_lines_fit_and_keep_every_word(self):
        text = ' '.join(f'word{i}' for i in range(12))
        lines = nowplaying.wrap(text, self.FONT, width=150, max_lines=20)
        self.assertGreater(len(lines), 1)
        self.assertEqual(' '.join(lines).split(), text.split())
        for line in lines:
            self.assertLessEqual(nowplaying.text_width(line, self.FONT), 150)

    def test_long_text_is_cut_to_max_lines_with_an_ellipsis(self):
        text = 'a very long song title that goes on and on ' * 5
        for max_lines in (1, 2, 3):
            with self.subTest(max_lines=max_lines):
                lines = nowplaying.wrap(text, self.FONT, max_lines=max_lines)
                self.assertEqual(len(lines), max_lines)
                self.assertTrue(lines[-1].endswith('…'))

    def test_text_that_fits_gets_no_ellipsis(self):
        lines = nowplaying.wrap('Two short words', self.FONT, max_lines=1)
        self.assertEqual(lines, ['Two short words'])


class FormattingTest(unittest.TestCase):

    def test_esc_doubles_dollar_signs(self):
        self.assertEqual(nowplaying.esc('$5 and $$'), '$$5 and $$$$')
        self.assertEqual(nowplaying.esc('no dollars'), 'no dollars')

    def test_fmt_time(self):
        cases = {0: '0:00', 5: '0:05', 59.9: '0:59', 65: '1:05', 600: '10:00', 3725: '62:05'}
        for sec, want in cases.items():
            with self.subTest(sec=sec):
                self.assertEqual(nowplaying.fmt_time(sec), want)

    def test_conky_font(self):
        cases = {
            'Ubuntu Sans Bold 17': 'Ubuntu Sans:bold:size=17',
            'Ubuntu Sans 13': 'Ubuntu Sans:size=13',
            'DejaVu Sans 15': 'DejaVu Sans:size=15',
            'Ubuntu Sans Bold 10': 'Ubuntu Sans:bold:size=10',
        }
        for pango, want in cases.items():
            with self.subTest(pango):
                self.assertEqual(nowplaying.conky_font(pango), want)


class WindowButtonsTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(nowplaying, REGIONS='regions.json', DRAW='draw.txt')
        self.redirect(nowplaying.spotify_api, LIKED_FILE='liked')
        patcher = mock.patch.object(nowplaying, 'state', nowplaying.State())
        patcher.start()
        self.addCleanup(patcher.stop)

    def render(self, status):
        answers = {'status': status,
                   'metadata': 'track1\tTitle\tArtist\tAlbum\t\t10000000\t200000000'}
        with mock.patch.object(nowplaying, 'playerctl', side_effect=lambda cmd, *_: answers[cmd]), \
                mock.patch.object(nowplaying.threading, 'Thread'), \
                mock.patch.object(nowplaying.spotify_api, 'write_liked'):
            text = nowplaying.render()
        with open(nowplaying.REGIONS) as f:
            return text, json.load(f), support.read(nowplaying.DRAW)

    def assert_left_of(self, a, b):
        self.assertLessEqual(a[2], b[0], f'{a} overlaps or is right of {b}')

    def test_heart_minimize_close_run_left_to_right_without_overlapping(self):
        text, regions, draw = self.render('Playing')
        self.assert_left_of(regions['heart'], regions['minimize'])
        self.assert_left_of(regions['minimize'], regions['close'])
        # the close icon is drawn inside the text area; only its hit area reaches the border
        window = next(line for line in draw.splitlines() if line.startswith('window '))
        _, _, close_cx, _, size = window.split()
        self.assertLessEqual(float(close_cx) + float(size) / 2, nowplaying.MARGIN + nowplaying.widget_width)
        # the heart is drawn by draw.lua, with its hover box matching its click region
        self.assertNotIn('♡', text)
        heart = next(line for line in draw.splitlines() if line.startswith('heart '))
        self.assertEqual([float(v) for v in heart.split()[5:]], regions['heart'])

    def test_heart_state_follows_the_liked_file(self):
        for liked, want in (('1', '1'), ('0', '0'), ('', '0')):
            with self.subTest(liked=liked):
                nowplaying.write_atomic(nowplaying.spotify_api.LIKED_FILE, liked)
                nowplaying.state.liked = None
                _, _, draw = self.render('Playing')
                heart = next(line for line in draw.splitlines() if line.startswith('heart '))
                self.assertEqual(heart.split()[4], want)

    def test_buttons_stay_when_spotify_is_not_playing(self):
        text, regions, draw = self.render('Stopped')
        self.assertIn('not playing', text)
        self.assertEqual(sorted(regions), ['close', 'minimize'])
        self.assertIn('\nwindow ', draw)
        self.assertNotIn('bar ', draw)


class SizeSettingsTest(support.TempDirTest):
    """The size settings change the width and the lyrics' height; the text scale changes
    only the text, never where the controls sit or how big the widget is."""

    def setUp(self):
        super().setUp()
        self.redirect(nowplaying, REGIONS='regions.json', DRAW='draw.txt', LYRICS='lyrics.txt', LOG='nowplaying.log',
                      LYRICS_SCROLL='lyrics-scroll')
        self.redirect(nowplaying.spotify_api, LIKED_FILE='liked')
        self.redirect(nowplaying.size_file, PATH='size')
        state = nowplaying.State()
        state.track, state.lyrics = 'track1', {'synced': [(1.0, 'a line'), (2.0, '')]}
        patcher = mock.patch.object(nowplaying, 'state', state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def render(self, settings, title='Title'):
        nowplaying.size_file.save(settings)
        nowplaying.state.lyrics_key = ('track1', title, 'Artist', 'Album', 200)   # fetched already
        answers = {'status': 'Playing',
                   'metadata': f'track1\t{title}\tArtist\tAlbum\t\t10000000\t200000000'}
        with mock.patch.object(nowplaying, 'playerctl', side_effect=lambda cmd, *_: answers[cmd]), \
                mock.patch.object(nowplaying.threading, 'Thread'), \
                mock.patch.object(nowplaying.spotify_api, 'write_liked'):
            text = nowplaying.render()
        draw = {line.split()[0]: line.split()[1:] for line in support.read(nowplaying.DRAW).splitlines()}
        with open(nowplaying.REGIONS) as f:
            return text, draw, json.load(f)

    def test_the_width_goto_follows_the_width_setting_only(self):
        for settings, want in (((505, 63, 1.0), 545), ((620, 63, 1.0), 660), ((620, 200, 1.7), 660)):
            with self.subTest(settings=settings):
                text, _, _ = self.render(settings)
                self.assertIn(f'${{goto {want}}}', text.splitlines()[0])

    def test_text_scale_leaves_the_layout_in_place(self):
        _, draw, regions = self.render((505, 105, 1.0))
        for scale in (0.7, 1.5, 2.0):
            with self.subTest(scale=scale):
                _, scaled_draw, scaled_regions = self.render((505, 105, scale))
                for key in ('controls', 'window', 'heart'):
                    self.assertEqual(scaled_draw[key], draw[key])
                self.assertEqual(scaled_draw['lyrics'][2], draw['lyrics'][2])   # top
                self.assertEqual(scaled_draw['lyrics'][5], '105')                # height
                for key in ('prev', 'play', 'next', 'minimize', 'close'):
                    self.assertEqual(scaled_regions[key], regions[key])
                self.assertEqual(scaled_draw['scale'][1], f'{scale:g}')         # the lyrics' text

    def test_text_beside_the_artwork_stops_growing_where_it_would_not_fit(self):
        self.assertEqual(nowplaying.header_scale_for(1.0), 1.0)
        self.assertEqual(nowplaying.header_scale_for(0.7), 0.7)
        capped = nowplaying.header_scale_for(2.0)
        self.assertGreater(capped, 1.0)
        self.assertLess(capped, 2.0)
        _, draw, _ = self.render((505, 63, 2.0))
        self.assertEqual(draw['scale'][1:], ['2', f'{capped:g}'])

    def test_a_long_title_wraps_and_moves_the_controls_down(self):
        _, short, _ = self.render((505, 63, 1.0))
        text, long, _ = self.render((505, 63, 1.0), title='A title long enough to need a second line '
                                                          'and then a third one as well')
        self.assertEqual(text.count('${color2}'), 3)                        # three title lines
        self.assertGreater(float(long['controls'][3]), float(short['controls'][3]))
        self.assertGreater(float(long['lyrics'][2]), float(short['lyrics'][2]))

    def test_lyrics_height_sets_the_lyrics_area(self):
        _, short, short_regions = self.render((505, 40, 1.0))
        _, tall, tall_regions = self.render((505, 400, 1.0))
        self.assertEqual((short['lyrics'][5], tall['lyrics'][5]), ('40', '400'))
        self.assertEqual(short['lyrics'][2], tall['lyrics'][2])
        top = float(short['lyrics'][2])
        right = nowplaying.MARGIN + 505
        self.assertEqual(short_regions['lyrics'], [nowplaying.ART_LEFT, top, right, top + 40])
        self.assertEqual(tall_regions['lyrics'], [nowplaying.ART_LEFT, top, right, top + 400])

    def test_synced_lyrics_get_no_wheel(self):
        _, _, regions = self.render((505, 63, 1.0))
        self.assertIsNone(regions['lyrics_scroll'])

    def test_the_wheel_reaches_the_last_plain_line_at_the_bottom(self):
        nowplaying.state.lyrics = {'plain': [f'line {i}' for i in range(10)]}
        row = float(self.render((505, 63, 1.0))[1]['lyrics'][3])
        for rows, want in ((3, 7), (3.5, 7), (10, 0), (12, 0)):
            with self.subTest(rows=rows):
                _, draw, regions = self.render((505, round(rows * row), 1.0))
                self.assertEqual(regions['lyrics_scroll'], [draw['lyrics'][4], want])

    def test_no_lyrics_means_no_lyrics_area(self):
        nowplaying.state.lyrics = {}
        _, draw, regions = self.render((505, 63, 1.0))
        self.assertNotIn('lyrics', draw)
        self.assertEqual((regions['lyrics'], regions['lyrics_scroll']), (None, None))


class FilesTest(support.TempDirTest):

    def test_write_atomic_and_read_round_trip(self):
        path = os.path.join(self.dir, 'out.txt')
        nowplaying.write_atomic(path, 'first\n')
        nowplaying.write_atomic(path, '  second line  \n')
        self.assertEqual(nowplaying._read(path), 'second line')
        self.assertEqual(os.listdir(self.dir), ['out.txt'])   # no .tmp left behind

    def test_read_of_a_missing_file_is_empty(self):
        self.assertEqual(nowplaying._read(os.path.join(self.dir, 'missing')), '')



class LrclibTest(unittest.TestCase):

    def test_request_and_parse(self):
        with mock.patch('urllib.request.urlopen', return_value=support.response(b'{"id": 1}')) as urlopen:
            self.assertEqual(nowplaying._lrclib('get', track_name='Song & Dance', duration=200), {'id': 1})
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url,
                         'https://lrclib.net/api/get?track_name=Song+%26+Dance&duration=200')
        self.assertIn('conky-spotify-nowplaying', req.get_header('User-agent'))

    def test_not_found_is_none(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(404)):
            self.assertIsNone(nowplaying._lrclib('get', track_name='x'))

    def test_other_http_errors_are_raised(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                nowplaying._lrclib('get', track_name='x')


SYNCED = '[00:01.50] First\n[00:04.00]Second\nnot a timed line\n[01:02.25] Third'


class FetchLyricsTest(support.TempDirTest):
    KEY = ('spotify:track:1', 'Song', 'Artist', 'Album', 200)

    def setUp(self):
        super().setUp()
        self.redirect(nowplaying, LOG='nowplaying.log', LYRICS_CACHE='lyrics')
        self.state = nowplaying.State()
        self.state.lyrics_key = self.KEY
        patcher = mock.patch.object(nowplaying, 'state', self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, get=None, search=()):
        def lrclib(path, **params):
            return get if path == 'get' else list(search)
        with mock.patch.object(nowplaying, '_lrclib', side_effect=lrclib) as mocked:
            nowplaying.fetch_lyrics(self.KEY)
        return mocked

    def test_exact_match_is_parsed(self):
        mocked = self.fetch(get={'syncedLyrics': SYNCED})
        self.assertEqual(self.state.lyrics, {'synced': [(1.5, 'First'), (4.0, 'Second'), (62.25, 'Third')]})
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args.kwargs, {'artist_name': 'Artist', 'track_name': 'Song',
                                                   'album_name': 'Album', 'duration': 200})

    def test_search_takes_the_closest_length_within_3_seconds(self):
        self.fetch(get={'plainLyrics': 'unsynced only', 'syncedLyrics': None}, search=[
            {'duration': 190, 'syncedLyrics': '[00:01.00]far'},
            {'duration': 199.5, 'syncedLyrics': None},
            {'duration': 202, 'syncedLyrics': '[00:01.00]near'},
            {'duration': 201, 'syncedLyrics': '[00:01.00]nearest'},
        ])
        self.assertEqual(self.state.lyrics, {'synced': [(1.0, 'nearest')]})

    def test_search_falls_back_to_within_20_seconds(self):
        self.fetch(get=None, search=[
            {'duration': 230, 'syncedLyrics': '[00:01.00]too far'},
            {'duration': 215, 'syncedLyrics': '[00:01.00]other edit'},
        ])
        self.assertEqual(self.state.lyrics, {'synced': [(1.0, 'other edit')]})

    def test_plain_lyrics_are_kept_when_nothing_synced_is_close(self):
        self.fetch(get={'plainLyrics': '\n  First  \n\n\n\nSecond\nThird\n\n', 'syncedLyrics': None},
                   search=[{'duration': 250, 'syncedLyrics': '[00:01.00]too far'},
                           {'duration': 200, 'plainLyrics': 'another plain'}])
        self.assertEqual(self.state.lyrics, {'plain': ['First', '', 'Second', 'Third']})
        self.assertIn('lyrics: 4 plain lines for Artist - Song (200 s)', support.read(nowplaying.LOG))

    def test_plain_lyrics_come_from_the_closest_search_hit_without_an_exact_match(self):
        self.fetch(get=None, search=[{'duration': 215, 'plainLyrics': 'other edit'},
                                     {'duration': 201, 'plainLyrics': 'nearest'},
                                     {'duration': 200, 'plainLyrics': None, 'instrumental': True}])
        self.assertEqual(self.state.lyrics, {'plain': ['nearest']})

    def test_synced_lyrics_from_the_search_beat_plain_ones(self):
        self.fetch(get={'plainLyrics': 'plain', 'syncedLyrics': None},
                   search=[{'duration': 215, 'syncedLyrics': '[00:01.00]synced', 'plainLyrics': 'synced'}])
        self.assertEqual(self.state.lyrics, {'synced': [(1.0, 'synced')]})

    def test_synced_lyrics_without_a_timestamp_fall_back_to_plain_ones(self):
        self.fetch(get={'syncedLyrics': 'First\nSecond', 'plainLyrics': 'First\nSecond'})
        self.assertEqual(self.state.lyrics, {'plain': ['First', 'Second']})

    def test_plain_lyrics_too_far_off_in_length_are_not_kept(self):
        self.fetch(get=None, search=[{'duration': 250, 'plainLyrics': 'x'}])
        self.assertEqual(self.state.lyrics, {})
        self.assertIn('lyrics: none for Artist - Song (200 s)', support.read(nowplaying.LOG))

    def test_nothing_close_enough_means_no_lyrics(self):
        self.fetch(get=None, search=[{'duration': 250, 'syncedLyrics': '[00:01.00]x'}])
        self.assertEqual(self.state.lyrics, {})

    def test_network_error_leaves_lyrics_unknown_and_schedules_a_retry(self):
        with mock.patch.object(nowplaying, '_lrclib', side_effect=urllib.error.URLError('offline')):
            before = time.time()
            nowplaying.fetch_lyrics(self.KEY)
        self.assertIsNone(self.state.lyrics)
        self.assertGreaterEqual(self.state.lyrics_retry, before + 30)
        self.assertLessEqual(self.state.lyrics_retry, time.time() + 30)

    def test_result_for_a_track_no_longer_playing_is_dropped(self):
        self.state.lyrics_key = ('spotify:track:2', 'Other', 'Artist', 'Album', 180)
        self.fetch(get={'syncedLyrics': SYNCED})
        self.assertIsNone(self.state.lyrics)

    # The on-disk cache in front of LRCLIB.

    def replay(self, **lrclib):
        """Plays the track again, as after a restart: fresh state, same key."""
        self.state.lyrics = None
        return self.fetch(**lrclib)

    def age(self, key, seconds):
        path = nowplaying.lyrics_cache_path(key)
        entry = json.loads(support.read(path))
        entry['fetched'] -= seconds
        nowplaying.write_atomic(path, json.dumps(entry))

    def test_fetched_lyrics_are_cached_and_replayed_without_lrclib(self):
        self.fetch(get={'syncedLyrics': SYNCED})
        self.assertTrue(os.path.exists(nowplaying.lyrics_cache_path(self.KEY)))
        mocked = self.replay(get={'syncedLyrics': '[00:09.00]changed'})
        self.assertEqual(mocked.call_count, 0)
        self.assertEqual(self.state.lyrics, {'synced': [(1.5, 'First'), (4.0, 'Second'), (62.25, 'Third')]})
        self.assertIn('synced lines for Artist - Song (200 s), from the cache',
                      support.read(nowplaying.LOG))

    def test_the_cache_is_keyed_by_the_lookup_not_the_spotify_id(self):
        self.assertEqual(nowplaying.lyrics_cache_path(self.KEY),
                         nowplaying.lyrics_cache_path(('spotify:track:9',) + self.KEY[1:]))
        self.assertNotEqual(nowplaying.lyrics_cache_path(self.KEY),
                            nowplaying.lyrics_cache_path(self.KEY[:4] + (201,)))

    def test_no_lyrics_is_cached_until_it_expires(self):
        self.fetch(get=None)
        self.assertEqual(self.state.lyrics, {})
        self.age(self.KEY, nowplaying.NO_LYRICS_TTL - 60)
        self.assertEqual(self.replay(get={'syncedLyrics': SYNCED}).call_count, 0)
        self.assertEqual(self.state.lyrics, {})
        self.age(self.KEY, 120)
        self.assertEqual(self.replay(get={'syncedLyrics': SYNCED}).call_count, 1)
        self.assertEqual(self.state.lyrics['synced'][0], (1.5, 'First'))

    def test_plain_lyrics_are_cached_and_do_not_expire(self):
        self.fetch(get={'plainLyrics': 'First\nSecond'})
        self.age(self.KEY, 365 * 24 * 3600)
        self.assertEqual(self.replay(get={'syncedLyrics': SYNCED}).call_count, 0)
        self.assertEqual(self.state.lyrics, {'plain': ['First', 'Second']})
        self.assertIn('2 plain lines for Artist - Song (200 s), from the cache', support.read(nowplaying.LOG))

    def test_no_lyrics_cached_before_plain_ones_were_kept_is_asked_again(self):
        os.makedirs(nowplaying.LYRICS_CACHE)
        nowplaying.write_atomic(nowplaying.lyrics_cache_path(self.KEY),
                                json.dumps({'fetched': time.time(), 'synced': []}))
        self.assertEqual(self.replay(get={'plainLyrics': 'First'}).call_count, 2)   # get, then search
        self.assertEqual(self.state.lyrics, {'plain': ['First']})

    def test_synced_lyrics_cached_before_plain_ones_were_kept_still_count(self):
        os.makedirs(nowplaying.LYRICS_CACHE)
        nowplaying.write_atomic(nowplaying.lyrics_cache_path(self.KEY),
                                json.dumps({'fetched': 0, 'synced': [[1.5, 'First']]}))
        self.assertEqual(self.replay().call_count, 0)
        self.assertEqual(self.state.lyrics, {'synced': [(1.5, 'First')]})

    def test_no_lyrics_dated_in_the_future_counts_as_expired(self):
        self.fetch(get=None)
        self.age(self.KEY, -3600)
        self.assertEqual(self.replay(get={'syncedLyrics': SYNCED}).call_count, 1)

    def test_synced_lyrics_do_not_expire(self):
        self.fetch(get={'syncedLyrics': SYNCED})
        self.age(self.KEY, 365 * 24 * 3600)
        self.assertEqual(self.replay().call_count, 0)
        self.assertEqual(len(self.state.lyrics['synced']), 3)

    def test_failed_lookup_is_not_cached_and_still_retries(self):
        for error in (urllib.error.URLError('offline'), support.http_error(500)):
            with mock.patch.object(nowplaying, '_lrclib', side_effect=error):
                nowplaying.fetch_lyrics(self.KEY)
            self.assertFalse(os.path.exists(nowplaying.lyrics_cache_path(self.KEY)))
            self.assertIsNotNone(self.state.lyrics_retry)
            self.state.lyrics_retry = None
        self.assertEqual(self.replay(get={'syncedLyrics': SYNCED}).call_count, 1)

    def test_an_unreadable_entry_is_looked_up_again(self):
        os.makedirs(nowplaying.LYRICS_CACHE)
        for broken in ('not json', '{}', '{"fetched": 0, "synced": [[1]]}'):
            with open(nowplaying.lyrics_cache_path(self.KEY), 'w') as f:
                f.write(broken)
            self.assertEqual(self.replay(get={'syncedLyrics': SYNCED}).call_count, 1, broken)

    def test_the_cache_stays_under_its_cap_dropping_the_least_recently_used(self):
        keys = [(f'spotify:track:{i}', f'Song {i}', 'Artist', 'Album', 200) for i in range(5)]
        with mock.patch.object(nowplaying, 'LYRICS_CACHE_MAX', 3):
            for i, key in enumerate(keys):
                self.state.lyrics_key = key
                with mock.patch.object(nowplaying, '_lrclib', return_value={'syncedLyrics': SYNCED}):
                    nowplaying.fetch_lyrics(key)
                path = nowplaying.lyrics_cache_path(key)
                os.utime(path, (1000 + i, 1000 + i))
                if i == 1:                   # song 0 is played again, so song 1 is now oldest
                    self.state.lyrics_key = keys[0]
                    nowplaying.fetch_lyrics(keys[0])
                    self.assertGreater(os.path.getmtime(nowplaying.lyrics_cache_path(keys[0])), 2000)
            kept = {k[1] for k in keys if os.path.exists(nowplaying.lyrics_cache_path(k))}
        self.assertEqual(sorted(os.listdir(nowplaying.LYRICS_CACHE)),
                         sorted(os.path.basename(nowplaying.lyrics_cache_path(k)) for k in keys
                                if k[1] in kept))
        self.assertEqual(kept, {'Song 0', 'Song 3', 'Song 4'})


class WriteLyricsTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(nowplaying, LYRICS='lyrics.txt', LYRICS_SCROLL='lyrics-scroll')
        self.state = nowplaying.State()
        patcher = mock.patch.object(nowplaying, 'state', self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, lyrics, track='track1'):
        self.state.lyrics = lyrics
        return nowplaying.write_lyrics(track)

    def scrolled(self):
        nowplaying.write_atomic(nowplaying.LYRICS_SCROLL, 'x 3')

    def test_synced_lyrics_are_timed_and_follow_playback(self):
        version, count, static = self.write({'synced': [(1.5, 'First'), (4.0, '')]})
        self.assertEqual(support.read(nowplaying.LYRICS), 'synced\n0.00\t♫\n1.50\tFirst\n4.00\t♫\n')
        self.assertEqual((count, static), (3, False))

    def test_plain_lyrics_are_untimed_and_static(self):
        version, count, static = self.write({'plain': ['First', '', 'Second']})
        self.assertEqual(support.read(nowplaying.LYRICS), 'plain\n\tFirst\n\t\n\tSecond\n')
        self.assertEqual((count, static), (3, True))

    def test_no_lyrics_writes_nothing(self):
        for lyrics in (None, {}, {'synced': []}, {'plain': []}):
            with self.subTest(lyrics=lyrics):
                self.assertIsNone(self.write(lyrics))
        self.assertFalse(os.path.exists(nowplaying.LYRICS))

    def test_new_lyrics_start_at_the_top(self):
        first = self.write({'plain': ['a', 'b']})
        self.scrolled()
        self.assertEqual(self.write({'plain': ['a', 'b']}), first)       # the same: left alone
        self.assertTrue(os.path.exists(nowplaying.LYRICS_SCROLL))
        self.write({'plain': ['c']}, track='track2')
        self.assertFalse(os.path.exists(nowplaying.LYRICS_SCROLL))

    def test_the_same_lyrics_after_a_track_without_any_start_at_the_top(self):
        first = self.write({'plain': ['a', 'b']})
        self.scrolled()
        self.write({}, track='track2')
        self.assertEqual(self.write({'plain': ['a', 'b']}), first)
        self.assertFalse(os.path.exists(nowplaying.LYRICS_SCROLL))

    def test_plain_and_synced_lyrics_of_one_length_differ_in_version(self):
        plain = self.write({'plain': ['a', 'b']})
        synced = self.write({'synced': [(0.0, 'a'), (1.0, 'b')]})
        self.assertNotEqual(plain[0], synced[0])


class StopLoop(Exception):
    pass


class LibraryLoopTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(nowplaying, LOG='nowplaying.log')
        self.state = nowplaying.State()
        patcher = mock.patch.object(nowplaying, 'state', self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_loop(self, steps, rate_limited=None, offset=None):
        """Runs library_loop() for len(steps) turns, refresh_library() returning each step in
        turn; returns the sleeps it asked for and the refresh mock."""
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == len(steps):
                raise StopLoop

        api = mock.Mock(TOKEN_FILE=__file__, refresh_library=mock.Mock(side_effect=steps),
                        rate_limited_until=mock.Mock(side_effect=rate_limited or [None] * len(steps)),
                        scan_offset=mock.Mock(return_value=offset))
        with mock.patch.object(nowplaying, 'spotify_api', api), \
                mock.patch.object(nowplaying.time, 'sleep', side_effect=sleep):
            with self.assertRaises(StopLoop):
                nowplaying.library_loop()
        return sleeps, api.refresh_library

    def log(self):
        return support.read(nowplaying.LOG)

    def test_a_scan_is_paced_page_by_page_then_settles_to_a_minute(self):
        scanning = {'scanned': 0, 'total': 120, 'keys': {}, 'scan': {'offset': 50, 'keys': {}}}
        done = {'scanned': time.time() + 60, 'total': 120, 'keys': {'a\tx': ['u']}}
        idle = {'scanned': 1, 'total': 120, 'keys': {'a\tx': ['u']}}
        self.state.liked_checked = 99.0
        sleeps, _ = self.run_loop([scanning, done, idle])
        page = nowplaying.LIBRARY_PAGE_SECONDS
        self.assertEqual(sleeps, [page, 60, 60])
        self.assertIn(f'library scan: 50 of 120 indexed, next page in {page} s', self.log())
        self.assertEqual(self.log().count('library scan done: 120 songs, 1 titles'), 1)
        self.assertEqual(self.state.liked_checked, 0)

    def test_a_rate_limit_pauses_without_a_request_and_logs_once(self):
        until = time.time() + 600
        sleeps, refresh = self.run_loop([None, None], rate_limited=[until, until], offset=100)
        refresh.assert_not_called()
        self.assertEqual(sleeps, [60, 60])
        self.assertEqual(self.log().count('library scan paused at offset 100 until '
                                          + time.strftime('%T', time.localtime(until))), 1)

    def test_a_failed_page_is_logged_and_retried_a_minute_later(self):
        sleeps, _ = self.run_loop([OSError('offline')])
        self.assertEqual(sleeps, [60])
        self.assertIn('library refresh failed: OSError: offline', self.log())


if __name__ == '__main__':
    unittest.main()
