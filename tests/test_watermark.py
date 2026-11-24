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

    def test_validate_and_merge_patches(self):
        single = {'template': '720', 'offset': 120}
        shifted = {'template': '2026-48', 'offset_x': 111, 'offset_y': 115}
        watermark.validate(single)
        watermark.validate(shifted)
        watermark.validate([single, shifted])
        self.assertEqual(watermark.merge_patches(None, single), single)
        self.assertEqual(watermark.merge_patches(single, single), single)
        self.assertEqual(watermark.merge_patches(single, shifted), [single, shifted])
        for bad in ([], 'bad', {'template': '720', 'offset': True},
                    {'template': '720', 'offset': 40},
                    {'template': '720', 'offset': 250},
                    {'template': '720', 'offset': 120, 'offset_x': 120},
                    {'template': 'unknown', 'offset': 120}):
            with self.assertRaises(ValueError):
                watermark.validate(bad)

