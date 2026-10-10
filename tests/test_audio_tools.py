"""Generated-media checks for non-destructive extraction, speed, and fades."""

import array
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipcanvas import engine, model


class AudioToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="clipcanvas-audio-tools-")
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.root = Path(cls.scratch.name)
        cls.ffmpeg, cls.env = engine._tool("ffmpeg")
        base = [cls.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1"]
        for name, tone in (("tone.mkv", "if(lt(t\\,0.5)\\,880\\,440)"),
                           ("second.mkv", "660"), ("silent-track.mkv", "0")):
            subprocess.run(base + [
                "-f", "lavfi", "-i", "color=c=white:s=160x90:r=30:d=4",
                "-f", "lavfi", "-i", f"aevalsrc=0.12*sin(2*PI*({tone})*t):s=48000:d=4",
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(cls.root / name),
            ], env=cls.env, check=True, capture_output=True)
        subprocess.run(base + [
            "-f", "lavfi", "-i", "color=c=white:s=160x90:r=30:d=16",
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-pix_fmt", "yuv420p", str(cls.root / "no-audio.mp4"),
        ], env=cls.env, check=True, capture_output=True)
        subprocess.run(base + [
            "-i", str(cls.root / "tone.mkv"), "-vn", "-c:a", "pcm_s24le",
            str(cls.root / "tone.wav"),
        ], env=cls.env, check=True, capture_output=True)

    def clip(self, name="tone.mkv"):
        return model.new_clip(self.root / name)

    def project(self):
        project = model.new_project(160, 90)
        project["clips"] = [self.clip()]
        return project

    def samples(self, output):
        raw = subprocess.run([
            self.ffmpeg, "-v", "error", "-i", str(output), "-vn", "-ar", "8000",
            "-ac", "1", "-f", "f32le", "pipe:1",
        ], env=self.env, check=True, capture_output=True).stdout
        samples = array.array("f")
        samples.frombytes(raw)
        return samples

    def amplitude(self, samples, at, frequency=440, duration=.1):
        segment = samples[round(at * 8000):round((at + duration) * 8000)]
        self.assertTrue(segment, f"No samples at {at}s")
        real = sum(value * math.cos(2 * math.pi * frequency * i / 8000)
                   for i, value in enumerate(segment))
        imag = sum(value * math.sin(2 * math.pi * frequency * i / 8000)
                   for i, value in enumerate(segment))
        return 2 * math.hypot(real, imag) / len(segment)

    def render_samples(self, project, name):
        target = self.root / name
        engine.render(project, target, preview=True)
        return self.samples(target)

    def test_extract_copies_timing_speed_volume_fades_without_rewriting_source(self):
        project = self.project()
        project["clips"][0].update({"in": .6, "out": 3.6, "speed": 2, "volume": .7,
                                    "fade_in": .25, "fade_out": .3})
        before = self.clip("no-audio.mp4")
        before["out"] = 1.0
        after = copy.deepcopy(before)
        after["id"] = "tail"
        project["clips"] = [before, project["clips"][0], after]
        unchanged = copy.deepcopy(project)
        source = self.root / "tone.mkv"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        files_before = set(self.root.iterdir())
        extracted = model.detach_audio(project, 1)
        self.assertEqual(project, unchanged)
        self.assertEqual(set(self.root.iterdir()), files_before)
        self.assertEqual(extracted["clips"][1]["volume"], 0)
        track = extracted["audio"][-1]
        for key in ("path", "duration", "in", "out", "speed", "volume", "fade_in", "fade_out"):
            self.assertEqual(track[key], project["clips"][1][key])
        self.assertEqual(track["start"], 1.0)
        self.assertEqual(model.clip_duration(track), 1.5)
        expected = self.render_samples(project, "before-extract.mp4")
        actual = self.render_samples(extracted, "after-extract.mp4")
        # Extraction must neither double the level nor change its fade envelope.
        for at in (1.03, 1.5, 2.35):
            self.assertAlmostEqual(self.amplitude(actual, at), self.amplitude(expected, at), delta=.003)
        steady = self.amplitude(actual, 1.5)
        self.assertGreater(steady, .06)
        self.assertLess(self.amplitude(actual, 1.03), steady * .65)
        self.assertLess(self.amplitude(actual, 2.35), steady * .65)
        self.assertLess(self.amplitude(actual, .5), .001)
        self.assertLess(self.amplitude(actual, 2.7), .001)
        self.assertLess(self.amplitude(actual, 1.5, 880), .002)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_extracted_audio_and_wav_support_extreme_speed_without_pitch_change(self):
        for speed in (.25, 4):
            for kind in ("extract", "wav"):
                with self.subTest(speed=speed, kind=kind):
                    project = self.project()
                    project["clips"][0].update({"in": .6, "out": 3.4, "speed": speed})
                    if kind == "extract":
                        project = model.detach_audio(project, 0)
                    else:
                        track = model.new_audio(self.root / "tone.wav")
                        track.update({"in": .6, "out": 3.4, "speed": speed, "start": .5})
                        project["audio"] = [track]
                        project["clips"] = [self.clip("no-audio.mp4")]
                    output = self.root / f"speed-{kind}-{speed}.mp4"
                    engine.render(project, output, preview=True)
                    self.assertAlmostEqual(engine.probe(output)["duration"], model.project_duration(project), delta=.07)
                    samples = self.samples(output)
                    start = project["audio"][0]["start"]
                    at = start + .2
                    strengths = {hz: self.amplitude(samples, at, hz, .2) for hz in range(220, 901, 5)}
                    self.assertAlmostEqual(max(strengths, key=strengths.get), 440, delta=5)
                    self.assertGreater(strengths[440], .08)
                    if kind == "wav":
                        self.assertLess(self.amplitude(samples, .1), .001)
                        end = start + 2.8 / speed
                        self.assertGreater(self.amplitude(samples, end - .25), .06)
                        self.assertLess(self.amplitude(samples, end + .1), .001)

    def test_extraction_retains_transition_envelopes_on_both_sides(self):
        project = self.project()
        project["clips"].append(self.clip("second.mkv"))
        project["clips"][0].update(transition="fade", transition_duration=.6)
        expected = self.render_samples(project, "transition-original.mp4")
        extracted = model.detach_audio(model.detach_audio(project, 0), 1)
        self.assertEqual(extracted["audio"][0]["transition_fade_out"], .6)
        self.assertEqual(extracted["audio"][1]["transition_fade_in"], .6)
        actual = self.render_samples(extracted, "transition-extracted.mp4")
        for at in (3.45, 3.65, 3.85, 4.2):
            for frequency in (440, 660):
                self.assertAlmostEqual(self.amplitude(actual, at, frequency),
                                       self.amplitude(expected, at, frequency), delta=.004)

    def test_no_audio_and_repeated_extraction_are_rejected_without_changes(self):
        project = self.project()
        project["clips"] = [self.clip("no-audio.mp4")]
        before = copy.deepcopy(project)
        with self.assertRaisesRegex(ValueError, "no audio track"):
            model.detach_audio(project, 0)
        self.assertEqual(project, before)
        project = model.detach_audio(self.project(), 0)
        before = copy.deepcopy(project)
        with self.assertRaisesRegex(ValueError, "already been extracted"):
            model.detach_audio(project, 0)
        self.assertEqual(project, before)
        split = model.split_item(project, "audio", 0, 2)
        with self.assertRaisesRegex(ValueError, "already been extracted"):
            model.detach_audio(split, 0)

    def test_a_real_silent_audio_stream_stays_silent_when_extracted(self):
        # Stream absence is an error; a present track containing silence is valid.
        project = self.project()
        project["clips"] = [self.clip("silent-track.mkv")]
        project = model.detach_audio(project, 0)
        samples = self.render_samples(project, "silent-extracted.mp4")
        self.assertLess(max(abs(value) for value in samples), .00001)

    def test_picture_fades_render_black_edges_and_leave_middle_white(self):
        project = self.project()
        project["clips"][0].update({"in": .6, "out": 3.6, "speed": 1.5,
                                    "video_fade_in": .5, "video_fade_out": .5})
        output = self.root / "picture-fades.mp4"
        engine.render(project, output, preview=True)
        def brightness(at):
            pixels = subprocess.run([
                self.ffmpeg, "-v", "error", "-threads", "1", "-ss", str(at), "-i", str(output),
                "-frames:v", "1", "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ], env=self.env, check=True, capture_output=True).stdout
            return sum(pixels) / len(pixels)
        self.assertLess(brightness(0), 5)
        self.assertGreater(brightness(1), 245)
        self.assertLess(brightness(1.95), 45)
        split = model.split_item(project, "clips", 0, 1)
        self.assertEqual(split["clips"][0]["video_fade_out"], 0)
        self.assertEqual(split["clips"][1]["video_fade_in"], 0)
        with self.assertRaisesRegex(ValueError, "picture fade"):
            model.split_item(project, "clips", 0, .1)

    def test_v2_defaults_audio_validation_and_speed_aware_split(self):
        project = self.project()
        track = model.new_audio(self.root / "tone.wav")
        track.update({"in": .5, "out": 3.5, "speed": 2, "start": .2,
                      "fade_in": .1, "fade_out": .1})
        project["audio"] = [track]
        split = model.split_item(project, "audio", 0, .8)
        left, right = split["audio"]
        self.assertAlmostEqual(left["out"], 1.7)
        self.assertAlmostEqual(right["in"], 1.7)
        self.assertEqual(right["start"], .8)
        self.assertEqual((left["fade_out"], right["fade_in"]), (0, 0))
        for speed in (0, float("nan"), True, 4.1):
            invalid = copy.deepcopy(project)
            invalid["audio"][0]["speed"] = speed
            with self.assertRaises(ValueError):
                model.validate_project(invalid)
        for key in ("speed", "fade_in", "fade_out", "transition_fade_in", "transition_fade_out"):
            project["audio"][0].pop(key)
        for key in ("fade_in", "fade_out", "video_fade_in", "video_fade_out"):
            project["clips"][0].pop(key)
        target = self.root / "existing-v2.clipcanvas"
        target.write_text(json.dumps(project), encoding="utf-8")
        migrated = model.load_project(target)
        self.assertEqual(migrated["version"], model.PROJECT_VERSION)
        self.assertEqual(migrated["audio"][0]["speed"], 1)
        for key in ("fade_in", "fade_out", "video_fade_in", "video_fade_out"):
            self.assertEqual(migrated["clips"][0][key], 0)


if __name__ == "__main__":
    unittest.main()
