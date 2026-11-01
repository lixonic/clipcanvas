"""Moving main footage preserves source framing, timing and exported pixels."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import cairo

from clipcanvas import engine, model, workflow
from clipcanvas.scrubber import ScrubSession


class ClipPositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='clipcanvas-position-')
        cls.root = Path(cls.scratch.name)
        cls.photo = cls.root/'source.png'
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 320, 180)
        cr = cairo.Context(surface)
        for x, width, color in ((0, 80, (1, 0, 0)), (80, 160, (0, .8, 0)),
                                (240, 80, (0, 0, 1))):
            cr.set_source_rgb(*color); cr.rectangle(x, 0, width, 180); cr.fill()
        cr.set_source_rgb(1, 1, 1); cr.rectangle(150, 80, 20, 20); cr.fill()
        surface.write_to_png(str(cls.photo))
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')
        cls.video = cls.root/'source.mp4'
        subprocess.run([cls.ffmpeg, '-v', 'error', '-loop', '1', '-i', str(cls.photo),
            '-f', 'lavfi', '-i', 'sine=f=430:r=48000:d=0.8', '-t', '0.8', '-r', '30',
            '-c:v', 'libx264', '-crf', '0', '-pix_fmt', 'yuv420p', '-threads', '1',
            '-c:a', 'aac', str(cls.video)], env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def project(self, width=160, height=90, image=False, **settings):
        project = model.new_project(width, height)
        clip = model.new_image(self.photo, .8) if image else model.new_clip(self.video)
        clip.update(settings); project['clips'] = [clip]
        return project

    def decode(self, path):
        return subprocess.check_output([self.ffmpeg, '-v', 'error', '-i', str(path),
            '-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1',
            'pipe:1'], env=self.env)

    @staticmethod
    def packed(frame):
        stride = frame.get('stride', frame['width']*3)
        return b''.join(frame['pixels'][y*stride:y*stride+frame['width']*3]
                        for y in range(frame['height']))

    def assert_color(self, frame, x, y, color):
        start = y*frame.get('stride', frame['width']*3)+x*3
        actual = frame['pixels'][start:start+3]
        for value, expected in zip(actual, color):
            self.assertAlmostEqual(value, expected, delta=12, msg=str(tuple(actual)))

    def test_free_position_at_original_size_exposes_black_inside_fixed_canvas(self):
        frame = engine.preview_frame(self.project(offset_x=.25, offset_y=.2), .2, preview_edge=0)
        self.assertEqual((frame['width'], frame['height']), (160, 90))
        self.assert_color(frame, 20, 45, (0, 0, 0))
        self.assert_color(frame, 80, 8, (0, 0, 0))
        self.assert_color(frame, 50, 45, (255, 0, 0))
        outside = engine.preview_frame(self.project(offset_x=1), .2, preview_edge=0)
        self.assertFalse(any(outside['pixels']))

    def test_moving_zoomed_footage_recovers_source_outside_the_previous_crop(self):
        project = self.project(160, 160, zoom=2, position_x=0)
        original = engine.preview_frame(project, .2, preview_edge=0)
        self.assert_color(original, 120, 40, (255, 0, 0))
        project['clips'][0]['offset_x'] = -1
        moved = engine.preview_frame(project, .2, preview_edge=0)
        # An offset of one canvas width must reveal the next source region,
        # rather than translating an already-cropped picture into black.
        self.assert_color(moved, 120, 40, (0, 204, 0))

    def test_fit_and_rotation_move_the_complete_oriented_picture(self):
        project = self.project(160, 240, fit='fit', offset_y=.2)
        frame = engine.preview_frame(project, .2, preview_edge=0)
        self.assert_color(frame, 20, 100, (0, 0, 0))
        self.assert_color(frame, 20, 140, (255, 0, 0))
        project = self.project(90, 160, rotation=90, fit='fit', offset_x=.2)
        frame = engine.preview_frame(project, .2, preview_edge=0)
        self.assert_color(frame, 8, 30, (0, 0, 0))
        self.assert_color(frame, 50, 20, (255, 0, 0))

    def test_validation_saved_split_clipboard_and_legacy_offsets(self):
        project = self.project(offset_x=.3, offset_y=-.2)
        for key in ('offset_x', 'offset_y'):
            for value in (-1.01, 1.01, float('nan'), float('inf'), True, '.2'):
                bad = copy.deepcopy(project); bad['clips'][0][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    model.validate_project(bad)
        split = model.split_item(project, 'clips', 0, .4)
        self.assertEqual([(c['offset_x'], c['offset_y']) for c in split['clips']],
                         [(.3, -.2), (.3, -.2)])
        selection = {('clips', project['clips'][0]['id'])}
        pasted, _ = workflow.paste_items(project, workflow.copy_items(project, selection), .8, 1)
        self.assertEqual(pasted['clips'][1]['offset_x'], .3)
        saved = self.root/'position.clipcanvas'; model.save_project(split, saved)
        self.assertEqual(model.load_project(saved), split)
        legacy = copy.deepcopy(project)
        legacy['clips'][0].pop('offset_x'); legacy['clips'][0].pop('offset_y')
        saved.write_text(json.dumps(legacy))
        loaded = model.load_project(saved)
        self.assertEqual((loaded['clips'][0]['offset_x'], loaded['clips'][0]['offset_y']), (0, 0))

    def test_preview_live_export_and_native_preserve_position_and_original_audio(self):
        session = ScrubSession()
        digest = hashlib.sha256(self.video.read_bytes()).digest()
        try:
            for index, (width, height, settings) in enumerate((
                    (160, 90, {'offset_x': .25, 'offset_y': .2}),
                    (160, 240, {'fit': 'fit', 'zoom': 1.5, 'offset_x': -.3, 'offset_y': .15}),
                    (90, 160, {'rotation': 90, 'zoom': 2, 'offset_y': -.3}))):
                with self.subTest(settings=settings):
                    project = self.project(width, height, **settings); before = copy.deepcopy(project)
                    target = self.root/f'position-{index}.mkv'
                    engine.render(project, target, quality='lossless')
                    raw = self.decode(target); size = width*height*3
                    expected = raw[6*size:7*size]
                    self.assertEqual(engine.preview_frame(project, .2, preview_edge=0)['pixels'], expected)
                    live = self.root/f'position-live-{index}.mkv'
                    with live.open('wb') as output:
                        for chunk in engine.live_stream(project, preview_edge=0):output.write(chunk)
                    self.assertEqual(self.decode(live), raw)
                    native = session.frame(project, .2, preview_edge=0)
                    self.assertEqual(session.last_backend, 'native')
                    error = sum(abs(a-b) for a, b in zip(self.packed(native), expected))/size
                    self.assertLess(error, 6)
                    self.assertEqual(project, before)
            dry = self.project(); wet = copy.deepcopy(dry)
            wet['clips'][0].update(offset_x=.5, offset_y=-.4)
            plain, moved = self.root/'plain.wav', self.root/'moved.wav'
            engine.render_audio(dry, plain); engine.render_audio(wet, moved)
            self.assertEqual(moved.read_bytes(), plain.read_bytes())
            self.assertEqual(hashlib.sha256(self.video.read_bytes()).digest(), digest)
        finally:
            session.close()

    def test_offset_photo_motion_keeps_exact_seek_and_split_phase(self):
        for motion in ('zoom_in', 'zoom_out'):
            with self.subTest(motion=motion):
                project = self.project(image=True, zoom=1.5, offset_x=.2, offset_y=.1,
                                       motion=motion, zoom_amount=.4)
                output = self.root/f'position-{motion}.mkv'
                engine.render(project, output, quality='lossless')
                raw = self.decode(output); size = 160*90*3
                for index in (0, 6, 12, 15, 23):
                    frame = engine.preview_frame(project, index/30, preview_edge=0)
                    self.assertEqual(frame['pixels'], raw[index*size:(index+1)*size])
                split = model.split_item(project, 'clips', 0, .4)
                target = self.root/f'position-{motion}-split.mkv'
                engine.render(split, target, quality='lossless')
                self.assertEqual(self.decode(target), raw)
