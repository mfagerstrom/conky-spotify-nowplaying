#!/usr/bin/env python3
"""Keep track of everything a session is waiting on, with as few reads as possible.

A session records each thing it waits on in a ledger, one line per row, and reads
them all back through this script instead of watching each one. Ported from the
PlaywrightTesting repository's scripts/catchup.py, less its GitHub Actions run
tallies: this repository has no workflows, so what a session waits on here is a
Launchpad build, a pull request merge, or a blocking issue.

usage:
  scripts/catchup.py add-lp <ledger> <version> [label]  wait for Launchpad to build and
                                                        publish a source version
  scripts/catchup.py add-pr <ledger> <pr> [label]       wait for a pull request to merge
  scripts/catchup.py add-issue <ledger> <issue> [label] wait for a blocking issue to close
  scripts/catchup.py check <ledger>                     read the Launchpad rows, once
  scripts/catchup.py wait  <ledger> [--interval 180] [--all]
                                                        wait until a row finishes
                                                        (--all: until every row has)

A Launchpad row is read from Launchpad's public API, which needs no sign-in and
does not count against the GitHub rate limit. It settles once the source is
Published and every build is Successfully built, or once a build fails, or the
source is superseded or deleted. A version that never shows up in the archive
within LP_MISSING seconds of being recorded settles as likely rejected, since
Launchpad sends its rejection mail to the no-reply maintainer address.

A pull request row is not polled while the push is live. `add-pr` reads it once,
to record it and to settle it at once if it has already merged or closed. After
that, the forwarder's pull_request close notice settles it, with no API call.
When a forwarder connects, every waiter reads each open pull request once, for a
close sent while nobody was listening, and a read that fails there is tried once
more thirty seconds later. While no forwarder is live, every timer check also
reads each open pull request, so a merge during an outage is still caught within
one timer interval.

An issue row is the issue a blocked session waits on. `add-issue` reads it once,
and settles it at once if it is already closed. After that it settles the way a
pull request row does, and every timer check also reads each open issue, push or
no push, since a blocked session can wait for days.

`wait` is the single watcher a session may run, and it never checks more often
than every three minutes on a timer. It exits as soon as any row finishes, so the
session can act on that result while the rest are still out, and the session then
runs it again on the rows still open. `--all` waits for every row instead. The
add commands and `check` take a lock beside the ledger to rewrite it, so a row
recorded while `wait` is checking keeps its place.

A `sidebar:` line names the move the session makes in the Code tab sidebar. `add-lp`
prints one when its build is the only one open, and `check`, or a `wait` that exits
on a finished build, prints one when no build is left open. Pull request and issue
rows never count as builds. A `wait` that exits on a closed pull request or a
closed issue prints the move that goes with it.

`wait` also listens for GitHub's pull_request and issues events through
`gh webhook forward` (the cli/gh-webhook extension), so a merge or a close is seen
within seconds. The extension is not installed from here: this machine installs
software through apt only, so when it is missing `wait` says so and reads on the
timer. GitHub accepts one forwarder hook per repository, so the waiters on a
machine share one. The waiter holding a lock under ~/.cache/catchup runs the
forwarder, reads its output on a thread, and writes each close to a shared events
file as one short line. Every waiter reads that file, which costs no API calls.
Past a megabyte the events file is renamed to events.jsonl.old, and readers follow
the rename.

gh runs the extension as a child process, and a child that outlives its waiter
keeps the hook and blocks the next forwarder. The lock proves no live waiter owns
such a process, so the holder stops any it finds before starting a forwarder. It
also deletes the last hook a forwarder on this machine made, if GitHub has not
dropped it already, and any forwarder hook GitHub has marked inactive. An active
hook made on another machine is left alone: the forwarder here is refused, retries
after a delay that doubles up to ten minutes, and `wait` reads on the timer
meanwhile. Every five minutes the holder confirms its hook is still on the
repository and restarts the forwarder when it is gone.

While the push is live and no Launchpad row is open, the timer runs every fifteen
minutes and only catches a lost notice. While a Launchpad row is open, or the push
is down, it runs every three minutes.

The ledger is tab separated: label, key, link, state, tally. The key is
lp:<version>, pr:<number> or issue:<number>, and the link is the page a reader
follows for that row.
"""
import calendar
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

