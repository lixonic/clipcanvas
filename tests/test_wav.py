"""WAV soundtrack regression checks using generated media, never user files."""

import array
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipcanvas import engine, model


class WavSoundtrackTests(unittest.TestCase):
    FORMATS = (
        ("pcm16-mono.wav", "pcm_s16le", 44100, 1, 310),
        ("pcm24-stereo.wav", "pcm_s24le", 48000, 2, 470),
        ("float32-stereo.WAV", "pcm_f32le", 48000, 2, 730),
    )

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="clipcanvas-wav-tests-")
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.root = Path(cls.scratch.name)
        cls.ffmpeg, cls.env = engine._tool("ffmpeg")
        base = [cls.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-filter_threads", "1"]
        subprocess.run(base + [
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-pix_fmt", "yuv420p", str(cls.root / "silent-video.mp4"),
        ], env=cls.env, check=True, capture_output=True)
        for name, codec, rate, channels, frequency in cls.FORMATS:
            # A different tone in the first half-second makes an incorrect
            # source trim detectable in the exported sound, not just metadata.
            source = (f"aevalsrc=if(lt(t\\,0.5)\\,"
                      f"0.125*sin(2*PI*{frequency * 2}*t)\\,"
                      f"0.125*sin(2*PI*{frequency}*t)):s={rate}:d=2.5")
            subprocess.run(base + [
                "-f", "lavfi", "-i", source, "-c:a", codec, "-ar", str(rate),
                "-ac", str(channels), str(cls.root / name),
            ], env=cls.env, check=True, capture_output=True)

    def project(self):
        project = model.new_project(160, 90)
        project["clips"].append(model.new_clip(self.root / "silent-video.mp4"))
        for index, (name, _, _, _, _) in enumerate(self.FORMATS):
            track = model.new_audio(self.root / name)
            track.update({"in": 0.6, "out": 2.1, "start": 0.25 + index * 0.35,
                          "volume": 0.5, "fade_in": 0.2, "fade_out": 0.2})
            project["audio"].append(track)
        return project

    def test_wav_import_waveform_and_project_roundtrip(self):
        for name, _, _, _, _ in self.FORMATS:
            with self.subTest(wav=name):
                metadata = engine.probe(self.root / name)
                self.assertTrue(metadata["has_audio"])
                self.assertFalse(metadata["has_video"])
                self.assertAlmostEqual(metadata["duration"], 2.5, delta=0.01)
                waveform = engine.waveform(self.root / name, max_points=50)
                self.assertEqual(len(waveform), 50)
                self.assertTrue(all(math.isfinite(value) and 0 <= value <= 1
                                    for value in waveform))
                self.assertGreater(sum(waveform), 40)
        project = self.project()
        self.assertTrue(model.validate_project(project, require_clips=True))
        saved = self.root / "wav-layers.clipcanvas"
        model.save_project(project, saved)
        self.assertEqual(model.load_project(saved), project)

    def test_wav_layers_trim_offsets_and_fades_in_export(self):
        project = self.project()
        output = self.root / "wav-layers.mp4"
        engine.render(project, output, quality="high")
        metadata = engine.probe(output)
        self.assertTrue(metadata["has_audio"])
        self.assertTrue(metadata["has_video"])
        self.assertAlmostEqual(metadata["duration"], 3.0, delta=0.07)
        raw = subprocess.run([
            self.ffmpeg, "-v", "error", "-i", str(output), "-vn", "-ar", "8000",
            "-ac", "1", "-f", "f32le", "pipe:1",
        ], env=self.env, check=True, capture_output=True).stdout
        samples = array.array("f")
        samples.frombytes(raw)
        self.assertGreaterEqual(len(samples), 3 * 8000)

        def amplitude(at, frequency, duration=0.1):
            segment = samples[round(at * 8000):round((at + duration) * 8000)]
            real = sum(value * math.cos(2 * math.pi * frequency * i / 8000)
                       for i, value in enumerate(segment))
            imag = sum(value * math.sin(2 * math.pi * frequency * i / 8000)
                       for i, value in enumerate(segment))
            return 2 * math.hypot(real, imag) / len(segment)

        for index, (name, _, _, _, frequency) in enumerate(self.FORMATS):
            with self.subTest(wav=name):
                start = project["audio"][index]["start"]
                # All three WAV encodings remain audible when layered together.
                steady = amplitude(1.2, frequency)
                self.assertGreater(steady, 0.035)
                self.assertLess(amplitude(start - 0.15, frequency), 0.002)
                self.assertLess(amplitude(start + 0.02, frequency), steady * 0.65)
                self.assertLess(amplitude(start + 1.38, frequency), steady * 0.65)
                self.assertLess(amplitude(start + 1.55, frequency), 0.002)
                # The doubled opening tone must have been removed by the trim.
                self.assertLess(amplitude(start + 0.2, frequency * 2), 0.003)
        tail = samples[round(2.7 * 8000):round(2.9 * 8000)]
        self.assertLess(math.sqrt(sum(value * value for value in tail) / len(tail)),
                        0.0001)


if __name__ == "__main__":
    unittest.main()
