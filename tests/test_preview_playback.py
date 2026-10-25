"""Preview must preserve every soundtrack sample when its producer stalls."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

from clipcanvas import engine, model, preview_cache, live_player

Gst.init(None)


class PreviewPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='clipcanvas-preview-tests-')
        cls.root = Path(cls.folder.name)
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')
        cls.source = cls.root/'long-gop.mp4'
        # Audio is stream 0, video is stream 1; proxies have only video stream 0.
        subprocess.run([cls.ffmpeg,'-v','error','-f','lavfi','-i','sine=f=430:r=48000:d=4',
            '-f','lavfi','-i','testsrc2=s=960x540:r=24:d=4','-map','0:a','-map','1:v',
            '-c:a','aac','-c:v','libx264','-preset','ultrafast','-g','240','-threads','2',str(cls.source)],
            env=cls.env,check=True)
        cls.project = model.new_project(960,540)
        cls.project['clips'] = [model.new_clip(cls.source)]
        cls.project['clips'][0]['volume'] = .4
        cls.project['audio'] = [model.new_audio(cls.source)]
        cls.project['audio'][0].update(volume=.3,start=.2,audio_effects={'echo_enabled':True})

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def pcm(self, path):
        return subprocess.check_output([self.ffmpeg,'-v','error','-i',str(path),'-vn',
            '-f','s16le','-ar','48000','-ac','2','pipe:1'],env=self.env)

    def stream(self, project, edge, target, **kwargs):
        with target.open('wb') as output:
            for chunk in engine.live_stream(project,preview_edge=edge,**kwargs):output.write(chunk)

    def test_fast_proxy_reuse_resolution_original_audio_and_full_export(self):
        root = self.root/'reuse';root.mkdir()
        original = copy.deepcopy(self.project);digest = hashlib.sha256(self.source.read_bytes()).digest()
        statuses = []
        with mock.patch.object(preview_cache,'directory',return_value=root/'cache'):
            fast = root/'fast.mkv';self.stream(original,640,fast,status=statuses.append)
            cached = list((root/'cache').glob('*.mp4'));self.assertEqual(len(cached),1)
            self.assertIn('Preparing Fast preview',statuses[0])
            meta = engine.probe(cached[0]);self.assertEqual((meta['width'],meta['height']),(640,360))
            self.assertFalse(meta['has_audio']);self.assertEqual(meta['fps'],24)
            with mock.patch.object(engine,'_execute',side_effect=AssertionError('Warm preview must reuse the cached video')):
                again = root/'again.mkv';self.stream(original,640,again)
            with mock.patch.object(preview_cache,'prepare',side_effect=AssertionError('Full resolution must read originals')):
                full = root/'full.mkv';self.stream(original,0,full)
                master = root/'master.mkv';engine.render(original,master,quality='lossless')
            self.assertEqual(self.pcm(fast),self.pcm(full))
            self.assertEqual(self.pcm(again),self.pcm(full))
            self.assertEqual(self.pcm(master),self.pcm(full))
            self.assertEqual(engine.probe(full)['width'],960)
            self.assertEqual(engine.probe(master)['width'],960)
            self.assertEqual(engine.probe(fast)['width'],640)
            joined = model.split_item(original,'clips',0,2)
            joined['clips'][0].update(transition='fade',transition_duration=.3)
            fast_join = root/'fast-join.mkv';self.stream(joined,640,fast_join)
            full_join = root/'full-join.mkv';self.stream(joined,0,full_join)
            self.assertEqual(self.pcm(fast_join),self.pcm(full_join),'Cached picture joins must keep original clip and soundtrack audio')
            self.assertEqual(self.project,original)
            self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(),digest)
            frame = engine.preview_frame(original,1.5,preview_edge=640)
            self.assertEqual((frame['width'],frame['height']),(640,360))

    def test_cache_invalidation_eviction_active_leases_and_cancelled_build(self):
        root = self.root/'cache-lifecycle';root.mkdir()
        source = root/'source.mp4';source.write_bytes(self.source.read_bytes())
        with mock.patch.object(preview_cache,'directory',return_value=root/'cache'):
            a = root/'a';a.mkdir();b = root/'b';b.mkdir()
            first = Path(preview_cache.prepare(source,640,a))
            expected = first.read_bytes()
            stat = source.stat();source.touch()
            # A changed mtime, even at the same size/path, invalidates the entry.
            import os
            os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
            with mock.patch.object(preview_cache,'MAX_FILES',1):
                second = Path(preview_cache.prepare(source,640,b))
            self.assertNotEqual(first.name,second.name)
            self.assertEqual(len(list((root/'cache').glob('*.mp4'))),1)
            self.assertEqual(first.read_bytes(),expected,'Eviction must not remove an active stream lease')
            cancel = threading.Event()
            def cancelled(*args):
                cancel.set()
                raise engine.RenderCancelled('Cancelled while encoding')
            with mock.patch.object(engine,'_execute',side_effect=cancelled),self.assertRaises(engine.RenderCancelled):
                preview_cache.prepare(source,320,b,cancel)
            self.assertIsNone(preview_cache.available(source,320))
            self.assertFalse(list((root/'cache').glob('.building-*')))

    def test_split_and_transition_keep_soundtrack_samples_and_small_audio_packets(self):
        p = copy.deepcopy(self.project)
        p['clips'][0]['volume'] = 0
        baseline = self.root/'baseline.mkv';self.stream(p,0,baseline)
        expected = self.pcm(baseline)
        split = model.split_item(p,'clips',0,2)
        for effect in ('none','fade','circle_wipe'):
            project = copy.deepcopy(split)
            project['clips'][0].update(transition=effect,transition_duration=.5)
            path = self.root/(effect+'.mkv');self.stream(project,0,path)
            actual = self.pcm(path)
            # Overlap shortens the scene; the lower soundtracks keep absolute time.
            count = round(model.project_duration(project)*48000)*4
            self.assertEqual(actual,expected[:count])
            binary,env = engine._tool('ffprobe')
            packets = json.loads(subprocess.check_output([binary,'-v','error','-select_streams','a:0',
                '-show_entries','packet=pts_time,duration_time','-show_packets','-of','json',str(path)],env=env))['packets']
            self.assertLessEqual(max(float(packet['duration_time']) for packet in packets),.022)
            for left,right in zip(packets,packets[1:]):
                self.assertAlmostEqual(float(left['pts_time'])+float(left['duration_time']),float(right['pts_time']),delta=.002)

    def test_cached_fractional_rate_seeks_preserve_frame_timing(self):
        root = self.root/'fractional';root.mkdir()
        source = root/'fractional.mp4'
        subprocess.run([self.ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=s=960x540:r=30000/1001:d=2',
            '-c:v','libx264','-preset','ultrafast','-g','240','-threads','2',str(source)],env=self.env,check=True)
        p = model.new_project(960,540);p['clips'] = [model.new_clip(source)]
        model.adapt_frame_rate(p)
        with mock.patch.object(preview_cache,'directory',return_value=root/'cache'):
            preview_cache.prepare(source,640,root)
            for at in (.3,1.3,1.8):
                cached = engine.preview_frame(p,at,preview_edge=640)
                with mock.patch.object(preview_cache,'lease_available',return_value=None):
                    original = engine.preview_frame(p,at,preview_edge=640)
                self.assertEqual(cached['position'],original['position'])
                error = sum(abs(a-b) for a,b in zip(cached['pixels'],original['pixels']))/len(original['pixels'])
                self.assertLess(error,6,'A cached seek must show the same source frame')

    def pump(self, condition, timeout=12):
        deadline = time.monotonic()+timeout
        context = GLib.MainContext.default()
        while time.monotonic()<deadline:
            while context.pending():context.iteration(False)
            if condition():return
            time.sleep(.005)
        self.fail('Native preview did not reach the expected buffering state')

    def test_starvation_pauses_both_streams_retains_samples_and_respects_user_pause(self):
        p = model.new_project(160,90);p['clips'] = [model.new_clip(self.source)]
        stream = b''.join(engine.live_stream(p,preview_edge=0))
        gate = threading.Event();waiting = threading.Event();errors = [];ended = [];states = [];audio = []
        def stalled(*args, **kwargs):
            yield stream[:len(stream)//2]
            waiting.set()
            while not gate.wait(.01):
                if args[2].is_set():return
            yield stream[len(stream)//2:]
        description = live_player.pipeline_description
        with mock.patch.object(engine,'live_stream',side_effect=stalled), \
             mock.patch.object(live_player,'pipeline_description',side_effect=lambda:description(
                 'audio/x-raw,format=S16LE,rate=48000,channels=2 ! appsink name=audio emit-signals=true sync=true')):
            player = live_player.StreamPlayer(p,0,True,lambda _:Gst.FlowReturn.OK,errors.append,lambda:ended.append(True),
                preview_edge=0,buffering=lambda active,message:states.append(active))
            def received(sink):
                sample = sink.emit('pull-sample');buffer = sample.get_buffer()
                audio.append(buffer.extract_dup(0,buffer.get_size()))
                return Gst.FlowReturn.OK
            player.pipeline.get_by_name('audio').connect('new-sample',received)
            try:
                self.pump(lambda:waiting.is_set() and False in states and len(audio)>5)
                self.pump(lambda:player.buffering and states[-1] and len(states)>2)
                self.assertEqual(player.pipeline.get_state(0)[1],Gst.State.PAUSED)
                player.pause();gate.set()
                self.pump(lambda:not player.buffering)
                self.assertEqual(player.pipeline.get_state(0)[1],Gst.State.PAUSED)
                count = len(audio);time.sleep(.1)
                self.assertEqual(len(audio),count,'Buffer completion must not undo a user pause')
                player.play();self.pump(lambda:bool(ended))
                reference = self.root/'buffered-reference.mkv';reference.write_bytes(stream)
                self.assertEqual(b''.join(audio),self.pcm(reference),'Buffering must retain every audio sample')
                self.assertFalse(errors)
            finally:
                gate.set();player.stop();player.thread.join(5)
                self.assertFalse(player.thread.is_alive())

    def test_short_stream_finishes_without_filling_the_normal_buffer(self):
        p = model.new_project(160,90);p['clips'] = [model.new_card('#123456',.125)]
        errors,ended = [],[];description = live_player.pipeline_description
        with mock.patch.object(live_player,'pipeline_description',side_effect=lambda:description('fakesink sync=true')):
            player = live_player.StreamPlayer(p,0,True,lambda _:Gst.FlowReturn.OK,errors.append,lambda:ended.append(True),preview_edge=0)
            try:
                self.pump(lambda:bool(ended))
                self.assertFalse(errors)
            finally:player.stop();player.thread.join(5)


if __name__ == '__main__':unittest.main()