MIN_INTERVAL = 180
PUSH_FALLBACK_INTERVAL = 900
# How often a waiter reads the local events file; no API call is made.
EVENT_POLL = 5
# A pull request or issue read that failed on connect is tried once more after this.
EVENT_RECHECK = 30
# An events line is about 250 bytes, so the file holds about 4,000 events.
EVENTS_ROTATE = 1_000_000
SEEN_LIMIT = 10_000
FORWARDER_RESTART = 60
FORWARDER_RESTART_MAX = 600
HOOK_CHECK = 300
EXTENSION = 'cli/gh-webhook'
PUSH_DIR = os.path.expanduser('~/.cache/catchup')
FIELDS = ('label', 'key', 'link', 'state', 'tally')
PR_PREFIX = 'pr:'
ISSUE_PREFIX = 'issue:'
LP_PREFIX = 'lp:'
FORWARDED_EVENTS = 'pull_request,issues'
SOURCE = 'conky-spotify-nowplaying'
PPA = '~mfagerstrom/+archive/ubuntu/conky-spotify-nowplaying'
LP_API = f'https://api.launchpad.net/devel/{PPA}'
LP_PAGE = f'https://launchpad.net/{PPA}/+packages'
LP_TIMEOUT = 30
# Accepting an upload takes a few minutes; one that has not shown up by now was rejected.
LP_MISSING = 45 * 60
LP_BUILT = 'Successfully built'
# Build states that will not turn into a successful build on their own.
LP_FAILED = ('Failed to build', 'Dependency wait', 'Chroot problem', 'Failed to upload',
             'Cancelled build', 'Build for superseded Source')
LP_GONE = ('Superseded', 'Deleted', 'Obsolete')


def gh(*args, timeout=None):
    try:
        r = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'no answer in {timeout}s') from None
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def launchpad(url):
    """One read of Launchpad's public API."""
    try:
        with urllib.request.urlopen(url, timeout=LP_TIMEOUT) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f'Launchpad read failed: {e}') from None


def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''


def write_file(path, text):
    """Replace a file whole, so a reader never sees it half written."""
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        f.write(text)
    os.replace(tmp, path)


