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
                      BACKOFF_FILE='rate-limited-until', LIKED_FILE='liked',
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
