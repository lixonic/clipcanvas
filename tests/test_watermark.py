"""Exercise detection and the actual export/preview filter on compressed video."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipcanvas import engine, model, watermark


class WatermarkTests(unittest.TestCase):
    def test_detect_skip_preview_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary, env = engine._tool('ffmpeg')
            width, height = 1280, 720
            base = bytearray([64]) * (width*height)
            output = root/'marked.mp4'
            def encode(pixels, path):
                subprocess.run([binary, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
                    '-s', '1280x720', '-i', 'pipe:0', '-vf', 'tpad=stop_mode=clone:stop_duration=0.4',
                    '-threads', '1', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', str(path)],
                    input=pixels, env=env, check=True)
            encode(base, root/'clean.mp4')
            self.assertIsNone(watermark.detect(model.new_clip(root/'clean.mp4')))
            side = 48
            for i, alpha in enumerate(watermark._template('720')):
                y, x = divmod(i, side)
                base[(height-120+y)*width+width-120+x] += round(.7*alpha)
            encode(base, output)
            clip = model.new_clip(output)
            clip['sparkle_patch'] = watermark.detect(clip)
            self.assertEqual(clip['sparkle_patch'], {'template':'720', 'offset':120})
            p = model.new_project(width,height)
            p['clips'] = [clip]
            model.validate_project(p)
            # The flat background makes any surviving mark measurable.
            picture = engine.preview_frame(p,.1,preview_edge=0)['pixels']
            repaired = [picture[3*((height-120+y)*width+width-120+x)]
                        for y in range(side) for x in range(side)]
            self.assertLess(sum(abs(value-64) for value in repaired)/len(repaired), 2)
            self.assertLess(max(repaired), 85)
            target = root/'exported.mp4'
            engine.render(p, target)
            self.assertIsNone(watermark.detect(model.new_clip(target)))

    def test_two_sparkles_iterative_detection_preview_export_and_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary, env = engine._tool('ffmpeg')
            width, height = 1280, 720
            side = 48
            base = bytearray([64]) * (width * height)
            template = watermark._template('720')
            # Place one sparkle at a standard offset and a second shifted sparkle nearby.
            for ox, oy, strength in ((144, 144, 0.6), (111, 115, 0.75)):
                for i, alpha in enumerate(template):
                    y, x = divmod(i, side)
                    idx = (height - oy + y) * width + (width - ox + x)
                    base[idx] = min(255, base[idx] + round(strength * alpha))
            source = root / 'double.mp4'
            subprocess.run([binary, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
                '-s', '1280x720', '-i', 'pipe:0', '-vf', 'tpad=stop_mode=clone:stop_duration=0.4',
                '-threads', '1', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', str(source)],
                input=base, env=env, check=True)
            clip = model.new_clip(source)
            first = watermark.detect(clip)
            self.assertIsNotNone(first)
            clip['sparkle_patch'] = watermark.merge_patches(clip.get('sparkle_patch'), first)
            second = watermark.detect(clip)
            self.assertIsNotNone(second)
            clip['sparkle_patch'] = watermark.merge_patches(clip.get('sparkle_patch'), second)
            self.assertIsInstance(clip['sparkle_patch'], list)
            self.assertEqual(len(clip['sparkle_patch']), 2)
            # A third scan on the already-repaired clip finds no remaining sparkle.
            self.assertIsNone(watermark.detect(clip))
            project = model.new_project(width, height)
            project['clips'] = [clip]
            model.validate_project(project)
            saved = root / 'double.clip'
            model.save_project(project, saved)
            reloaded = model.load_project(saved)
            self.assertEqual(reloaded['clips'][0]['sparkle_patch'], clip['sparkle_patch'])
            picture = engine.preview_frame(reloaded, .1, preview_edge=0)['pixels']
            for ox, oy in ((144, 144), (111, 115)):
                repaired = [picture[3 * ((height - oy + y) * width + (width - ox + x))]
                            for y in range(side) for x in range(side)]
                self.assertLess(sum(abs(value - 64) for value in repaired) / len(repaired), 2.5)
                self.assertLess(max(repaired), 85)
            target = root / 'double-exported.mp4'
            engine.render(reloaded, target)
            self.assertIsNone(watermark.detect(model.new_clip(target)))

    def test_disappearing_static_sparkle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary, env = engine._tool('ffmpeg')
            width, height, fps = 1280, 720, 24
            side = 48
            ox, oy = 144, 154
            template = watermark._template('2026-48')
            raw = bytearray()
            for idx in range(48):
                frame = bytearray([64]) * (width * height)
                if idx < 6:
                    for i, alpha in enumerate(template):
                        y, x = divmod(i, side)
                        frame[(height - oy + y) * width + (width - ox + x)] = min(255, 64 + round(0.8 * alpha))
                elif idx >= 20:
                    # Place a distinct bright square where the sparkle used to be; it must stay untouched.
                    for y in range(18, 30):
                        for x in range(18, 30):
                            frame[(height - oy + y) * width + (width - ox + x)] = 200
                raw.extend(frame)
            source = root / 'transient.mp4'
            subprocess.run([binary, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
                '-s', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0',
                '-threads', '1', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', str(source)],
                input=bytes(raw), env=env, check=True)
            clip = model.new_clip(source)
            patch = watermark.detect(clip)
            self.assertIsNotNone(patch)
            self.assertIn('start', patch)
            self.assertIn('end', patch)
            self.assertLess(patch['end'], 0.5)
            clip['sparkle_patch'] = patch
            self.assertIsNone(watermark.detect(clip))
            project = model.new_project(width, height)
            project['fps'] = float(fps)
            project['clips'] = [clip]
            model.validate_project(project)
            saved = root / 'transient.clip'
            model.save_project(project, saved)
            reloaded = model.load_project(saved)
            self.assertEqual(reloaded['clips'][0]['sparkle_patch'], patch)
            early = engine.preview_frame(reloaded, 0.0, preview_edge=0)['pixels']
            repaired = [early[3 * ((height - oy + y) * width + (width - ox + x))]
                        for y in range(side) for x in range(side)]
            self.assertLess(sum(abs(v - 64) for v in repaired) / len(repaired), 2.5)
            self.assertLess(max(repaired), 85)
            late = engine.preview_frame(reloaded, 1.2, preview_edge=0)['pixels']
            marker = [late[3 * ((height - oy + y) * width + (width - ox + x))]
                      for y in range(20, 28) for x in range(20, 28)]
            self.assertGreater(sum(marker) / len(marker), 180)
            target = root / 'transient-exported.mp4'
            engine.render(reloaded, target)
            self.assertIsNone(watermark.detect(model.new_clip(target)))

    def test_drifting_and_disappearing_sparkle_and_split(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary, env = engine._tool('ffmpeg')
            width, height, fps = 1280, 720, 24
            side = 48
            template = watermark._template('2026-48')
            raw = bytearray()
            positions = {}
            for idx in range(60):
                frame = bytearray([64]) * (width * height)
                if idx < 30:
                    ox = 140 - idx // 2
                    oy = 144 - idx // 2
                    positions[idx] = (ox, oy)
                    for i, alpha in enumerate(template):
                        y, x = divmod(i, side)
                        frame[(height - oy + y) * width + (width - ox + x)] = min(255, 64 + round(0.85 * alpha))
                elif idx < 34:
                    ox, oy = 125, 129
                    strength = 0.85 * (34 - idx) / 5.0
                    for i, alpha in enumerate(template):
                        y, x = divmod(i, side)
                        frame[(height - oy + y) * width + (width - ox + x)] = min(255, 64 + round(strength * alpha))
                raw.extend(frame)
            source = root / 'drifting.mp4'
            subprocess.run([binary, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
                '-s', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0',
                '-threads', '1', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', str(source)],
                input=bytes(raw), env=env, check=True)
            clip = model.new_clip(source)
            patch = watermark.detect(clip)
            self.assertIsNotNone(patch)
            self.assertIn('segments', patch)
            self.assertGreaterEqual(len(patch['segments']), 3)
            self.assertLess(patch['segments'][-1][1], 2.0)
            clip['sparkle_patch'] = patch
            self.assertIsNone(watermark.detect(clip))
            project = model.new_project(width, height)
            project['fps'] = float(fps)
            project['clips'] = [clip]
            model.validate_project(project)
            saved = root / 'drifting.clip'
            model.save_project(project, saved)
            reloaded = model.load_project(saved)
            self.assertEqual(reloaded['clips'][0]['sparkle_patch'], patch)
            # Check both original and split clips at start, mid-drift, and late-drift.
            split = model.split_item(reloaded, 'clips', 0, 0.5)
            for candidate in (reloaded, split):
                for frame_idx in (0, 14, 26):
                    ox, oy = positions[frame_idx]
                    pic = engine.preview_frame(candidate, frame_idx / fps, preview_edge=0)['pixels']
                    repaired = [pic[3 * ((height - oy + y) * width + (width - ox + x))]
                                for y in range(side) for x in range(side)]
                    self.assertLess(sum(abs(v - 64) for v in repaired) / len(repaired), 2.5)
                    self.assertLess(max(repaired), 85)
            target = root / 'drifting-exported.mp4'
            engine.render(reloaded, target)
            self.assertIsNone(watermark.detect(model.new_clip(target)))

    def test_validate_and_merge_patches(self):
        single = {'template': '720', 'offset': 120}
        shifted = {'template': '2026-48', 'offset_x': 111, 'offset_y': 115}
        timed = {'template': '2026-48', 'offset_x': 143, 'offset_y': 154, 'start': 0.0, 'end': 0.25}
        tracked = {'template': '2026-48', 'segments': [[0.0, 0.5, 139, 145], [0.5, 1.0, 135, 141]]}
        watermark.validate(single)
        watermark.validate(shifted)
        watermark.validate(timed)
        watermark.validate(tracked)
        watermark.validate([single, shifted, timed, tracked])
        self.assertEqual(watermark.merge_patches(None, single), single)
        self.assertEqual(watermark.merge_patches(single, single), single)
        self.assertEqual(watermark.merge_patches(single, shifted), [single, shifted])
        self.assertEqual(watermark.merge_patches(timed, timed), timed)
        self.assertEqual(watermark.merge_patches(tracked, tracked), tracked)
        self.assertEqual(watermark.merge_patches(single, tracked), [single, tracked])
        for bad in ([], 'bad', {'template': '720', 'offset': True},
                    {'template': '720', 'offset': 40},
                    {'template': '720', 'offset': 250},
                    {'template': '720', 'offset': 120, 'offset_x': 120},
                    {'template': 'unknown', 'offset': 120},
                    {'template': '720', 'offset': 120, 'start': 0.0},
                    {'template': '720', 'offset': 120, 'start': -0.1, 'end': 1.0},
                    {'template': '720', 'offset': 120, 'start': 1.0, 'end': 1.0},
                    {'template': '720', 'offset': 120, 'start': True, 'end': 1.0},
                    {'template': '720', 'segments': []},
                    {'template': '720', 'segments': [[0.0, 0.5, 40, 120]]},
                    {'template': '720', 'segments': [[0.5, 1.0, 120, 120], [0.2, 0.8, 120, 120]]},
                    {'template': '720', 'segments': [[0.0, 0.5, 120, 120]], 'offset': 120}):
            with self.assertRaises(ValueError):
                watermark.validate(bad)

    def test_manual_region_preview_export_and_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary, env = engine._tool('ffmpeg')
            source = root/'source.mp4'
            subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i',
                            'color=gray:s=320x180:d=0.4,drawbox=x=40:y=60:w=20:h=12:c=white:t=fill',
                            '-threads', '1', str(source)], check=True, env=env)
            clip = model.new_clip(source)
            clip['watermark_region'] = [36/320, 56/180, 64/320, 76/180]
            project = model.new_project(320, 180)
            project['clips'] = [clip]
            path = root/'edit.clip'
            model.save_project(project, path)
            project = model.load_project(path)
            self.assertEqual(project['clips'][0]['watermark_region'], clip['watermark_region'])
            target = root/'export.mp4'
            engine.render(project, target)
            exported = model.new_project(320, 180)
            exported['clips'] = [model.new_clip(target)]
            for timeline in (project, exported):
                pixels = engine.preview_frame(timeline, .1, preview_edge=0)['pixels']
                center = pixels[3*(66*320+50)]
                background = pixels[3*(66*320+20)]
                self.assertLess(abs(center-background), 5)
            engine.thumbnail(source, root/'edge.png', watermark_region=[0, 0, .2, .2])

    def test_invalid_manual_regions(self):
        for region in ([0, 0, 0, .1], [0, 0, 2, 1], [False, 0, 1, 1],
                       [0, float('nan'), .5, .5], 'invalid'):
            with self.assertRaises(ValueError):
                watermark.validate_region(region)
