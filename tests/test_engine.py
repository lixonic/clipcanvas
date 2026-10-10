"""Repeatable integration checks using small, generated, copyright-free media.

Run from the source directory: python3 -m unittest discover -s tests -v
Requires a working FFmpeg/ffprobe installation; no network or user media.
"""

import array
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest

from clipcanvas import engine, model


class EngineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="clipcanvas-tests-")
        cls.root = Path(cls.scratch.name)
        cls.ffmpeg, cls.env = engine._tool("ffmpeg")
        base = [cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1"]
        sources = {
            "landscape.mp4": ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1.5",
                              "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.5", "-c:a", "aac"],
            "portrait.mp4": ["-f", "lavfi", "-i", "testsrc2=s=180x320:r=25:d=2"],
            "third.mp4": ["-f", "lavfi", "-i", "color=c=blue:s=240x136:r=24:d=1.5"],
        }
        for name, args in sources.items():
            subprocess.run(base + args + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                           "-threads", "1", str(cls.root / name)], env=cls.env, check=True)
        subprocess.run(base + ["-f", "lavfi", "-i", "sine=frequency=200:duration=4:sample_rate=48000",
                               str(cls.root / "music.wav")], env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def project(self, width=320, height=180, count=2):
        p = model.new_project(width, height)
        for name in ("landscape.mp4", "portrait.mp4", "third.mp4")[:count]:
            p["clips"].append(model.new_clip(self.root / name))
        return p

    def test_all_transitions_both_orientations_and_mixed_cuts(self):
        for effect in model.TRANSITIONS:
            with self.subTest(effect=effect):
                p = self.project(180, 320) if effect in ("fade", "slideright") else self.project()
                p["clips"][0].update(transition=effect, transition_duration=0.4)
                p["clips"][1]["fit"] = "fit"
                out = self.root / f"transition-{effect}.mp4"
                engine.render(p, out, preview=True)
                meta = engine.probe(out)
                self.assertEqual((meta["width"], meta["height"]), (p["width"], p["height"]))
                self.assertAlmostEqual(meta["duration"], model.project_duration(p), delta=0.07)
                self.assertAlmostEqual(meta["fps"], 30, places=2)
                self.assertTrue(meta["has_audio"])
        p = self.project(count=3)
        p["clips"][0]["transition"] = "none"
        p["clips"][1]["transition"] = "dissolve"
        engine.render(p, self.root / "cut-then-transition.mp4", quality="high")
        self.assertAlmostEqual(engine.probe(self.root / "cut-then-transition.mp4")["duration"],
                               model.project_duration(p), delta=0.07)

    def test_layered_audio_offsets_and_levels(self):
        p = self.project(count=1)
        for start, volume in ((0.25, 0.3), (0.75, 0.2)):
            audio = model.new_audio(self.root / "music.wav")
            audio.update(start=start, volume=volume, out=1.5)
            p["audio"].append(audio)
        out = self.root / "layers.mp4"
        engine.render(p, out)
        raw = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(out), "-vn", "-ar", "8000", "-ac", "1",
                              "-f", "f32le", "pipe:1"], env=self.env, stdout=subprocess.PIPE, check=True).stdout
        samples = array.array("f")
        samples.frombytes(raw)

        def amplitude(at, frequency):
            segment = samples[int(at * 8000):int((at + 0.1) * 8000)]
            real = sum(value * math.cos(2 * math.pi * frequency * i / 8000) for i, value in enumerate(segment))
            imag = sum(value * math.sin(2 * math.pi * frequency * i / 8000) for i, value in enumerate(segment))
            return 2 * math.hypot(real, imag) / len(segment)

        before = amplitude(0.05, 200)
        one_layer = amplitude(0.45, 200)
        two_layers = amplitude(1.0, 200)
        self.assertLess(before, 0.002)
        self.assertGreater(one_layer, 0.025)
        self.assertGreater(two_layers, one_layer * 1.3)
        self.assertAlmostEqual(two_layers / one_layer, 5 / 3, delta=0.2)
        self.assertGreater(amplitude(0.05, 440), 0.08)

    def test_cancellation_and_source_protection(self):
        p = self.project()
        source = self.root / "landscape.mp4"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaises(engine.RenderError):
            engine.render(p, source, preview=True)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

        destination = self.root / "cancelled.mp4"
        destination.write_bytes(b"existing destination")
        p["width"], p["height"] = 1920, 1080
        p["clips"] = [copy.deepcopy(p["clips"][0]) for _ in range(6)]
        cancel = threading.Event()

        def progress(fraction, message):
            if fraction > 0:
                cancel.set()

        with self.assertRaises(engine.RenderCancelled):
            engine.render(p, destination, cancel=cancel, progress=progress)
        self.assertEqual(destination.read_bytes(), b"existing destination")
        self.assertFalse(list(self.root.glob(".clipcanvas-*")))

    def test_project_roundtrip_defaults_and_invalid_values(self):
        p = self.project()
        p["audio"].append(model.new_audio(self.root / "music.wav"))
        path = self.root / "saved.clipcanvas"
        model.save_project(p, path)
        self.assertEqual(model.load_project(path), p)
        minimal = {"version": 1, "width": 320, "height": 180,
                   "clips": [{"path": p["clips"][0]["path"], "duration": 1.5}]}
        path.write_text(json.dumps(minimal), encoding="utf-8")
        loaded = model.load_project(path)
        self.assertEqual(loaded["clips"][0]["in"], 0)
        self.assertEqual(loaded["clips"][0]["fit"], "fill")
        self.assertEqual(loaded["clips"][0]["transition"], "none")
        self.assertEqual(loaded["audio"], [])
        minimal["clips"][0]["in"] = "invalid"
        path.write_text(json.dumps(minimal), encoding="utf-8")
        with self.assertRaises(ValueError):
            model.load_project(path)

    def test_portrait_thumbnail_retains_source_aspect(self):
        target = self.root / "portrait-thumb.png"
        engine.thumbnail(self.root / "portrait.mp4", target, width=320, height=180)
        # PNG IHDR stores dimensions at fixed byte offsets, avoiding a GUI dependency.
        width, height = struct.unpack(">II", target.read_bytes()[16:24])
        self.assertLessEqual(width, 320)
        self.assertLessEqual(height, 180)
        self.assertAlmostEqual(width / height, 180 / 320, delta=0.01)


if __name__ == "__main__":
    unittest.main()
