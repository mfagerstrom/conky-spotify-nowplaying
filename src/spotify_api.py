#!/usr/bin/env python3
"""Minimal Spotify Web API client for the conky widget's like button.

  spotify_api.py login    one-time browser login (PKCE; no client secret needed)
  spotify_api.py toggle   like/unlike the track currently playing in Spotify
  spotify_api.py status   print whether the current track is liked

The refresh token is kept in ~/.config/conky-spotify-nowplaying/spotify-token.json (mode 600).
The like state for the widget is written to ~/.cache/conky-spotify-nowplaying/liked ("1"/"0").
"""
import base64, fcntl, hashlib, http.server, json, os, secrets, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request

CONF_DIR = os.path.expanduser('~/.config/conky-spotify-nowplaying')
CACHE_DIR = os.path.expanduser('~/.cache/conky-spotify-nowplaying')
CLIENT_ID_FILE = os.path.join(CONF_DIR, 'spotify-client-id')
TOKEN_FILE = os.path.join(CONF_DIR, 'spotify-token.json')
LIKED_FILE = os.path.join(CACHE_DIR, 'liked')
TOGGLED_FILE = LIKED_FILE + '-toggled'   # mtime = last click; pollers back off after it
LOCK_FILE = LIKED_FILE + '.lock'
LIBRARY_FILE = os.path.join(CACHE_DIR, 'library.json')
BACKOFF_FILE = os.path.join(CACHE_DIR, 'rate-limited-until')   # epoch seconds, from Retry-After
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPES = 'user-library-read user-library-modify'


class NotLoggedIn(Exception):
    pass


class RateLimited(Exception):
    """Spotify answered 429; no requests are sent until `until` (epoch seconds)."""
    def __init__(self, until):
        super().__init__(f'rate limited for {until - time.time():.0f} more seconds')
        self.until = until


def rate_limited_until():
    try:
        until = float(open(BACKOFF_FILE).read())
    except (OSError, ValueError):
        return None
    return until if until > time.time() else None


def client_id():
    return open(CLIENT_ID_FILE).read().strip()


