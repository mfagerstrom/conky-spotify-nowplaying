"""Tests for conky-mouse.py's placement (keeping the window on screen across monitors, and
on its own monitor as monitors come and go and the display scale changes), resizing (which edges a press drags, and the size settings a drag leads to), and the wheel
over plain lyrics."""
import os
import time
import unittest
from unittest import mock

import support

conky_mouse = support.load_conky_mouse()

W, H = 545, 200   # about the widget's size
# Two 1920x1080 monitors side by side, and a third above the first one.
MONITORS = [(0, 0, 1920, 1080, True, 'DP-1'), (1920, 0, 1920, 1080, False, 'DP-2'),
            (0, -1080, 1920, 1080, False, 'DP-3')]


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


class PositionTest(support.TempDirTest):

    def setUp(self):
        super().setUp()
        self.redirect(conky_mouse, CONF='.', POSITION='position')

    def test_saved_with_its_anchor_and_read_back(self):
        anchor = conky_mouse.anchor_for(2500, 400, W, H, MONITORS, 1)
        self.assertEqual(anchor, ('DP-2', 580, 400, 1920, 1))
        conky_mouse.save_position(2500, 400, anchor)
        self.assertEqual(support.read(conky_mouse.POSITION), '2500 400 DP-2 580 400 1920 1\n')
        self.assertEqual(conky_mouse.load_position(), (2500, 400, anchor))

    def test_a_file_without_a_whole_anchor_reads_without_one(self):
        for text in ('2044 12\n', '2044 12 eDP-1 2044 12 192\n', '2044 12 eDP-1 a b c d\n',
                     '2044 12 eDP-1 2044 12 0 2\n', '2044 12 eDP-1 2044 12 3072 0\n'):
            with self.subTest(text=text):
                with open(conky_mouse.POSITION, 'w') as f:
                    f.write(text)
                self.assertEqual(conky_mouse.load_position(), (2044, 12, None))
        conky_mouse.save_position(2044, 12, None)
        self.assertEqual(support.read(conky_mouse.POSITION), '2044 12\n')

    def test_missing_or_bad_file_is_no_position(self):
        self.assertIsNone(conky_mouse.load_position())
        for text in ('left top\n', '2044\n'):
            with self.subTest(text=text):
                with open(conky_mouse.POSITION, 'w') as f:
                    f.write(text)
                self.assertIsNone(conky_mouse.load_position())

    def test_no_anchor_without_monitors(self):
        self.assertIsNone(conky_mouse.anchor_for(100, 100, W, H, [], 1))


def doubled(mons):
    return [(x * 2, y * 2, w * 2, h * 2, p, n) for x, y, w, h, p, n in mons]


class ResolveTest(unittest.TestCase):

    SAVED = (2500, 400, ('DP-2', 580, 400, 1920, 1))

    def test_unchanged_monitors_keep_the_position(self):
        self.assertEqual(conky_mouse.resolve(self.SAVED, MONITORS, 1), self.SAVED)

    def test_follows_its_monitor_when_another_is_removed(self):
        # The first monitor unplugged: the second slides over to x = 0.
        mons = [(0, 0, 1920, 1080, True, 'DP-2')]
        self.assertEqual(conky_mouse.resolve(self.SAVED, mons, 1), (580, 400, self.SAVED[2]))

    def test_follows_its_monitor_when_one_is_added_before_it(self):
        mons = [(0, 0, 3072, 1920, False, 'eDP-1')] + [(x + 3072, y, w, h, p, n) for x, y, w, h, p, n in MONITORS]
        self.assertEqual(conky_mouse.resolve(self.SAVED, mons, 1)[:2], (3072 + 1920 + 580, 400))

    def test_its_monitor_gone_keeps_where_it_was(self):
        mons = [MONITORS[0]]
        self.assertEqual(conky_mouse.resolve(self.SAVED, mons, 1), self.SAVED)
        with mock.patch.object(conky_mouse, 'monitors', return_value=mons):   # then kept on screen
            self.assertEqual(conky_mouse.clamp_to_monitor(2500, 400, W, H), (1920 - W, 400))

    def test_a_scale_change_follows_its_monitors_width(self):
        self.assertEqual(conky_mouse.resolve(self.SAVED, doubled(MONITORS), 2),
                         (3840 + 1160, 800, ('DP-2', 1160, 800, 3840, 2)))

    def test_its_monitor_scaled_before_xft_dpi_follows(self):
        # XWayland's coordinates and Xft.dpi change a moment apart: the width decides.
        self.assertEqual(conky_mouse.resolve(self.SAVED, doubled(MONITORS), 1)[:2], (3840 + 1160, 800))

    def test_text_scaling_alone_moves_nothing(self):
        # Large Text raises Xft.dpi but not the scale or any monitor's width.
        self.assertEqual(conky_mouse.resolve(self.SAVED, MONITORS, conky_mouse.scale_of(120))[:2], (2500, 400))

    def test_its_monitor_gone_scales_with_the_display(self):
        self.assertEqual(conky_mouse.resolve(self.SAVED, doubled(MONITORS)[:1], 2),
                         (5000, 800, ('DP-2', 1160, 800, 3840, 2)))

    def test_and_back_again(self):
        there = conky_mouse.resolve(self.SAVED, doubled(MONITORS), 2)
        self.assertEqual(conky_mouse.resolve(there, MONITORS, 1), self.SAVED)
        there = conky_mouse.resolve(self.SAVED, doubled(MONITORS)[:1], 2)
        self.assertEqual(conky_mouse.resolve(there, MONITORS, 1), self.SAVED)

    def test_without_an_anchor_nothing_moves(self):
        self.assertEqual(conky_mouse.resolve((2044, 12, None), MONITORS, 2), (2044, 12, None))