def locked(path):
    """The ledger's lock, held while a ledger is read and rewritten."""
    lock = open(path + '.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def read_ledger(path):
    rows = []
    if os.path.exists(path):
        for line in open(path):
            if line.strip():
                parts = line.rstrip('\n').split('\t') + [''] * len(FIELDS)
                rows.append(dict(zip(FIELDS, parts)))
    return rows


def write_ledger(path, rows):
    write_file(path, ''.join('\t'.join(r[k] for k in FIELDS) + '\n' for r in rows))


def record(path, row):
    """Add a row, replacing any earlier row with the same key."""
    with locked(path):
        rows = [r for r in read_ledger(path) if r['key'] != row['key']]
        rows.append(row)
        write_ledger(path, rows)


def open_rows(path):
    return {r['key'] for r in read_ledger(path) if r['state'] != 'done'}


def done_rows(path):
    return {r['key'] for r in read_ledger(path) if r['state'] == 'done'}


def open_builds(path):
    return {key for key in open_rows(path) if is_lp(key)}


def is_pr(key):
    return key.startswith(PR_PREFIX)


def is_issue(key):
    return key.startswith(ISSUE_PREFIX)


def is_lp(key):
    return key.startswith(LP_PREFIX)


def utc(seconds=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(seconds))


def since(stamp):
    """Seconds since a utc() stamp, or 0 when the stamp does not parse."""
    try:
        return time.time() - calendar.timegm(time.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ'))
    except ValueError:
        return 0


def sidebar_cue(path):
    """Name the sidebar move once the ledger holds builds and none is still open."""
    if any(is_lp(r['key']) for r in read_ledger(path)) and not open_builds(path):
        print('sidebar: no Launchpad builds open; move this session from Tests Running '
              'to Working')


def handed_back(path, before, every):
    """Whether wait stops now: a row finished since it started, unless it waits for all."""
    if every:
        return False
    return report_finished(path, before)


def report_finished(path, before):
    """Print the rows finished since wait started and the sidebar move. False if none."""
    finished = done_rows(path) - before
    if not finished:
        return False
    left = len(open_rows(path))
    print(f'wait: {len(finished)} finished; {left} still open'
          + ('; act on the result, then run wait again' if left else ''))
    if any(is_lp(key) for key in finished):
        sidebar_cue(path)
    if any(is_pr(key) for key in finished):
        print('sidebar: a pull request closed; move this session to Working if it still '
              'holds work (open rows, running agents, an unfinished task), to Needs Review '
              'if another pull request of its own waits on the user, or to Completed once '
              'nothing is left')
    if any(is_issue(key) for key in finished):
        print('sidebar: a blocking issue closed; move this session from Blocked to Working, '
              'then start the deferred task again from its first step')
    return True


def settle_row(path, key, state, tally_text):
    """Mark an open row done. False when no open row has that key."""
    with locked(path):
        rows = read_ledger(path)
        hit = [r for r in rows if r['key'] == key and r['state'] != 'done']
        for r in hit:
            r['state'], r['tally'] = 'done', tally_text
        write_ledger(path, rows)
    kind, name = key.split(':', 1)
    for r in hit:
        print(f'done: {r["label"]} - {r["link"]}\n  {tally_text}')
        print(f'{kind}: {name} {state}')
    return bool(hit)


# Launchpad rows

def read_lp(version, added):
    """One read of a source version in the PPA: (link, status, shut), where shut is
    (state, tally) once the row is settled and None while it is still going."""
    found = launchpad(f'{LP_API}?ws.op=getPublishedSources&source_name={SOURCE}'
                      f'&exact_match=true&version={version}')['entries']
    if not found:
        if added and since(added) > LP_MISSING:
            return LP_PAGE, 'missing', ('rejected', f'{version} never appeared in the archive '
                                        f'in {LP_MISSING // 60}m; the upload was likely '
                                        'rejected (a reused version or a bad signature)')
        return LP_PAGE, 'not in the archive yet', None
    source = found[0]
    if source['status'] in LP_GONE:
        return LP_PAGE, source['status'], ('gone', f'source {source["status"]}')
    builds = launchpad(f'{source["self_link"]}?ws.op=getBuilds')['entries']
    link = builds[0]['web_link'] if len(builds) == 1 else LP_PAGE
    for b in builds:
        if b['buildstate'] in LP_FAILED:
            return b['web_link'], b['buildstate'], (
                'failed', f'{b["title"]}: {b["buildstate"]} - {b.get("build_log_url") or ""}')
    states = ', '.join(sorted({b['buildstate'] for b in builds})) or 'no builds yet'
    status = f'source {source["status"]}; {states}'
    if source['status'] == 'Published' and builds and all(
            b['buildstate'] == LP_BUILT for b in builds):
        return link, status, ('published', status)
    return link, status, None


def add_lp(path, version, label):
    link, status, shut = read_lp(version, utc())
    key = f'{LP_PREFIX}{version}'
    record(path, {'label': label, 'key': key, 'link': link, 'state': status,
                  'tally': f'added {utc()}'})
    print(f'waiting: {label} - {link}')
    if shut:
        settle_row(path, key, *shut)
    elif open_builds(path) == {key}:
        print('sidebar: a Launchpad build is open; move this session to Tests Running')


def check(path):
    """Read every open Launchpad row once. Returns how many rows are still open."""
    rows = [r for r in read_ledger(path) if r['state'] != 'done' and is_lp(r['key'])]
    updates, shut = {}, {}
    for r in rows:
        try:
            link, status, done = read_lp(r['key'][len(LP_PREFIX):],
                                         r['tally'].removeprefix('added '))
        except (RuntimeError, ValueError, KeyError) as e:
            print(f'{r["label"]}: {e}; will retry next check')
            continue
        updates[r['key']] = (link, status)
        if done:
            shut[r['key']] = done
    # A row can be recorded while the reads above are out, so the ledger is read again here.
    with locked(path):
        rows = read_ledger(path)
        for r in rows:
            if r['key'] in updates and r['state'] != 'done':
                r['link'], r['state'] = updates[r['key']]
        write_ledger(path, rows)
    for key, done in shut.items():
        settle_row(path, key, *done)
    rows = read_ledger(path)
    waiting = [r for r in rows if r['state'] != 'done']
    summary = '; '.join(f'{r["label"]}: {r["state"]}' for r in waiting) or 'none'
    print(f'{len(rows) - len(waiting)} of {len(rows)} done; still waiting: {summary}')
    return len(waiting)


# Pull request and issue rows

def pr_tally(state, sha, merged_at, closed_at):
    if state == 'merged':
        return f'merged {(sha or "")[:9]} at {merged_at}'
    return f'closed without merging at {closed_at}'


def issue_tally(reason, closed_at):
    """What a closed issue row reads: completed, not planned, or duplicate, and when."""
    reason = (reason or 'closed').lower().replace('_', ' ')
    return f'closed as {reason} at {closed_at}'


def notice_tally(event):
    """The tally for a pull request or issue row, from its close notice."""
    if event.get('event') == 'issue':
        return issue_tally(event.get('reason'), event.get('closed_at'))
    return pr_tally(event['state'], event.get('sha'), event.get('merged_at'),
                    event.get('closed_at'))


def read_pr(number):
    """One read of a pull request: its url, and (state, tally) once it is shut."""
    pr = json.loads(gh('pr', 'view', str(number), '--json',
                       'url,state,mergedAt,closedAt,mergeCommit'))
    state = pr['state'].lower()
    shut = None
    if state in ('merged', 'closed'):
        sha = (pr.get('mergeCommit') or {}).get('oid')
        shut = (state, pr_tally(state, sha, pr.get('mergedAt'), pr.get('closedAt')))
    return pr, shut


def add_pr(path, number, label):
    pr, shut = read_pr(number)
    key = f'{PR_PREFIX}{number}'
    # An open row keeps the time it was recorded, so an older close notice for the same
    # pull request, from before a reopen, is not taken for this one.
    record(path, {'label': label, 'key': key, 'link': pr['url'], 'state': 'open',
                  'tally': f'added {utc()}'})
    print(f'waiting for merge: {label} - {pr["url"]}')
    if shut:
        settle_row(path, key, *shut)


def read_issue(number):
    """One read of an issue: its url, and (state, tally) once it is closed."""
    issue = json.loads(gh('api', f'repos/{{owner}}/{{repo}}/issues/{number}'))
    issue['url'] = issue['html_url']
    shut = None
    if issue['state'].lower() == 'closed':
        shut = ('closed', issue_tally(issue.get('state_reason'), issue.get('closed_at')))
    return issue, shut


def add_issue(path, number, label):
    issue, shut = read_issue(number)
    key = f'{ISSUE_PREFIX}{number}'
    record(path, {'label': label, 'key': key, 'link': issue['url'], 'state': 'open',
                  'tally': f'added {utc()}'})
    print(f'waiting for close: {label} - {issue["url"]}')
    if shut:
        settle_row(path, key, *shut)


def reconcile_rows(path, keys=None, issues_only=False):
    """Read each open pull request and issue row once, for a notice nobody heard.

    Given keys, reads only those rows. With issues_only, skips the pull requests.
    Returns the keys whose read failed.
    """
    failed = set()
    for r in read_ledger(path):
        key = r['key']
        if r['state'] == 'done' or is_lp(key) or (issues_only and not is_issue(key)):
            continue
        if keys is not None and key not in keys:
            continue
        number = key.split(':', 1)[1]
        try:
            _, shut = read_issue(number) if is_issue(key) else read_pr(number)
        except (RuntimeError, ValueError, KeyError) as e:
            print(f'{r["label"]}: {"issue" if is_issue(key) else "pull request"} '
                  f'read failed: {e}')
            failed.add(key)
            continue
        if shut:
            settle_row(path, key, *shut)
    return failed


# The shared forwarder

def installed():
    """Whether gh has the forwarder extension. It is never installed from here."""
    try:
        if EXTENSION in gh('extension', 'list', timeout=60):
            return True
    except RuntimeError as e:
        print(f'push: could not list gh extensions: {e}')
        return False
    print(f'push: {EXTENSION} is not installed; install it by hand to get push notices')
    return False


class Push:
    """This waiter's share of the pull_request and issues event stream on this machine."""

    def __init__(self):
        self.proc = self.hook = self.handle = None
        self.holder, self.marked, self.error, self.threads = False, '', '', []
        self.started = self.retry_at = self.checked = 0.0
        self.backoff = FORWARDER_RESTART
        # The latest close notice for each row, by key.
        self.seen = {}
        # Set when GitHub gave no answer, so wait tries again on its next timer check.
        self.retryable = False
        self.usable = installed()
        if not self.usable:
            return
        try:
            self.repo = gh('repo', 'view', '--json', 'nameWithOwner',
                           '-q', '.nameWithOwner').strip()
        except RuntimeError as e:
            print(f'push: could not reach GitHub, push is off for now: {e}')
            self.usable, self.retryable = False, True
            return
        base = os.path.join(PUSH_DIR, self.repo.replace('/', '__'))
        os.makedirs(base, exist_ok=True)
        self.events = os.path.join(base, 'events.jsonl')
        self.state = os.path.join(base, 'forwarder.state')
        self.hook_file = os.path.join(base, 'forwarder.hook')
        self.lock = open(os.path.join(base, 'forwarder.lock'), 'a')
        # A row can be recorded after its close notice landed, so the files are read whole.
        try:
            with open(self.events + '.old', 'rb') as f:
                for line in f:
                    self.fold(line)
        except FileNotFoundError:
            pass
        self.read()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def tend(self):
        """Hold the lock and keep a forwarder running under it. True while notices arrive."""
        if not self.usable:
            return False
        if not self.holder:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return read_file(self.state) == 'live'
            self.holder = True
            print(f'push: this waiter holds the forwarder for {self.repo}')
        now = time.time()
        if self.running() and now - self.checked >= HOOK_CHECK:
            self.checked = now
            if self.hook is None or self.hook_exists(self.hook) is False:
                print('push: the forwarder has no hook on the repository')
                self.halt()
        if self.running():
            self.mark('live' if self.hook else 'starting')
            return self.hook is not None
        if self.proc:
            lived = now - self.started
            print(f'push: forwarder stopped after {lived:.0f}s with {self.proc.returncode}: '
                  f'{self.error or "no error message"}')
            self.halt()
            self.proc = None
            # A forwarder refused at once, as when another machine holds the hook, waits
            # twice as long after each refusal.
            if lived >= FORWARDER_RESTART:
                self.backoff = FORWARDER_RESTART
            self.retry_at = now + self.backoff
            self.backoff = min(self.backoff * 2, FORWARDER_RESTART_MAX)
        if now < self.retry_at:
            self.mark('down')
            return False
        self.start()
        return False

    def mark(self, state):
        if state != self.marked:
            write_file(self.state, state)
            self.marked = state

    def start(self):
        orphan = f'extensions/gh-webhook/gh-webhook forward --repo {self.repo} '
        if subprocess.run(['pkill', '-f', orphan]).returncode == 0:
            print('push: stopped a forwarder that outlived its waiter')
        made_here = read_file(self.hook_file)
        if made_here:
            self.retire(made_here)
        self.clear_inactive()
        # gh runs the extension as a child process, so halt() signals the whole group.
        self.proc = subprocess.Popen(
            ['gh', 'webhook', 'forward', '--repo', self.repo, '--events', FORWARDED_EVENTS],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        self.hook, self.error = None, ''
        self.started = self.checked = time.time()
        self.threads = [threading.Thread(target=self.pump, args=(self.proc.stdout,)),
                        threading.Thread(target=self.keep_error, args=(self.proc.stderr,))]
        for t in self.threads:
            t.daemon = True
            t.start()
        self.mark('starting')
        print(f'push: forwarder started at {utc()}')

    def retire(self, hook):
        """Wait for GitHub to drop a hook made here, and delete it if it stays."""
        for _ in range(5):
            exists = self.hook_exists(hook)
            if exists is not True:
                break
            time.sleep(2)
        if exists:
            try:
                gh('api', '-X', 'DELETE', f'repos/{self.repo}/hooks/{hook}')
                print(f'push: deleted hook {hook}, which a forwarder here left behind')
                exists = False
            except RuntimeError as e:
                print(f'push: could not delete hook {hook}: {e}')
        if exists is False and os.path.exists(self.hook_file):
            os.remove(self.hook_file)

    def clear_inactive(self):
        """Delete forwarder hooks GitHub has marked inactive, which no forwarder is using."""
        try:
            hooks = json.loads(gh('api', f'repos/{self.repo}/hooks'))
        except (RuntimeError, ValueError) as e:
            print(f'push: could not list hooks: {e}')
            return
        for hook in hooks:
            # A forwarder's hook is named cli and stays active while one is connected.
            if hook.get('name') != 'cli' or hook.get('active'):
                continue
            try:
                gh('api', '-X', 'DELETE', f'repos/{self.repo}/hooks/{hook["id"]}')
                print(f'push: deleted inactive hook {hook["id"]}')
            except RuntimeError as e:
                print(f'push: could not delete inactive hook {hook["id"]}: {e}')

    def hook_exists(self, hook):
        """Whether the repository has the hook, or None when the API gave no answer."""
        try:
            gh('api', '--silent', f'repos/{self.repo}/hooks/{hook}')
            return True
        except RuntimeError as e:
            return False if 'HTTP 404' in str(e) else None

    def pump(self, pipe):
        """Write each close the forwarder prints to the events file as one short line."""
        for raw in pipe:
            try:
                event = json.loads(raw)
                if event.get('pull_request'):
                    pr = event['pull_request']
                    # Only a close settles a row; opens, pushes and reviews are not written.
                    if event.get('action') != 'closed':
                        continue
                    line = {'event': 'pr', 'key': f'{PR_PREFIX}{pr["number"]}',
                            'state': 'merged' if pr.get('merged') else 'closed',
                            'sha': pr.get('merge_commit_sha'),
                            'merged_at': pr.get('merged_at'),
                            'closed_at': pr.get('closed_at'), 'at': utc()}
                elif event.get('issue'):
                    issue = event['issue']
                    # Only a close settles an issue row; edits, labels and reopens do not.
                    if event.get('action') != 'closed':
                        continue
                    line = {'event': 'issue', 'key': f'{ISSUE_PREFIX}{issue["number"]}',
                            'state': 'closed', 'reason': issue.get('state_reason'),
                            'closed_at': issue.get('closed_at'), 'at': utc()}
                elif 'hook_id' in event:
                    self.hook = event['hook_id']
                    write_file(self.hook_file, str(self.hook))
                    line = {'event': 'hook', 'hook': self.hook, 'at': utc()}
                else:
                    continue
                if os.path.exists(self.events) and os.path.getsize(self.events) > EVENTS_ROTATE:
                    os.replace(self.events, self.events + '.old')
                with open(self.events, 'a') as f:
                    f.write(json.dumps(line) + '\n')
            # A bad line is skipped: a stopped thread leaves the forwarder blocked on a full pipe.
            except Exception as e:
                print(f'push: skipped a forwarder line: {e!r}'[:200])

    def keep_error(self, pipe):
        """Keep the forwarder's error message; its other lines are usage text and event logs."""
        detail = False
        for raw in pipe:
            text = raw.decode(errors='replace').strip()
            if text.startswith('Error'):
                self.error, detail = text, True
            elif detail and text:
                self.error, detail = f'{self.error} {text}', False

    def halt(self):
        """Stop the forwarder's process group and let the threads write what it printed."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                pass
            for t in self.threads:
                t.join(timeout=10)
            if not any(t.is_alive() for t in self.threads):
                break
        self.proc.wait()

    def stop(self):
        if not self.holder:
            return
        if self.proc:
            self.halt()
            self.proc = None
        self.mark('down')
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.holder = False

    def read(self):
        """Fold the notices added since the last read into seen. True if a forwarder connected."""
        connected = False
        for line in self.new_lines():
            connected = self.fold(line) or connected
        return connected

    def new_lines(self):
        """Complete lines added to the events file since the last read, across a rename.

        A reader that misses two renames between reads loses the file between them, and
        the timer check covers those rows.
        """
        try:
            inode = os.stat(self.events).st_ino
        except FileNotFoundError:
            return []
        lines = []
        if self.handle and os.fstat(self.handle.fileno()).st_ino != inode:
            lines = self.drain()
            self.handle.close()
            self.handle = None
        if not self.handle:
            self.handle, self.partial = open(self.events, 'rb'), b''
        return lines + self.drain()

    def drain(self):
        *lines, self.partial = (self.partial + self.handle.read()).split(b'\n')
        return lines

    def fold(self, raw):
        """Record one events line. True when the line says a forwarder connected."""
        try:
            line = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(line, dict):
            return False
        if line.get('event') == 'hook':
            return True
        key = line.get('key')
        # The latest close wins: either can be closed, reopened and closed again.
        if line.get('event') in ('pr', 'issue') and key:
            self.seen.pop(key, None)
            self.seen[key] = line
            if len(self.seen) > SEEN_LIMIT:
                del self.seen[next(iter(self.seen))]
        return False


def wait(path, interval, every=False):
    push = Push()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    before = done_rows(path)
    try:
        if not push.usable:
            print('push: off, reading on the timer only')
        live = push.tend()
        reconcile_rows(path)
        check(path)
        last = time.time()
        # The close notice last acted on, by key, and the reads that failed on connect.
        acted, retry, retry_at = {}, set(), 0.0
        while open_rows(path) and not handed_back(path, before, every):
            sys.stdout.flush()
            time.sleep(EVENT_POLL)
            live = push.tend()
            connected = push.read()
            now = time.time()
            if connected:
                # A forwarder that just connected missed the closes sent before it.
                retry, retry_at = reconcile_rows(path), now + EVENT_RECHECK
            elif retry and now >= retry_at:
                # One retry; a read that fails again waits for the next connect or the timer.
                for key in reconcile_rows(path, retry):
                    print(f'{key} read failed twice; waiting for its push notice or the timer')
                retry = set()
            for row in read_ledger(path):
                key = row['key']
                event = push.seen.get(key)
                if row['state'] == 'done' or not event or acted.get(key) == event:
                    continue
                if event.get('at', '') >= row['tally'].removeprefix('added '):
                    acted[key] = event
                    print(f'push: notice for {key} arrived {event.get("at")}')
                    settle_row(path, key, event['state'], notice_tally(event))
            slow = live and not open_builds(path)
            if now - last >= (max(interval, PUSH_FALLBACK_INTERVAL) if slow else interval):
                # A close sent while the forwarder is down never arrives, so the timer reads
                # the rows itself. Issue rows are read even while it is live.
                reconcile_rows(path, issues_only=live)
                check(path)
                last = now
                if push.retryable:
                    push = Push()
                    if push.usable:
                        print('push: reached GitHub, push is on')
        # The loop test skips handed_back once nothing is open, so the last row's
        # sidebar move is printed here.
        if not open_rows(path):
            report_finished(path, before)
    finally:
        push.stop()


def main(argv):
    adders = {'add-lp': (add_lp, 'Launchpad build of {}'),
              'add-pr': (add_pr, 'pull request {}'),
              'add-issue': (add_issue, 'issue {}')}
    if len(argv) >= 4 and argv[1] in adders:
        adder, default = adders[argv[1]]
        name = argv[3].lstrip('#')
        try:
            adder(argv[2], name, ' '.join(argv[4:]) or default.format(name))
        except (RuntimeError, ValueError, KeyError) as e:
            print(f'{argv[1]}: {name} not recorded: {e}')
            return 1
    elif len(argv) == 3 and argv[1] == 'check':
        check(argv[2])
        sidebar_cue(argv[2])
    elif len(argv) >= 3 and argv[1] == 'wait':
        interval = MIN_INTERVAL
        if '--interval' in argv:
            interval = max(MIN_INTERVAL, int(argv[argv.index('--interval') + 1]))
        wait(argv[2], interval, '--all' in argv)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
