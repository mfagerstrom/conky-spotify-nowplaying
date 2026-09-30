"""Reads the Spotify desktop app's own Liked Songs set from disk, without the Web API.

The app keeps its library in a LevelDB under ~/.cache/spotify/Users/<name>-user/primary.ldb,
with one key per saved track:

  !cit#cit# <len>spotify:track:<id> # <len>spotify:user:<name>:collection # ...

A like in the app writes that key and an unlike writes a deletion for it, into the
database's write-ahead log first, so the set follows the app's own heart within moments.

The database is only ever read, never locked or written: the app holds LevelDB's lock
while it runs, so this reads the files directly. Table files (.ldb, or .sst from older
LevelDB versions) never change once written, so each is parsed once and its entries kept
by name; the log is read again on every call. A file the app deletes or is still writing
is skipped until the next call.
"""
import ctypes, glob, os, re, struct, threading, time

# Where the app keeps its cache: the apt and tarball builds, then the snap and flatpak.
ROOTS = ['~/.cache/spotify', '~/snap/spotify/common/.cache/spotify', '~/.var/app/com.spotify.Client/cache/spotify']
FIND_SECONDS = 60                             # how long a found (or missing) database is trusted
_SAVED = re.compile(rb'!cit#cit#.spotify:track:([0-9A-Za-z]{22})#.spotify:user:.+:collection#', re.S)

_lock = threading.Lock()
_found = (0.0, None)                          # (when looked up, database directory or None)
_read = (0.0, False)                          # (when saved_tracks last ran, whether it read the set)
_tables = {}                                  # (path, size, mtime) -> {track id: (seq, saved)}

try:
    _snappy = ctypes.CDLL('libsnappy.so.1')
    _snappy.snappy_uncompressed_length.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                                   ctypes.POINTER(ctypes.c_size_t)]
    _snappy.snappy_uncompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
                                          ctypes.POINTER(ctypes.c_size_t)]
except OSError:
    _snappy = None


class _Unreadable(Exception):
    """The database cannot be read here at all, rather than one file of it for now."""


def database():
    """The directory of the app's library database, or None when there is none. With
    several accounts on this machine, the one the app wrote to last."""
    global _found
    now = time.time()
    if now - _found[0] < FIND_SECONDS:
        return _found[1]
    dirs = [d for root in ROOTS
            for d in glob.glob(os.path.join(os.path.expanduser(root), 'Users', '*', 'primary.ldb'))]
    newest = max(dirs, key=_last_write, default=None)
    _found = (now, newest)
    return newest


def _last_write(d):
    return max((os.path.getmtime(p) for p in glob.glob(os.path.join(d, '*.log'))), default=0.0)


def _unsnappy(data):
    n = ctypes.c_size_t()
    if _snappy.snappy_uncompressed_length(data, len(data), ctypes.byref(n)):
        raise ValueError('bad snappy block')
    out = ctypes.create_string_buffer(n.value)
    if _snappy.snappy_uncompress(data, len(data), out, ctypes.byref(n)):
        raise ValueError('bad snappy block')
    return out.raw[:n.value]


