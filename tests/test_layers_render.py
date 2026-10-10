"""Generated-media checks for the shared canvas layout and offline composites."""

import copy
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import wave

import cairo
from clipcanvas import engine, model, visuals


class VisualRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='clipcanvas-visual-tests-')
        cls.root = Path(cls.scratch.name)
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')
        base = [cls.ffmpeg, '-v', 'error', '-y', '-filter_threads', '1']
        subprocess.run(base + ['-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=30:d=2',
                               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
                               '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', '-c:a', 'aac',
                               str(cls.root/'base.mp4')], env=cls.env, check=True)
        subprocess.run(base + ['-f', 'lavfi', '-i', 'color=c=red:s=160x90:r=30:d=0.4',
                               '-f', 'lavfi', '-i', 'color=c=lime:s=160x90:r=30:d=0.4',
                               '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                               '-c:v', 'libx264', '-threads', '1', str(cls.root/'overlay.mp4')], env=cls.env, check=True)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 80, 80)
        cr = cairo.Context(surface);cr.set_source_rgba(1, 0, 0, .5)
        cr.rectangle(0, 0, 80, 80);cr.fill();surface.write_to_png(str(cls.root/'red.png'))
        cr.set_operator(cairo.OPERATOR_SOURCE);cr.set_source_rgba(0, 1, 0, 1);cr.paint()
        surface.write_to_png(str(cls.root/'green.png'))

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def project(self, width=320, height=180):
        p = model.new_project(width, height)
        p['clips'] = [model.new_clip(self.root/'base.mp4')]
        return p

    def pixels(self, path, at):
        return subprocess.run([self.ffmpeg, '-v', 'error', '-threads', '1', '-i', str(path),
                               '-ss', str(at), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                               '-threads', '1', 'pipe:1'], env=self.env, check=True, stdout=subprocess.PIPE).stdout

    @staticmethod
    def pixel(raw, width, x, y):
        return tuple(raw[(y*width+x)*3:(y*width+x)*3+3])

    def test_transparent_images_keep_timing_and_layer_order_both_canvases(self):
        for width, height in ((320, 180), (180, 320)):
            with self.subTest(canvas=(width, height)):
                p = self.project(width, height)
                first = model.new_visual(self.root/'red.png', start=.3, end=1.4)
                first.update(width=.5, height=.5, fit='fill')
                second = model.new_visual(self.root/'green.png', start=.8, end=1.1)
                second.update(width=.2, height=.2, fit='fill')
                p['layers'] = [first, second]
                output = self.root/f'images-{width}.mp4';engine.render(p, output)
                before = self.pixel(self.pixels(output, .1), width, width//2, height//2)
                red = self.pixel(self.pixels(output, .5), width, width//2, height//2)
                green = self.pixel(self.pixels(output, .9), width, width//2, height//2)
                after = self.pixel(self.pixels(output, 1.6), width, width//2, height//2)
                self.assertGreater(before[2], 220);self.assertLess(before[0], 15)
                self.assertGreater(red[0], 100);self.assertGreater(red[2], 100)
                self.assertGreater(green[1], 230);self.assertLess(green[0], 20)
                self.assertGreater(after[2], 220);self.assertLess(after[0], 15)

    def test_unicode_text_styles_match_native_preview_and_do_not_enter_filter_code(self):
        p = self.project()
        layer = model.new_text('Café “50%” 😀\nمرحبا, [outvideo];', .2, 1.6)
        layer.update(font_size=24, bold=True, italic=True, color='#00ff00', outline_width=1,
                     background='#000000a0', width=.85, height=.72, rotation=12, opacity=.8, align='left')
        p['layers'] = [layer]
        output = self.root/'unicode.mp4';engine.render(p, output, quality='master')
        raw = self.pixels(output, .7)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 180)
        cr = cairo.Context(surface);cr.set_source_rgb(0, 0, 1);cr.paint()
        visuals.draw(cr, p, .7, (0, 0, 320, 180))
        pixels = memoryview(surface.get_data())
        expected = bytes(channel for offset in range(0, len(pixels), 4)
                         for channel in (pixels[offset+2], pixels[offset+1], pixels[offset]))
        self.assertLess(sum(abs(a-b) for a, b in zip(raw, expected))/len(raw), 8)
        self.assertGreater(sum(raw[i+1] > 110 and raw[i+1] > raw[i+2] for i in range(0, len(raw), 3)), 150)
        with tempfile.TemporaryDirectory() as scratch:
            _, graph, _ = engine._graph(p, scratch=scratch)
            self.assertNotIn(layer['text'], graph)
        # Transparent corners outside the rotated box leave the source intact.
        self.assertGreater(self.pixel(raw, 320, 2, 2)[2], 220)

    def test_caption_gaps_exact_timing_and_shared_style(self):
        p = self.project()
        p['captions'] = [model.new_caption('First', .25, .65), model.new_caption('Second', 1, 1.4)]
        p['caption_style'].update(font_size=25, color='#ffff00', background='#000000ff',
                                  outline_width=0, x=.5, y=.8, width=.8, height=.25)
        output = self.root/'captions.mp4';engine.render(p, output)
        def dark(at):
            r = self.pixels(output, at)
            return sum(max(self.pixel(r, 320, x, 144)) < 40 for x in range(35, 285))
        self.assertLess(dark(.1), 5)
        self.assertGreater(dark(.4), 120)
        self.assertLess(dark(.8), 5)
        self.assertGreater(dark(1.2), 100)
        self.assertLess(dark(1.6), 5)
        # Cue count does not create one FFmpeg input per subtitle.
        p['captions'] = [model.new_caption(str(i), i*.01, (i+1)*.01) for i in range(150)]
        with tempfile.TemporaryDirectory() as scratch:
            inputs, _, _ = engine._graph(p, scratch=scratch)
            self.assertEqual(inputs.count('-i'), 2)
            self.assertTrue((Path(scratch)/'captions.ffconcat').is_file())

    def test_video_overlay_source_trim_speed_and_freeze_last_frame(self):
        p = self.project()
        layer = model.new_visual(self.root/'overlay.mp4', kind='video', start=.2, end=1.5)
        layer.update(width=.4, height=.6, fit='fit', speed=2, **{'in': .1})
        p['layers'] = [layer]
        output = self.root/'moving-overlay.mp4';engine.render(p, output)
        red = self.pixel(self.pixels(output, .25), 320, 160, 90)
        green = self.pixel(self.pixels(output, .5), 320, 160, 90)
        frozen = self.pixel(self.pixels(output, 1.2), 320, 160, 90)
        after = self.pixel(self.pixels(output, 1.7), 320, 160, 90)
        self.assertGreater(red[0], 220);self.assertLess(red[1], 25)
        self.assertGreater(green[1], 220);self.assertLess(green[0], 25)
        self.assertGreater(frozen[1], 220)
        self.assertGreater(after[2], 220)
        # Fit padding is transparent, not a black panel around the overlay.
        self.assertGreater(self.pixel(self.pixels(output, .5), 320, 160, 43)[2], 220)

    def test_preview_scale_preserves_relative_text_layout(self):
        p = self.project(1920, 1080)
        layer = model.new_text('Title', 0, 2);layer.update(font_size=90, height=.25)
        p['layers'] = [layer]
        output = self.root/'scaled-preview.mp4';engine.render(p, output, preview=True)
        self.assertEqual((engine.probe(output)['width'], engine.probe(output)['height']), (640, 360))
        raw = self.pixels(output, .7)
        self.assertGreater(sum(min(raw[i:i+3]) > 150 for i in range(0, len(raw), 3)), 150)

    def test_speech_audio_restores_muted_video_and_protects_all_media(self):
        p = self.project();p['clips'][0]['volume'] = 0
        # The audio-only path does not decode visual assets or rasterize titles.
        invalid_image = self.root/'image.data';invalid_image.write_bytes(b'not an image')
        p['layers'] = [model.new_visual(invalid_image)]
        original = self.root/'speech.wav';engine.render_audio(p, original, source='video')
        with wave.open(str(original)) as f:
            self.assertEqual((f.getframerate(), f.getnchannels(), f.getsampwidth()), (16000, 1, 2))
            self.assertAlmostEqual(f.getnframes()/f.getframerate(), 2, delta=.05)
            import array
            samples = array.array('h');samples.frombytes(f.readframes(f.getnframes()))
            self.assertGreater(max(abs(s) for s in samples), 2500)
        silent = self.root/'silent.wav';engine.render_audio(p, silent, source='mix')
        with wave.open(str(silent)) as f:
            import array
            samples = array.array('h');samples.frombytes(f.readframes(f.getnframes()))
            self.assertLess(max(abs(s) for s in samples), 2)
        with self.assertRaises(engine.RenderError):
            engine.render_audio(p, invalid_image)
        with self.assertRaises(engine.RenderError):
            engine.render(p, invalid_image)
        existing = self.root/'cancelled.wav';existing.write_bytes(b'keep this')
        cancel = threading.Event();cancel.set()
        with self.assertRaises(engine.RenderCancelled):
            engine.render_audio(p, existing, cancel=cancel)
        self.assertEqual(existing.read_bytes(), b'keep this')
        self.assertEqual(p['clips'][0]['volume'], 0)


if __name__ == '__main__':
    unittest.main()
