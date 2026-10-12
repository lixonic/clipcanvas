"""Attached sounds follow layer times in playback/export and retain originals."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

from clipcanvas import engine,model


class LayerSoundRenderTests(unittest.TestCase):
    def test_entry_exit_sounds_join_mix_but_not_original_speech_and_preserve_project(self):
        with tempfile.TemporaryDirectory(prefix='clipcanvas-layer-sound-') as directory:
            root=Path(directory);p=model.new_project(160,90);p['clips']=[model.new_card('#173a49',2)]
            p['layers']=[model.new_text('Pop sound',.4,1.7)];p['layers'][0]['sound_effect'].update(preset='pop',volume=.4,trigger='both')
            before=copy.deepcopy(p);mixed=root/'mix.wav';original=root/'original.wav'
            engine.render_audio(p,mixed,'mix');engine.render_audio(p,original,'video')
            def pcm(path,start,length):
                with wave.open(str(path)) as audio:
                    rate=audio.getframerate();audio.setpos(round(start*rate));return audio.readframes(round(length*rate))
            self.assertTrue(all(value==0 for value in pcm(mixed,.05,.2)))
            self.assertTrue(any(pcm(mixed,.4,.1)));self.assertTrue(any(pcm(mixed,1.5,.2)))
            self.assertTrue(all(value==0 for value in pcm(original,0,2)))
            self.assertEqual(p,before);self.assertEqual(p['audio'],[])
            full=root/'full.mkv';seeked=root/'seek.mkv'
            for path,start in ((full,0),(seeked,1.2)):
                with path.open('wb') as output:
                    for data in engine.live_stream(p,start):output.write(data)
            ffmpeg,env=engine._tool('ffmpeg')
            def raw(path,start):
                return subprocess.run([ffmpeg,'-v','error','-i',str(path),'-ss',str(start),'-t','0.7','-vn',
                    '-ar','48000','-ac','2','-c:a','pcm_s16le','-f','s16le','pipe:1'],env=env,capture_output=True,check=True).stdout
            self.assertEqual(raw(full,1.2),raw(seeked,0))
            picture=engine.preview_frame(p,1.55)
            self.assertEqual((picture['width'],picture['height']),(160,90));self.assertEqual(p,before)

    def test_short_element_trims_sound_and_original_speech_ignores_missing_cue(self):
        with tempfile.TemporaryDirectory(prefix='clipcanvas-short-sound-') as directory:
            root=Path(directory);p=model.new_project(160,90)
            p['clips']=[model.new_card('#173a49',2)]
            p['layers']=[model.new_text('Short',.4,.5)]
            p['layers'][0]['sound_effect'].update(preset='whoosh',trigger='both')
            mixed=root/'short.wav';engine.render_audio(p,mixed,'mix')
            with wave.open(str(mixed)) as audio:
                audio.setpos(round(.6*audio.getframerate()))
                self.assertFalse(any(audio.readframes(round(.4*audio.getframerate()))))
            sound=p['layers'][0]['sound_effect']
            sound.update(preset='file',path=str(root/'missing.wav'))
            before=copy.deepcopy(p)
            engine.render_audio(p,mixed,'video')
            self.assertEqual(p,before)
            with self.assertRaisesRegex(engine.RenderError,'Missing media'):
                engine.render_audio(p,mixed,'mix')
