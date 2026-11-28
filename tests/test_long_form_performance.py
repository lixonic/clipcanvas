"""Long-form playback parity, bounded resource, instrumentation and UI layout tests."""
import array
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import gi
gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gst, Gtk
import cairo

from clipcanvas import engine, lazy_preview, live_player, model, visuals

Gst.init(None)


class LongFormPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='clipcanvas-long-form-tests-')
        cls.root = Path(cls.folder.name)
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')
        cls.v1 = cls.root / 'v1.mp4'
        cls.v2 = cls.root / 'v2.mp4'
        cls.v_long = cls.root / 'v_long.mp4'
        cls.img = cls.root / 'photo.png'
        subprocess.run([
            cls.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=24:d=2',
            '-f', 'lavfi', '-i', 'sine=f=330:r=48000:d=2',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(cls.v1),
        ], env=cls.env, check=True)
        subprocess.run([
            cls.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'smptebars=s=160x90:r=24:d=2',
            '-f', 'lavfi', '-i', 'sine=f=520:r=48000:d=2',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(cls.v2),
        ], env=cls.env, check=True)
        subprocess.run([
            cls.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=24:d=24',
            '-f', 'lavfi', '-i', 'sine=f=330:r=48000:d=24',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '24', '-c:a', 'aac', str(cls.v_long),
        ], env=cls.env, check=True)
        subprocess.run([
            cls.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:d=1',
            '-frames:v', '1', str(cls.img),
        ], env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def _reference_streams(self, project, name):
        target = self.root / f'{name}.mkv'
        with target.open('wb') as out:
            for chunk in engine.live_stream(project, 0, preview_edge=0):
                out.write(chunk)
        video = subprocess.check_output([
            self.ffmpeg, '-v', 'error', '-i', str(target), '-an',
            '-pix_fmt', 'yuv420p', '-f', 'rawvideo', 'pipe:1',
        ], env=self.env)
        audio = subprocess.check_output([
            self.ffmpeg, '-v', 'error', '-i', str(target), '-vn',
            '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', '-f', 's16le', 'pipe:1',
        ], env=self.env)
        return video, audio

    def _assert_video_parity(self, actual, expected, frame_bytes, max_mae=2.0):
        self.assertEqual(len(actual), len(expected))
        if actual == expected:
            return
        worst = 0.0
        for frame in range(len(expected) // frame_bytes):
            a = actual[frame * frame_bytes:(frame + 1) * frame_bytes]
            b = expected[frame * frame_bytes:(frame + 1) * frame_bytes]
            if a != b:
                mae = sum(abs(x - y) for x, y in zip(a, b)) / frame_bytes
                worst = max(worst, mae)
        self.assertLessEqual(worst, max_mae)

    def _assert_audio_parity(self, actual, expected, max_sample_diff=0):
        n = min(len(actual), len(expected))
        self.assertLessEqual(abs(len(actual) - len(expected)) // 4, 512)
        if actual[:n] == expected[:n]:
            return
        a = array.array('h')
        a.frombytes(actual[:n])
        b = array.array('h')
        b.frombytes(expected[:n])
        self.assertLessEqual(max(abs(x - y) for x, y in zip(a, b)), max_sample_diff)

    def test_composed_transitions_images_layers_captions_and_match_move_parity(self):
        frame_bytes = 160 * 90 * 3 // 2

        # 1. Consecutive transition chain (dissolve + circle_wipe + flow + fade)
        p1 = model.new_project(160, 90)
        p1['clips'] = [
            model.new_clip(self.v1), model.new_clip(self.v2),
            model.new_clip(self.v1), model.new_clip(self.v2), model.new_clip(self.v1),
        ]
        for idx, effect in enumerate(('dissolve', 'circle_wipe', 'flow', 'fade')):
            p1['clips'][idx].update(transition=effect, transition_duration=0.5)
        engine._source_rate(p1)
        ref_v1, ref_a1 = self._reference_streams(p1, 'chain')
        for start in (0.0, 1.75, 5.1):
            with self.subTest(case='chain', start=start):
                act_v = b''.join(lazy_preview.video(p1, start, 0, threading.Event()))
                act_a = b''.join(lazy_preview.audio(p1, start, threading.Event()))
                self._assert_video_parity(act_v, ref_v1[math.ceil(start * 24 - 1e-7) * frame_bytes:], frame_bytes)
                self._assert_audio_parity(act_a, ref_a1[round(start * 48000) * 4:])

        # 2. Image clip with slow zoom, animated layers, word-highlight captions, and element sounds
        p2 = model.new_project(160, 90)
        img_clip = model.new_image(self.img, duration=2.5)
        img_clip['motion'] = 'zoom_in'
        p2['clips'] = [model.new_clip(self.v1), img_clip, model.new_clip(self.v2), model.new_clip(self.v1)]
        p2['clips'][1].update(transition='dissolve', transition_duration=0.5)
        engine._source_rate(p2)
        title = model.new_text('Persistent Title', 0.2, 7.5)
        title['animation'].update(enter='fade', exit='fade', duration=0.4)
        title['sound_effect'].update(preset='pop', volume=0.4, trigger='both')
        reveal = model.new_text('Word Reveal Here', 1.5, 4.5)
        reveal['animation'].update(text_effect='reveal', enter='rise', duration=0.3)
        p2['layers'] = [title, reveal]
        for i in range(6):
            cue = model.new_caption(f'Hello world cue {i}', i * 1.2 + 0.1, i * 1.2 + 1.0)
            cue['animation'].update(enter='fade', text_effect='highlight', duration=0.2)
            if i in (0, 4):
                cue['sound_effect'].update(preset='click', volume=0.3, trigger='enter')
            p2['captions'].append(cue)
        ref_v2, ref_a2 = self._reference_streams(p2, 'composed')
        for start in (0.0, 1.8, 5.5):
            with self.subTest(case='composed', start=start):
                act_v = b''.join(lazy_preview.video(p2, start, 0, threading.Event()))
                act_a = b''.join(lazy_preview.audio(p2, start, threading.Event()))
                self._assert_video_parity(act_v, ref_v2[math.ceil(start * 24 - 1e-7) * frame_bytes:], frame_bytes)
                self._assert_audio_parity(act_a, ref_a2[round(start * 48000) * 4:])

        # 3. Match & Move across transition
        p3 = model.new_project(160, 90)
        p3['clips'] = [model.new_clip(self.v1), model.new_clip(self.v2), model.new_clip(self.v1), model.new_clip(self.v2)]
        p3['clips'][1].update(transition='match_move', transition_duration=0.5)
        engine._source_rate(p3)
        rows = model.timeline(p3)
        m1 = model.new_text('Morphing', rows[1]['start'], rows[1]['start'] + rows[1]['duration'])
        m1.update(x=0.3, y=0.3, match_id='hero')
        m2 = model.new_text('Morphing', rows[2]['start'], rows[2]['start'] + rows[2]['duration'])
        m2.update(x=0.7, y=0.7, match_id='hero')
        p3['layers'] = [m1, m2]
        ref_v3, ref_a3 = self._reference_streams(p3, 'match_move')
        for start in (0.0, 3.6):
            with self.subTest(case='match_move', start=start):
                act_v = b''.join(lazy_preview.video(p3, start, 0, threading.Event()))
                act_a = b''.join(lazy_preview.audio(p3, start, threading.Event()))
                self._assert_video_parity(act_v, ref_v3[math.ceil(start * 24 - 1e-7) * frame_bytes:], frame_bytes)
                self._assert_audio_parity(act_a, ref_a3[round(start * 48000) * 4:])

    def test_deep_seek_single_long_clip_with_audio_effects_and_captions_parity(self):
        frame_bytes = 160 * 90 * 3 // 2
        p = model.new_project(160, 90)
        clip = model.new_clip(self.v_long)
        clip['fade_out'] = 2.0
        p['clips'] = [clip]
        engine._source_rate(p)
        track = model.new_audio(self.v_long)
        track['volume'] = 0.4
        track['fade_out'] = 3.0
        track['audio_effects'] = {'echo_enabled': True, 'compressor_enabled': True}
        p['audio'] = [track]
        for i in range(8):
            cue = model.new_caption(f'Long caption {i}', i * 3.0 + 0.2, i * 3.0 + 2.4)
            cue['animation'].update(enter='fade', text_effect='highlight', duration=0.25)
            if i == 7:
                cue['sound_effect'].update(preset='pop', volume=0.4, trigger='enter')
            p['captions'].append(cue)
        self.assertTrue(lazy_preview.supported(p))
        ref_v, ref_a = self._reference_streams(p, 'deep_long')
        for start in (0.0, 18.0, 21.5):
            with self.subTest(start=start):
                act_v = b''.join(lazy_preview.video(p, start, 0, threading.Event()))
                act_a = b''.join(lazy_preview.audio(p, start, threading.Event()))
                self.assertEqual(act_v, ref_v[math.ceil(start * 24 - 1e-7) * frame_bytes:])
                # Allow <= 2 LSB (16-bit PCM) float rounding from shifted afade/acompressor envelopes.
                self._assert_audio_parity(act_a, ref_a[round(start * 48000) * 4:], max_sample_diff=2)

    def test_caption_sprite_lru_cache_reuses_sprites_across_runs(self):
        p = model.new_project(160, 90)
        p['clips'] = [model.new_card('#112233', 6.0)]
        p['captions'] = [model.new_caption('Cached line one', 0.2, 2.0), model.new_caption('Cached line two', 2.5, 4.5)]
        with tempfile.TemporaryDirectory(dir=self.root) as s1, tempfile.TemporaryDirectory(dir=self.root) as s2:
            engine._caption_stream(p, s1, 160, 90, 6.0, window_start=0.0, window_end=5.0)
            with mock.patch.object(visuals, '_paint', side_effect=AssertionError('Warm caption sprites must reuse cache')):
                result = engine._caption_stream(p, s2, 160, 90, 6.0, window_start=0.0, window_end=5.0)
                self.assertIsNotNone(result)

    def test_playback_metrics_and_perf_log_with_clean_cancellation(self):
        p = model.new_project(160, 90)
        p['clips'] = [model.new_clip(self.v1) for _ in range(4)]
        p['clips'][-2]['transition'] = 'dissolve'
        engine._source_rate(p)
        log_path = self.root / 'perf.jsonl'
        pictures, errors = [], []
        description = live_player.pipeline_description
        with mock.patch.dict(os.environ, {'CLIPCANVAS_PERF_LOG': str(log_path)}), \
             mock.patch.object(live_player, 'pipeline_description', side_effect=lambda: description('fakesink sync=true')):
            player = live_player.StreamPlayer(
                p, 0.0, True,
                lambda sample: (pictures.append(sample.get_buffer().pts), Gst.FlowReturn.OK)[1],
                errors.append, lambda: None, preview_edge=0,
            )
            try:
                deadline = time.monotonic() + 10
                context = GLib.MainContext.default()
                while time.monotonic() < deadline and not pictures and not errors:
                    while context.pending():
                        context.iteration(False)
                    time.sleep(0.005)
                self.assertFalse(errors)
                self.assertTrue(pictures)
            finally:
                player.stop()
                player.thread.join(5)
                self.assertFalse(player.thread.is_alive())
        snap = player.metrics.snapshot()
        self.assertEqual(snap['route'], 'windowed')
        self.assertIsNotNone(snap['first_picture_ms'])
        self.assertIsNotNone(snap['first_video_prep_ms'])
        self.assertIsNotNone(snap['first_video_ffmpeg_ms'])
        self.assertIsNotNone(snap['stop_ms'])
        self.assertTrue(log_path.is_file())
        logged = json.loads(log_path.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(logged['route'], 'windowed')

    def test_i420_row_packer_matches_reference_stride_alignment(self):
        width, height = 180, 100  # 180 % 8 == 4 -> needs chroma padding
        src = bytes((i * 37) % 251 for i in range(width * height * 3 // 2))
        packed = live_player._pack_i420_rows(src, width, height)
        planes = []
        at = 0
        for w, h in ((width, height), (width // 2, height // 2), (width // 2, height // 2)):
            padding = bytes((-w) % 4)
            planes.extend(src[at + row * w:at + (row + 1) * w] + padding for row in range(h))
            at += w * h
        self.assertEqual(packed, b''.join(planes))

    def test_dense_timeline_cached_layout_hit_testing_snapping_and_fast_draw(self):
        from clipcanvas.app import Timeline
        p = model.new_project(1920, 1080)
        p['clips'] = [model.new_card('#224466', 54.0) for _ in range(200)]
        p['clips'][-2]['transition'] = 'dissolve'
        for i in range(1500):
            p['captions'].append(model.new_caption(f'Cue {i}', i * 7.0, i * 7.0 + 3.0))

        class DummySnap:
            def get_active(self):
                return True

        class DummyOwner:
            def __init__(self, project):
                self.project = project
                self.playhead = 10700.0
                self.thumbs = {}
                self.waves = {}
                self.selected = ('clips', 198)
                self.selection = {('clips', project['clips'][198]['id'])}
                self.busy = False
                self.snap_check = DummySnap()

            def is_selected(self, kind, index):
                return self.selected == (kind, index)

        tl = Timeline(DummyOwner(p))
        tl.scale = 28.0
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1200, 300)
        alloc = Gdk.Rectangle()
        alloc.x, alloc.y, alloc.width, alloc.height = 0, 0, int(10800 * 28 + 200), 300
        with mock.patch.object(tl, 'get_allocation', return_value=alloc):
            cr = cairo.Context(surface)
            cr.rectangle(100 + 10700 * 28 - 8, 0, 17, 300)
            cr.clip()
            tl.draw(tl, cr)
            self.assertEqual(len(tl.hit), 200 + 1500)
            self.assertEqual(len(tl.join_hits), 199)
            self.assertIsNotNone(tl.add_hit)
            # Hit testing matches linear scan near end of 3-hour timeline.
            target_hit = next(h for h in tl.hit if h[:2] == ('captions', 1495))
            found = tl.item_at(target_hit[2] + target_hit[4] / 2, target_hit[3] + target_hit[5] / 2)
            self.assertEqual(found[:2], ('captions', 1495))
            # Snap lookup works near a distant clip boundary.
            tl.drag = {'mode': 'edit', 'kind': 'captions', 'index': 1495, 'x': 100 + 10465 * 28,
                       'edge': None, 'start': 10465.0, 'boundary': 100 + 10465 * 28}
            near_boundary_x = 100 + (195 * 54.0) * 28 + 3  # within 8px snap threshold
            snapped_delta = tl.drag_delta(near_boundary_x)
            self.assertAlmostEqual(10465.0 + snapped_delta, 195 * 54.0, places=5)
            tl.drag = None
            # Playhead-strip draw stays well under the 50 ms budget (typically < 1 ms).
            t0 = time.monotonic()
            for _ in range(10):
                cr2 = cairo.Context(surface)
                cr2.rectangle(100 + 10700 * 28 - 8, 0, 17, 300)
                cr2.clip()
                tl.draw(tl, cr2)
            per_draw_ms = (time.monotonic() - t0) * 100.0
            self.assertLess(per_draw_ms, 20.0)


if __name__ == '__main__':
    unittest.main()
