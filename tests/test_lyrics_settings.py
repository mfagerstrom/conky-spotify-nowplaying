"""Tests for lyrics_settings.py: reading and writing the tray menu's lyrics settings."""
import os
import unittest

import support

import lyrics_settings  # noqa: E402  (support puts src/ on the path)


class LyricsSettingsTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(lyrics_settings, SHOWN='conf/lyrics', SCROLLING='conf/lyrics-scrolling')

    def test_missing_files_are_the_defaults(self):
        self.assertTrue(lyrics_settings.shown())
        self.assertEqual(lyrics_settings.scrolling(), lyrics_settings.AUTOSCROLL)

    def test_round_trip(self):
        lyrics_settings.set_shown(False)
        lyrics_settings.set_scrolling(lyrics_settings.STATIC)
        self.assertFalse(lyrics_settings.shown())
        self.assertEqual(lyrics_settings.scrolling(), lyrics_settings.STATIC)
        self.assertEqual(support.read(lyrics_settings.SHOWN), 'off\n')
        lyrics_settings.set_shown(True)
        lyrics_settings.set_scrolling(lyrics_settings.AUTOSCROLL)
        self.assertTrue(lyrics_settings.shown())
        self.assertEqual(lyrics_settings.scrolling(), lyrics_settings.AUTOSCROLL)
        self.assertEqual(sorted(os.listdir(os.path.dirname(lyrics_settings.SHOWN))),
                         ['lyrics', 'lyrics-scrolling'])            # no .tmp left behind

    def test_anything_unknown_is_the_default(self):
        os.makedirs(os.path.dirname(lyrics_settings.SHOWN))
        for path in (lyrics_settings.SHOWN, lyrics_settings.SCROLLING):
            with open(path, 'w') as f:
                f.write('  garbage \n')
        self.assertTrue(lyrics_settings.shown())
        self.assertEqual(lyrics_settings.scrolling(), lyrics_settings.AUTOSCROLL)


if __name__ == '__main__':
    unittest.main()
