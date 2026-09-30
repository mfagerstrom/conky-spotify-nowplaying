"""Tests for spotify_api.py: track keys, the Liked Songs index, time windows, 429 backoff."""
import os
import time
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
                      TOGGLED_FILE='liked-toggled', LOCK_FILE='liked.lock',
                      LOOKUPS_FILE='cache/lookups.json', LOG_FILE='cache/nowplaying.log')
        for cache in (spotify_api._tracks, spotify_api._album_upcs):
            patcher = mock.patch.dict(cache, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(spotify_api, '_lookups_loaded', False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def track(self, uri, key, album=None, upc=...):
        """Answers what the API would say about a track: its key, album and album UPC
        (None for an album without one)."""
        album = album or 'album-' + uri
        spotify_api._tracks[uri] = (key, album)
        spotify_api._album_upcs[album] = 'upc-' + album if upc is ... else upc

    def at(self, when):
        """Freezes spotify_api's clock at `when` for the rest of the test."""
        patcher = mock.patch.object(spotify_api, 'time', mock.Mock(
            time=lambda: when, strftime=time.strftime, localtime=time.localtime))
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
        track = {'name': 'Under Pressure', 'artists': [{'name': 'Queen'}, {'name': 'David Bowie'}],
                 'album': {'id': 'hot-space'}}
        with mock.patch.object(spotify_api, 'api', return_value=track) as api:
            self.assertEqual(spotify_api.track_key('spotify:track:abc'), 'under pressure\tqueen')
            self.assertEqual(spotify_api.track_key('spotify:track:abc'), 'under pressure\tqueen')
        api.assert_called_once_with('GET', '/tracks/abc')

    def test_album_upc_is_fetched_once_and_may_be_missing(self):
        with mock.patch.object(spotify_api, 'api', side_effect=[
                {'external_ids': {'upc': '859777506334'}}, {}]) as api:
            self.assertEqual(spotify_api._album_upc('a'), '859777506334')
            self.assertEqual(spotify_api._album_upc('a'), '859777506334')
            self.assertIsNone(spotify_api._album_upc('b'))
        self.assertEqual(api.call_args_list, [mock.call('GET', '/albums/a'), mock.call('GET', '/albums/b')])


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
        self.track('spotify:track:2', 'b\tx')

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

    def test_a_page_right_after_a_heart_click_is_fetched_again(self):
        before = {'scanned': 0, 'total': 120, 'keys': {}, 'scan': {'offset': 50, 'keys': {}}}
        spotify_api._save_library(before)
        open(spotify_api.TOGGLED_FILE, 'w').close()
        os.utime(spotify_api.TOGGLED_FILE, (NOW - 5, NOW - 5))
        lib, _ = self.refresh(page(50, 120, [saved('spotify:track:2', 'B', 'X')]))
        self.assertEqual(lib, before)
        self.assertEqual(spotify_api._load_library(), before)

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
        self.track('spotify:track:single', 'a\tx')
        self.track('spotify:track:album', 'a\tx')
        self.track('spotify:track:other', 'b\tx')
        with mock.patch.object(spotify_api, 'is_liked', return_value=False):
            self.assertTrue(spotify_api.is_liked_any('spotify:track:single'))
            self.assertFalse(spotify_api.is_liked_any('spotify:track:other'))

    def test_likes_and_unlikes_during_a_scan_outlive_it(self):
        spotify_api._save_library({'scanned': 0, 'total': 120, 'keys': {'b\tx': ['spotify:track:2']},
                                   'scan': {'offset': 50, 'keys': {'b\tx': ['spotify:track:2b']}}})
        self.track('spotify:track:1', 'a\tx')
        self.track('spotify:track:2', 'b\tx')
        self.track('spotify:track:2b', 'b\tx')
        with mock.patch.object(spotify_api, 'api') as api:
            spotify_api._push_like('spotify:track:1', True)
            spotify_api._push_like('spotify:track:2', False)
        api.assert_called_with('DELETE', '/me/library', uris='spotify:track:2,spotify:track:2b')
        lib = spotify_api._load_library()
        self.assertEqual(lib['keys'], {'a\tx': ['spotify:track:1']})
        self.assertEqual(lib['scan']['keys'], {'a\tx': ['spotify:track:1']})


class DuplicateListingTest(SpotifyApiTest):
    """Spotify sometimes lists one album twice, under two album IDs with one UPC. The app
    shows a song on one listing as not liked when only its twin on the other is saved."""

    def setUp(self):
        super().setUp()
        self.track('spotify:track:playing', 'goner - demo\tkids that fly', 'deluxe', '859777506334')
        self.track('spotify:track:twin', 'goner - demo\tkids that fly', 'deluxe-again', '859777506334')
        self.track('spotify:track:single', 'goner - demo\tkids that fly', 'single', '111')
        patcher = mock.patch.object(spotify_api, 'is_liked', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, *uris):
        spotify_api._save_library({'scanned': NOW, 'total': len(uris),
                                   'keys': {'goner - demo\tkids that fly': list(uris)}})

    def test_a_saved_twin_listing_does_not_fill_the_heart(self):
        self.save('spotify:track:twin')
        self.assertFalse(spotify_api.is_liked_any('spotify:track:playing'))

    def test_a_saved_release_on_another_product_still_does(self):
        self.save('spotify:track:twin', 'spotify:track:single')
        self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))

    def test_the_exact_track_still_does(self):
        self.save('spotify:track:twin')
        with mock.patch.object(spotify_api, 'is_liked', return_value=True):
            self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))

    def test_albums_without_a_upc_are_never_twins(self):
        self.track('spotify:track:playing', 'goner - demo\tkids that fly', 'deluxe', None)
        self.track('spotify:track:twin', 'goner - demo\tkids that fly', 'deluxe-again', None)
        self.save('spotify:track:twin')
        self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))

    def test_another_track_on_the_same_album_still_counts(self):
        self.track('spotify:track:reissue', 'goner - demo\tkids that fly', 'deluxe', '859777506334')
        self.save('spotify:track:reissue')
        self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))

    def test_the_heart_stops_looking_at_the_first_save_that_counts(self):
        self.save('spotify:track:single', 'spotify:track:twin')
        with mock.patch.object(spotify_api, '_album_upc', wraps=spotify_api._album_upc) as upc:
            self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))
        self.assertEqual(upc.call_count, 2)                   # playing and single; twin never looked up

    def test_album_without_external_ids_has_no_upc(self):
        with mock.patch.object(spotify_api, 'api', return_value={'external_ids': None}):
            self.assertIsNone(spotify_api._album_upc('bare'))

    def test_unliking_leaves_the_twin_saved_and_indexed(self):
        self.save('spotify:track:playing', 'spotify:track:twin', 'spotify:track:single')
        with mock.patch.object(spotify_api, 'api') as api:
            spotify_api._push_like('spotify:track:playing', False)
        api.assert_called_once_with('DELETE', '/me/library',
                                    uris='spotify:track:playing,spotify:track:single')
        self.assertEqual(spotify_api._load_library()['keys'],
                         {'goner - demo\tkids that fly': ['spotify:track:twin']})
        self.assertFalse(spotify_api.is_liked_any('spotify:track:playing'))


