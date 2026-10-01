"""Tests for the launcher's supervisor: which of the widget's processes it starts again,
and when."""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

import support

BIN = os.path.join(os.path.dirname(support.SRC), 'bin', 'conky-spotify-nowplaying')


def load_launcher():
    loader = importlib.machinery.SourceFileLoader('launcher', BIN)
    spec = importlib.util.spec_from_loader('launcher', loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


launcher = load_launcher()


class Proc:
    """A stand-in for a child process: running until it is ended or exits with `code`."""

    def __init__(self, name):
        self.name, self.returncode, self.ended = name, None, False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.ended, self.returncode = True, -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.terminate()


class SuperviseTest(support.TempDirTest):

    def run_supervisor(self, passes, at_pass):
        """Runs supervise() for `passes` passes, calling at_pass(n, procs) after each; returns
        every process it started, in order."""
        self.redirect(launcher, PIDFILE='run.pid')
        started, clock, count = [], [1000.0], [0]

        def popen(cmd, cwd):
            name = os.path.basename(cmd[-1]).split('.')[0] if cmd[0] != 'conky' else 'conky'
            started.append(Proc(name))
            return started[-1]

        def sleep(seconds):
            clock[0] += seconds
            if seconds == 0.5:                          # the end of a pass
                count[0] += 1
                at_pass(count[0], started)
                if count[0] >= passes:
                    raise StopIteration

        with mock.patch.object(launcher.subprocess, 'Popen', side_effect=popen), \
                mock.patch.object(launcher.time, 'sleep', side_effect=sleep), \
                mock.patch.object(launcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(launcher.signal, 'signal'):
            with self.assertRaises(StopIteration):
                launcher.supervise()
        return started

    def names(self, procs):
        return [p.name for p in procs]

    def test_a_scale_change_starts_nowplaying_and_conky_again(self):
        def at_pass(n, procs):
            if n == 5:
                next(p for p in procs if p.name == 'conky-mouse').returncode = launcher.RESCALED
        started = self.run_supervisor(8, at_pass)
        self.assertEqual(self.names(started[:4]), ['nowplaying', 'conky', 'conky-mouse', 'tray'])
        self.assertTrue(started[0].ended and started[1].ended)
        self.assertFalse(started[3].ended)                          # the tray keeps running
        self.assertEqual(sorted(self.names(started[4:])), ['conky', 'conky-mouse', 'nowplaying'])

    def test_any_other_exit_starts_only_that_one_again(self):
        def at_pass(n, procs):
            if n == 5:
                next(p for p in procs if p.name == 'conky-mouse').returncode = 1
        started = self.run_supervisor(8, at_pass)
        self.assertEqual(self.names(started[4:]), ['conky-mouse'])
        self.assertFalse(any(p.ended for p in started))


if __name__ == '__main__':
    unittest.main()
