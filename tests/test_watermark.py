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
