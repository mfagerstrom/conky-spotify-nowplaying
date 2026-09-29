"""Tests for conky-mouse.py's placement (keeping the window on screen across monitors) and
resizing (which edges a press drags, and the size settings a drag leads to)."""
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


class ResizeEdgesTest(unittest.TestCase):

    def edges(self, x, y, s=1):
        return conky_mouse.resize_edges(x, y, W * s, H * s, s)

    def test_inside_is_a_move(self):
        for pos in ((W // 2, H // 2), (6, 6), (W - 7, H - 7), (30, 100)):
            with self.subTest(pos=pos):
                self.assertEqual(self.edges(*pos), '')

    def test_edges(self):
        self.assertEqual(self.edges(0, H // 2), 'l')
        self.assertEqual(self.edges(W - 1, H // 2), 'r')
        self.assertEqual(self.edges(W // 2, 5), 't')
        self.assertEqual(self.edges(W // 2, H - 6), 'b')

    def test_corners_reach_along_both_edges(self):
        self.assertEqual(self.edges(0, 0), 'lt')
        self.assertEqual(self.edges(W - 1, 20), 'rt')        # on the right edge, near the top
        self.assertEqual(self.edges(20, H - 1), 'lb')        # on the bottom edge, near the left
        self.assertEqual(self.edges(W - 23, H - 1), 'rb')
        self.assertEqual(self.edges(W - 25, H - 1), 'b')     # just past the corner's reach

    def test_bands_follow_the_display_scale(self):
        self.assertEqual(self.edges(11, H, 2), 'l')          # 6 px band, doubled
        self.assertEqual(self.edges(12, H, 2), '')
        self.assertTrue(set(conky_mouse.CURSORS) >= {self.edges(x, y, 2) for x in (0, W, 2 * W - 1)
                                                      for y in (0, H, 2 * H - 1)} - {''})


class DraggedTest(unittest.TestCase):
    ROOM = 10 ** 6            # the monitor's room left over; most tests stay well inside it

    def dragged(self, edges, dx, dy, settings=(505, 63, 1.0), s=1, room=(ROOM, ROOM)):
        return conky_mouse.dragged(settings, edges, dx, dy, *room, s)

    def test_side_edges_change_the_width_only(self):
        self.assertEqual(self.dragged('r', 100, 999), (605, 63, 1.0))
        self.assertEqual(self.dragged('l', 100, 999), (405, 63, 1.0))
        self.assertEqual(self.dragged('r', 100, 0, s=2), (555, 63, 1.0))          # display scale

    def test_top_and_bottom_change_the_lyrics_height_only(self):
        self.assertEqual(self.dragged('b', 999, 42), (505, 105, 1.0))
        self.assertEqual(self.dragged('t', 999, 42), (505, 21, 1.0))
        self.assertEqual(self.dragged('t', 0, -84, s=2), (505, 105, 1.0))

    def test_corners_change_both_and_never_the_text(self):
        self.assertEqual(self.dragged('rb', 50, 20, (505, 63, 1.5)), (555, 83, 1.5))
        self.assertEqual(self.dragged('lt', 50, 20, (505, 63, 0.7)), (455, 43, 0.7))

    def test_limits(self):
        self.assertEqual(self.dragged('r', -999, 0), (400, 63, 1.0))
        self.assertEqual(self.dragged('b', 0, -999), (505, 16, 1.0))

    def test_growth_stops_at_the_monitor(self):
        self.assertEqual(self.dragged('r', 500, 0, room=(120, 0)), (625, 63, 1.0))
        self.assertEqual(self.dragged('b', 0, 500, room=(0, 50)), (505, 113, 1.0))
        self.assertEqual(self.dragged('rb', 500, 500, s=2, room=(100, 40)), (555, 83, 1.0))

    def test_shrinking_needs_no_room(self):
        self.assertEqual(self.dragged('r', -100, 0, room=(0, 0)), (405, 63, 1.0))
        self.assertEqual(self.dragged('lt', 20, 20, room=(0, 0)), (485, 43, 1.0))


class ResizedTest(unittest.TestCase):
    START, OLD = (1000, 100, 1090, 400), (505, 63, 1.0)

    def resized(self, edges, new, lyrics=True):
        return conky_mouse.resized(self.START, edges, self.OLD, new, 2, lyrics)

    def test_the_edges_across_from_the_dragged_ones_stay_put(self):
        self.assertEqual(self.resized('r', (555, 63, 1.0)), (1000, 100, 1190, 400))
        self.assertEqual(self.resized('l', (555, 63, 1.0)), (900, 100, 1190, 400))
        self.assertEqual(self.resized('b', (505, 13 + 63, 1.0)), (1000, 100, 1090, 426))
        self.assertEqual(self.resized('t', (505, 43, 1.0)), (1000, 140, 1090, 360))
        self.assertEqual(self.resized('lt', (455, 83, 1.0)), (1100, 60, 990, 440))

    def test_without_lyrics_the_height_stays(self):
        self.assertEqual(self.resized('rb', (555, 163, 1.0), lyrics=False), (1000, 100, 1190, 400))


class WithoutIdleEdgesTest(unittest.TestCase):

    def test_no_lyrics_leaves_only_the_width_to_change(self):
        for edges, want in (('t', ''), ('b', ''), ('rb', 'r'), ('lt', 'l'), ('r', 'r')):
            with self.subTest(edges=edges):
                self.assertEqual(conky_mouse.without_idle_edges(edges, {'lyrics': False}), want)
                self.assertEqual(conky_mouse.without_idle_edges(edges, {'lyrics': True}), edges)
                self.assertEqual(conky_mouse.without_idle_edges(edges, {}), want)   # not written yet


if __name__ == '__main__':
    unittest.main()
