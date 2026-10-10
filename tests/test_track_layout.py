"""Saved row layouts remain separate from media sequence and composition."""
import copy
from pathlib import Path
import tempfile
import unittest
from clipcanvas import model, track_layout


class TrackLayoutTests(unittest.TestCase):
    def project(self):
        p=model.new_project()
        p['layers']=[model.new_text('Title')]
        p['audio']=[model.new_audio('/tmp/music.wav',{'duration':3,'has_audio':True})]
        p['captions']=[model.new_caption('Words',0,2)]
        return p

    def test_reorder_save_reopen_add_delete_keeps_media_unchanged(self):
        p=self.project();before=copy.deepcopy(p)
        default=[row[0] for row in track_layout.rows(p)]
        self.assertEqual(default,['video','layers:'+p['layers'][0]['id'],'audio:'+p['audio'][0]['id'],'captions'])
        p['timeline_order']=track_layout.moved_order(p,'captions',-1)
        p['timeline_order']=track_layout.moved_order(p,'captions',-1)
        p['timeline_order']=track_layout.moved_order(p,'captions',-1)
        self.assertEqual([row[0] for row in track_layout.rows(p)][0],'captions')
        self.assertIsNone(track_layout.moved_order(p,'captions',-1))
        for key in before:
            if key!='timeline_order':self.assertEqual(p[key],before[key])
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'rows.clipcanvas';model.save_project(p,path)
            self.assertEqual(model.load_project(path),p)
        p['layers'].clear();p['audio'].append(model.new_audio('/tmp/voice.wav',{'duration':2,'has_audio':True}))
        rows=track_layout.rows(p)
        self.assertNotIn(default[1],[row[0] for row in rows])
        self.assertEqual(rows[-1][0],'audio:'+p['audio'][-1]['id'])
        for first,second in zip(rows,rows[1:]):self.assertLessEqual(first[3]+first[4],second[3])

    def test_invalid_orders_rejected_and_older_projects_use_defaults(self):
        for value in (None,'video',['video','video'],[False],['unknown'],['audio:']):
            with self.subTest(value=value):
                p=self.project();p['timeline_order']=value
                with self.assertRaises(ValueError):model.validate_project(p,check_files=False)
        p=self.project();p.pop('timeline_order')
        self.assertEqual(track_layout.rows(p)[0][0],'video')
        self.assertTrue(model.validate_project(p,check_files=False))


if __name__=='__main__':unittest.main()
