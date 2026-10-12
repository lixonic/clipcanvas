"""Native joins preserve old timing/look while bounding geometry cache memory."""
from collections import OrderedDict
import copy
from pathlib import Path
import subprocess
import tempfile
import threading
import tracemalloc
import unittest
from unittest import mock

from clipcanvas import audio_effects, engine, model, transitions, transition_render as native


def reference_join(args,filters,left,right,clip,overlap,offset,width,height,fps,right_duration,scratch,tag,cancel=None,with_alpha=False,left_frames=None):
    label=f'joinv{tag}'
    # Compare the original RGB effect with opaque backgrounds. The historical
    # custom sampler accidentally used chroma as alpha when overlays negotiated
    # yuva444p. Native composition deliberately fixes that unrelated artifact.
    filters.append(f'[{left}]format=yuv444p[referenceleft{tag}]')
    filters.append(f'[{right}]format=yuv444p[referenceright{tag}]')
    left,right=f'referenceleft{tag}',f'referenceright{tag}'
    filters.append(f'[{left}][{right}]'+transitions.join_filter(clip,overlap,offset)
                   +f',fps={engine._fmt(fps)},settb=AVTB'+(',format=yuva444p' if with_alpha else '')+f'[{label}]')
    return label


class NativeTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='clipcanvas-native-transition-tests-')
        cls.root=Path(cls.folder.name);cls.ffmpeg,cls.env=engine._tool('ffmpeg')
        for name,rate in (('a', '30000/1001'),('b','24')):
            subprocess.run([cls.ffmpeg,'-v','error','-f','lavfi','-i',f'testsrc2=s=128x72:r={rate}:d=2',
                            '-f','lavfi','-i','sine=f=470:r=48000:d=2','-c:v','libx264',
                            '-preset','ultrafast','-threads','1','-c:a','aac',str(cls.root/f'{name}.mp4')],
                           env=cls.env,check=True)

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def stream(self,project,name):
        path=self.root/name
        with path.open('wb') as output:
            for packet in engine.live_stream(project,preview_edge=0):output.write(packet)
        return path

    def decode(self,path,audio=False):
        options=['-vn','-ac','2','-ar','48000','-f','s16le'] if audio else ['-an','-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo']
        return subprocess.run([self.ffmpeg,'-v','error','-threads','1','-i',str(path),*options,'-threads','1','pipe:1'],
                              env=self.env,capture_output=True,check=True).stdout

    def test_fractional_frames_speed_trim_directions_and_audio_match_old_renderer(self):
        for key in native.FAST_KEYS:
            for direction in (('left',) if key=='circle_wipe' else transitions.DIRECTIONS):
                with self.subTest(effect=key,direction=direction):
                    project=model.new_project(128,72)
                    project['clips']=[model.new_clip(self.root/'a.mp4'),model.new_clip(self.root/'b.mp4')]
                    project['clips'][0].update({'in':.13,'out':1.73,'speed':1.25,'transition':key,
                        'transition_duration':.37,'transition_direction':direction,'transition_colour':'#20d040'})
                    project['audio']=[model.new_audio(self.root/'a.mp4')]
                    project['audio'][0].update(start=.25,out=1.25,volume=.2,audio_effects=audio_effects.preset('Echo'))
                    project['layers']=[model.new_text('Join',.7,1.6)];project['layers'][0]['font_size']=12
                    before=copy.deepcopy(project)
                    fast=self.stream(project,f'native-{key}-{direction}.mkv')
                    with mock.patch.object(native,'append',side_effect=reference_join):
                        old=self.stream(project,f'old-{key}-{direction}.mkv')
                    actual,expected=self.decode(fast),self.decode(old)
                    self.assertEqual(len(actual),len(expected),'Transition must retain every original frame')
                    errors=[abs(a-b) for a,b in zip(actual,expected)]
                    # Native float blending can round one channel differently.
                    # A boundary pixel can also straddle a float circle radius.
                    self.assertLess(sum(errors)/len(errors),.15)
                    stride=128*72*3
                    for at in range(0,len(errors),stride):
                        self.assertLess(sum(errors[at:at+stride])/stride,.3)
                    if key=='flow':self.assertLessEqual(max(errors),5)
                    self.assertEqual(self.decode(fast,True),self.decode(old,True))
                    self.assertEqual(project,before)

    def test_mixed_native_joins_deep_seek_keep_global_frames_and_audio(self):
        p=model.new_project(128,72)
        p['clips']=[model.new_clip(self.root/'b.mp4') for _ in range(5)]
        for clip,key in zip(p['clips'],native.FAST_KEYS):
            clip.update(transition=key,transition_duration=.375)
        full=self.stream(p,'mixed-full.mkv')
        # Start on the composition grid: FFmpeg's output seek rounds an
        # arbitrary pointer to the next picture and resets that picture to 0.
        for at in (1.75,3.375,5.125,6.75):
            path=self.root/f'mixed-seek-{at}.mkv'
            with path.open('wb') as output:
                for packet in engine.live_stream(p,at,preview_edge=0):output.write(packet)
            def sample(source,time,audio=False):
                options=['-vn','-t','0.1','-c:a','pcm_s16le','-ar','48000','-ac','2','-f','s16le'] if audio else ['-an','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo']
                return subprocess.run([self.ffmpeg,'-v','error','-i',str(source),'-ss',str(time),*options,'-threads','1','pipe:1'],
                                      env=self.env,capture_output=True,check=True).stdout
            self.assertEqual(sample(full,at+.1),sample(path,.1))
            self.assertEqual(sample(full,at,True),sample(path,0,True))


