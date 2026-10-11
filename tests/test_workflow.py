"""Saved library, clipboard transactions and persistent desktop preferences."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from clipcanvas import model, workflow, storage
from clipcanvas.preferences import Preferences


class WorkflowTests(unittest.TestCase):
    def test_group_clipboard_preserves_relative_timing_fresh_ids_and_effects(self):
        p=model.new_project();p['clips']=[model.new_clip('/tmp/a.mp4',{'duration':5,'fps':30})]
        p['audio']=[model.new_audio('/tmp/voice.wav',{'duration':2})]
        p['audio'][0].update(start=2,source_clip_id=p['clips'][0]['id'],audio_effects={'echo_enabled':True,'echo_delay_ms':500})
        p['layers']=[model.new_text('Title',3,4)];p['captions']=[model.new_caption('Words',3.5,4.5)]
        selection={(group,item['id']) for group in workflow.GROUPS for item in p[group]}
        before=copy.deepcopy(p);text=workflow.copy_items(p,selection)
        new,selected=workflow.paste_items(p,text,5,1)
        self.assertEqual(p,before);self.assertEqual(len(selected),4)
        self.assertEqual(new['audio'][1]['start'],7)
        self.assertEqual(new['layers'][1]['start'],8);self.assertEqual(new['layers'][1]['end'],9)
        self.assertEqual(new['captions'][1]['start'],8.5)
        self.assertEqual(new['audio'][1]['source_clip_id'],new['clips'][1]['id'])
        self.assertEqual(new['audio'][1]['audio_effects'],p['audio'][0]['audio_effects'])
        self.assertTrue(selection.isdisjoint(selected))
        new['audio'][1]['audio_effects']['echo_delay_ms']=700
        self.assertEqual(p,before)
        for bad in ('normal text',workflow.CLIPBOARD_PREFIX+'{}',workflow.CLIPBOARD_PREFIX+'{"items":[{}]}'):
            with self.assertRaises(ValueError):workflow.paste_items(p,bad,0,0)
        self.assertEqual(p,before)

    def test_library_roundtrip_collection_migration_and_original_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'unused.wav';source.write_bytes(b'original')
            p=model.new_project();p['assets']=[model.new_asset(model.new_audio(source,{'duration':4}),'audio')]
            path=root/'library.clipcanvas';model.save_project(p,path)
            self.assertEqual(model.load_project(path),p)
            self.assertFalse(p['audio']);self.assertFalse(p['clips'])
            with self.assertRaises(ValueError):model.save_project(p,source)
            collected=model.load_project(storage.collect_project(p,root/'portable'))
            self.assertTrue(Path(collected['assets'][0]['path']).is_file())
            self.assertEqual(Path(collected['assets'][0]['path']).read_bytes(),b'original')
            legacy=model.new_project();legacy['version']=4;legacy.pop('assets')
            legacy['audio']=[model.new_audio(source,{'duration':4})]
            path.write_text(json.dumps(legacy));loaded=model.load_project(path)
            self.assertEqual(loaded['version'],5);self.assertEqual(loaded['assets'][0]['kind'],'audio')
            self.assertEqual(loaded['assets'][0]['path'],str(source))
            legacy['audio']=[{}];path.write_text(json.dumps(legacy))
            with self.assertRaises(ValueError):model.load_project(path)

    def test_preferences_restart_valid_folders_and_invalid_file_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);media=root/'media';media.mkdir();projects=root/'projects';projects.mkdir()
            prefs=Preferences(root/'config');prefs.remember_folder('media',media)
            prefs.remember_folder('project',projects);prefs.set('last_voice_effects',{'echo_delay_ms':750})
            restored=Preferences(root/'config')
            self.assertEqual(restored.folder('media'),str(media));self.assertEqual(restored.folder('project'),str(projects))
            self.assertEqual(restored.folder('export'),str(projects))
            self.assertEqual(restored.get('last_voice_effects')['echo_delay_ms'],750)
            media.rmdir();self.assertEqual(restored.folder('media'),str(projects))
            prefs.path.write_text('invalid');self.assertEqual(Preferences(root/'config').values,{})
