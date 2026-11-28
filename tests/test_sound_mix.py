"""Many attached cues use bounded blocks and one final audio input."""
import copy
from pathlib import Path
import subprocess
import tempfile
import threading
import tracemalloc
import unittest
from unittest import mock
from clipcanvas import engine,model,sound_effects,sound_mix


class SoundPremixTests(unittest.TestCase):
    def test_custom_exit_cues_probe_duration_once_even_with_different_lengths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);wav=sound_effects.generate('pop',root/'original.wav')
            p=model.new_project(160,90);p['clips']=[model.new_card('#123456',8)]
            for index in range(20):
                item=model.new_text('Sound',index*.1,index*.1+.2+index*.01)
                item['sound_effect'].update(preset='file',path=str(wav),trigger='both')
                p['layers'].append(item)
            with mock.patch.object(sound_effects,'duration',wraps=sound_effects.duration) as read:
                sound_mix.mix(p,root)
                self.assertEqual(read.call_count,1)

    def test_hundreds_of_cues_decode_unique_file_once_and_use_one_graph_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=model.new_project(160,90);p['clips']=[model.new_card('#122638',240)]
            for index in range(200):
                cue=model.new_caption('word',index, index+.7);cue['sound_effect'].update(preset='click',trigger='both',volume=.25)
                p['captions'].append(cue)
            before=copy.deepcopy(p);original=sound_mix._decode
            tracemalloc.start()
            with mock.patch.object(sound_mix,'_decode',wraps=original) as decode:
                path=sound_mix.mix(p,root);self.assertEqual(decode.call_count,1)
            _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
            self.assertLess(peak,4*1024*1024)
            self.assertEqual(engine.probe(path)['duration'],240)
            with mock.patch.object(sound_mix,'mix',return_value=path):
                args,graph,_=engine._graph(p,audio_only=True,scratch=root)
            self.assertEqual(args.count('-i'),2);self.assertIn('amix=inputs=2:',graph)
            self.assertEqual(p,before)
            cancel=threading.Event();cancel.set()
            with self.assertRaises(engine.RenderCancelled):sound_mix.mix(p,root,cancel)

    def test_float_premix_preserves_headroom_and_clips_at_element_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);p=model.new_project(160,90);p['clips']=[model.new_card('#123456',1)]
            for index in range(4):
                item=model.new_text('Sound',.2,.25);item['sound_effect'].update(preset='pop',volume=2);p['layers'].append(item)
            path=sound_mix.mix(p,root);binary,env=engine._tool('ffmpeg')
            import array
            samples=array.array('f');samples.frombytes(subprocess.run([binary,'-v','error','-i',str(path),'-f','f32le','pipe:1'],env=env,capture_output=True,check=True).stdout)
            self.assertGreater(max(samples),1)
            self.assertFalse(any(samples[round(.3*48000)*2:]))

    def test_interval_mix_skips_distant_cues_and_matches_full_mix(self):
        import array
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            p=model.new_project(160,90);p['clips']=[model.new_card('#123456',120)]
            late=model.new_text('Late',105.0,106.0);late['sound_effect'].update(preset='pop',volume=.5,trigger='enter')
            p['layers'].append(late)
            early_dir=root/'early';early_dir.mkdir()
            with mock.patch.object(sound_mix,'_decode',side_effect=AssertionError('Cue-free window must not decode')):
                self.assertIsNone(sound_mix.mix(p,early_dir,start=0.0,end=12.0))
            self.assertEqual(list(early_dir.iterdir()),[])
            active_dir=root/'active';active_dir.mkdir()
            bounded=sound_mix.mix(p,active_dir,start=104.0,end=108.0)
            self.assertIsNotNone(bounded)
            self.assertAlmostEqual(engine.probe(bounded)['duration'],4.0,places=3)
            self.assertLess(Path(bounded).stat().st_size,2*1024*1024)
            # Compare sample-for-sample with a shorter full mix shifted identically.
            p_short=model.new_project(160,90);p_short['clips']=[model.new_card('#123456',6)]
            cue=model.new_text('Cue',1.0,2.0);cue['sound_effect'].update(preset='pop',volume=.5,trigger='enter')
            p_short['layers'].append(cue)
            full_dir=root/'full';full_dir.mkdir()
            win_dir=root/'win';win_dir.mkdir()
            full_wav=sound_mix.mix(p_short,full_dir)
            win_wav=sound_mix.mix(p_short,win_dir,start=0.5,end=3.5)
            binary,env=engine._tool('ffmpeg')
            full_pcm=subprocess.check_output([binary,'-v','error','-i',str(full_wav),'-f','f32le','pipe:1'],env=env)
            win_pcm=subprocess.check_output([binary,'-v','error','-i',str(win_wav),'-f','f32le','pipe:1'],env=env)
            s0,s1=round(0.5*48000)*8,round(3.5*48000)*8
            self.assertEqual(win_pcm,full_pcm[s0:s1])
