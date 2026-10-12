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
