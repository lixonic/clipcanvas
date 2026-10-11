"""Unencoded streaming must preserve composition timing and the final audio mix."""
import copy
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from clipcanvas import audio_effects, engine, model
import test_engine


class LiveStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root = cls.fixtures.root
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def project(self):
        p = model.new_project(320,180)
        p['clips'] = [model.new_clip(self.root/name) for name in ('landscape.mp4','portrait.mp4','third.mp4')]
        p['clips'][0].update(transition='fade',transition_duration=.4,video_fade_in=.2)
        p['clips'][1].update(transition='wipeleft',transition_duration=.4)
        p['audio'] = [model.new_audio(self.root/'music.wav')]
        p['audio'][0].update(start=.3,volume=.25,fade_in=.2)
        fx=audio_effects.defaults();fx.update(gain_db=-3,echo_enabled=True,echo_delay_ms=200)
        p['audio'][0]['audio_effects']=fx
        p['layers'] = [model.new_text('Live title',.2,2.7)]
        p['layers'][0].update(font_size=22,y=.3)
        p['captions'] = [model.new_caption('Caption one',.4,1.8),model.new_caption('Caption two',2,4)]
        p['caption_style']['font_size']=18
        return p

    def stream_file(self,p,start,name):
        path=self.root/name
        with path.open('wb') as out:
            for data in engine.live_stream(p,start):out.write(data)
        return path

    def pixels(self,path,at):
        return subprocess.run([self.ffmpeg,'-v','error','-i',str(path),'-ss',str(at),
            '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],env=self.env,
            capture_output=True,check=True).stdout

    def pcm(self,path,at,length):
        return subprocess.run([self.ffmpeg,'-v','error','-i',str(path),'-ss',str(at),
            '-t',str(length),'-vn','-c:a','pcm_s16le','-ar','48000','-ac','2','-f','s16le','pipe:1'],
            env=self.env,capture_output=True,check=True).stdout

    def test_stream_has_raw_video_pcm_audio_and_is_source_safe(self):
        p=self.project();before=copy.deepcopy(p)
        path=self.stream_file(p,0,'full-live.mkv')
        binary,env=engine._tool('ffprobe')
        info=subprocess.run([binary,'-v','error','-show_entries','stream=codec_name','-of','csv=p=0',str(path)],
            capture_output=True,text=True,env=env,check=True).stdout
        self.assertEqual(set(info.split()),{'rawvideo','pcm_s16le'})
        self.assertEqual(p,before)
        # The new path never uses the MP4 preview encoder or creates an MP4.
        self.assertFalse(any(self.root.glob('*preview*.mp4')))
        self.assertEqual(len(self.pixels(path,.5)),320*180*3)

    def test_seek_inside_transition_and_later_clip_keeps_layers_and_audio_history(self):
        p=self.project();full=self.stream_file(p,0,'reference-live.mkv')
        # The third clip begins at 2.7 s. Seeking at 3 removes the completed
        # main clips but must retain global caption/music and echo timestamps.
        for start in (1.2,3):
            with self.subTest(start=start):
                seeked=self.stream_file(p,start,f'seek-{start}.mkv')
                self.assertEqual(self.pixels(full,start+.1),self.pixels(seeked,.1))
                expected=self.pcm(full,start,.2);actual=self.pcm(seeked,0,.2)
                self.assertEqual(len(expected),len(actual))
                self.assertEqual(expected,actual)

    def test_cancellation_reaps_the_producer_and_removes_its_workspace(self):
        cancel=threading.Event();stream=engine.live_stream(self.project(),cancel=cancel)
        before=set(Path(tempfile.gettempdir()).glob('clipcanvas-live-*'))
        next(stream)
        added=set(Path(tempfile.gettempdir()).glob('clipcanvas-live-*'))-before
        self.assertEqual(len(added),1)
        cancel.set();self.assertEqual(list(stream),[])
        self.assertFalse(any(path.exists() for path in added))

    def test_video_overlay_opacity_speed_and_freeze_survive_seek(self):
        p=self.project()
        overlay=model.new_visual(self.root/'portrait.mp4','video',1.7,4)
        overlay.update(speed=2,opacity=.4,rotation=18,x=.7)
        p['layers'].append(overlay)
        full=self.stream_file(p,0,'overlay-live.mkv')
        seeked=self.stream_file(p,3,'overlay-seek.mkv')
        self.assertEqual(self.pixels(full,3.1),self.pixels(seeked,.1))
        p['layers'].pop()
        plain=self.stream_file(p,3,'no-overlay-live.mkv')
        self.assertNotEqual(self.pixels(plain,.1),self.pixels(seeked,.1))

    def test_scrub_frame_matches_stream_through_cues_transitions_and_later_clips(self):
        p=self.project()
        p['layers'].append(model.new_visual(self.root/'portrait.mp4','video',1.7,4))
        full=self.stream_file(p,0,'scrub-reference.mkv')
        original=copy.deepcopy(p)
        for at in (.39,.6,1.2,2.2,3.1):
            with self.subTest(at=at):
                picture=engine.preview_frame(p,at)
                reference=self.pixels(full,at)
                self.assertEqual(len(picture['pixels']),len(reference))
                # Pruning inactive layers and dropping completed joins can
                # remove intermediate chroma conversions. Allow their small
                # RGB rounding differences while comparing the whole frame.
                error=sum(abs(a-b) for a,b in zip(picture['pixels'],reference))/len(reference)
                self.assertLess(error,3)
                self.assertEqual((picture['width'],picture['height']),(320,180))
        self.assertEqual(p,original)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(engine.RenderCancelled):engine.preview_frame(p,1,cancel)

    def test_auto_fractional_rate_is_used_by_stream_and_final_export(self):
        source=self.root/'fractional.mp4'
        subprocess.run([self.ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30000/1001:d=0.5',
            '-c:v','libx264','-threads','1',str(source)],env=self.env,check=True)
        p=model.new_project(160,90);p['clips']=[model.new_clip(source)];p['fps']=60
        output=self.root/'fractional-export.mp4';engine.render(p,output)
        self.assertAlmostEqual(engine.probe(output)['fps'],30000/1001,places=5)
        streamed=self.stream_file(p,0,'fractional-live.mkv')
        self.assertAlmostEqual(engine.probe(streamed)['fps'],30000/1001,places=5)


class AutomaticFrameRateTests(unittest.TestCase):
    def test_first_clip_reorder_and_fractional_rate(self):
        p=model.new_project()
        p['clips']=[model.new_clip('/tmp/a.mp4',{'duration':5,'fps':30000/1001}),
                    model.new_clip('/tmp/b.mp4',{'duration':5,'fps':25})]
        self.assertAlmostEqual(model.adapt_frame_rate(p),30000/1001)
        p['clips'].reverse();self.assertEqual(model.adapt_frame_rate(p),25)
        p['clips'][0]['speed']=2;self.assertEqual(model.adapt_frame_rate(p),25)
        p['clips'][0]['source_fps']=float('nan')
        with self.assertRaises(ValueError):model.validate_project(p,check_files=False)

    def test_legacy_project_keeps_rate_until_metadata_is_available(self):
        p=model.new_project();p['fps']=24
        p['clips']=[model.new_clip('/tmp/a.mp4',{'duration':5})]
        self.assertEqual(model.adapt_frame_rate(p),24)
        p['clips'][0]['source_fps']=60000/1001
        self.assertAlmostEqual(model.adapt_frame_rate(p),60000/1001)
        with tempfile.TemporaryDirectory() as root:
            saved=model.save_project(p,Path(root)/'rate.clipcanvas')
            loaded=model.load_project(saved)
            self.assertEqual(loaded['fps'],p['fps'])
            self.assertEqual(loaded['clips'][0]['source_fps'],p['clips'][0]['source_fps'])


class DeepScrubTests(unittest.TestCase):
    def test_deep_source_seek_preserves_speed_fades_overlay_and_transition(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ffmpeg,env=engine._tool('ffmpeg');source=root/'long.mp4'
            subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=18',
                            '-c:v','libx264','-threads','1',str(source)],env=env,check=True)
            p=model.new_project(160,90);p['clips']=[model.new_clip(source),model.new_card('#148ac9',3)]
            p['clips'][0].update(speed=1.5,video_fade_in=2,video_fade_out=2,transition='fade',transition_duration=.8)
            p['layers']=[model.new_visual(source,'video',2,14)]
            p['layers'][0].update(width=.4,height=.4,opacity=.5,speed=2)
            p['captions']=[model.new_caption('Tail',10,14)];p['caption_style']['font_size']=12
            original=copy.deepcopy(p);graph=engine._graph
            for at in (1.4,8,10.6,11.6,12.4):
                fast=engine.preview_frame(p,at)
                def reference(*args,**kwargs):
                    kwargs['picture_start']=None
                    return graph(*args,**kwargs)
                with mock.patch.object(engine,'_graph',reference):baseline=engine.preview_frame(p,at)
                error=sum(abs(a-b) for a,b in zip(fast['pixels'],baseline['pixels']))/len(fast['pixels'])
                self.assertLess(error,3,f'Frame at {at} differs: {error}')
            args,_,_=graph(p,True,root,video_only=True,picture_start=8)
            self.assertGreater(float(args[args.index('-ss')+1]),11)
            self.assertEqual(p,original)
