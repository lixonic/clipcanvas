"""Crop playback must not allocate a magnified frame for every timeline clip."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipcanvas import engine, model
from tools.benchmark_crop_memory import generated_project, measure


@unittest.skipUnless(Path('/proc/self/status').is_file(), 'Linux process memory accounting is required')
class CropMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='clipcanvas-crop-memory-tests-')
        cls.root = Path(cls.scratch.name)
        cls.source = cls.root/'source.mp4'
        binary, env = engine._tool('ffmpeg')
        subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=s=320x180:r=30:d=0.5', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-threads', '1', str(cls.source)], env=env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_many_moved_400_percent_clips_finish_under_safe_memory_ceiling(self):
        # The former graph crossed 300 MiB before producing a single picture
        # for this small timeline; high-resolution real projects exhausted RAM.
        # The watchdog kills a regression promptly instead of risking CI/host RAM.
        for offset in (0, .3):
            for transition, frames in (('none', 150), ('fade', 96)):
                with self.subTest(offset=offset, transition=transition):
                    project = generated_project(self.source, clips=10, zoom=4,
                                                offset=offset, transition=transition)
                    result = measure(project, memory_limit_mib=300, timeout=20)
                    self.assertIsNone(result['stopped'], str(result))
                    self.assertEqual(result['returncode'], 0, str(result))
                    self.assertEqual(result['frames'], frames, str(result))
                    self.assertLess(result['peak_rss_mib'], 300, str(result))

    def test_animated_cropped_photos_keep_a_bounded_working_image(self):
        photo=self.root/'motion-source.png'
        engine.thumbnail(self.source,photo,width=320,height=180)
        for motion in ('zoom_in','zoom_out'):
            with self.subTest(motion=motion):
                project=model.new_project(960,540)
                project['clips']=[model.new_image(photo,.5) for _ in range(6)]
                for clip in project['clips']:
                    clip.update(zoom=4,offset_x=.3,offset_y=-.15,motion=motion,zoom_amount=.5)
                result=measure(project,memory_limit_mib=300,timeout=20)
                self.assertIsNone(result['stopped'],str(result))
                self.assertEqual(result['returncode'],0,str(result))
                self.assertEqual(result['frames'],90,str(result))
