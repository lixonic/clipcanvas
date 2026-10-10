"""Timeline edits and moving/recovering projects must preserve source timing."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from clipcanvas import model, storage


class ProjectEditingTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'original media')
        self.project = model.new_project(320, 180)
        self.project['clips'] = [model.new_clip(self.source, {'duration': 10})]

    def test_split_uses_speed_and_retains_outgoing_transition(self):
        clip = self.project['clips'][0]
        clip.update(speed=2, transition='fade', transition_duration=.5)
        self.project['clips'].append(model.new_clip(self.source, {'duration': 10}))
        before = copy.deepcopy(self.project)
        result = model.split_item(self.project, 'clips', 0, 2)
        left, right, _ = result['clips']
        self.assertEqual((left['in'], left['out'], right['in'], right['out']), (0, 4, 4, 10))
        self.assertEqual(left['transition'], 'none')
        self.assertEqual(right['transition'], 'fade')
        self.assertNotEqual(left['id'], right['id'])
        self.assertEqual(model.project_duration(result), model.project_duration(before))
        self.assertEqual(self.project, before)
        with self.assertRaises(ValueError):model.split_item(self.project, 'clips', 0, 4.9)
        with self.assertRaises(ValueError):model.split_item(self.project, 'clips', 0, 0)

    def test_audio_split_preserves_placement_and_outer_fades(self):
        audio = model.new_audio(self.source, {'duration': 8})
        audio.update(start=1, **{'in': 2, 'out': 7}, fade_in=.3, fade_out=.4)
        self.project['audio'] = [audio]
        result = model.split_item(self.project, 'audio', 0, 3)
        left, right = result['audio']
        self.assertEqual((left['in'], left['out'], right['in'], right['out']), (2, 4, 4, 7))
        self.assertEqual((left['start'], right['start']), (1, 3))
        self.assertEqual((left['fade_in'], left['fade_out'], right['fade_in'], right['fade_out']), (.3, 0, 0, .4))
        with self.assertRaises(ValueError):model.split_item(self.project, 'audio', 0, 1.1)

    def test_trim_clamps_to_source_and_keeps_audio_in_sync(self):
        self.project['clips'][0]['speed'] = 2
        trimmed = model.trim_item(self.project, 'clips', 0, 'left', 1)
        self.assertEqual(trimmed['clips'][0]['in'], 2)
        self.assertEqual(model.project_duration(trimmed), 4)
        shortened = model.trim_item(trimmed, 'clips', 0, 'right', -99)
        self.assertGreaterEqual(model.clip_duration(shortened['clips'][0]), 1/30-.00001)
        audio = model.new_audio(self.source, {'duration': 8})
        audio.update(start=1, **{'in': 2, 'out': 7})
        self.project['audio'] = [audio]
        left = model.trim_item(self.project, 'audio', 0, 'left', .5)['audio'][0]
        self.assertEqual((left['in'], left['start'], left['out']), (2.5, 1.5, 7))
        extended = model.trim_item(self.project, 'audio', 0, 'left', -99)['audio'][0]
        self.assertEqual((extended['in'], extended['start']), (1, 0))

    def test_portable_copy_moves_without_original_paths_and_rejects_overwrite(self):
        # Identical basenames and repeated references must not overwrite each other.
        other = self.root / 'other/source.mp4'
        other.parent.mkdir();other.write_bytes(b'different original')
        self.project['clips'] += [model.new_clip(other, {'duration': 3}), copy.deepcopy(self.project['clips'][0])]
        portable = self.root / 'portable'
        saved = storage.collect_project(self.project, portable)
        serialized = json.loads(Path(saved).read_text())
        self.assertTrue(all(not Path(c['path']).is_absolute() for c in serialized['clips']))
        self.assertEqual(len(list((portable/'Media').iterdir())), 2)
        with self.assertRaises(ValueError):storage.collect_project(self.project, portable)
        moved = self.root / 'moved'
        portable.rename(moved)
        self.source.unlink();other.unlink()
        loaded = model.load_project(moved/'Project.clipcanvas')
        self.assertTrue(model.validate_project(loaded))
        self.assertEqual(Path(loaded['clips'][0]['path']).read_bytes(), b'original media')
        self.assertEqual(Path(loaded['clips'][1]['path']).read_bytes(), b'different original')

    def test_recovery_survives_a_new_session_and_does_not_replace_saved_project(self):
        saved = self.root/'saved.clipcanvas'
        model.save_project(self.project, saved)
        changed = copy.deepcopy(self.project);changed['clips'][0]['out'] = 5
        recovery = storage.Recovery(self.root/'state')
        recovery.write(changed, saved)
        new_session = storage.Recovery(self.root/'state')
        self.assertEqual(new_session.available(), [recovery.path])
        recovered, original = new_session.read(recovery.path)
        self.assertEqual(recovered, changed)
        self.assertEqual(original, str(saved))
        self.assertEqual(model.load_project(saved), self.project)
        recovery.clear();self.assertEqual(new_session.available(), [])

    def test_legacy_migration_and_invalid_speed(self):
        path = self.root/'old.clipcanvas'
        old = copy.deepcopy(self.project);old['version'] = 1
        for key in ('speed', 'rotation', 'flip_x', 'flip_y'):old['clips'][0].pop(key)
        path.write_text(json.dumps(old))
        migrated = model.load_project(path)
        self.assertEqual(migrated['version'], model.PROJECT_VERSION)
        self.assertEqual(migrated['clips'][0]['speed'], 1)
        for invalid in (0, float('nan'), True, 5):
            migrated['clips'][0]['speed'] = invalid
            with self.assertRaises(ValueError):model.validate_project(migrated)


if __name__ == '__main__':unittest.main()
