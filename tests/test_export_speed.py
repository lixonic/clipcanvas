"""Generated-media checks for export speed without changing the composition."""
import copy
import hashlib
import math
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from clipcanvas import color_grading, engine, model
import test_engine


class ExportSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root = cls.fixtures.root
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def project(self, effect='fade'):
        project = model.new_project(320, 180)
        project['clips'] = [model.new_clip(self.root/name)
                            for name in ('landscape.mp4', 'portrait.mp4', 'third.mp4')]
        project['clips'][0].update(transition=effect, transition_duration=.3,
            zoom=1.4, offset_x=.1, color_grade=color_grading.PRESETS['Adventure'],
            saturation=.85, video_fade_in=.1)
        project['clips'][1].update(speed=1.25, fit='fit')
        text = model.new_text('Export speed', .2, 1.2)
        text.update(font_size=22, width=.7)
        text['animation'].update(enter='fade', exit='fade', duration=.15)
        project['layers'] = [text]
        project['captions'] = [model.new_caption('Same timing', 1.2, 2.2)]
        project['caption_style']['font_size'] = 18
        music = model.new_audio(self.root/'music.wav')
        music.update(volume=.3, fade_in=.1,
                     audio_effects={'echo_enabled': True, 'echo_delay_ms': 100})
        project['audio'] = [music]
        return project

    def decode(self, path, audio=False):
        output = ['-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 's16le'] if audio else [
            '-map', '0:v:0', '-pix_fmt', 'yuv420p', '-c:v', 'rawvideo', '-f', 'rawvideo']
        return subprocess.check_output([self.ffmpeg, '-v', 'error', '-threads', '1',
            '-i', str(path)] + output + ['-threads', '1', 'pipe:1'], env=self.env)

    def test_mp4_speeds_preserve_composition_timing_sound_and_sources(self):
        project = self.project()
        original = copy.deepcopy(project)
        hashes = {item['path']: hashlib.sha256(Path(item['path']).read_bytes()).digest()
                  for item in model.media_items(project)}
        reference = self.root/'speed-reference.mkv'
        engine.render(project, reference, quality='lossless')
        expected = self.decode(reference)
        previous_audio = None
        for speed in ('fast', 'balanced', 'compact'):
            with self.subTest(speed=speed):
                output = self.root/f'speed-{speed}.mp4'
                engine.render(project, output, quality='master', speed=speed)
                meta = engine.probe(output)
                self.assertEqual((meta['width'], meta['height'], meta['fps']), (320, 180, 30))
                self.assertAlmostEqual(meta['duration'], model.project_duration(project), delta=.07)
                actual = self.decode(output)
                self.assertEqual(len(actual), len(expected), 'Every composed frame must be exported')
                mse = sum((a-b)**2 for a, b in zip(actual, expected))/len(expected)
                self.assertGreater(10*math.log10(255**2/max(mse, 1e-9)), 32,
                                   'MP4 must remain close to the original composition')
                audio = self.decode(output, audio=True)
                self.assertTrue(any(audio))
                if previous_audio is not None:
                    self.assertEqual(audio, previous_audio, 'Speed must not alter the soundtrack')
                previous_audio = audio
        self.assertEqual(project, original)
        for path, digest in hashes.items():
            with open(path, 'rb') as stream:
                self.assertEqual(hashlib.sha256(stream.read()).digest(), digest)

    def test_parallel_export_filters_match_serial_frames_and_samples(self):
        for effect in ('none', 'fade', 'circle_wipe', 'flow', 'match_move'):
            with self.subTest(effect=effect):
                project = self.project(effect)
                serial = self.root/f'serial-{effect}.mkv'
                parallel = self.root/f'parallel-{effect}.mkv'
                with mock.patch.object(engine, '_render_filter_threads', return_value=1):
                    engine.render(project, serial, quality='lossless', speed='balanced')
                engine.render(project, parallel, quality='lossless', speed='fast')
                self.assertEqual(self.decode(parallel), self.decode(serial))
                self.assertEqual(self.decode(parallel, audio=True), self.decode(serial, audio=True))

    def test_prepared_preview_ignores_export_speed(self):
        project = self.project()
        fast = self.root/'preview-speed-fast.mp4'
        compact = self.root/'preview-speed-compact.mp4'
        engine.render(project, fast, preview=True, speed='fast')
        engine.render(project, compact, preview=True, speed='compact')
        self.assertEqual(self.decode(fast), self.decode(compact))
        self.assertEqual(self.decode(fast, audio=True), self.decode(compact, audio=True))

    def test_invalid_speed_keeps_existing_destination_and_creates_no_work_files(self):
        destination = self.root/'invalid-speed.mp4'
        destination.write_bytes(b'existing video')
        for speed in ('ultrafast', '', None, [], {}):
            with self.subTest(speed=speed), self.assertRaises(engine.RenderError):
                engine.render(self.project(), destination, speed=speed)
            self.assertEqual(destination.read_bytes(), b'existing video')
            self.assertFalse(list(self.root.glob('.clipcanvas-*')))

    def test_filter_budget_respects_single_cpu_and_preview(self):
        for cpus in (None, 1, 2, 64):
            with self.subTest(cpus=cpus), mock.patch.object(engine.os, 'cpu_count', return_value=cpus):
                self.assertEqual(engine._render_filter_threads(True), 1)
                self.assertEqual(engine._render_filter_threads(False), 2 if cpus and cpus > 1 else 1)
