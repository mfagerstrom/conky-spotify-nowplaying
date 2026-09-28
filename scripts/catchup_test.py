#!/usr/bin/env python3
"""Checks catchup.py's Launchpad, pull request and issue rows against stubbed reads.

usage: python3 scripts/catchup_test.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catchup  # noqa: E402

OPEN_ISSUE = {'html_url': 'https://example.test/issues/9', 'state': 'open'}
CLOSED_ISSUE = {'html_url': 'https://example.test/issues/9', 'state': 'closed',
                'state_reason': 'completed', 'closed_at': '2026-09-23T18:00:00Z'}
SOURCE = {'status': 'Published', 'self_link': 'https://lp.test/+sourcepub/1'}


def build(state):
    return {'title': 'amd64 build', 'buildstate': state, 'web_link': 'https://lp.test/+build/1',
            'build_log_url': 'https://lp.test/log.txt.gz'}


def launchpad(sources, builds=()):
    """A stub for catchup.launchpad answering the sources query and the builds query."""
    def read(url):
        if 'getBuilds' in url:
            return {'entries': list(builds)}
        return {'entries': list(sources)}
    return read


class Ledger(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.ledger = os.path.join(self.dir, 'ledger.tsv')

    def run_main(self, *args, gh=None, lp=None):
        out = io.StringIO()
        with mock.patch.object(catchup, 'gh', return_value=json.dumps(gh or OPEN_ISSUE)) as g, \
                mock.patch.object(catchup, 'launchpad', side_effect=lp or launchpad([])), \
                contextlib.redirect_stdout(out):
            code = catchup.main(['catchup.py', *args])
        return code, out.getvalue(), g


class LaunchpadRow(Ledger):
    """A Launchpad row waits for the PPA to build and publish a version."""

    def test_a_building_version_stays_open_and_names_tests_running(self):
        code, out, _ = self.run_main('add-lp', self.ledger, '1.0.2',
                                     lp=launchpad([SOURCE], [build('Currently building')]))
        self.assertEqual(code, 0, out)
        self.assertIn('waiting: Launchpad build of 1.0.2 - https://lp.test/+build/1', out)
        self.assertIn('sidebar: a Launchpad build is open; move this session to Tests Running',
                      out)
        self.assertEqual(catchup.open_builds(self.ledger), {'lp:1.0.2'})

    def test_published_and_built_settles_and_names_the_move_back(self):
        self.run_main('add-lp', self.ledger, '1.0.2', lp=launchpad([]))
        code, out, _ = self.run_main('check', self.ledger,
                                     lp=launchpad([SOURCE], [build('Successfully built')]))
        self.assertEqual(code, 0, out)
        self.assertIn('lp: 1.0.2 published', out)
        self.assertIn('sidebar: no Launchpad builds open; move this session from Tests Running',
                      out)

    def test_a_failed_build_settles_with_its_log(self):
        self.run_main('add-lp', self.ledger, '1.0.2', lp=launchpad([]))
        _, out, _ = self.run_main('check', self.ledger,
                                  lp=launchpad([SOURCE], [build('Failed to build')]))
        self.assertIn('lp: 1.0.2 failed', out)
        self.assertIn('https://lp.test/log.txt.gz', out)

    def test_a_published_source_still_building_stays_open(self):
        self.run_main('add-lp', self.ledger, '1.0.2', lp=launchpad([]))
        _, out, _ = self.run_main('check', self.ledger,
                                  lp=launchpad([SOURCE], [build('Uploading build')]))
        self.assertIn('still waiting: Launchpad build of 1.0.2: source Published; '
                      'Uploading build', out)
        self.assertNotIn('sidebar', out)

    def test_a_version_missing_past_the_limit_settles_as_rejected(self):
        with open(self.ledger, 'w') as f:
            f.write('v\tlp:1.0.2\tlink\tnot in the archive yet\tadded 2026-01-01T00:00:00Z\n')
        _, out, _ = self.run_main('check', self.ledger, lp=launchpad([]))
        self.assertIn('lp: 1.0.2 rejected', out)

    def test_a_failed_read_leaves_the_row_open(self):
        self.run_main('add-lp', self.ledger, '1.0.2', lp=launchpad([]))
        _, out, _ = self.run_main('check', self.ledger,
                                  lp=mock.Mock(side_effect=RuntimeError('Launchpad read failed')))
        self.assertIn('will retry next check', out)
        self.assertEqual(catchup.open_builds(self.ledger), {'lp:1.0.2'})


class IssueRow(Ledger):
    """An issue row waits for a blocking issue to close and never counts as a build."""

    def test_an_open_issue_records_one_open_row(self):
        code, out, gh = self.run_main('add-issue', self.ledger, '#9', 'blocker')
        self.assertEqual(code, 0, out)
        self.assertIn('waiting for close: blocker - https://example.test/issues/9', out)
        self.assertEqual(gh.call_args.args, ('api', 'repos/{owner}/{repo}/issues/9'))
        rows = catchup.read_ledger(self.ledger)
        self.assertEqual([(r['key'], r['state']) for r in rows], [('issue:9', 'open')])

    def test_a_closed_issue_settles_at_once(self):
        code, out, _ = self.run_main('add-issue', self.ledger, '9', gh=CLOSED_ISSUE)
        self.assertEqual(code, 0, out)
        self.assertIn('issue: 9 closed', out)
        row = catchup.read_ledger(self.ledger)[0]
        self.assertEqual(row['tally'], 'closed as completed at 2026-09-23T18:00:00Z')

    def test_a_failed_read_records_nothing_and_exits_nonzero(self):
        out = io.StringIO()
        with mock.patch.object(catchup, 'gh', side_effect=RuntimeError('HTTP 404')), \
                contextlib.redirect_stdout(out):
            code = catchup.main(['catchup.py', 'add-issue', self.ledger, '9'])
        self.assertEqual(code, 1)
        self.assertIn('add-issue: 9 not recorded', out.getvalue())
        self.assertEqual(catchup.read_ledger(self.ledger), [])

    def test_check_neither_reads_nor_counts_an_issue_row(self):
        self.run_main('add-issue', self.ledger, '9')
        code, out, gh = self.run_main('check', self.ledger)
        self.assertEqual(code, 0, out)
        gh.assert_not_called()
        self.assertEqual(catchup.open_builds(self.ledger), set())
        self.assertNotIn('sidebar', out)

    def test_a_closed_issue_names_the_move_out_of_blocked(self):
        self.run_main('add-issue', self.ledger, '9')
        before = catchup.done_rows(self.ledger)
        out = io.StringIO()
        with mock.patch.object(catchup, 'gh', return_value=json.dumps(CLOSED_ISSUE)), \
                contextlib.redirect_stdout(out):
            self.assertEqual(catchup.reconcile_rows(self.ledger, issues_only=True), set())
            self.assertTrue(catchup.report_finished(self.ledger, before))
        self.assertIn('sidebar: a blocking issue closed; move this session from Blocked',
                      out.getvalue())
        self.assertNotIn('Tests Running', out.getvalue())

    def test_issues_only_leaves_pull_request_rows_unread(self):
        with open(self.ledger, 'w') as f:
            f.write('pr\tpr:5\turl\topen\tadded 2026-09-23T00:00:00Z\n')
        with mock.patch.object(catchup, 'gh') as gh:
            catchup.reconcile_rows(self.ledger, issues_only=True)
        gh.assert_not_called()


class Forwarder(Ledger):
    """The forwarder writes only closes, and the reader keeps the latest one."""

    def push(self):
        push = object.__new__(catchup.Push)
        push.events = os.path.join(self.dir, 'events.jsonl')
        push.hook_file = os.path.join(self.dir, 'hook')
        push.seen = {}
        return push

    def pumped(self, push, *events):
        with contextlib.redirect_stdout(io.StringIO()):
            push.pump([json.dumps(e).encode() for e in events])
        with open(push.events, 'rb') as f:
            return f.read().splitlines()

    def test_an_issue_close_is_written_and_kept(self):
        push = self.push()
        closed = {'action': 'closed', 'issue': {'number': 9, 'state_reason': 'completed',
                                                'closed_at': '2026-09-23T18:00:00Z'}}
        lines = self.pumped(push, {'action': 'labeled', 'issue': {'number': 9}}, closed)
        self.assertEqual(len(lines), 1)
        push.fold(lines[0])
        self.assertEqual(catchup.notice_tally(push.seen['issue:9']),
                         'closed as completed at 2026-09-23T18:00:00Z')

    def test_a_merge_is_written_and_kept(self):
        push = self.push()
        merged = {'action': 'closed', 'pull_request': {
            'number': 7, 'merged': True, 'merge_commit_sha': 'abcdef1234567',
            'merged_at': '2026-09-28T18:29:11Z', 'closed_at': '2026-09-28T18:29:11Z'}}
        lines = self.pumped(push, {'action': 'opened', 'pull_request': {'number': 7}}, merged)
        self.assertEqual(len(lines), 1)
        push.fold(lines[0])
        self.assertEqual(catchup.notice_tally(push.seen['pr:7']),
                         'merged abcdef123 at 2026-09-28T18:29:11Z')

    def test_a_hook_line_says_a_forwarder_connected(self):
        push = self.push()
        lines = self.pumped(push, {'hook_id': 5})
        self.assertTrue(push.fold(lines[0]))


class PushOff(Ledger):
    """A Push that could not reach GitHub reads nothing, and wait carries on by the timer."""

    def test_wait_settles_on_the_timer_when_github_gives_no_answer(self):
        pr = {'url': 'https://example.test/pull/7', 'state': 'OPEN'}
        merged = dict(pr, state='MERGED', mergedAt='2026-09-28T18:29:11Z',
                      closedAt='2026-09-28T18:29:11Z', mergeCommit={'oid': 'abcdef1234567'})
        slept, views = [], []

        def answer(*args, timeout=None):
            if args[:2] == ('extension', 'list'):
                return catchup.EXTENSION
            if args[:2] == ('repo', 'view'):
                views.append(args)
                raise RuntimeError('no answer in 10s')
            if args[:2] == ('pr', 'view'):
                return json.dumps(merged if slept else pr)
            raise AssertionError(f'unexpected gh call: {args}')

        def nap(seconds):
            # A wait that never settles fails here instead of spinning forever.
            if len(slept) >= 3:
                raise AssertionError('wait did not settle on the timer')
            slept.append(seconds)

        out = io.StringIO()
        with mock.patch.object(catchup, 'gh', side_effect=answer), \
                mock.patch.object(catchup.time, 'sleep', side_effect=nap), \
                mock.patch.object(catchup.signal, 'signal'), \
                contextlib.redirect_stdout(out):
            catchup.add_pr(self.ledger, '7', 'pull request 7')
            push = catchup.Push()
            self.assertFalse(push.usable)
            self.assertTrue(push.retryable)
            self.assertFalse(push.read())
            push.stop()
            catchup.wait(self.ledger, 0)
        self.assertEqual(len(slept), 1)
        # The Push above, the one wait starts with, and the retry on the timer.
        self.assertEqual(len(views), 3)
        self.assertEqual(catchup.read_ledger(self.ledger)[0]['state'], 'done')
        self.assertIn('push: off, reading on the timer only', out.getvalue())

    def test_a_failed_extension_listing_is_retried(self):
        with mock.patch.object(catchup, 'gh', side_effect=RuntimeError('no answer in 60s')), \
                contextlib.redirect_stdout(io.StringIO()):
            push = catchup.Push()
        self.assertFalse(push.usable)
        self.assertTrue(push.retryable)
        self.assertFalse(push.read())

    def test_a_missing_extension_is_not_retried(self):
        with mock.patch.object(catchup, 'gh', return_value='cli/gh-copilot\n'), \
                contextlib.redirect_stdout(io.StringIO()):
            push = catchup.Push()
        self.assertFalse(push.usable)
        self.assertFalse(push.retryable)
        self.assertFalse(push.read())


if __name__ == '__main__':
    unittest.main()
