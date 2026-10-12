"""Check the actual decoded transition frames, not just filter strings."""

import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipcanvas import engine, transitions


class TransitionCatalogTests(unittest.TestCase):
    def test_catalog_is_complete_and_legacy_renders_are_preserved(self):
        self.assertEqual(tuple(s["label"] for s in transitions.CATALOG),
                         ("None", "Dissolve", "Slide", "Circle Wipe", "Colour Wipe",
                          "Line Wipe", "Match & Move", "Flow", "Stack"))
        for old, new in (("wipeleft", "line_wipe"), ("slideright", "slide"), ("dissolve", "fade")):
            self.assertEqual(transitions.canonical_key(old), new)
            self.assertIn(f"transition={old}:", transitions.join_filter({"transition": old}, .5, 1))
        self.assertIn("objects inside a video cannot be matched", transitions.get_spec("match_move")["description"])

    def test_untrusted_project_settings_cannot_enter_filter_code(self):
        for key, value in (("transition", "fade;movie=/etc/passwd"),
                           ("transition_direction", "left:expr=bad"),
                           ("transition_colour", "#ff0000';movie=bad")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                transitions.join_filter({"transition": "line_wipe", key: value}, .4, 1)
        for overlap, offset in ((float("nan"), 1), (.4, float("inf")), (-1, 0), (0, 1), (.4, -1)):
            with self.subTest(overlap=overlap, offset=offset), self.assertRaises(ValueError):
                transitions.join_filter({"transition": "fade"}, overlap, offset)


class TransitionFrameTests(unittest.TestCase):
    width, height, fps = 96, 64, 20

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="clipcanvas-transition-tests-")
        cls.root = Path(cls.scratch.name)
        cls.ffmpeg, cls.env = engine._tool("ffmpeg")
        for name, colour, pattern in (("out", "red", "drawbox=x=0:y=0:w=18:h=ih:color=white:t=fill"),
                                      ("in", "blue", "drawbox=x=iw-18:y=0:w=18:h=ih:color=black:t=fill")):
            subprocess.run([cls.ffmpeg, "-v", "error", "-y", "-filter_threads", "1",
                            "-f", "lavfi", "-i", f"color=c={colour}:s={cls.width}x{cls.height}:r={cls.fps}:d=1.4",
                            "-vf", pattern, "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", "-threads", "1", str(cls.root / f"{name}.mp4")],
                           env=cls.env, check=True, capture_output=True)
        cls.source_hashes = {name: hashlib.sha256((cls.root / f"{name}.mp4").read_bytes()).hexdigest()
                             for name in ("out", "in")}

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def render(self, effect, direction="left", colour="#20d040"):
        dest = self.root / f"{effect}-{direction}-{colour[1:]}.mp4"
        join = transitions.join_filter({"transition": effect, "transition_direction": direction,
                                        "transition_colour": colour}, .6, .8)
        graph = f"[0:v]format=yuv444p,settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]format=yuv444p,settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]{join}[out]"
        result = subprocess.run([self.ffmpeg, "-v", "error", "-y", "-filter_complex_threads", "1",
                                 "-i", str(self.root / "out.mp4"), "-i", str(self.root / "in.mp4"),
                                 "-filter_complex", graph, "-map", "[out]", "-c:v", "libx264",
                                 "-crf", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "1", str(dest)],
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode()[-2000:])
        raw = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(dest), "-vf", "format=rgb24",
                              "-threads", "1", "-f", "rawvideo", "pipe:1"], env=self.env, capture_output=True, check=True).stdout
        stride = self.width * self.height * 3
        frames = [raw[i:i + stride] for i in range(0, len(raw), stride)]
        self.assertTrue(all(len(frame) == stride for frame in frames))
        self.assertAlmostEqual(engine.probe(dest)["duration"], 2.2, delta=1 / self.fps)
        return frames

    def pixel(self, frame, x, y):
        at = (y * self.width + x) * 3
        return tuple(frame[at:at + 3])

    def assert_colour(self, frame, x, y, expected, delta=8):
        got = self.pixel(frame, x, y)
        self.assertTrue(all(abs(a - b) <= delta for a, b in zip(got, expected)), f"{got} != {expected}")

    def test_real_frames_endpoints_and_distinct_clip_motion(self):
        rendered = {}
        for spec in transitions.CATALOG:
            effect = spec["key"]
            if effect == "none":
                continue
            with self.subTest(effect=effect):
                frames = self.render(effect)
                self.assert_colour(frames[5], 48, 32, (255, 0, 0))
                self.assert_colour(frames[35], 48, 32, (0, 0, 255))
                rendered[effect] = frames[22]
        # Match & Move's geometry belongs to editable layers, not these baked
        # test clips. Its background dissolve is deliberately explicit.
        self.assertEqual(rendered.pop("match_move"), rendered["fade"])
        self.assertEqual(len({hashlib.sha256(frame).hexdigest() for frame in rendered.values()}), len(rendered))
        self.assert_colour(rendered["circle_wipe"], 48, 32, (0, 0, 255))
        self.assert_colour(rendered["circle_wipe"], 22, 3, (255, 0, 0))
        self.assert_colour(rendered["colour_wipe"], 48, 32, (32, 208, 64))
        for name, digest in self.source_hashes.items():
            self.assertEqual(hashlib.sha256((self.root / f"{name}.mp4").read_bytes()).hexdigest(), digest)

    def test_direction_and_colour_reach_real_frames(self):
        # At a quarter of the wipe, the incoming scene starts at the selected
        # edge, with a narrow user-coloured boundary. Colour Wipe instead
        # fills half the canvas with colour before revealing the new scene.
        for direction, incoming, outgoing in (("left", (74, 32), (24, 32)),
                                               ("right", (20, 32), (74, 32)),
                                               ("up", (48, 52), (48, 10)),
                                               ("down", (48, 10), (48, 52))):
            with self.subTest(direction=direction):
                frames = self.render("line_wipe", direction, "#20d040")
                self.assert_colour(frames[19], *incoming, (0, 0, 255))
                self.assert_colour(frames[19], *outgoing, (255, 0, 0))
                colour_frames = self.render("colour_wipe", direction, "#20d040")
                self.assert_colour(colour_frames[19], *incoming, (32, 208, 64))
                self.assert_colour(colour_frames[19], *outgoing, (255, 0, 0))
        for colour, expected in (("#ffffff", (255, 255, 255)), ("#000000", (0, 0, 0)), ("#20d040", (32, 208, 64))):
            with self.subTest(colour=colour):
                self.assert_colour(self.render("colour_wipe", colour=colour)[22], 48, 32, expected)

    def test_public_engine_preview_and_export_use_same_transition(self):
        from clipcanvas import model
        for effect in ("circle_wipe", "colour_wipe", "line_wipe", "flow", "stack", "slide"):
            with self.subTest(effect=effect):
                project = model.new_project(self.width, self.height)
                project["clips"] = [model.new_clip(self.root / "out.mp4"), model.new_clip(self.root / "in.mp4")]
                project["clips"][0].update(transition=effect, transition_duration=.6,
                                             transition_direction="down", transition_colour="#20d040")
                snapshot = repr(project)
                output = self.root / f"engine-{effect}.mkv"
                engine.render(project, output, quality="lossless")
                self.assertAlmostEqual(engine.probe(output)["duration"], model.project_duration(project), delta=.06)
                exported = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(output), "-ss", "1.1",
                                           "-frames:v", "1", "-threads", "1", "-pix_fmt", "rgb24",
                                           "-f", "rawvideo", "pipe:1"], env=self.env, capture_output=True, check=True).stdout
                frame = engine.preview_frame(project, 1.1, preview_edge=0)
                self.assertEqual(frame["pixels"], exported, "Paused preview must use the export effect")
                live = b"".join(engine.live_stream(project, start=1.1, preview_edge=0))
                decoded = subprocess.run([self.ffmpeg, "-v", "error", "-i", "pipe:0", "-frames:v", "1",
                                          "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                                         input=live, env=self.env, capture_output=True, check=True).stdout
                self.assertEqual(decoded, exported, "Live playback must use the export effect")
                self.assertEqual(repr(project), snapshot)


if __name__ == "__main__":
    unittest.main()
