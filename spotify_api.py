#!/usr/bin/env python3
"""Minimal Spotify Web API client for the conky widget's like button.

  spotify_api.py login    one-time browser login (PKCE; no client secret needed)
  spotify_api.py toggle   like/unlike the track currently playing in Spotify
  spotify_api.py status   print whether the current track is liked

The refresh token is kept in ~/.config/conky/spotify-token.json (mode 600).
The like state for the widget is written to ~/.cache/conky-nowplaying/liked ("1"/"0").
"""
import base64, hashlib, http.server, json, os, secrets, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request

CONF_DIR = os.path.expanduser('~/.config/conky')
CLIENT_ID_FILE = os.path.join(CONF_DIR, 'spotify-client-id')
TOKEN_FILE = os.path.join(CONF_DIR, 'spotify-token.json')
LIKED_FILE = os.path.expanduser('~/.cache/conky-nowplaying/liked')
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPES = 'user-library-read user-library-modify'


class NotLoggedIn(Exception):
    pass


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
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(tok, f)
    return tok


def login():
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
    url = 'https://api.spotify.com/v1' + path + ('?' + urllib.parse.urlencode(params) if params else '')
    req = urllib.request.Request(url, method=method, headers={'Authorization': 'Bearer ' + access_token()})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read()
        return json.loads(body) if body else None


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
    return bool(api('GET', '/me/library/contains', uris=uri)[0])


def write_liked(value):
    os.makedirs(os.path.dirname(LIKED_FILE), exist_ok=True)
    with open(LIKED_FILE + '.tmp', 'w') as f:
        f.write('' if value is None else ('1' if value else '0'))
    os.replace(LIKED_FILE + '.tmp', LIKED_FILE)


def toggle():
    uri = current_track_uri()
    if not uri:
        return
    liked = is_liked(uri)
    write_liked(not liked)  # show the new state right away
    try:
        api('DELETE' if liked else 'PUT', '/me/library', uris=uri)
    except Exception:
        write_liked(liked)
        raise


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'login':
        login()
    elif cmd == 'toggle':
        toggle()
    elif cmd == 'status':
        uri = current_track_uri()
        print(uri, is_liked(uri) if uri else None)
    else:
        sys.exit(__doc__)