def _post_token(data):
    req = urllib.request.Request('https://accounts.spotify.com/api/token',
                                 data=urllib.parse.urlencode(data).encode(),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _store(tok, old_refresh=None):
    tok['expires_at'] = time.time() + tok.get('expires_in', 3600) - 60
    tok.setdefault('refresh_token', old_refresh)  # refresh responses may omit it
    os.makedirs(CONF_DIR, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(tok, f)
    return tok


SETUP_URL = 'https://github.com/mfagerstrom/conky-spotify-nowplaying#like-button-optional'


def login():
    if not os.path.exists(CLIENT_ID_FILE):
        # Started from the widget's heart or the app menu, so there may be no terminal.
        msg = ('The like button needs a Spotify app Client ID first. Opening the setup steps; '
               f'save the ID to {CLIENT_ID_FILE}, then click the heart again.')
        print(msg)
        subprocess.run(['notify-send', '-a', 'Spotify Now Playing', 'Spotify Now Playing', msg],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(['xdg-open', SETUP_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.exit(1)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(16)
    url = 'https://accounts.spotify.com/authorize?' + urllib.parse.urlencode({
        'client_id': client_id(), 'response_type': 'code', 'redirect_uri': REDIRECT_URI,
        'scope': SCOPES, 'code_challenge_method': 'S256', 'code_challenge': challenge, 'state': state})

    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if urllib.parse.urlparse(self.path).path != '/callback':
                self.send_response(404); self.end_headers(); return
            ok = q.get('state', [''])[0] == state and 'code' in q
            result['code'] = q['code'][0] if ok else None
            result['error'] = q.get('error', [''])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            msg = 'Logged in &mdash; you can close this tab.' if ok else 'Login failed &mdash; check the terminal.'
            self.wfile.write(f'<html><body style="font-family:sans-serif"><h2>{msg}</h2></body></html>'.encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 8888), Handler)
    print('Opening Spotify login in your browser. If it does not open, visit:\n' + url)
    subprocess.Popen(['xdg-open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while 'code' not in result:
        server.handle_request()
    if not result['code']:
        sys.exit(f"Login failed: {result['error'] or 'state mismatch'}")
    _store(_post_token({'grant_type': 'authorization_code', 'code': result['code'],
                        'redirect_uri': REDIRECT_URI, 'client_id': client_id(),
                        'code_verifier': verifier}))
    print('Logged in. The widget can now show and toggle likes.')


def access_token():
    try:
        tok = json.load(open(TOKEN_FILE))
    except (OSError, ValueError):
        raise NotLoggedIn
    if time.time() >= tok.get('expires_at', 0):
        tok = _store(_post_token({'grant_type': 'refresh_token', 'refresh_token': tok['refresh_token'],
                                  'client_id': client_id()}), tok['refresh_token'])
    return tok['access_token']


def api(method, path, **params):
    # Requests made while rate limited can extend the penalty, so don't send any.
    until = rate_limited_until()
    if until:
        raise RateLimited(until)
    url = 'https://api.spotify.com/v1' + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, method=method, headers={'Authorization': 'Bearer ' + access_token()})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        until = time.time() + int(e.headers.get('Retry-After') or 60)
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(BACKOFF_FILE, 'w') as f:
            f.write(str(until))
        raise RateLimited(until)


def current_track_uri():
    """spotify:track:<id> for the playing track, or None (ads, podcasts, Spotify closed)."""
    out = subprocess.run(['playerctl', '-p', 'spotify', 'metadata', 'mpris:trackid'],
                         capture_output=True, text=True).stdout.strip()
    if out.startswith('/com/spotify/track/'):
        return 'spotify:track:' + out.rsplit('/', 1)[1]
    if out.startswith('spotify:track:'):
        return out
    return None


def is_liked(uri):
    """Whether this exact track is saved (see is_liked_any for what the heart shows)."""
    return bool(api('GET', '/me/library/contains', uris=uri)[0])


# Spotify's app shows a song as liked when *any* release of it is saved (single vs album
# version, deluxe editions, ...), but the Web API only checks the exact track ID. To match
# the app we keep an index of Liked Songs keyed by (title, first artist).

_track_keys = {}


def _key(name, artist):
    return f'{name.strip().lower()}\t{artist.strip().lower()}'


def track_key(uri):
    if uri not in _track_keys:
        t = api('GET', '/tracks/' + uri.rsplit(':', 1)[1])
        _track_keys[uri] = _key(t['name'], t['artists'][0]['name'])
    return _track_keys[uri]


def _load_library():
    try:
        return json.load(open(LIBRARY_FILE))
    except (OSError, ValueError):
        return None


def _save_library(lib):
    os.makedirs(os.path.dirname(LIBRARY_FILE), exist_ok=True)
    with open(LIBRARY_FILE + '.tmp', 'w') as f:
        json.dump(lib, f)
    os.replace(LIBRARY_FILE + '.tmp', LIBRARY_FILE)


def _index_page(lib, items):
    """Adds saved tracks to the index; returns how many were new."""
    new = 0
    for item in items:
        t = item.get('track')
        if not t or not t.get('uri'):
            continue
        uris = lib['keys'].setdefault(_key(t['name'], t['artists'][0]['name']), [])
        if t['uri'] not in uris:
            uris.append(t['uri'])
            new += 1
    return new


def refresh_library(max_age=24 * 3600):
    """Full rescan when the index is missing, stale, or the library shrank (unlikes made
    elsewhere); otherwise just pick up the newest likes from the first page."""
    lib = _load_library()
    first = api('GET', '/me/tracks', limit=50)
    if lib and time.time() - lib['scanned'] < max_age and first['total'] >= lib['total']:
        _index_page(lib, first['items'])
        lib['total'] = first['total']
        _save_library(lib)
        return lib
    lib = {'scanned': time.time(), 'total': first['total'], 'keys': {}}
    page = first
    while True:
        _index_page(lib, page['items'])
        if not page.get('next'):
            break
        page = api('GET', '/me/tracks', limit=50, offset=page['offset'] + page['limit'])
    _save_library(lib)
    return lib


def is_liked_any(uri):
    """What the heart shows: this track, or any same-titled release by the same artist."""
    if is_liked(uri):
        return True
    lib = _load_library()
    return bool(lib and lib['keys'].get(track_key(uri)))


def write_liked(value):
    os.makedirs(os.path.dirname(LIKED_FILE), exist_ok=True)
    with open(LIKED_FILE + '.tmp', 'w') as f:
        f.write('' if value is None else ('1' if value else '0'))
    os.replace(LIKED_FILE + '.tmp', LIKED_FILE)


def _notify_rate_limited(until):
    hours = (until - time.time()) / 3600
    wait = f'{hours:.1f} hours' if hours >= 1 else f'{max(hours * 60, 1):.0f} minutes'
    subprocess.run(['notify-send', '-a', 'Spotify Now Playing', 'Spotify Now Playing',
                    f'Spotify is rate limiting the like button; it will work again in about {wait}.'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def toggle():
    """Flip the heart immediately, then make Spotify match it.

    The new state is taken from what the widget shows (the liked file), not from a
    network round trip, so the heart reacts instantly. Toggles are serialized with a
    lock and each one pushes the *current* displayed state, so fast repeated clicks
    always leave Spotify matching the heart."""
    uri = current_track_uri()
    if not uri:
        return
    until = rate_limited_until()
    if until:
        _notify_rate_limited(until)
        return
    os.makedirs(os.path.dirname(LIKED_FILE), exist_ok=True)
    with open(LOCK_FILE, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            shown = open(LIKED_FILE).read().strip() if os.path.exists(LIKED_FILE) else ''
            liked = (shown == '1') if shown in ('0', '1') else is_liked_any(uri)
            write_liked(not liked)
            open(TOGGLED_FILE, 'w').close()
            _push_like(uri, open(LIKED_FILE).read().strip() == '1')
        except RateLimited as e:
            write_liked(None)   # unknown: we couldn't read or change it
            _notify_rate_limited(e.until)


def _push_like(uri, want):
    lib = _load_library() or {'scanned': 0, 'total': 0, 'keys': {}}
    key = track_key(uri)
    if want:
        api('PUT', '/me/library', uris=uri)
        lib['keys'].setdefault(key, [])
        if uri not in lib['keys'][key]:
            lib['keys'][key].append(uri)
    else:
        # unliking removes every saved release of the song, or the heart would stay on
        uris = sorted(set(lib['keys'].pop(key, [])) | {uri})
        api('DELETE', '/me/library', uris=','.join(uris))
    _save_library(lib)


def recently_toggled(seconds=15):
    try:
        return time.time() - os.path.getmtime(TOGGLED_FILE) < seconds
    except OSError:
        return False


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'login':
        login()
    elif cmd == 'toggle':
        toggle()
    elif cmd == 'status':
        uri = current_track_uri()
        print(uri, 'exact:', is_liked(uri) if uri else None, 'any release:', is_liked_any(uri) if uri else None)
    else:
        sys.exit(__doc__)