class GeometryCacheTests(unittest.TestCase):
    def test_reuse_eviction_and_cancellation_preserve_active_render_files(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(native,'_entries',OrderedDict()), \
             mock.patch.object(native,'_workspace',None),mock.patch.object(native,'MAX_CACHE_ENTRIES',1):
            root=Path(directory);values=native.progress_values(.4,.8,25)[1]
            with mock.patch.object(native,'_png',wraps=native._png) as write:
                first=native.masks('circle_wipe','left',128,72,values,root,1)
                calls=write.call_count
                second=native.masks('circle_wipe','left',128,72,values,root,2)
                self.assertEqual(write.call_count,calls)
            file=first['main'].parent/'000001.png';saved=file.read_bytes()
            self.assertEqual(file.stat().st_ino,(second['main'].parent/file.name).stat().st_ino)
            evicted=next(iter(native._entries.values()))[0]
            native.masks('line_wipe','right',128,72,values,root,3)
            self.assertFalse(evicted.exists());self.assertEqual(file.read_bytes(),saved)
            cancel=threading.Event();original=native._png
            def stop_after_one(*args,**kwargs):
                original(*args,**kwargs);cancel.set()
            before=set(Path(native._workspace.name).iterdir())
            with mock.patch.object(native,'_png',side_effect=stop_after_one),self.assertRaises(engine.RenderCancelled):
                native.masks('circle_wipe','left',130,72,values,root,4,cancel)
            self.assertEqual(set(Path(native._workspace.name).iterdir()),before)
            self.assertEqual(file.read_bytes(),saved)
            with mock.patch.object(native,'MAX_CACHE_BYTES',1):
                leased=native.masks('line_wipe','up',132,72,values,root,5)
                self.assertEqual(len(native._entries),0)
                self.assertTrue((leased['main'].parent/'000001.png').is_file())
            native._workspace.cleanup()

    def test_full_resolution_masks_use_scanline_memory_not_video_frame_cache(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(native,'_entries',OrderedDict()),mock.patch.object(native,'_workspace',None):
            tracemalloc.start()
            try:
                paths=native.masks('circle_wipe','left',1920,1080,native.progress_values(.5,0,30)[1],directory,1)
                _,peak=tracemalloc.get_traced_memory()
            finally:tracemalloc.stop()
            self.assertLess(peak,4*1024*1024)
            files=list(paths['main'].parent.glob('*.png'))
            self.assertEqual(len(files),16)
            self.assertLess(sum(path.stat().st_size for path in files),1024*1024)
            native._workspace.cleanup()