class LocalLikesTest(SpotifyApiTest):
    """Likes read from the Spotify app's own files (spotify_local), with the Web API only
    where those cannot answer."""

    def setUp(self):
        super().setUp()
        self.track('spotify:track:playing', 'song\tartist', 'album', '111')
        self.track('spotify:track:single', 'song\tartist', 'single', '222')
        spotify_api._save_library({'scanned': NOW, 'total': 1,
                                   'keys': {'song\tartist': ['spotify:track:single']}})

    def app_has(self, *ids):
        """The app's Liked Songs: these IDs, or unreadable for None."""
        saved = None if ids == (None,) else frozenset(ids)
        patcher = mock.patch.object(spotify_api.spotify_local, 'saved_tracks', return_value=saved)
        patcher.start()
        self.addCleanup(patcher.stop)

    def log_in(self):
        os.makedirs(os.path.dirname(spotify_api.TOKEN_FILE), exist_ok=True)
        with open(spotify_api.TOKEN_FILE, 'w') as f:
            f.write('{}')

    def test_the_exact_track_is_read_from_the_app_without_a_request(self):
        self.app_has('playing')
        with mock.patch.object(spotify_api, 'api') as api:
            self.assertTrue(spotify_api.is_liked('spotify:track:playing'))
            self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))
        api.assert_not_called()

    def test_the_apps_set_is_read_once_per_check(self):
        self.log_in()
        self.app_has('single')
        spotify_api.is_liked_any('spotify:track:playing')
        self.assertEqual(spotify_api.spotify_local.saved_tracks.call_count, 1)

    def test_the_web_api_answers_when_the_app_cannot(self):
        self.app_has(None)
        with mock.patch.object(spotify_api, 'api', return_value=[True]) as api:
            self.assertTrue(spotify_api.is_liked('spotify:track:playing'))
        api.assert_called_once_with('GET', '/me/library/contains', uris='spotify:track:playing')

    def test_another_release_saved_in_the_app_still_fills_the_heart(self):
        self.log_in()
        self.app_has('single')
        with mock.patch.object(spotify_api, 'api') as api:
            self.assertTrue(spotify_api.is_liked_any('spotify:track:playing'))
        api.assert_not_called()                               # the index and cache answered

    def test_a_release_unliked_in_the_app_since_the_index_saw_it_does_not(self):
        self.log_in()
        self.app_has()
        self.assertFalse(spotify_api.is_liked_any('spotify:track:playing'))

    def test_rate_limited_or_logged_out_the_apps_answer_for_the_track_stands(self):
        self.app_has('single')
        with mock.patch.object(spotify_api, 'api', side_effect=AssertionError('no requests')):
            self.assertFalse(spotify_api.is_liked_any('spotify:track:playing'))  # logged out
            self.log_in()
            with open(spotify_api.BACKOFF_FILE, 'w') as f:
                f.write(str(NOW + 3600))
            self.at(NOW)
            self.assertFalse(spotify_api.is_liked_any('spotify:track:playing'))


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

    def test_every_request_is_logged_with_its_status_but_not_its_query(self):
        with mock.patch('urllib.request.urlopen', return_value=support.response(b'[true]')):
            spotify_api.api('GET', '/me/library/contains', uris='spotify:track:1')
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                spotify_api.api('DELETE', '/me/library', uris='spotify:track:1')
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(urllib.error.URLError):
                spotify_api.api('GET', '/me/tracks')
        lines = [line[9:] for line in support.read(spotify_api.LOG_FILE).splitlines()]
        self.assertEqual(lines, ['api: GET /v1/me/library/contains 200', 'api: DELETE /v1/me/library 500',
                                 'api: GET /v1/me/tracks URLError'])

    def test_a_429_is_logged_with_the_wait(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(429, {'Retry-After': '76616'})):
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.api('GET', '/me/tracks')
        log = support.read(spotify_api.LOG_FILE)
        self.assertIn('api: GET /v1/me/tracks 429', log)
        self.assertIn('api: rate limited for 76616 s, sending nothing until', log)

    def test_an_unreadable_retry_after_waits_a_minute(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(429, {'Retry-After': 'soon'})):
            with self.assertRaises(spotify_api.RateLimited) as caught:
                spotify_api.api('GET', '/me/tracks')
        self.assertEqual(caught.exception.until, NOW + 60)


