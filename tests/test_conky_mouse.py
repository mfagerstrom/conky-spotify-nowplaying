"""Tests for conky-mouse.py's placement: keeping the window on screen across monitors."""
import unittest
from unittest import mock

import support

conky_mouse = support.load_conky_mouse()

W, H = 545, 200   # about the widget's size
# Two 1920x1080 monitors side by side, and a third above the first one.
MONITORS = [(0, 0, 1920, 1080, True), (1920, 0, 1920, 1080, False), (0, -1080, 1920, 1080, False)]


class ClampToMonitorTest(unittest.TestCase):

    def clamp(self, x, y, monitors=MONITORS):
        with mock.patch.object(conky_mouse, 'monitors', return_value=monitors):
            return conky_mouse.clamp_to_monitor(x, y, W, H)

    def test_no_monitors_leaves_the_position_alone(self):
        self.assertEqual(self.clamp(-5000, 9000, monitors=[]), (-5000, 9000))

    def test_on_screen_position_is_kept(self):
        for pos in ((100, 100), (0, 0), (1920 - W, 1080 - H), (2500, 400), (100, -900)):
            with self.subTest(pos=pos):
                self.assertEqual(self.clamp(*pos), pos)

    def test_overhanging_edges_are_pulled_in(self):
        self.assertEqual(self.clamp(-50, 500), (0, 500))                      # left edge
        self.assertEqual(self.clamp(3840 - W + 60, 500), (3840 - W, 500))     # right edge of the second
        self.assertEqual(self.clamp(2500, 1080 - H + 40), (2500, 1080 - H))   # bottom of the second

    def test_straddling_two_monitors_moves_onto_the_one_holding_the_centre(self):
        # Centre at x = 1700 + 272 = 1972: on the second monitor.
        self.assertEqual(self.clamp(1700, 300), (1920, 300))
        # Centre at x = 1600 + 272 = 1872: on the first.
        self.assertEqual(self.clamp(1600, 300), (1920 - W, 300))
        # Straddling the first and the one above it, centre below the seam.
        self.assertEqual(self.clamp(300, -60), (300, 0))

    def test_off_screen_window_goes_to_the_nearest_monitor(self):
        self.assertEqual(self.clamp(5000, 500), (3840 - W, 500))    # far right: the second
        self.assertEqual(self.clamp(-3000, 400), (0, 400))          # far left: the first
        self.assertEqual(self.clamp(2500, 3000), (2500, 1080 - H))  # below the second
        self.assertEqual(self.clamp(500, -4000), (500, -1080))      # above: the one on top


if __name__ == '__main__':
    unittest.main()
