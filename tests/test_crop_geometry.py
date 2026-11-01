"""Mouse placement preserves source framing and opposite resize anchors."""
import unittest

from clipcanvas import crop_geometry as crop


class CropGeometryTests(unittest.TestCase):
    def assert_rect(self, actual, expected):
        for value, reference in zip(actual, expected):
            self.assertAlmostEqual(value, reference)

    def test_fill_and_fit_place_complete_source_behind_screen_crop(self):
        canvas = (10, 20, 400, 400)
        self.assert_rect(crop.picture_rect({'fit': 'fill'}, 400, 200, canvas),
                         (-190, 20, 800, 400))
        self.assert_rect(crop.picture_rect({'fit': 'fit'}, 400, 200, canvas),
                         (10, 120, 400, 200))
        self.assert_rect(crop.picture_rect({'fit': 'fill', 'position_x': 1}, 400, 200, canvas),
                         (-390, 20, 800, 400))

    def test_source_moves_at_matching_ratio_and_full_size(self):
        canvas = (10, 20, 400, 200)
        clip = {'fit': 'fill'}
        moved = dict(clip, **crop.move(clip, canvas, 100, -50))
        self.assertEqual(moved['offset_x'], .25)
        self.assertEqual(moved['offset_y'], -.25)
        self.assert_rect(crop.picture_rect(moved, 400, 200, canvas),
                         (110, -30, 400, 200))
        limits = crop.move(moved, canvas, 10000, -10000)
        self.assertEqual(limits, {'offset_x': 1, 'offset_y': -1})

    def test_saved_offsets_are_independent_of_view_size_and_origin(self):
        clip = {'zoom': 2, 'offset_x': .25, 'offset_y': -.25}
        self.assert_rect(crop.picture_rect(clip, 400, 200, (10, 20, 400, 200)),
                         (-90, -130, 800, 400))
        self.assert_rect(crop.picture_rect(clip, 400, 200, (100, 200, 200, 100)),
                         (50, 125, 400, 200))
        first = crop.move(clip, (10, 20, 400, 200), 40, 20)
        second = crop.move(clip, (100, 200, 200, 100), 20, 10)
        self.assertEqual(first, second)

    def test_corner_resize_enlarges_while_opposite_corner_stays_fixed(self):
        canvas = (10, 20, 400, 200)
        for corner, delta, expected in (
                ('se', (400, 200), (10, 20, 800, 400)),
                ('nw', (-400, -200), (-390, -180, 800, 400)),
                ('ne', (400, -200), (10, -180, 800, 400)),
                ('sw', (-400, 200), (-390, 20, 800, 400))):
            with self.subTest(corner=corner):
                clip = {'fit': 'fill'}
                resized = dict(clip, **crop.resize(clip, canvas, corner, *delta, 400, 200))
                self.assertAlmostEqual(resized['zoom'], 2)
                self.assert_rect(crop.picture_rect(resized, 400, 200, canvas), expected)

    def test_resize_retains_fit_border_and_asymmetric_fill_content_anchor(self):
        canvas = (0, 0, 400, 400)
        clip = {'fit': 'fit', 'position_x': 1, 'position_y': 0}
        resized = dict(clip, **crop.resize(clip, canvas, 'se', 400, 400, 400, 200))
        self.assert_rect(crop.picture_rect(resized, 400, 200, canvas), (0, 200, 800, 400))
        clip = {'fit': 'fill', 'position_x': .25, 'offset_x': .1}
        before = crop.picture_rect(clip, 400, 200, canvas)
        resized = dict(clip, **crop.resize(clip, canvas, 'se', 200, 200, 400, 200))
        after = crop.picture_rect(resized, 400, 200, canvas)
        self.assertAlmostEqual(resized['zoom'], 1.5)
        self.assertAlmostEqual(after[0], before[0]*1.5)
        self.assertAlmostEqual(after[1], before[1]*1.5)

    def test_zoom_limits_preserve_existing_placement(self):
        canvas = (10, 20, 400, 200)
        clip = {'zoom': 4, 'offset_x': .2, 'offset_y': -.1}
        resized = crop.resize(clip, canvas, 'se', 10000, 10000)
        self.assertEqual(resized['zoom'], 4)
        self.assertAlmostEqual(resized['offset_x'], .2)
        self.assertAlmostEqual(resized['offset_y'], -.1)
        zoomed_out = crop.resize({'zoom': 1}, canvas, 'se', -10000, -10000)
        self.assertEqual(zoomed_out, {'zoom': 1, 'offset_x': 0, 'offset_y': 0})

    def test_magnified_view_anchors_visible_handle_but_saves_canvas_offsets(self):
        canvas = (-200, -100, 800, 400)
        visible = (0, 0, 400, 200)
        clip = {'zoom': 1}
        resized = dict(clip, **crop.resize(clip, canvas, 'se', 400, 200,
                                           400, 200, visible))
        self.assertEqual(resized['zoom'], 2)
        self.assertEqual(resized['offset_x'], .25)
        self.assertEqual(resized['offset_y'], .25)
        # The image point beneath the visible northwest handle (0, 0)
        # remains there; the transformed source origin doubles its distance.
        self.assert_rect(crop.picture_rect(resized, 400, 200, canvas),
                         (-400, -200, 1600, 800))
