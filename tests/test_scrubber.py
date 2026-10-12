"""Native seeks reuse a decoder and retain scene geometry and source safety."""
import copy
import hashlib
import threading
import unittest
from unittest import mock
from clipcanvas import engine, model
from clipcanvas.scrubber import ScrubSession
import test_engine


class NativeScrubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=test_engine.EngineIntegrationTests;cls.fixtures.setUpClass()

    @classmethod
    def tearDownClass(cls):cls.fixtures.tearDownClass()

    def test_repeated_seeks_reuse_decoder_keep_size_and_read_originals(self):
        p=model.new_project(320,180);p['clips']=[model.new_clip(self.fixtures.root/'landscape.mp4')]
        p['clips'][0].update({'in':.1,'out':1.3,'speed':1.5,'video_fade_in':.2})
        p['layers']=[model.new_text('Local title',.1,.7)];p['layers'][0].update(font_size=20,y=.3)
        p['captions']=[model.new_caption('Words',.3,.6)];p['caption_style']['font_size']=16
        before=copy.deepcopy(p);source=self.fixtures.root/'landscape.mp4';digest=hashlib.sha256(source.read_bytes()).digest()
        session=ScrubSession()
        try:
            for at in (.1,.3,.5,.2):
                picture=session.frame(p,at)
                self.assertEqual(session.last_backend,'native')
                source_at=p['clips'][0]['in']+picture['position']*p['clips'][0]['speed']
                self.assertLessEqual(abs(session._source_pts-source_at),1/30+.002)
                self.assertEqual((picture['width'],picture['height']),(320,180))
                reference=engine.preview_frame(p,at,preview_edge=0)
                difference=sum(abs(a-b) for a,b in zip(picture['pixels'],reference['pixels']))/len(reference['pixels'])
                self.assertLess(difference,12, 'Native source/canvas must preserve the requested scene')
            self.assertEqual(session.open_count,1)
            self.assertEqual(p,before);self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),digest)
            pipeline=session.pipeline;session.close();self.assertIsNone(session.pipeline)
            self.assertEqual(pipeline.get_state(0)[1].value_nick,'null')
        finally:session.close()

    def test_transitions_overlay_video_and_cancellation_use_correct_path(self):
        p=model.new_project(160,90);p['clips']=[model.new_card('#112233',1),model.new_card('#885522',1)]
        p['clips'][0].update(transition='slide',transition_duration=.4)
        session=ScrubSession()
        try:
            picture=session.frame(p,.8)
            self.assertEqual(session.last_backend,'composition')
            self.assertEqual(picture['pixels'],engine.preview_frame(p,.8)['pixels'])
            p['layers']=[model.new_visual(self.fixtures.root/'portrait.mp4','video',0,1)]
            session.frame(p,.2);self.assertEqual(session.last_backend,'composition')
            cancel=threading.Event();cancel.set()
            with self.assertRaises(engine.RenderCancelled):session.frame(p,.2,cancel)
            p['layers']=[]
            with mock.patch.object(engine,'preview_frame',side_effect=AssertionError('Cards need no subprocess')):
                picture=session.frame(p,.2,preview_edge=0)
                self.assertEqual(session.last_backend,'native');self.assertEqual((picture['width'],picture['height']),(160,90))
        finally:session.close()