def _varint(b, i):
    value = shift = 0
    while True:
        byte = b[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def _block(f, handle):
    offset, i = _varint(handle, 0)
    size, _ = _varint(handle, i)
    f.seek(offset)
    raw = f.read(size + 1)                    # the block, then its compression type (a CRC follows)
    if len(raw) != size + 1:
        raise ValueError('short block')
    if raw[size] == 1:
        if not _snappy:
            raise _Unreadable('libsnappy is not installed')
        return _unsnappy(raw[:size])
    return raw[:size]


def _entries(block):
    """(key, value) pairs of a table block, whose keys share prefixes with the one before."""
    restarts = struct.unpack('<I', block[-4:])[0]
    end, i, key = len(block) - 4 - 4 * restarts, 0, b''
    while i < end:
        shared, i = _varint(block, i)
        unshared, i = _varint(block, i)
        length, i = _varint(block, i)
        key = key[:shared] + block[i:i + unshared]
        i += unshared
        yield key, block[i:i + length]
        i += length


def _note(found, key, seq, saved):
    m = _SAVED.match(key)
    if m and seq > found.get(m.group(1), (-1,))[0]:
        found[m.group(1)] = (seq, saved)


def _table(path):
    """{track id: (sequence number, saved)} from one table file. Its index block names
    each data block by a key at or past the block's last one, so only the blocks that can
    hold saved-track keys are read."""
    found = {}
    with open(path, 'rb') as f:
        f.seek(-48, os.SEEK_END)
        footer = f.read(48)
        if footer[40:] != b'\x57\xfb\x80\x8b\x24\x75\x47\xdb':
            raise ValueError('not a finished table')
        i = _varint(footer, _varint(footer, 0)[1])[1]      # past the metaindex handle
        previous = b''
        for last, handle in _entries(_block(f, footer[i:])):
            if last[:-8] >= b'!cit#cit#' and previous[:-8] < b'!cit#cit$':
                for key, _ in _entries(_block(f, handle)):
                    tag = struct.unpack('<Q', key[-8:])[0]
                    _note(found, key[:-8], tag >> 8, tag & 0xFF == 1)
            previous = last
    return found


def _log(path):
    """{track id: (sequence number, saved)} from the write-ahead log: 32 KiB blocks of
    records, which carry batches of puts and deletes, split across blocks where needed.
    A batch the app is still writing at the end is left for the next read."""
    with open(path, 'rb') as f:
        data = f.read()
    found, pending, i = {}, b'', 0
    while i + 7 <= len(data):
        left = 32768 - i % 32768
        if left < 7:                          # a block's tail too short for a header is padding
            i += left
            continue
        length, kind = struct.unpack('<H', data[i + 4:i + 6])[0], data[i + 6]
        payload = data[i + 7:i + 7 + length]
        i += 7 + length
        if len(payload) < length:
            break
        if kind in (1, 2):                    # a whole batch, or its first part
            pending = payload
        elif kind in (3, 4):                  # a middle or last part
            pending += payload
        if kind not in (1, 4):
            continue
        batch, pending = pending, b''
        try:
            seq, count = struct.unpack('<QI', batch[:12])
            j = 12
            for n in range(count):
                op = batch[j]
                size, j = _varint(batch, j + 1)
                key = batch[j:j + size]
                j += size
                if op == 1:
                    size, j = _varint(batch, j)
                    j += size
                _note(found, key, seq + n, op == 1)
        except (IndexError, struct.error):
            continue
    return found


def readable():
    """Whether the app's Liked Songs can be read here: whether the last read worked, with
    a read of its own when there has been none for FIND_SECONDS."""
    if time.time() - _read[0] >= FIND_SECONDS:
        saved_tracks()
    return _read[1]


def saved_tracks():
    """The track IDs saved in the app's Liked Songs, or None when the app's database
    cannot be found or read."""
    global _read
    saved = _saved_tracks()
    _read = (time.time(), saved is not None)
    return saved


def _saved_tracks():
    d = database()
    if not d:
        return None
    with _lock:
        try:
            names = os.listdir(d)
        except OSError:
            return None
        merged, seen = {}, set()
        for name in names:
            path = os.path.join(d, name)
            try:
                if name.endswith(('.ldb', '.sst')):      # .sst: tables from older LevelDB
                    st = os.stat(path)
                    key = (path, st.st_size, st.st_mtime)
                    seen.add(key)
                    if key not in _tables:
                        _tables[key] = _table(path)
                    found = _tables[key]
                elif name.endswith('.log'):
                    found = _log(path)
                else:
                    continue
            except _Unreadable:
                return None
            except (OSError, ValueError, IndexError, struct.error):
                continue                      # deleted by a compaction, or still being written
            for track, entry in found.items():
                if entry[0] > merged.get(track, (-1,))[0]:
                    merged[track] = entry
        for key in set(_tables) - seen:
            del _tables[key]
    return frozenset(t.decode() for t, (_, saved) in merged.items() if saved)


def is_saved(uri):
    """Whether the app has this spotify:track: URI in Liked Songs, or None when its
    database cannot be read."""
    saved = saved_tracks()
    return None if saved is None else uri.rsplit(':', 1)[1] in saved
