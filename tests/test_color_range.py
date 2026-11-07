"""Imports must retain source levels without an implicit color grade."""
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from clipcanvas import color_grading, engine, model, preview_cache
from clipcanvas.scrubber import ScrubSession


def difference(left, right):
    return sum(abs(a-b) for a, b in zip(left, right))/len(left)


class ImportedColorRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='clipcanvas-range-')
        cls.root = Path(cls.folder.name)
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')
        # Dark neutral steps expose crushed shadows; broad color patches expose
        # accidental matrix/transfer changes without chroma-edge noise.
        colors = [(n, n, n) for n in (0, 8, 16, 24, 32, 48, 64, 96)] + [
            (180, 80, 40), (60, 160, 90), (50, 80, 180), (220, 220, 220)]
        pixels = b''.join(bytes(colors[min(11, x//32)]) for y in range(216) for x in range(384))
        cls.paths = {}
        for name, level, transfer in (
                ('full-srgb', 'pc', 'iec61966-2-1'),
                ('full-bt709', 'pc', 'bt709'),
                ('limited', 'tv', 'bt709')):
            path = cls.root/(name+'.mp4')
            subprocess.run([cls.ffmpeg, '-v', 'error', '-y', '-filter_threads', '1',
                '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '384x216', '-r', '24',
                '-i', 'pipe:0', '-vf', f'scale=out_color_matrix=bt709:out_range={level},'
                'tpad=stop_mode=clone:stop_duration=1', '-t', '1',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0', '-threads', '1',
                '-pix_fmt', 'yuvj420p' if level == 'pc' else 'yuv420p',
                '-color_range', level, '-colorspace', 'bt709', '-color_primaries', 'bt709',
                '-color_trc', transfer, str(path)], input=pixels, env=cls.env, check=True)
            cls.paths[name] = path

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def pixels(self, path):
        return subprocess.check_output([self.ffmpeg, '-v', 'error', '-i', str(path),
            '-frames:v', '1', '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], env=self.env)

    def project(self, path):
        project = model.new_project(384, 216)
        project['fps'] = 24
        project['clips'] = [model.new_clip(path)]
        return project

    def test_import_native_seek_composition_and_exports_preserve_source_color(self):
        session = ScrubSession()
        try:
            for name, path in self.paths.items():
                with self.subTest(source=name):
                    project = self.project(path)
                    before = copy.deepcopy(project)
                    digest = hashlib.sha256(path.read_bytes()).digest()
                    self.assertFalse(color_grading.active(project['clips'][0]))
                    self.assertEqual(color_grading.filters(project['clips'][0]), [])
                    self.assertEqual(engine.probe(path)['color_range'], 'tv' if name == 'limited' else 'pc')
                    original = self.pixels(path)
                    for at in (.1, .6, .2):
                        native = session.frame(project, at, preview_edge=0)['pixels']
                        self.assertEqual(session.last_backend, 'native')
                        self.assertLess(difference(native, original), 2)
                    # Forward and backward seeks reuse the native decoder.
                    self.assertEqual(session.open_count, list(self.paths).index(name)+1)
                    composed = engine.preview_frame(project, .2, preview_edge=0)['pixels']
                    self.assertLess(difference(composed, original), 2)
                    for quality, suffix in (('lossless', '.mkv'), ('master', '.mp4')):
                        output = self.root/(name+'-export'+suffix)
                        engine.render(project, output, quality=quality)
                        self.assertLess(difference(self.pixels(output), original), 3)
                    live = self.root/(name+'-live.mkv')
                    with live.open('wb') as output:
                        for chunk in engine.live_stream(project, 0):
                            output.write(chunk)
                    playback = ScrubSession()
                    try:
                        picture = playback._picture(str(live), .2, None)
                        self.assertLess(difference(bytes(picture.get_pixels()), original), 3)
                    finally:
                        playback.close()
                    self.assertEqual(project, before)
                    self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
        finally:
            session.close()
        self.assertIsNone(session._range_filter)

    def test_proxy_uses_its_own_range_and_missing_filter_falls_back(self):
        path = self.paths['full-srgb']
        project = self.project(path)
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as lease:
            with mock.patch.dict(os.environ, {'XDG_CACHE_HOME': cache}):
                proxy = preview_cache.prepare(path, 192, lease)
                self.assertEqual(engine.probe(proxy)['color_range'], 'tv')
                session = ScrubSession()
                try:
                    native = session.frame(project, .2, preview_edge=192)['pixels']
                    self.assertEqual(session.last_backend, 'native')
                    self.assertIsNone(session._range_filter)
                    composed = engine.preview_frame(project, .2, preview_edge=192)['pixels']
                    self.assertLess(difference(native, composed), 2)
                finally:
                    session.close()
        session = ScrubSession()
        try:
            with mock.patch('clipcanvas.scrubber._FullRangeFilter', side_effect=engine.RenderError('Unavailable')):
                result = session.frame(project, .2, preview_edge=0)
            self.assertEqual(session.last_backend, 'composition')
            self.assertLess(difference(result['pixels'], self.pixels(path)), 2)
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
