"""Tests for spotify_api.py: track keys, the Liked Songs index, time windows, 429 backoff."""
import os
import unittest
import urllib.error
from unittest import mock

import support

import spotify_api  # noqa: E402  (support puts src/ on the path)

NOW = 1_000_000.0


class SpotifyApiTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(spotify_api, CONF_DIR='config', CACHE_DIR='cache',
                      TOKEN_FILE='config/spotify-token.json', LIBRARY_FILE='cache/library.json',
                      BACKOFF_FILE='rate-limited-until', LIKED_FILE='liked', LIBRARY_LOCK='cache/library.json.lock',
                      TOGGLED_FILE='liked-toggled', LOCK_FILE='liked.lock')
        patcher = mock.patch.dict(spotify_api._track_keys, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def at(self, when):
        """Freezes spotify_api's clock at `when` for the rest of the test."""
        patcher = mock.patch.object(spotify_api, 'time', mock.Mock(time=lambda: when))
        patcher.start()
        self.addCleanup(patcher.stop)


def saved(uri, name, artist):
    return {'track': {'uri': uri, 'name': name, 'artists': [{'name': artist}]}}


class TrackKeyTest(SpotifyApiTest):

    def test_key_ignores_case_and_surrounding_space(self):
        self.assertEqual(spotify_api._key('  Hey Jude ', 'The BEATLES'),
                         spotify_api._key('hey jude', 'the beatles'))
        self.assertEqual(spotify_api._key('Song', 'Artist'), 'song\tartist')

    def test_track_key_uses_the_first_artist_and_is_cached(self):
        track = {'name': 'Under Pressure', 'artists': [{'name': 'Queen'}, {'name': 'David Bowie'}]}
        with mock.patch.object(spotify_api, 'api', return_value=track) as api:
            self.assertEqual(spotify_api.track_key('spotify:track:abc'), 'under pressure\tqueen')
            self.assertEqual(spotify_api.track_key('spotify:track:abc'), 'under pressure\tqueen')
        api.assert_called_once_with('GET', '/tracks/abc')


class LibraryTest(SpotifyApiTest):

    def test_index_page_groups_releases_and_counts_new_ones(self):
        lib = {'keys': {}}
        items = [
            saved('spotify:track:single', 'Song', 'Artist'),
            saved('spotify:track:album', 'SONG ', 'artist'),
            saved('spotify:track:other', 'Other', 'Artist'),
            {'track': None},                                   # removed from Spotify
            {'track': {'uri': None, 'name': 'Local', 'artists': [{'name': 'Me'}]}},  # local file
        ]
        self.assertEqual(spotify_api._index_page(lib, items), 3)
        self.assertEqual(lib['keys'], {
            'song\tartist': ['spotify:track:single', 'spotify:track:album'],
            'other\tartist': ['spotify:track:other'],
        })
        self.assertEqual(spotify_api._index_page(lib, items[:2]), 0)   # already indexed

    def test_save_and_load_round_trip(self):
        lib = {'scanned': NOW, 'total': 1, 'keys': {'song\tartist': ['spotify:track:1']}}
        spotify_api._save_library(lib)
        self.assertEqual(spotify_api._load_library(), lib)
        self.assertEqual(os.listdir(os.path.dirname(spotify_api.LIBRARY_FILE)), ['library.json'])

    def test_load_of_a_missing_or_corrupt_library_is_none(self):
        self.assertIsNone(spotify_api._load_library())
        os.makedirs(os.path.dirname(spotify_api.LIBRARY_FILE))
        with open(spotify_api.LIBRARY_FILE, 'w') as f:
            f.write('{not json')
        self.assertIsNone(spotify_api._load_library())


def page(offset, total, items):
    return {'offset': offset, 'limit': 50, 'total': total, 'items': items,
            'next': 'more' if offset + 50 < total else None}


class RefreshLibraryTest(SpotifyApiTest):

    def setUp(self):
        super().setUp()
        self.at(NOW)

    def refresh(self, response):
        """One refresh_library() call answered with `response`; returns (index, the call)."""
        with mock.patch.object(spotify_api, 'api', return_value=response) as api:
            lib = spotify_api.refresh_library()
        api.assert_called_once()
        return lib, api.call_args

    def test_missing_index_starts_a_scan_with_one_request_and_saves_it(self):
        lib, call = self.refresh(page(0, 120, [saved('spotify:track:1', 'A', 'X')]))
        self.assertEqual(call, mock.call('GET', '/me/tracks', limit=50, offset=0))
        self.assertEqual(lib['scan'], {'offset': 50, 'keys': {'a\tx': ['spotify:track:1']}})
        self.assertEqual((lib['scanned'], lib['total']), (0, 120))
        self.assertEqual(spotify_api._load_library(), lib)
        self.assertEqual(spotify_api.scan_offset(), 50)

    def test_a_scan_in_progress_resumes_from_its_saved_offset(self):
        spotify_api._save_library({'scanned': 0, 'total': 120, 'keys': {},
                                   'scan': {'offset': 50, 'keys': {'a\tx': ['spotify:track:1']}}})
        lib, call = self.refresh(page(50, 120, [saved('spotify:track:2', 'B', 'X')]))
        self.assertEqual(call, mock.call('GET', '/me/tracks', limit=50, offset=50))
        self.assertEqual(lib['scan']['offset'], 100)
        self.assertEqual(set(lib['scan']['keys']), {'a\tx', 'b\tx'})

    def test_a_429_keeps_the_saved_offset(self):
        before = {'scanned': 0, 'total': 120, 'keys': {}, 'scan': {'offset': 50, 'keys': {}}}
        spotify_api._save_library(before)
        with mock.patch.object(spotify_api, 'api', side_effect=spotify_api.RateLimited(NOW + 60)):
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.refresh_library()
        self.assertEqual(spotify_api._load_library(), before)

    def test_the_last_page_replaces_the_keys_and_ends_the_scan(self):
        spotify_api._save_library({'scanned': NOW - 90_000, 'total': 60,
                                   'keys': {'gone\tx': ['spotify:track:old']},
                                   'scan': {'offset': 50, 'keys': {'a\tx': ['spotify:track:1']}}})
        lib, _ = self.refresh(page(50, 60, [saved('spotify:track:2', 'B', 'X')]))
        self.assertNotIn('scan', lib)
        self.assertEqual(lib['keys'], {'a\tx': ['spotify:track:1'], 'b\tx': ['spotify:track:2']})
        self.assertEqual((lib['scanned'], lib['total']), (NOW, 60))
        self.assertIsNone(spotify_api.scan_offset())

    def test_a_one_page_library_is_scanned_in_one_call(self):
        lib, _ = self.refresh(page(0, 1, [saved('spotify:track:1', 'A', 'X')]))
        self.assertNotIn('scan', lib)
        self.assertEqual((lib['scanned'], lib['keys']), (NOW, {'a\tx': ['spotify:track:1']}))

    def test_a_fresh_index_only_reads_the_newest_likes(self):
        spotify_api._save_library({'scanned': NOW - 60, 'total': 120, 'keys': {'a\tx': ['spotify:track:1']}})
        lib, _ = self.refresh(page(0, 121, [saved('spotify:track:2', 'B', 'X')]))
        self.assertNotIn('scan', lib)
        self.assertEqual(set(lib['keys']), {'a\tx', 'b\tx'})
        self.assertEqual((lib['scanned'], lib['total']), (NOW - 60, 121))

    def test_a_stale_or_shrunk_index_starts_a_scan_and_keeps_answering_meanwhile(self):
        cases = [('stale', NOW - 90_000, 120), ('shrunk', NOW - 60, 119)]
        for name, scanned, total in cases:
            with self.subTest(name):
                old = {'a\tx': ['spotify:track:1']}
                spotify_api._save_library({'scanned': scanned, 'total': 120, 'keys': old})
                lib, _ = self.refresh(page(0, total, [saved('spotify:track:2', 'B', 'X')]))
                self.assertEqual(lib['scan']['offset'], 50)
                self.assertEqual(lib['keys'], old)

    def test_a_heart_click_saved_while_a_page_is_on_its_way_is_kept(self):
        spotify_api._save_library({'scanned': 0, 'total': 120, 'keys': {'b\tx': ['spotify:track:2']},
                                   'scan': {'offset': 50, 'keys': {'b\tx': ['spotify:track:2']}}})
        spotify_api._track_keys['spotify:track:2'] = 'b\tx'

        def api(method, path, **params):
            if path == '/me/tracks':                            # the click lands mid-request
                with mock.patch.object(spotify_api, 'api'):
                    spotify_api._push_like('spotify:track:2', False)
                return page(50, 120, [saved('spotify:track:3', 'C', 'X')])

        with mock.patch.object(spotify_api, 'api', side_effect=api):
            lib = spotify_api.refresh_library()
        self.assertEqual(lib['keys'], {})
        self.assertEqual(lib['scan'], {'offset': 100, 'keys': {'c\tx': ['spotify:track:3']}})
        self.assertEqual(spotify_api._load_library(), lib)

    def test_a_page_for_an_offset_the_scan_has_left_is_dropped(self):
        with mock.patch.object(spotify_api, 'api', return_value=page(100, 120, [])) as api:
            with mock.patch.object(spotify_api, '_load_library', side_effect=[
                    {'scanned': 0, 'total': 120, 'keys': {}, 'scan': {'offset': 100, 'keys': {}}},
                    {'scanned': 0, 'total': 120, 'keys': {}, 'scan': {'offset': 50, 'keys': {}}}]):
                lib = spotify_api.refresh_library()
        api.assert_called_once()
        self.assertEqual(lib['scan']['offset'], 50)

    def test_liked_any_counts_keys_a_scan_has_gathered_so_far(self):
        spotify_api._save_library({'scanned': 0, 'total': 120, 'keys': {},
                                   'scan': {'offset': 50, 'keys': {'a\tx': ['spotify:track:album']}}})
        spotify_api._track_keys['spotify:track:single'] = 'a\tx'
        spotify_api._track_keys['spotify:track:other'] = 'b\tx'
        with mock.patch.object(spotify_api, 'is_liked', return_value=False):
            self.assertTrue(spotify_api.is_liked_any('spotify:track:single'))
            self.assertFalse(spotify_api.is_liked_any('spotify:track:other'))

    def test_likes_and_unlikes_during_a_scan_outlive_it(self):
        spotify_api._save_library({'scanned': 0, 'total': 120, 'keys': {'b\tx': ['spotify:track:2']},
                                   'scan': {'offset': 50, 'keys': {'b\tx': ['spotify:track:2b']}}})
        spotify_api._track_keys.update({'spotify:track:1': 'a\tx', 'spotify:track:2': 'b\tx'})
        with mock.patch.object(spotify_api, 'api') as api:
            spotify_api._push_like('spotify:track:1', True)
            spotify_api._push_like('spotify:track:2', False)
        api.assert_called_with('DELETE', '/me/library', uris='spotify:track:2,spotify:track:2b')
        lib = spotify_api._load_library()
        self.assertEqual(lib['keys'], {'a\tx': ['spotify:track:1']})
        self.assertEqual(lib['scan']['keys'], {'a\tx': ['spotify:track:1']})


class TimeWindowTest(SpotifyApiTest):

    def write(self, path, text):
        with open(path, 'w') as f:
            f.write(text)

    def test_rate_limited_until_a_future_time(self):
        self.write(spotify_api.BACKOFF_FILE, str(NOW + 90))
        self.at(NOW)
        self.assertEqual(spotify_api.rate_limited_until(), NOW + 90)

    def test_rate_limit_in_the_past_has_lifted(self):
        self.write(spotify_api.BACKOFF_FILE, str(NOW - 1))
        self.at(NOW)
        self.assertIsNone(spotify_api.rate_limited_until())

    def test_missing_or_unreadable_backoff_is_not_limited(self):
        self.at(NOW)
        self.assertIsNone(spotify_api.rate_limited_until())
        self.write(spotify_api.BACKOFF_FILE, 'garbage')
        self.assertIsNone(spotify_api.rate_limited_until())

    def test_recently_toggled(self):
        self.write(spotify_api.TOGGLED_FILE, '')
        os.utime(spotify_api.TOGGLED_FILE, (NOW, NOW))
        cases = [(NOW + 5, {}, True), (NOW + 16, {}, False), (NOW + 25, {'seconds': 30}, True)]
        for when, kwargs, want in cases:
            with self.subTest(after=when - NOW, **kwargs):
                with mock.patch.object(spotify_api, 'time', mock.Mock(time=lambda: when)):
                    self.assertIs(spotify_api.recently_toggled(**kwargs), want)

    def test_never_toggled(self):
        self.assertFalse(spotify_api.recently_toggled())



class ApiTest(SpotifyApiTest):

    def setUp(self):
        super().setUp()
        self.at(NOW)
        patcher = mock.patch.object(spotify_api, 'access_token', return_value='token')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_json_response(self):
        with mock.patch('urllib.request.urlopen', return_value=support.response(b'[true]')) as urlopen:
            self.assertEqual(spotify_api.api('GET', '/me/library/contains', uris='spotify:track:1'), [True])
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url,
                         'https://api.spotify.com/v1/me/library/contains?uris=spotify%3Atrack%3A1')
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(req.get_header('Authorization'), 'Bearer token')

    def test_empty_response_is_none(self):
        with mock.patch('urllib.request.urlopen', return_value=support.response(b'')):
            self.assertIsNone(spotify_api.api('PUT', '/me/library', uris='spotify:track:1'))

    def test_429_records_retry_after_and_blocks_further_calls(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(429, {'Retry-After': '120'})):
            with self.assertRaises(spotify_api.RateLimited) as caught:
                spotify_api.api('GET', '/me/tracks')
        self.assertEqual(caught.exception.until, NOW + 120)
        self.assertEqual(float(support.read(spotify_api.BACKOFF_FILE)), NOW + 120)
        with mock.patch('urllib.request.urlopen') as urlopen:
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.api('GET', '/me/tracks')
        urlopen.assert_not_called()

    def test_429_without_retry_after_waits_a_minute(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(429)):
            with self.assertRaises(spotify_api.RateLimited) as caught:
                spotify_api.api('GET', '/me/tracks')
        self.assertEqual(caught.exception.until, NOW + 60)

    def test_other_http_errors_are_raised_without_backoff(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                spotify_api.api('GET', '/me/tracks')
        self.assertFalse(os.path.exists(spotify_api.BACKOFF_FILE))


class LikedFileTest(SpotifyApiTest):

    def test_write_liked(self):
        for value, want in ((True, '1'), (False, '0'), (None, '')):
            with self.subTest(value=value):
                spotify_api.write_liked(value)
                self.assertEqual(support.read(spotify_api.LIKED_FILE), want)


if __name__ == '__main__':
    unittest.main()
