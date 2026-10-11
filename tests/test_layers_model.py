"""Visual media must remain portable; titles and captions retain exact text."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from clipcanvas import model, storage


class LayerProjectTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = self.root / "base.mp4"
        self.image = self.root / "logo.png"
        self.overlay = self.root / "inset.mp4"
        for source in (self.source, self.image, self.overlay):
            source.write_bytes(source.name.encode("utf-8"))
        self.project = model.new_project(320, 180)
        self.project["clips"] = [model.new_clip(self.source, {"duration": 10})]
        self.project["layers"] = [
            model.new_text("Hello — नमस्ते\nسلام", 1.25, 3.75),
            model.new_visual(self.image, "image", 2, 12),
            model.new_visual(self.overlay, "video", 0, 4, {"duration": 8}),
        ]
        self.project["captions"] = [model.new_caption("Speech → editable captions", 0.2, 1.6)]

    def test_schema3_unicode_roundtrip_and_visual_source_protection(self):
        original = copy.deepcopy(self.project)
        target = self.root / "video.clipcanvas"
        model.save_project(self.project, target)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], model.PROJECT_VERSION)
        self.assertEqual(data["layers"][1]["path"], self.image.name)
        self.assertEqual(data["layers"][2]["path"], self.overlay.name)
        self.assertEqual(model.load_project(target), original)
        self.assertEqual(self.project, original)
        # Overlay timing cannot silently lengthen the base video.
        self.assertEqual(model.project_duration(self.project), 10)
        for path in (self.image, self.overlay, self.source):
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "original media"):
                model.save_project(self.project, path)
            self.assertEqual(path.read_bytes(), before)

    def test_portable_visual_files_are_copied_once_and_survive_source_deletion(self):
        self.project["layers"].append(model.new_visual(self.image, start=3, end=5))
        target = storage.collect_project(self.project, self.root / "portable")
        self.assertEqual(len(list((Path(target).parent / "Media").iterdir())), 3)
        for source in (self.source, self.image, self.overlay):
            source.unlink()
        relocated = self.root / "moved"
        Path(target).parent.rename(relocated)
        loaded = model.load_project(relocated / "Project.clipcanvas")
        self.assertTrue(model.validate_project(loaded, check_files=True))
        self.assertEqual(Path(loaded["layers"][1]["path"]).read_bytes(), b"logo.png")
        self.assertEqual(Path(loaded["layers"][2]["path"]).read_bytes(), b"inset.mp4")
        self.assertEqual(loaded["layers"][1]["path"], loaded["layers"][3]["path"])
        self.assertEqual(loaded["layers"][0]["text"], self.project["layers"][0]["text"])

    def test_v1_absolute_and_v2_relative_migrate_to_independent_new_defaults(self):
        for version in (1, 2):
            with self.subTest(version=version):
                old = copy.deepcopy(self.project)
                old["version"] = version
                for key in ("layers", "captions", "caption_style"):
                    old.pop(key)
                if version == 2:
                    old["clips"][0]["path"] = self.source.name
                target = self.root / f"version-{version}.clipcanvas"
                target.write_text(json.dumps(old), encoding="utf-8")
                migrated = model.load_project(target)
                self.assertEqual(migrated["version"], model.PROJECT_VERSION)
                self.assertEqual(migrated["clips"][0]["path"], str(self.source))
                self.assertEqual(migrated["layers"], [])
                self.assertEqual(migrated["captions"], [])
                self.assertEqual(migrated["caption_style"], model.caption_style())
        first, second = model.new_project(), model.new_project()
        first["caption_style"]["font_size"] = 100
        first["layers"].append(model.new_text())
        self.assertEqual(second["caption_style"]["font_size"], 48)
        self.assertEqual(second["layers"], [])

    def test_invalid_layers_and_caption_data_are_rejected(self):
        mutations = [
            ("layers", 0, "start", -1),
            ("layers", 0, "end", 1.25),
            ("layers", 0, "x", float("nan")),
            ("layers", 0, "width", 0),
            ("layers", 0, "opacity", True),
            ("layers", 0, "rotation", float("inf")),
            ("layers", 0, "color", "url(bad)"),
            ("layers", 0, "font_size", 0),
            ("layers", 0, "bold", "yes"),
            ("layers", 0, "text", "x" * 10001),
            ("layers", 1, "path", "relative.png"),
            ("layers", 1, "fit", "stretch"),
            ("layers", 2, "in", 8),
            ("layers", 2, "speed", 0),
            ("captions", 0, "end", 0.2),
            ("captions", 0, "start", True),
            ("captions", 0, "text", "x" * 2001),
        ]
        for group, index, key, value in mutations:
            with self.subTest(group=group, key=key, value=str(value)[:30]):
                invalid = copy.deepcopy(self.project)
                invalid[group][index][key] = value
                with self.assertRaises(ValueError):
                    model.validate_project(invalid, check_files=False)
        invalid = copy.deepcopy(self.project)
        invalid["caption_style"]["background"] = "#nope"
        with self.assertRaises(ValueError):
            model.validate_project(invalid)
        for group, limit in (("layers", 500), ("captions", 5000)):
            invalid = copy.deepcopy(self.project)
            invalid[group] = invalid[group][:1] * (limit + 1)
            with self.assertRaisesRegex(ValueError, str(limit)):
                model.validate_project(invalid)
        self.image.unlink()
        with self.assertRaisesRegex(ValueError, "Missing media"):
            model.validate_project(self.project)
        self.assertTrue(model.validate_project(self.project, check_files=False))

    def test_recovery_includes_new_content_and_media_items_are_editable_references(self):
        recovery = storage.Recovery(self.root / "recovery")
        saved = self.root / "saved.clipcanvas"
        recovery.write(self.project, saved)
        reopened, original_path = recovery.read(recovery.path)
        self.assertEqual(reopened, self.project)
        self.assertEqual(original_path, str(saved))
        items = model.media_items(self.project)
        self.assertEqual(items, [self.project["clips"][0], self.project["layers"][1], self.project["layers"][2]])
        items[1]["opacity"] = 0.4
        self.assertEqual(self.project["layers"][1]["opacity"], 0.4)
        self.assertEqual(model.new_visual(self.image)["kind"], "image")
        with self.assertRaises(ValueError):
            model.new_visual(self.image, kind="sound")


if __name__ == "__main__":
    unittest.main()