class TokenTest(SpotifyApiTest):
    """The token endpoint goes through the same backoff as the Web API."""

    def setUp(self):
        super().setUp()
        self.at(NOW)
        os.makedirs(os.path.dirname(spotify_api.TOKEN_FILE), exist_ok=True)
        with open(spotify_api.TOKEN_FILE, 'w') as f:
            f.write('{"access_token": "old", "refresh_token": "r", "expires_at": 0}')
        patcher = mock.patch.object(spotify_api, 'client_id', return_value='id')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_refresh_is_logged(self):
        with mock.patch('urllib.request.urlopen',
                        return_value=support.response(b'{"access_token": "new", "expires_in": 3600}')):
            self.assertEqual(spotify_api.access_token(), 'new')
        self.assertIn('api: POST accounts.spotify.com/api/token 200', support.read(spotify_api.LOG_FILE))

    def test_a_429_on_refresh_records_the_backoff_and_blocks_every_request(self):
        with mock.patch('urllib.request.urlopen', side_effect=support.http_error(429, {'Retry-After': '300'})):
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.access_token()
        self.assertEqual(float(support.read(spotify_api.BACKOFF_FILE)), NOW + 300)
        with mock.patch('urllib.request.urlopen') as urlopen:
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.access_token()
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.api('GET', '/me/tracks')
        urlopen.assert_not_called()


