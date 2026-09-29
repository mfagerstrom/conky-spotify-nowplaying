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
        self.redirect(nowplaying, REGIONS='regions.json', DRAW='draw.txt', LYRICS='lyrics.txt', LOG='nowplaying.log')
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
        _, short, _ = self.render((505, 40, 1.0))
        _, tall, _ = self.render((505, 400, 1.0))
        self.assertEqual((short['lyrics'][5], tall['lyrics'][5]), ('40', '400'))
        self.assertEqual(short['lyrics'][2], tall['lyrics'][2])


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
        self.redirect(nowplaying, LOG='nowplaying.log')
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


if __name__ == '__main__':
    unittest.main()
