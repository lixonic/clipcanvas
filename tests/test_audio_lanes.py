"""Shared audio lanes preserve edits, order, clipboard groups and old projects."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from clipcanvas import model, track_layout, workflow


class AudioLaneTests(unittest.TestCase):
    def project(self):
        p=model.new_project();p['clips']=[model.new_card(duration=15)]
        for start in (0,3,0):
            item=model.new_audio('/tmp/voice.wav',{'duration':4})
            item.update(start=start,**{'in':1,'out':3,'speed':2,'volume':.4,'fade_in':.2,
                                       'audio_effects':{'echo_enabled':True,'echo_delay_ms':250}})
            p['audio'].append(item)
        return p

    def test_shared_lane_save_reorder_and_delete_first_clip(self):
        p=self.project();before=copy.deepcopy(p)
        lane=p['audio'][0]['lane_id']
        p=workflow.with_audio_placement(p,1,3,lane)
        self.assertEqual(before['audio'][1]['audio_effects'],p['audio'][1]['audio_effects'])
        expected=copy.deepcopy(before);expected['audio'][1]['lane_id']=lane
        self.assertEqual(p,expected)
        audio_rows=[r for r in track_layout.rows(p) if r[1]=='audio']
        self.assertEqual(len(audio_rows),2)
        p['timeline_order']=track_layout.moved_order(p,'audio:'+lane,-1)
        self.assertEqual(track_layout.rows(p)[0][0],'audio:'+lane)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'lanes.clipcanvas';model.save_project(p,path)
            self.assertEqual(model.load_project(path),p)
        p['audio'].pop(0)
        self.assertEqual(track_layout.rows(p)[0][0],'audio:'+lane)
        self.assertEqual(track_layout.key_for('audio',p['audio'][0]),'audio:'+lane)
        self.assertTrue(model.validate_project(p,check_files=False))

    def test_drag_placement_checks_trim_speed_edges_and_rejects_overlap_atomically(self):
        p=self.project();before=copy.deepcopy(p);lane=p['audio'][0]['lane_id'];item=p['audio'][1]
        self.assertTrue(track_layout.audio_fits(p,item,1,lane))
        self.assertFalse(track_layout.audio_fits(p,item,.99,lane))
        moved=workflow.with_audio_placement(p,1,1,lane)
        self.assertEqual(moved['audio'][1]['start'],1)
        for start,lane_id in ((.99,lane),(-1,lane),(1,''),(1,None),(1,False),(1,'x'*129)):
            with self.subTest(start=start,lane=lane_id):
                with self.assertRaises(ValueError):workflow.with_audio_placement(p,1,start,lane_id)
        self.assertEqual(p,before)
        # Simultaneous music and voice are valid on different lanes.
        self.assertTrue(model.validate_project(p,check_files=False))

    def test_split_stays_in_shared_lane_and_clipboard_keeps_lane_groups(self):
        p=self.project();p['audio'][1]['lane_id']=p['audio'][0]['lane_id']
        split=model.split_item(p,'audio',0,.5)
        self.assertEqual(split['audio'][0]['lane_id'],split['audio'][1]['lane_id'])
        self.assertEqual(len([r for r in track_layout.rows(split) if r[1]=='audio']),2)
        selected={('audio',item['id']) for item in split['audio'][:3]}
        text=workflow.copy_items(split,selected)
        pasted,selection=workflow.paste_items(split,text,0,0)
        copied=[item for kind,_,item in workflow.selected_items(pasted,selection)]
        self.assertEqual(len({item['lane_id'] for item in copied}),1)
        self.assertNotEqual(copied[0]['lane_id'],split['audio'][0]['lane_id'])
        self.assertEqual([item['start'] for item in copied],[0,.5,3])
        self.assertEqual(copied[0]['audio_effects'],split['audio'][0]['audio_effects'])
        # A paste into a free later interval can reuse the original lane.
        pasted,selection=workflow.paste_items(split,text,6,0)
        copied=[item for _,_,item in workflow.selected_items(pasted,selection)]
        self.assertEqual({item['lane_id'] for item in copied},{split['audio'][0]['lane_id']})
        invalid=json.loads(text[len(workflow.CLIPBOARD_PREFIX):])
        invalid['items'][0]['item']['speed']=0
        with self.assertRaises(ValueError):
            workflow.paste_items(split,workflow.CLIPBOARD_PREFIX+json.dumps(invalid),0,0)

    def test_format_five_migrates_existing_rows_without_retiming(self):
        p=self.project();p['version']=5
        for item in p['audio']:item.pop('lane_id')
        p['timeline_order']=['audio:'+p['audio'][1]['id'],'video','audio:'+p['audio'][0]['id']]
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'old.clipcanvas';path.write_text(json.dumps(p))
            loaded=model.load_project(path)
        self.assertEqual(loaded['version'],model.PROJECT_VERSION)
        self.assertEqual([r[0] for r in track_layout.rows(p)],[r[0] for r in track_layout.rows(loaded)])
        for original,item in zip(p['audio'],loaded['audio']):
            self.assertEqual(item['lane_id'],original['id'])
            self.assertEqual({k:v for k,v in item.items() if k!='lane_id'},original)


if __name__=='__main__':unittest.main()
