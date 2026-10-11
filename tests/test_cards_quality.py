"""File-free scenes, exact master frames and independently selected preview sizes."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from clipcanvas import engine, model, storage
import test_engine


class CardsQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root=cls.fixtures.root
        cls.ffmpeg=cls.fixtures.ffmpeg

    @classmethod
    def tearDownClass(cls):cls.fixtures.tearDownClass()

    def decode(self,path,pix_fmt='yuv420p'):
        return subprocess.check_output([self.ffmpeg,'-v','error','-i',str(path),'-map','0:v:0','-pix_fmt',pix_fmt,'-f','rawvideo','-threads','1','pipe:1'])

    def test_cards_only_lossless_master_matches_composed_frames_and_has_silent_audio(self):
        p=model.new_project(320,180);p['clips']=[model.new_card('#ff0000',.8),model.new_card('#0000ff',.8)]
        p['clips'][0].update(transition='fade',transition_duration=.2)
        original=copy.deepcopy(p)
        out=self.root/'cards-master.mkv';engine.render(p,out,quality='lossless')
        probe,env=engine._tool('ffprobe')
        info=json.loads(subprocess.check_output([probe,'-v','error','-show_streams','-of','json',str(out)],env=env))
        self.assertEqual([s['codec_name'] for s in info['streams']],['ffv1','pcm_f32le'])
        self.assertAlmostEqual(engine.probe(out)['duration'],1.4,delta=.05)
        live=self.root/'cards-live.mkv'
        with live.open('wb') as stream:
            for chunk in engine.live_stream(p,preview_edge=0):stream.write(chunk)
        self.assertEqual(self.decode(out),self.decode(live))
        audio=subprocess.check_output([self.ffmpeg,'-v','error','-i',str(out),'-map','0:a:0','-f','f32le','pipe:1'])
        self.assertTrue(audio);self.assertFalse(any(audio))
        for at,channel in ((.3,0),(1.1,2)):
            frame=engine.preview_frame(p,at,preview_edge=0)
            center=(90*320+160)*3
            self.assertGreater(frame['pixels'][center+channel],245)
        self.assertEqual(p,original)
        self.assertEqual(model.media_items(p),[])
        with self.assertRaises(engine.RenderError):engine.render(p,self.root/'wrong.mp4',quality='lossless')

    def test_preview_quality_dimensions_and_export_ignore_preview_size(self):
        for width,height in ((1920,1080),(1080,1920)):
            p=model.new_project(width,height);p['clips']=[model.new_card('#2488cc',.12)]
            for edge,expected in ((640,(640,360)),(1280,(1280,720)),(0,(1920,1080))):
                if height>width:expected=expected[::-1]
                frame=engine.preview_frame(p,0,preview_edge=edge)
                self.assertEqual((frame['width'],frame['height']),expected)
                self.assertEqual(len(frame['pixels']),expected[0]*expected[1]*3)
        p=model.new_project(960,540);p['clips']=[model.new_card('#2488cc',.2)]
        out=self.root/'full-export.mp4';engine.render(p,out,quality='master',preview_edge=640)
        meta=engine.probe(out);self.assertEqual((meta['width'],meta['height']),(960,540))
        out=self.root/'fast-rendered-preview.mp4';engine.render(p,out,preview=True,preview_edge=640)
        meta=engine.probe(out);self.assertEqual((meta['width'],meta['height']),(640,360))
        out=self.root/'full-rendered-preview.mp4';engine.render(p,out,preview=True,preview_edge=0)
        meta=engine.probe(out);self.assertEqual((meta['width'],meta['height']),(960,540))

    def test_cards_between_source_clips_follow_fps_captions_mix_and_portability(self):
        source=self.root/'portrait.mp4';digest=hashlib.sha256(source.read_bytes()).digest()
        p=model.new_project(180,320);p['clips']=[model.new_card('#ff0000',.5),model.new_clip(source),model.new_card('#0000ff',.8)]
        p['clips'][0].update(transition='wipeleft',transition_duration=.1)
        p['clips'][1].update(transition='fade',transition_duration=.2)
        p['audio']=[model.new_audio(self.root/'music.wav')]
        p['layers']=[model.new_text('Card title',2.2,3.1)]
        p['captions']=[model.new_caption('Card caption',2.2,3.1)]
        model.adapt_frame_rate(p);self.assertEqual(p['fps'],25)
        target=self.root/'mixed-cards.mkv';engine.render(p,target,quality='lossless')
        self.assertAlmostEqual(engine.probe(target)['duration'],model.project_duration(p),delta=.06)
        frame=engine.preview_frame(p,2.7,preview_edge=0);self.assertGreater(frame['pixels'][2],245)
        audio=self.root/'cards-speech.wav';engine.render_audio(p,audio,source='video')
        self.assertAlmostEqual(engine.probe(audio)['duration'],model.project_duration(p),delta=.06)
        before=copy.deepcopy(p);copy_path=storage.collect_project(p,self.root/'cards-portable')
        loaded=model.load_project(copy_path)
        self.assertEqual([c.get('color') for c in loaded['clips']],[c.get('color') for c in p['clips']])
        self.assertEqual(len(model.media_items(loaded)),2)
        self.assertEqual(p,before);self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),digest)

    def test_card_validation_migration_split_extension_and_recovery(self):
        p=model.new_project();p['clips']=[model.new_card('#123456',3)]
        self.assertTrue(model.validate_project(p))
        split=model.split_item(p,'clips',0,1)
        self.assertEqual([model.clip_duration(c) for c in split['clips']],[1,2])
        extended=model.trim_item(split,'clips',1,'right',5)
        self.assertEqual(model.clip_duration(extended['clips'][1]),7)
        self.assertEqual(model.project_duration(p),3)
        for key,value in [('color','#fff'),('color','red;movie=x'),('duration',float('inf')),('volume',1),('speed',2)]:
            bad=copy.deepcopy(p);bad['clips'][0][key]=value
            with self.assertRaises(ValueError):model.validate_project(bad)
        with self.assertRaises(ValueError):model.detach_audio(p,0)
        recovery=storage.Recovery(self.root/'cards-recovery');recovery.write(extended)
        self.assertEqual(recovery.read(recovery.path)[0],extended)
        old=model.new_project();old['version']=3
        path=self.root/'v3.clipcanvas';path.write_text(json.dumps(old))
        self.assertEqual(model.load_project(path)['version'],model.PROJECT_VERSION)
