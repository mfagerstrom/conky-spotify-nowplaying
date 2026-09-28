"""Loads the widget's modules for testing, away from the real cache and the desktop.

Importing this first points HOME at a throwaway directory, so every path the modules
work out from ~ at import time lands there rather than in the user's ~/.cache and
~/.config. Tests still point the paths they write to at their own temporary directory.
"""
import atexit
import ctypes
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
sys.path.insert(0, SRC)

HOME = tempfile.mkdtemp(prefix='conky-spotify-nowplaying-test-home-')
atexit.register(shutil.rmtree, HOME, ignore_errors=True)
os.environ['HOME'] = HOME


def load_nowplaying():
    """nowplaying runs `xrdb -query` at import to find the display scale; answer it
    with nothing, which means scale 1, so the import needs no X server."""
    if 'nowplaying' not in sys.modules:
        with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout='')):
            import nowplaying  # noqa: F401
    return sys.modules['nowplaying']


def load_conky_mouse():
    """conky-mouse.py has a hyphen in its name, and loads libX11 at import: load it by
    path, with a stand-in for the library."""
    if 'conky_mouse' not in sys.modules:
        spec = importlib.util.spec_from_file_location('conky_mouse', os.path.join(SRC, 'conky-mouse.py'))
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(ctypes, 'CDLL', return_value=mock.MagicMock()):
            spec.loader.exec_module(module)
        sys.modules['conky_mouse'] = module
    return sys.modules['conky_mouse']


class TempDirTest(unittest.TestCase):
    """Gives each test its own directory in self.dir, removed afterwards."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix='conky-spotify-nowplaying-test-')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def redirect(self, module, **names):
        """Points module.<NAME> at <file> inside self.dir, for this test only."""
        for name, filename in names.items():
            patcher = mock.patch.object(module, name, os.path.join(self.dir, filename))
            patcher.start()
            self.addCleanup(patcher.stop)


def read(path):
    with open(path) as f:
        return f.read()


def response(body):
    """A stand-in for the context-managed response urlopen returns, reading `body` (bytes)."""
    r = mock.MagicMock()
    r.__enter__.return_value = io.BytesIO(body)
    return r


def http_error(code, headers=None):
    return urllib.error.HTTPError('https://example.test/', code, 'error', headers or {}, None)
