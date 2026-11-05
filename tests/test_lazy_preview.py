"""Window boundaries must preserve the continuous reference stream."""
import copy
import math
import subprocess
import threading
import unittest
from clipcanvas import engine, model, lazy_preview, color_grading
import test_engine

class LazyPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=test_engine.EngineIntegrationTests;cls.fixtures.setUpClass()
    @classmethod
    def tearDownClass(cls):cls.fixtures.tearDownClass()

    def test_windows_and_continuous_sound_match_full_composition(self):
        p=model.new_project(160,90)
        p['clips']=[model.new_clip(self.fixtures.root/name) for name in
            ('landscape.mp4','portrait.mp4','third.mp4','landscape.mp4','portrait.mp4')]
        p['clips'][0]['out']=1.15
        p['clips'][1]['speed']=1.2
        for clip in p['clips']:
            clip.update(color_grade=color_grading.PRESETS['Warm'],video_fade_in=.1,video_fade_out=.1)
        engine._source_rate(p)
        p['audio']=[model.new_audio(self.fixtures.root/'music.wav')]
        p['audio'][0]['audio_effects']={'echo_enabled':True}
        original=copy.deepcopy(p)
        for start in (0,.37,2.1):
            with self.subTest(start=start):
                target=self.fixtures.root/'lazy-reference.mkv'
                with target.open('wb') as out:
                    for data in engine.live_stream(p,0,preview_edge=0):out.write(data)
                binary,env=engine._tool('ffmpeg')
                picture=subprocess.check_output([binary,'-v','error','-i',str(target),'-an','-pix_fmt','yuv420p','-f','rawvideo','pipe:1'],env=env)
                sound=subprocess.check_output([binary,'-v','error','-i',str(target),'-vn','-c:a','pcm_s16le','-f','s16le','pipe:1'],env=env)
                picture=picture[math.ceil(start*p['fps']-1e-7)*160*90*3//2:]
                sound=sound[round(start*48000)*4:]
                actual=b''.join(lazy_preview.video(p,start,0,threading.Event()))
                self.assertEqual(len(actual),len(picture))
                self.assertEqual(actual,picture)
                self.assertEqual(b''.join(lazy_preview.audio(p,start,threading.Event())),sound)
        self.assertEqual(p,original)

    def test_native_padded_width_seek_timestamps_and_end(self):
        import time
        from unittest import mock
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst, GLib
        from clipcanvas import live_player
        Gst.init(None)
        p=model.new_project(180,100)
        p['clips']=[model.new_card('#e03020',.5) for _ in range(5)]
        pictures=[];errors=[];ended=[]
        def picture(sample):
            buffer=sample.get_buffer()
            pixels=buffer.extract_dup(0,buffer.get_size())
            pictures.append((buffer.pts,pixels))
            return Gst.FlowReturn.OK
        description=live_player.pipeline_description
        with mock.patch.object(live_player,'pipeline_description',side_effect=lambda:description('fakesink sync=true')):
            player=live_player.StreamPlayer(p,.111,True,picture,errors.append,lambda:ended.append(True),preview_edge=0)
        try:
            deadline=time.monotonic()+10;context=GLib.MainContext.default()
            while time.monotonic()<deadline and not ended and not errors:
                while context.pending():context.iteration(False)
                time.sleep(.005)
            self.assertFalse(errors)
            self.assertTrue(ended)
            self.assertTrue(pictures)
            self.assertAlmostEqual(pictures[0][0]/Gst.SECOND,4/30-.111,places=6)
            for _,pixels in pictures:
                self.assertEqual(len(pixels),180*100*3)
                self.assertGreater(pixels[0],200)
                self.assertLess(pixels[1],70)
                self.assertEqual(pixels[:3],pixels[-3:])
        finally:
            player.stop();player.thread.join(5)
            self.assertFalse(player.thread.is_alive())

    def test_transitions_and_layers_use_existing_player(self):
        p=model.new_project();p['clips']=[model.new_card(duration=2) for _ in range(4)]
        self.assertTrue(lazy_preview.supported(p))
        p['clips'][0]['transition']='fade';self.assertFalse(lazy_preview.supported(p))
        p['clips'][0]['transition']='none';p['layers']=[model.new_text('Title',0,2)]
        self.assertFalse(lazy_preview.supported(p))
