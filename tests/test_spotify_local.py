"""Tests for spotify_local.py: reading the Spotify app's Liked Songs out of its LevelDB."""
import ctypes
import os
import struct
import unittest
from unittest import mock

import support

import spotify_local  # noqa: E402  (support puts src/ on the path)

MAGIC = b'\x57\xfb\x80\x8b\x24\x75\x47\xdb'
PUT, DELETE = 1, 0


def varint(n):
    out = b''
    while n >= 0x80:
        out += bytes([n & 0x7F | 0x80])
        n >>= 7
    return out + bytes([n])


def saved_key(track, user=b'me'):
    uri, collection = b'spotify:track:' + track.encode(), b'spotify:user:' + user + b':collection'
    return b'!cit#cit#' + bytes([len(uri)]) + uri + b'#' + bytes([len(collection)]) + collection + b'#\x00#'


def snappy(data):
    lib = ctypes.CDLL('libsnappy.so.1')
    lib.snappy_max_compressed_length.restype = ctypes.c_size_t
    n = ctypes.c_size_t(lib.snappy_max_compressed_length(len(data)))
    out = ctypes.create_string_buffer(n.value)
    assert lib.snappy_compress(data, len(data), out, ctypes.byref(n)) == 0
    return out.raw[:n.value]


def block(entries):
    """A table block with every key written whole and one restart point."""
    out = b''.join(varint(0) + varint(len(k)) + varint(len(v)) + k + v for k, v in entries)
    return out + struct.pack('<II', 0, 1)


def write_table(path, blocks, compress=False):
    """Writes a table of data blocks, each a list of (user key, seq, kind), sorted."""
    out, index = b'', []

    def put(data, squash=False):
        nonlocal out
        handle = varint(len(out)) + varint(len(snappy(data) if squash else data))
        out += (snappy(data) + b'\x01' if squash else data + b'\x00') + b'\0\0\0\0'
        return handle

    for entries in blocks:
        keys = [(k + struct.pack('<Q', seq << 8 | kind), b'') for k, seq, kind in entries]
        index.append((keys[-1][0], put(block(keys), compress)))
    meta = put(block([]))
    footer = meta + put(block(index))
    with open(path, 'wb') as f:
        f.write(out + footer.ljust(40, b'\0') + MAGIC)


def record(kind, payload):
    return b'\0\0\0\0' + struct.pack('<H', len(payload)) + bytes([kind]) + payload


def batch(seq, *ops):
    out = struct.pack('<QI', seq, len(ops))
    for op, key in ops:
        out += bytes([op]) + varint(len(key)) + key + (varint(0) if op == PUT else b'')
    return out


class SpotifyLocalTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.db = os.path.join(self.dir, 'cache', 'spotify', 'Users', 'me-user', 'primary.ldb')
        os.makedirs(self.db)
        for name, value in (('ROOTS', [os.path.join(self.dir, 'cache', 'spotify')]), ('_found', (0.0, None))):
            patcher = mock.patch.object(spotify_local, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(spotify_local._tables, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def table(self, name, *blocks, compress=False):
        write_table(os.path.join(self.db, name), blocks, compress)

    def log(self, name, data):
        with open(os.path.join(self.db, name), 'wb') as f:
            f.write(data)


class SavedTracksTest(SpotifyLocalTest):

    def test_saved_tracks_are_read_from_a_table(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, PUT), (saved_key('b' * 22), 6, PUT)])
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22, 'b' * 22})
        self.assertTrue(spotify_local.is_saved('spotify:track:' + 'a' * 22))
        self.assertFalse(spotify_local.is_saved('spotify:track:' + 'c' * 22))

    def test_an_unlike_in_the_log_outranks_the_like_in_a_table(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, PUT), (saved_key('b' * 22), 6, PUT)])
        self.log('000002.log', record(1, batch(9, (DELETE, saved_key('a' * 22)))))
        self.assertEqual(spotify_local.saved_tracks(), {'b' * 22})

    def test_a_like_again_outranks_an_older_unlike(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, DELETE)])
        self.table('000003.ldb', [(saved_key('a' * 22), 2, PUT)])       # older, in a newer file
        self.log('000002.log', record(1, batch(9, (PUT, saved_key('b' * 22)))))
        self.assertEqual(spotify_local.saved_tracks(), {'b' * 22})
        self.log('000002.log', record(1, batch(9, (PUT, saved_key('b' * 22)))) +
                 record(1, batch(10, (PUT, saved_key('a' * 22)))))
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22, 'b' * 22})

    def test_other_keys_and_other_collections_are_not_likes(self):
        self.table('000001.ldb',
                   [(b'!aaa#track:' + b'x' * 22, 1, PUT)],
                   [(b'!cit#cit#$spotify:episode:' + b'e' * 22 + b'# spotify:user:me:collection#', 2, PUT),
                    (saved_key('a' * 22), 3, PUT)],
                   [(b'!xmeta#' + saved_key('m' * 22), 4, PUT)])
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22})

    def test_tables_named_the_older_way_are_read(self):
        self.table('000001.sst', [(saved_key('a' * 22), 5, PUT)])
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22})

    def test_compressed_blocks_are_read(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, PUT)], compress=True)
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22})

    def test_without_libsnappy_a_compressed_table_makes_the_whole_set_unreadable(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, PUT)], compress=True)
        self.table('000002.ldb', [(saved_key('b' * 22), 6, PUT)])
        with mock.patch.object(spotify_local, '_snappy', None):
            self.assertIsNone(spotify_local.saved_tracks())

    def test_a_record_split_across_log_blocks_is_joined(self):
        whole = batch(7, (PUT, saved_key('a' * 22)))
        self.log('000002.log', record(2, whole[:10]) + record(3, whole[10:30]) + record(4, whole[30:]))
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22})

    def test_a_batch_still_being_written_is_left_for_the_next_read(self):
        whole = record(1, batch(8, (PUT, saved_key('b' * 22))))
        self.log('000002.log', record(1, batch(7, (PUT, saved_key('a' * 22)))) + whole[:-5])
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22})

    def test_a_table_still_being_written_is_skipped(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, PUT)])
        self.log('000009.ldb', b'half a table')
        self.assertEqual(spotify_local.saved_tracks(), {'a' * 22})

    def test_each_table_is_parsed_once_and_forgotten_once_deleted(self):
        self.table('000001.ldb', [(saved_key('a' * 22), 5, PUT)])
        with mock.patch.object(spotify_local, '_table', wraps=spotify_local._table) as table:
            spotify_local.saved_tracks()
            spotify_local.saved_tracks()
        self.assertEqual(table.call_count, 1)
        os.remove(os.path.join(self.db, '000001.ldb'))
        self.assertEqual(spotify_local.saved_tracks(), frozenset())
        self.assertEqual(spotify_local._tables, {})

    def test_no_database_is_none(self):
        spotify_local.ROOTS[:] = [os.path.join(self.dir, 'nowhere')]
        self.assertIsNone(spotify_local.saved_tracks())
        self.assertIsNone(spotify_local.is_saved('spotify:track:' + 'a' * 22))


class DatabaseTest(SpotifyLocalTest):

    def test_the_account_written_to_last_is_read(self):
        other = os.path.join(self.dir, 'cache', 'spotify', 'Users', 'other-user', 'primary.ldb')
        os.makedirs(other)
        self.log('000002.log', b'')
        with open(os.path.join(other, '000002.log'), 'wb'):
            pass
        os.utime(os.path.join(self.db, '000002.log'), (1, 1))
        self.assertEqual(spotify_local.database(), other)

    def test_the_lookup_is_trusted_for_a_while(self):
        self.assertEqual(spotify_local.database(), self.db)
        with mock.patch.object(spotify_local.glob, 'glob') as glob:
            self.assertEqual(spotify_local.database(), self.db)
        glob.assert_not_called()


if __name__ == '__main__':
    unittest.main()
