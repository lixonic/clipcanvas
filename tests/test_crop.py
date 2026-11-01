"""Saved clip framing crops originals consistently in preview and export."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import cairo

from clipcanvas import engine, model, preview_cache, workflow
from clipcanvas.scrubber import ScrubSession


class ClipCropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='clipcanvas-crop-')
        cls.root = Path(cls.scratch.name)
        cls.photo = cls.root/'bands.png'
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 320, 180)
        cr = cairo.Context(surface)
        for x, width, color in ((0, 80, (1, 0, 0)), (80, 160, (0, .8, 0)),
                                (240, 80, (0, 0, 1))):
            cr.set_source_rgb(*color); cr.rectangle(x, 0, width, 180); cr.fill()
        cr.set_source_rgb(1, 1, 1); cr.rectangle(150, 80, 20, 20); cr.fill()
        surface.write_to_png(str(cls.photo))
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')
        cls.video = cls.root/'bands.mp4'
        subprocess.run([cls.ffmpeg, '-v', 'error', '-loop', '1', '-i', str(cls.photo),
                        '-t', '0.8', '-r', '30', '-c:v', 'libx264', '-crf', '0',
                        '-pix_fmt', 'yuv420p', '-threads', '1', str(cls.video)],
                       env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def project(self, width=320, height=180, image=False, **settings):
        project = model.new_project(width, height)
        clip = model.new_image(self.photo, .8) if image else model.new_clip(self.video)
        clip.update(settings)
        project['clips'] = [clip]
        return project

    def decode(self, path):
        return subprocess.check_output([self.ffmpeg, '-v', 'error', '-i', str(path),
            '-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1',
            'pipe:1'], env=self.env)

    @staticmethod
    def pixel(frame, x, y):
        offset = y*frame.get('stride', frame['width']*3)+x*3
        return tuple(frame['pixels'][offset:offset+3])

    def assert_color(self, frame, x, y, color):
        actual = self.pixel(frame, x, y)
        for value, expected in zip(actual, color):
            self.assertAlmostEqual(value, expected, delta=12, msg=f'{actual} at {(x, y)}')

    @staticmethod
    def marker_width(frame):
        row = frame['height']//2
        return sum(min(ClipCropTests.pixel(frame, x, row)) > 220
                   for x in range(frame['width']))

    def test_zoom_validation_legacy_defaults_and_saved_split_clipboard(self):
        project = self.project(zoom=2.5, position_x=.2, position_y=.8)
        before = copy.deepcopy(project)
        digest = hashlib.sha256(self.video.read_bytes()).digest()
        for value in (0, .99, 4.01, float('nan'), float('inf'), True, '2'):
            broken = copy.deepcopy(project); broken['clips'][0]['zoom'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                model.validate_project(broken)
        for value in (1, 4):
            valid = copy.deepcopy(project); valid['clips'][0]['zoom'] = value
            model.validate_project(valid)
        split = model.split_item(project, 'clips', 0, .4)
        self.assertEqual([clip['zoom'] for clip in split['clips']], [2.5, 2.5])
        selection = {('clips', project['clips'][0]['id'])}
        pasted, _ = workflow.paste_items(project, workflow.copy_items(project, selection), .8, 1)
        self.assertEqual([clip['zoom'] for clip in pasted['clips']], [2.5, 2.5])
        target = self.root/'crop.clipcanvas'; model.save_project(split, target)
        self.assertEqual(model.load_project(target), split)
        legacy = copy.deepcopy(project); legacy['clips'][0].pop('zoom')
        target.write_text(json.dumps(legacy))
        reopened = model.load_project(target)
        self.assertEqual(reopened['clips'][0]['zoom'], 1)
        default = self.project()
        self.assertEqual(engine.preview_frame(legacy, .2, preview_edge=0)['pixels'],
                         engine.preview_frame(default, .2, preview_edge=0)['pixels'])
        self.assertEqual(project, before)
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).digest(), digest)

    def test_matching_ratio_zoom_enlarges_picture_and_clips_edges(self):
        normal = engine.preview_frame(self.project(), .2, preview_edge=0)
        enlarged = engine.preview_frame(self.project(zoom=2), .2, preview_edge=0)
        self.assertEqual((enlarged['width'], enlarged['height']), (320, 180))
        self.assertAlmostEqual(self.marker_width(enlarged), 2*self.marker_width(normal), delta=3)
        self.assert_color(normal, 10, 30, (255, 0, 0))
        self.assert_color(normal, 310, 30, (0, 0, 255))
        self.assert_color(enlarged, 10, 30, (0, 204, 0))
        self.assert_color(enlarged, 310, 30, (0, 204, 0))

    def test_fill_reframes_original_edges_for_portrait_and_square(self):
        for width, height in ((90, 160), (120, 120)):
            for position, expected in ((0, (255, 0, 0)), (.5, (0, 204, 0)),
                                       (1, (0, 0, 255))):
                with self.subTest(size=(width, height), position=position):
                    frame = engine.preview_frame(self.project(width, height, zoom=2,
                        position_x=position, position_y=.5), .2, preview_edge=0)
                    self.assertEqual((frame['width'], frame['height']), (width, height))
                    # The side bands lie outside the unzoomed portrait crop;
                    # they must still be available when reframing the source.
                    self.assert_color(frame, width//2, height//4, expected)

    def test_fit_zoom_crops_overflow_and_keeps_remaining_borders_centered(self):
        for position_x, expected, x in ((0, (255, 0, 0), 20), (1, (0, 0, 255), 140)):
            for position_y in (0, 1):
                with self.subTest(x=position_x, y=position_y):
                    frame = engine.preview_frame(self.project(160, 240, fit='fit', zoom=1.5,
                        position_x=position_x, position_y=position_y), .2, preview_edge=0)
                    self.assert_color(frame, x, 80, expected)
                    self.assert_color(frame, 80, 20, (0, 0, 0))
                    self.assert_color(frame, 80, 220, (0, 0, 0))

    def test_rotation_and_flip_are_applied_before_zoom_and_crop_position(self):
        for position, expected, y in ((0, (255, 0, 0), 20), (1, (0, 0, 255), 140)):
            with self.subTest(position=position):
                frame = engine.preview_frame(self.project(90, 160, rotation=90, zoom=2,
                    position_y=position), .2, preview_edge=0)
                self.assert_color(frame, 45, y, expected)
        flipped = engine.preview_frame(self.project(90, 160, rotation=90, flip_y=True,
            zoom=2, position_y=0), .2, preview_edge=0)
        self.assert_color(flipped, 45, 20, (0, 0, 255))

    def test_scrub_live_and_lossless_export_agree_on_saved_crop(self):
        session = ScrubSession()
        digest = hashlib.sha256(self.video.read_bytes()).digest()
        try:
            cases = ((90, 160, {'zoom': 2, 'position_x': 0}),
                     (160, 240, {'fit': 'fit', 'zoom': 1.5, 'position_x': 1, 'position_y': 1}),
                     (90, 160, {'rotation': 90, 'zoom': 2, 'position_y': 0}))
            for index, (width, height, settings) in enumerate(cases):
                with self.subTest(settings=settings):
                    project = self.project(width, height, **settings); before = copy.deepcopy(project)
                    target = self.root/f'crop-{index}.mkv'
                    engine.render(project, target, quality='lossless')
                    raw = self.decode(target); frame_size = width*height*3
                    expected = raw[6*frame_size:7*frame_size]
                    composed = engine.preview_frame(project, .2, preview_edge=0)
                    self.assertEqual(composed['pixels'], expected)
                    stream = self.root/f'crop-live-{index}.mkv'
                    with stream.open('wb') as output:
                        for chunk in engine.live_stream(project, preview_edge=0):output.write(chunk)
                    self.assertEqual(self.decode(stream), raw)
                    native = session.frame(project, .2, preview_edge=0)
                    self.assertEqual(session.last_backend, 'native')
                    stride = native['stride']
                    packed = b''.join(native['pixels'][y*stride:y*stride+width*3]
                                      for y in range(height))
                    error = sum(abs(a-b) for a, b in zip(packed, expected))/frame_size
                    self.assertLess(error, 6, 'Native scrubbing must retain the exported crop and borders')
                    self.assertEqual(project, before)
            self.assertEqual(hashlib.sha256(self.video.read_bytes()).digest(), digest)
        finally:
            session.close()

    def test_photo_motion_keeps_static_zoom_and_split_phase(self):
        project = self.project(image=True, zoom=1.5, motion='zoom_in', zoom_amount=.5)
        target = self.root/'photo-crop.mkv'; engine.render(project, target, quality='lossless')
        raw = self.decode(target); frame_size = 320*180*3
        widths = []
        for index in (0, 12, 23):
            frame = engine.preview_frame(project, index/30, preview_edge=0)
            self.assertEqual(frame['pixels'], raw[index*frame_size:(index+1)*frame_size])
            widths.append(self.marker_width(frame))
        self.assertGreater(widths[0], 25)
        self.assertGreater(widths[-1], widths[0]+8)
        split = model.split_item(project, 'clips', 0, .4)
        output = self.root/'photo-crop-split.mkv'; engine.render(split, output, quality='lossless')
        self.assertEqual(self.decode(output), raw)

    def test_transitions_cached_preview_keep_crop_layer_size_and_sound(self):
        logo = self.root/'crop-logo.png'
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 20, 20)
        cr = cairo.Context(surface); cr.set_source_rgb(1, 1, 1); cr.paint()
        surface.write_to_png(str(logo))
        soundtrack = self.root/'crop-tone.wav'
        subprocess.run([self.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
            'sine=f=430:r=48000:d=1.2', str(soundtrack)], env=self.env, check=True)
        work = self.root/'crop-proxy-work'; work.mkdir()
        with mock.patch.object(preview_cache, 'directory', return_value=self.root/'crop-proxies'):
            preview_cache.prepare(self.video, 160, work)
            for effect in ('fade', 'slide'):
                with self.subTest(transition=effect):
                    project = self.project(180, 320, zoom=2, position_x=0,
                        transition=effect, transition_duration=.4)
                    project['clips'].append(model.new_clip(self.video))
                    project['clips'][1].update(zoom=2, position_x=1)
                    overlay = model.new_visual(logo, 'image', 0, 1.2)
                    overlay.update(width=.2, height=.12)
                    project['layers'] = [overlay]
                    project['audio'] = [model.new_audio(soundtrack)]
                    before = copy.deepcopy(project)
                    uncropped = copy.deepcopy(project)
                    for clip in uncropped['clips']:clip['zoom'] = 1
                    dry = self.root/f'crop-audio-dry-{effect}.wav'
                    wet = self.root/f'crop-audio-wet-{effect}.wav'
                    engine.render_audio(uncropped, dry); engine.render_audio(project, wet)
                    self.assertEqual(wet.read_bytes(), dry.read_bytes(),
                                     'Changing picture crop must preserve every mixed audio sample')
                    target = self.root/f'crop-transition-{effect}.mkv'
                    engine.render(project, target, quality='lossless')
                    full = self.decode(target); frame_size = 180*320*3
                    small = subprocess.check_output([self.ffmpeg, '-v', 'error', '-i', str(target),
                        '-map', '0:v:0', '-vf', 'scale=90:160', '-pix_fmt', 'rgb24',
                        '-f', 'rawvideo', '-threads', '1', 'pipe:1'], env=self.env)
                    small_size = 90*160*3
                    for index in (6, 18, 30):
                        frame = {'width': 180, 'height': 320,
                                 'pixels': full[index*frame_size:(index+1)*frame_size]}
                        self.assertAlmostEqual(self.marker_width(frame), 36, delta=2,
                                               msg='The overlay size is independent of clip zoom and transition')
                        preview = engine.preview_frame(project, index/30, preview_edge=160)
                        self.assertEqual((preview['width'], preview['height']), (90, 160))
                        self.assertAlmostEqual(self.marker_width(preview), 18, delta=2)
                        expected = small[index*small_size:(index+1)*small_size]
                        error = sum(abs(a-b) for a, b in zip(preview['pixels'], expected))/small_size
                        self.assertLess(error, 4, 'Cached reduced preview must retain the exported framing')
                    first = {'width': 180, 'height': 320, 'pixels': full[6*frame_size:7*frame_size]}
                    last = {'width': 180, 'height': 320, 'pixels': full[30*frame_size:31*frame_size]}
                    self.assert_color(first, 20, 20, (255, 0, 0))
                    self.assert_color(last, 20, 20, (0, 0, 255))
                    joining = full[18*frame_size:19*frame_size]
                    red = sum(joining[x] for x in range(20*180*3, 21*180*3, 3))/180
                    blue = sum(joining[x+2] for x in range(20*180*3, 21*180*3, 3))/180
                    self.assertGreater(red, 40); self.assertGreater(blue, 40)
                    self.assertEqual(project, before)
