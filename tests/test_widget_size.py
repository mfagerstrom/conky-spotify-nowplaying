"""Tests for widget_size.py: reading and writing the size settings file."""
import unittest

import support

import widget_size  # noqa: E402  (support puts src/ on the path)


class SizeFileTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(widget_size, PATH='size')

    def write(self, text):
        with open(widget_size.PATH, 'w') as f:
            f.write(text)

    def test_missing_file_is_the_defaults(self):
        self.assertEqual(widget_size.load(), (505, 63, 1.0))

    def test_round_trip(self):
        widget_size.save((620, 105, 1.35))
        self.assertEqual(support.read(widget_size.PATH), '620 105 1.35\n')
        self.assertEqual(widget_size.load(), (620, 105, 1.35))

    def test_bad_and_out_of_range_values(self):
        cases = {'99999 999999 9': (9999, 99999, 2.0), '10 1 0.1': (400, 16, 0.7),
                 'nan x': (505, 63, 1.0), '612': (612, 63, 1.0), '': (505, 63, 1.0),
                 '612.6 104.4 1.254': (613, 104, 1.25)}
        for text, want in cases.items():
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(widget_size.load(), want)


if __name__ == '__main__':
    unittest.main()