class LoginTest(SpotifyApiTest):

    def test_login_during_a_lockout_opens_no_browser(self):
        self.at(NOW)
        with open(spotify_api.BACKOFF_FILE, 'w') as f:
            f.write(str(NOW + 600))
        with mock.patch.object(spotify_api.subprocess, 'Popen') as popen, \
                mock.patch('http.server.HTTPServer') as server:
            with self.assertRaises(spotify_api.RateLimited):
                spotify_api.login()
        popen.assert_not_called()
        server.assert_not_called()


class LibraryStampTest(SpotifyApiTest):

    def test_no_index(self):
        self.assertEqual(spotify_api.library_stamp(), (0.0, None))

    def test_an_index(self):
        spotify_api._save_library({'scanned': 1, 'total': 3, 'keys': {'a\tx': ['1', '2'], 'b\ty': ['3']}})
        saved_at, size = spotify_api.library_stamp()
        self.assertEqual(saved_at, os.path.getmtime(spotify_api.LIBRARY_FILE))
        self.assertEqual(size, (3, 3))


class LookupsTest(SpotifyApiTest):

    def restart(self):
        """Forgets what this process looked up, as a new process would."""
        spotify_api._tracks.clear()
        spotify_api._album_upcs.clear()
        spotify_api._lookups_loaded = False

    def test_lookups_survive_a_restart(self):
        track = {'name': 'Song', 'artists': [{'name': 'Artist'}], 'album': {'id': 'a'}}
        with mock.patch.object(spotify_api, 'api', side_effect=[track, {'external_ids': {'upc': '1'}}]):
            spotify_api._track('spotify:track:x')
            spotify_api._album_upc('a')
        self.restart()
        with mock.patch.object(spotify_api, 'api') as api:
            self.assertEqual(spotify_api._track('spotify:track:x'), ('song\tartist', 'a'))
            self.assertEqual(spotify_api._album_upc('a'), '1')
        api.assert_not_called()

    def test_the_oldest_lookups_go_past_the_cap(self):
        with mock.patch.object(spotify_api, 'MAX_LOOKUPS', 2):
            for n in range(3):
                spotify_api._tracks[f'spotify:track:{n}'] = ('k', 'a')
            spotify_api._save_lookups()
        self.restart()
        spotify_api._load_lookups()
        self.assertEqual(list(spotify_api._tracks), ['spotify:track:1', 'spotify:track:2'])

    def test_an_unreadable_file_is_ignored(self):
        os.makedirs(os.path.dirname(spotify_api.LOOKUPS_FILE), exist_ok=True)
        with open(spotify_api.LOOKUPS_FILE, 'w') as f:
            f.write('[1, 2')
        spotify_api._load_lookups()
        self.assertEqual(spotify_api._tracks, {})


class LibraryCurrentTest(SpotifyApiTest):

    def setUp(self):
        super().setUp()
        self.at(NOW)
        spotify_api._save_library({'scanned': NOW - 60, 'total': 2,
                                   'keys': {'a\tx': ['spotify:track:1'], 'b\ty': ['spotify:track:2']}})

    def current(self, saved):
        with mock.patch.object(spotify_api.spotify_local, 'saved_tracks', return_value=saved):
            return spotify_api.library_current()

    def test_current_when_the_index_holds_every_saved_song(self):
        self.assertTrue(self.current(frozenset({'1', '2'})))
        self.assertTrue(self.current(frozenset({'1'})))      # an unlike needs no refresh

    def test_not_current_with_a_song_the_index_lacks(self):
        self.assertFalse(self.current(frozenset({'1', '2', '3'})))

    def test_not_current_without_the_apps_set(self):
        self.assertFalse(self.current(None))

    def test_not_current_when_stale_or_scanning(self):
        spotify_api._save_library({'scanned': NOW - 25 * 3600, 'total': 1, 'keys': {'a\tx': ['spotify:track:1']}})
        self.assertFalse(self.current(frozenset({'1'})))
        spotify_api._save_library({'scanned': NOW, 'total': 1, 'keys': {'a\tx': ['spotify:track:1']},
                                   'scan': {'offset': 0, 'keys': {}}})
        self.assertFalse(self.current(frozenset({'1'})))


class LikedFileTest(SpotifyApiTest):

    def test_write_liked(self):
        for value, want in ((True, '1'), (False, '0'), (None, '')):
            with self.subTest(value=value):
                spotify_api.write_liked(value)
                self.assertEqual(support.read(spotify_api.LIKED_FILE), want)


if __name__ == '__main__':
    unittest.main()
