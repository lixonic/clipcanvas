"""Voice settings and actual processed sound from generated WAV/MP3 media."""
import array
import copy
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from clipcanvas import audio_effects, engine, model


class VoiceSettingsTests(unittest.TestCase):
    def test_defaults_bypass_presets_and_invalid_controls(self):
        self.assertFalse(audio_effects.active({}))
        for name in audio_effects.PRESETS:
            effects=audio_effects.preset(name);audio_effects.validate(effects)
            effects['enabled']=False
            self.assertEqual(audio_effects.filters({'audio_effects':effects}),[])
        for field,value in (('gain_db',float('nan')),('echo_repeats',1.5),('echo_delay_ms',0),
                            ('compressor_enabled',1),('echo_decay',1),('gain_db','6'),
                            ('bass_db',True),('unknown',1)):
            with self.subTest(field=field):
                p=model.new_project();track=model.new_audio('/tmp/voice.wav',{'duration':2,'has_audio':True})
                track['audio_effects']=audio_effects.defaults();track['audio_effects'][field]=value;p['audio']=[track]
                with self.assertRaises(ValueError):model.validate_project(p,check_files=False)
        with self.assertRaises(ValueError):audio_effects.validate({'highpass_hz':500,'lowpass_hz':300})
        with self.assertRaises(ValueError):audio_effects.preset('Bad')

    def test_effects_roundtrip_and_split_are_independent(self):
        p=model.new_project();p['audio']=[model.new_audio('/tmp/voice.wav',{'duration':3,'has_audio':True})]
        p['audio'][0]['audio_effects']=audio_effects.preset('Echo')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'voice.clipcanvas';model.save_project(p,path)
            self.assertEqual(model.load_project(path),p)
        split=model.split_item(p,'audio',0,1)
        self.assertEqual(split['audio'][0]['audio_effects'],p['audio'][0]['audio_effects'])
        split['audio'][1]['audio_effects']['gain_db']=6
        self.assertEqual(split['audio'][0]['audio_effects']['gain_db'],0)
        self.assertEqual(p['audio'][0]['audio_effects']['gain_db'],0)


class VoiceRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch=tempfile.TemporaryDirectory(prefix='clipcanvas-voice-tests-');cls.addClassCleanup(cls.scratch.cleanup)
        cls.root=Path(cls.scratch.name);cls.ffmpeg,cls.env=engine._tool('ffmpeg')
        base=[cls.ffmpeg,'-v','error','-y','-filter_threads','1']
        subprocess.run(base+['-f','lavfi','-i','color=c=blue:s=160x90:r=30:d=3',
                            '-c:v','libx264','-preset','ultrafast','-threads','1',str(cls.root/'silent.mp4')],env=cls.env,check=True)
        cls.make_audio('pulse.wav','aevalsrc=if(between(t\\,0.3\\,0.32)\\,0.2*sin(2*PI*1000*t)\\,0):s=48000:d=2')
        cls.make_audio('tone.wav','aevalsrc=0.06*sin(2*PI*1000*t):s=48000:d=2')
        cls.make_audio('bands.wav','aevalsrc=0.03*(sin(2*PI*100*t)+sin(2*PI*2000*t)+sin(2*PI*10000*t)):s=48000:d=2')
        cls.make_audio('levels.wav','aevalsrc=if(lt(t\\,1)\\,0.05\\,0.5)*sin(2*PI*1000*t):s=48000:d=2')
        subprocess.run(base+['-i',str(cls.root/'pulse.wav'),'-c:a','libmp3lame','-b:a','192k',str(cls.root/'pulse.mp3')],env=cls.env,check=True)

    @classmethod
    def make_audio(cls,name,source):
        subprocess.run([cls.ffmpeg,'-v','error','-y','-f','lavfi','-i',source,'-c:a','pcm_f32le',str(cls.root/name)],env=cls.env,check=True)

    def samples(self,path):
        raw=subprocess.run([self.ffmpeg,'-v','error','-i',str(path),'-vn','-af','pan=mono|c0=c0','-ar','48000','-ac','1','-f','f32le','pipe:1'],
                           env=self.env,check=True,capture_output=True).stdout
        samples=array.array('f');samples.frombytes(raw);return samples

    def amplitude(self,samples,at,freq=1000,length=.02):
        chunk=samples[round(at*48000):round((at+length)*48000)]
        self.assertTrue(chunk)
        real=sum(v*math.cos(2*math.pi*freq*i/48000) for i,v in enumerate(chunk))
        imag=sum(v*math.sin(2*math.pi*freq*i/48000) for i,v in enumerate(chunk))
        return 2*math.hypot(real,imag)/len(chunk)

    def rms(self,samples,at,length=.08):
        chunk=samples[round(at*48000):round((at+length)*48000)]
        return math.sqrt(sum(v*v for v in chunk)/len(chunk))

    def project(self,name):
        p=model.new_project(160,90);p['clips']=[model.new_clip(self.root/'silent.mp4')]
        p['audio']=[model.new_audio(self.root/name)];return p

    def test_echo_delay_decay_speed_trim_offset_and_boundary_wav_mp3_preview_export(self):
        for source in ('pulse.wav','pulse.mp3'):
            p=self.project(source);track=p['audio'][0]
            track.update(start=.25,**{'in':.2,'out':1.4,'speed':2})
            effects=audio_effects.preset('Echo');effects.update(echo_delay_ms=200,echo_level=.5,echo_decay=.5,echo_repeats=4)
            track['audio_effects']=effects
            for preview in (False,True):
                with self.subTest(source=source,preview=preview):
                    output=self.root/f'{source}-{preview}.mp4';engine.render(p,output,preview=preview)
                    samples=self.samples(output)
                    dry=self.rms(samples,.27)
                    self.assertGreater(dry,.01)
                    for at,ratio in ((.47,.5),(.67,.25)):
                        self.assertAlmostEqual(self.rms(samples,at)/dry,ratio,delta=.08)
                    self.assertLess(self.amplitude(samples,.901,length=.008),.001)
                    self.assertLess(self.amplitude(samples,.45,length=.03),.001)
            self.assertAlmostEqual(engine.probe(output)['duration'],3,delta=.07)

    def test_gain_bypass_and_audition_matches_preview_export_without_changing_source(self):
        p=self.project('tone.wav');track=p['audio'][0]
        digest=hashlib.sha256((self.root/'tone.wav').read_bytes()).hexdigest()
        effects=audio_effects.defaults();effects['gain_db']=6;track['audio_effects']=effects
        output=self.root/'gain.mp4';engine.render(p,output)
        sample=self.root/'gain-audition.wav';engine.render_track_sample(track,sample)
        dry=self.root/'dry-audition.wav';engine.render_track_sample(track,dry,with_effects=False)
        wet_amp=self.amplitude(self.samples(sample),.4,length=.1)
        dry_amp=self.amplitude(self.samples(dry),.4,length=.1)
        self.assertAlmostEqual(wet_amp/dry_amp,10**(6/20),delta=.02)
        self.assertAlmostEqual(self.amplitude(self.samples(output),.4,length=.1),wet_amp,delta=.004)
        effects['enabled']=False
        bypass=self.root/'bypass.wav';engine.render_track_sample(track,bypass)
        self.assertAlmostEqual(self.amplitude(self.samples(bypass),.4,length=.1),dry_amp,delta=.001)
        self.assertEqual(hashlib.sha256((self.root/'tone.wav').read_bytes()).hexdigest(),digest)

    def test_tone_compressor_and_peak_limiter_change_real_samples(self):
        track=model.new_audio(self.root/'bands.wav');dry=self.root/'bands-dry.wav'
        engine.render_track_sample(track,dry)
        track['audio_effects']=audio_effects.defaults();track['audio_effects'].update(bass_db=9,treble_db=-9)
        wet=self.root/'bands-wet.wav';engine.render_track_sample(track,wet)
        dry_samples=self.samples(dry);wet_samples=self.samples(wet)
        self.assertGreater(self.amplitude(wet_samples,.5,100,.1)/self.amplitude(dry_samples,.5,100,.1),1.7)
        self.assertLess(self.amplitude(wet_samples,.5,10000,.1)/self.amplitude(dry_samples,.5,10000,.1),.5)
        track['audio_effects']=audio_effects.preset('Telephone');telephone=self.root/'telephone.wav';engine.render_track_sample(track,telephone)
        narrow=self.samples(telephone)
        self.assertLess(self.amplitude(narrow,.5,100,.1),self.amplitude(dry_samples,.5,100,.1)*.3)
        self.assertLess(self.amplitude(narrow,.5,10000,.1),self.amplitude(dry_samples,.5,10000,.1)*.2)
        track=model.new_audio(self.root/'levels.wav');track['audio_effects']=audio_effects.defaults()
        track['audio_effects'].update(compressor_enabled=True,compressor_makeup_db=0,compressor_ratio=6,compressor_threshold_db=-20)
        compressed=self.root/'compressed.wav';engine.render_track_sample(track,compressed)
        samples=self.samples(compressed)
        self.assertLess(self.amplitude(samples,1.6,length=.1)/self.amplitude(samples,.5,length=.1),5)
        track['audio_effects'].update(compressor_enabled=False,gain_db=24)
        limited=self.root/'limited.wav';engine.render_track_sample(track,limited)
        self.assertLessEqual(max(abs(x) for x in self.samples(limited)),.952)

    def test_extraction_retains_effects_original_speech_bypasses_them_and_cancel_protects_files(self):
        video=self.root/'spoken.mp4'
        subprocess.run([self.ffmpeg,'-v','error','-y','-i',str(self.root/'silent.mp4'),'-i',str(self.root/'tone.wav'),
                        '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-shortest',str(video)],env=self.env,check=True)
        p=model.new_project(160,90);p['clips']=[model.new_clip(video)]
        p['clips'][0]['audio_effects']=audio_effects.preset('Telephone')
        p['clips'][0]['audio_effects']['gain_db']=-12
        detached=model.detach_audio(p,0)
        self.assertEqual(detached['audio'][0]['audio_effects'],p['clips'][0]['audio_effects'])
        original=self.root/'original.wav';engine.render_audio(p,original,source='video')
        self.assertGreater(self.amplitude(self.samples(original),.5,length=.1),.03)
        cancel=threading.Event();cancel.set();output=self.root/'cancel.wav';output.write_bytes(b'existing output')
        with self.assertRaises(engine.RenderCancelled):engine.render_track_sample(detached['audio'][0],output,cancel=cancel)
        self.assertEqual(output.read_bytes(),b'existing output')
        with self.assertRaises(engine.RenderError):engine.render_track_sample(detached['audio'][0],video)
        self.assertFalse(list(self.root.glob('.clipcanvas-*')))


if __name__=='__main__':unittest.main()