class MonitorsTest(unittest.TestCase):

    def test_names_and_the_primary_are_read(self):
        out = (' 0: +*DP-9 3840/480x2160/270+6912+0  DP-9\n'
               ' 1: +eDP-1 3072/290x1920/180+0+0  eDP-1\n')
        with mock.patch('subprocess.run', return_value=mock.Mock(stdout='Monitors: 2\n' + out)):
            self.assertEqual(conky_mouse.monitors(), [(6912, 0, 3840, 2160, True, 'DP-9'),
                                                      (0, 0, 3072, 1920, False, 'eDP-1')])

    def test_dpi_is_none_when_xrdb_has_none(self):
        with mock.patch('subprocess.run', return_value=mock.Mock(stdout='Xft.dpi:\t192\n')):
            self.assertEqual((conky_mouse.dpi(), conky_mouse.scale()), (192, 2))
        with mock.patch('subprocess.run', return_value=mock.Mock(stdout='')):
            self.assertEqual((conky_mouse.dpi(), conky_mouse.scale()), (None, 1))
        self.assertEqual([conky_mouse.scale_of(v) for v in (96, 120, 192, None)], [1, 1, 2, 1])


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


class ScrollLyricsTest(support.TempDirTest):
    # Lyrics at x 22..545, y 160..223 (logical px), whose last offset is 5.
    REGIONS = {'lyrics': [22, 160, 545, 223], 'lyrics_scroll': ['1234', 5]}

    def setUp(self):
        super().setUp()
        self.redirect(conky_mouse, LYRICS_SCROLL='lyrics-scroll')

    def wheel(self, down, regions=REGIONS, x=100, y=180, s=1):
        return conky_mouse.scroll_lyrics(regions, s, x, y, down)

    def shown(self):
        return support.read(conky_mouse.LYRICS_SCROLL)

    def test_the_wheel_moves_a_line_at_a_time_from_the_top(self):
        self.assertEqual([self.wheel(True), self.wheel(True), self.wheel(False)], [1, 2, 1])
        self.assertEqual(self.shown(), '1234 1')

    def test_it_stops_at_the_first_and_the_last_line(self):
        self.assertEqual(self.wheel(False), 0)
        for _ in range(8):
            self.wheel(True)
        self.assertEqual(self.shown(), '1234 5')

    def test_an_offset_past_a_shrunken_range_steps_back_from_its_end(self):
        with open(conky_mouse.LYRICS_SCROLL, 'w') as f:
            f.write('1234 34')
        self.assertEqual(self.wheel(False), 4)

    def test_an_offset_for_other_lyrics_starts_from_the_top(self):
        with open(conky_mouse.LYRICS_SCROLL, 'w') as f:
            f.write('999 4')
        self.assertEqual(self.wheel(True), 1)
        self.assertEqual(self.shown(), '1234 1')

    def test_outside_the_lyrics_or_over_synced_ones_it_does_nothing(self):
        synced = dict(self.REGIONS, lyrics_scroll=None)
        for regions, x, y in ((self.REGIONS, 100, 150), (self.REGIONS, 10, 180), (synced, 100, 180),
                              ({'lyrics': None, 'lyrics_scroll': None}, 100, 180), ({}, 100, 180)):
            with self.subTest(regions=regions, x=x, y=y):
                self.assertIsNone(self.wheel(True, regions, x, y))
        self.assertFalse(os.path.exists(conky_mouse.LYRICS_SCROLL))

    def test_the_area_follows_the_display_scale(self):
        self.assertIsNone(self.wheel(True, y=400))          # below the lyrics at scale 1
        self.assertEqual(self.wheel(True, y=400, s=2), 1)


class LogTest(support.TempDirTest):

    def test_a_line_starts_with_the_date_and_the_time(self):
        self.redirect(conky_mouse, LOG='mouse.log')
        days = {time.strftime('%F')}
        conky_mouse.log('press at 10,20')
        days.add(time.strftime('%F'))                  # the test may straddle midnight
        line = support.read(conky_mouse.LOG)
        self.assertRegex(line, r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d press at 10,20\n$')
        self.assertIn(line[:10], days)


if __name__ == '__main__':
    unittest.main()
