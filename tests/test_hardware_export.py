"""Actual hardware encoding where available, plus deterministic fallback checks."""
import copy
import hashlib
import math
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest import mock

from clipcanvas import engine, model
import test_engine
import test_export_speed


class HardwareExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root = cls.fixtures.root
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def project(self):
        return test_export_speed.ExportSpeedTests.project(self)

    def decode(self, path, audio=False):
        return test_export_speed.ExportSpeedTests.decode(self, path, audio)

    def test_missing_hardware_matches_software_frames_audio_and_sources(self):
        project = self.project()
        before = copy.deepcopy(project)
        hashes = {item['path']: hashlib.sha256(Path(item['path']).read_bytes()).digest()
                  for item in model.media_items(project)}
        software = self.root/'software-reference.mp4'
        automatic = self.root/'unavailable-automatic.mp4'
        with mock.patch.object(engine, '_vaapi_device', side_effect=AssertionError('Software must not probe')):
            engine.render(project, software, quality='master', encoding='software')
        messages = []
        with mock.patch.object(engine, '_vaapi_device', return_value=None):
            engine.render(project, automatic, quality='master', encoding='auto',
                          progress=lambda value, text: messages.append(text))
        self.assertIn('Graphics encoding unavailable. Using software…', messages)
        self.assertEqual(self.decode(automatic), self.decode(software))
        self.assertEqual(self.decode(automatic, True), self.decode(software, True))
        self.assertEqual(project, before)
        for path, digest in hashes.items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).digest(), digest)

    def test_failed_hardware_discards_partial_export_and_retries_software_once(self):
        output = self.root/'retry.mp4'
        output.write_bytes(b'previous export')
        execute = engine._execute
        attempts = []
        def run(arguments, *args):
            attempts.append(arguments)
            self.assertEqual(output.read_bytes(), b'previous export')
            if len(attempts) == 1:
                Path(arguments[-1]).write_bytes(b'partial hardware video')
                raise engine.RenderError('Graphics device stopped responding')
            return execute(arguments, *args)
        messages = []
        with mock.patch.object(engine, '_vaapi_device', return_value=Path('/dev/dri/not-present')), \
                mock.patch.object(engine, '_execute', side_effect=run):
            engine.render(self.project(), output, encoding='auto',
                          progress=lambda value, text: messages.append(text))
        self.assertEqual(len(attempts), 2)
        self.assertIn('Graphics encoding failed. Retrying with software…', messages)
        self.assertEqual(engine.probe(output)['width'], 320)
        self.assertFalse(list(self.root.glob('.clipcanvas-*')))

    def test_cancelled_hardware_never_retries_or_replaces_destination(self):
        output = self.root/'cancelled-hardware.mp4'
        output.write_bytes(b'previous export')
        with mock.patch.object(engine, '_vaapi_device', return_value=Path('/dev/dri/not-present')), \
                mock.patch.object(engine, '_execute', side_effect=engine.RenderCancelled('Cancelled')) as execute:
            with self.assertRaises(engine.RenderCancelled):
                engine.render(self.project(), output, encoding='auto')
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(output.read_bytes(), b'previous export')
        self.assertFalse(list(self.root.glob('.clipcanvas-*')))

    def test_both_encoders_failing_preserves_destination_and_reports_software_error(self):
        output = self.root/'failed-hardware-and-software.mp4'
        output.write_bytes(b'previous export')
        with mock.patch.object(engine, '_vaapi_device', return_value=Path('/dev/dri/not-present')), \
                mock.patch.object(engine, '_execute', side_effect=[engine.RenderError('Hardware failed'),
                    engine.RenderError('Software failed')]) as execute:
            with self.assertRaisesRegex(engine.RenderError, 'Software failed'):
                engine.render(self.project(), output, encoding='auto')
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(output.read_bytes(), b'previous export')
        self.assertFalse(list(self.root.glob('.clipcanvas-*')))

    def test_preview_and_lossless_bypass_hardware_and_match_software(self):
        with mock.patch.object(engine, '_vaapi_device', side_effect=AssertionError('Must not probe')):
            for preview, quality, suffix in ((True, 'master', '.mp4'), (False, 'lossless', '.mkv')):
                with self.subTest(preview=preview):
                    automatic = self.root/f'ignore-auto-{preview}{suffix}'
                    software = self.root/f'ignore-software-{preview}{suffix}'
                    engine.render(self.project(), automatic, preview=preview, quality=quality, encoding='auto')
                    engine.render(self.project(), software, preview=preview, quality=quality, encoding='software')
                    self.assertEqual(self.decode(automatic), self.decode(software))
                    self.assertEqual(self.decode(automatic, True), self.decode(software, True))

    def test_invalid_encoding_and_source_destinations_never_start_hardware_probe(self):
        output = self.root/'invalid-encoding.mp4'
        output.write_bytes(b'previous export')
        with mock.patch.object(engine, '_vaapi_device', side_effect=AssertionError('Must not probe')):
            for encoding in ('hardware', '', None, [], {}):
                with self.subTest(encoding=encoding), self.assertRaises(engine.RenderError):
                    engine.render(self.project(), output, encoding=encoding)
            with self.assertRaises(engine.RenderError):
                engine.render(self.project(), self.root/'landscape.mp4', encoding='auto')
        self.assertEqual(output.read_bytes(), b'previous export')
        self.assertFalse(list(self.root.glob('.clipcanvas-*')))

    def test_probe_failure_and_timeout_or_cancel_reap_children_and_workspaces(self):
        self.assertFalse(engine._probe_vaapi('/dev/dri/not-present', 320, 180, 30, 'master', 'fast'))
        sleeper = self.root/'sleeping-encoder'
        sleeper.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(30)\n')
        sleeper.chmod(0o755)
        popen = subprocess.Popen
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                children = []
                def launch(*args, **kwargs):
                    process = popen(*args, **kwargs)
                    children.append(process)
                    return process
                cancel = threading.Event()
                timer = threading.Timer(.08, cancel.set)
                started = time.monotonic()
                with mock.patch.object(engine, '_tool', return_value=(str(sleeper), None)), \
                        mock.patch.object(engine.subprocess, 'Popen', side_effect=launch):
                    if cancelled:
                        timer.start()
                        try:
                            with self.assertRaises(engine.RenderCancelled):
                                engine._probe_vaapi('device', 320, 180, 30, 'master', 'fast', cancel)
                        finally:
                            timer.cancel();timer.join()
                    else:
                        self.assertFalse(engine._probe_vaapi('device', 320, 180, 30, 'master', 'fast', timeout=.08))
                self.assertLess(time.monotonic()-started, 2)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertFalse(Path(children[0].args[-1]).parent.exists())

    def test_native_vaapi_keeps_all_frames_quality_and_sound_at_every_speed(self):
        project = self.project()
        if engine._vaapi_device(320, 180, 30, 'master', 'fast') is None:
            self.skipTest('No working VAAPI encoder is accessible')
        original = copy.deepcopy(project)
        reference = self.root/'hardware-reference.mkv'
        cpu = self.root/'hardware-reference.mp4'
        engine.render(project, reference, quality='lossless')
        engine.render(project, cpu, quality='master', encoding='software')
        expected = self.decode(reference)
        expected_audio = self.decode(cpu, True)
        scores = {}
        for quality, minimum in (('master', 32), ('high', 30), ('smaller', 27)):
            for speed in ('fast', 'balanced', 'compact'):
                with self.subTest(quality=quality, speed=speed):
                    output = self.root/f'vaapi-{quality}-{speed}.mp4'
                    messages = []
                    engine.render(project, output, quality=quality, speed=speed, encoding='auto',
                                  progress=lambda value, text: messages.append(text))
                    self.assertIn('Rendering with graphics hardware…', messages)
                    self.assertNotIn('Rendering with software…', messages, 'Native check must actually use the GPU')
                    info = engine.probe(output)
                    self.assertEqual((info['width'], info['height'], info['fps']), (320, 180, 30))
                    self.assertAlmostEqual(info['duration'], model.project_duration(project), delta=.07)
                    actual = self.decode(output)
                    self.assertEqual(len(actual), len(expected))
                    mse = sum((a-b)**2 for a, b in zip(actual, expected))/len(expected)
                    scores[quality, speed] = 10*math.log10(255**2/max(mse, 1e-9))
                    self.assertGreater(scores[quality, speed], minimum)
                    self.assertEqual(self.decode(output, True), expected_audio)
        self.assertGreater(scores['master', 'fast'], scores['smaller', 'fast']+1)
        self.assertEqual(project, original)
