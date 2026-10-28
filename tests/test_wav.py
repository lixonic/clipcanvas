"""WAV soundtrack regression checks using generated media, never user files."""

import array
import copy
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave
import threading

from clipcanvas import engine, model


class WavSoundtrackTests(unittest.TestCase):
    FORMATS = (
        ("pcm16-mono.wav", "pcm_s16le", 44100, 1, 310),
        ("pcm24-stereo.wav", "pcm_s24le", 48000, 2, 470),
        ("float32-stereo.WAV", "pcm_f32le", 48000, 2, 730),
    )

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="clipcanvas-wav-tests-")
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.root = Path(cls.scratch.name)
        cls.ffmpeg, cls.env = engine._tool("ffmpeg")
        base = [cls.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-filter_threads", "1"]
        subprocess.run(base + [
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-pix_fmt", "yuv420p", str(cls.root / "silent-video.mp4"),
        ], env=cls.env, check=True, capture_output=True)
        for name, codec, rate, channels, frequency in cls.FORMATS:
            # A different tone in the first half-second makes an incorrect
            # source trim detectable in the exported sound, not just metadata.
            source = (f"aevalsrc=if(lt(t\\,0.5)\\,"
                      f"0.125*sin(2*PI*{frequency * 2}*t)\\,"
                      f"0.125*sin(2*PI*{frequency}*t)):s={rate}:d=2.5")
            subprocess.run(base + [
                "-f", "lavfi", "-i", source, "-c:a", codec, "-ar", str(rate),
                "-ac", str(channels), str(cls.root / name),
            ], env=cls.env, check=True, capture_output=True)

    def project(self):
        project = model.new_project(160, 90)
        project["clips"].append(model.new_clip(self.root / "silent-video.mp4"))
        for index, (name, _, _, _, _) in enumerate(self.FORMATS):
            track = model.new_audio(self.root / name)
            track.update({"in": 0.6, "out": 2.1, "start": 0.25 + index * 0.35,
                          "volume": 0.5, "fade_in": 0.2, "fade_out": 0.2})
            project["audio"].append(track)
        return project

    def test_wav_import_waveform_and_project_roundtrip(self):
        for name, _, _, channels, _ in self.FORMATS:
            with self.subTest(wav=name):
                metadata = engine.probe(self.root / name)
                self.assertTrue(metadata["has_audio"])
                self.assertFalse(metadata["has_video"])
                self.assertAlmostEqual(metadata["duration"], 2.5, delta=0.01)
                waveform = engine.waveform(self.root / name, max_points=50)
                self.assertEqual(len(waveform), 50)
                self.assertTrue(all(math.isfinite(value) and 0 <= value <= 1
                                    for value in waveform))
                # FFmpeg's mono-to-stereo conversion uses equal-power gains.
                expected=.125 if channels==1 else .125/math.sqrt(2)
                self.assertTrue(all(abs(value-expected)<.002 for value in waveform))
        project = self.project()
        self.assertTrue(model.validate_project(project, require_clips=True))
        saved = self.root / "wav-layers.clipcanvas"
        model.save_project(project, saved)
        self.assertEqual(model.load_project(saved), project)

    def test_waveform_keeps_silence_short_pulses_high_frequencies_and_stereo_phase(self):
        path=self.root/'pulses.wav';rate=48000
        samples=array.array('h')
        for frame in range(rate*3):
            at=frame/rate
            amplitude=.8 if 1.237<=at<1.239 or 2.90<=at<2.95 else .2 if .3<=at<.5 else 0
            value=round(amplitude*32767*math.sin(2*math.pi*6000*at))
            samples.extend((value,-value))
        with wave.open(str(path),'wb') as stream:
            stream.setparams((2,2,rate,0,'NONE','not compressed'));stream.writeframes(samples.tobytes())
        digest=hashlib.sha256(path.read_bytes()).digest()
        peaks=engine.waveform(path)
        self.assertEqual(len(peaks),600)
        self.assertEqual(max(peaks[:50]),0)
        self.assertAlmostEqual(peaks[70],.2,delta=.001)
        self.assertAlmostEqual(peaks[247],.8,delta=.001)
        self.assertAlmostEqual(peaks[585],.8,delta=.001)
        self.assertEqual(max(peaks[300:550]),0)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),digest)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(engine.RenderCancelled):engine.waveform(path,cancel=cancel)

    def test_waveform_reads_past_thirty_minutes_without_stretching_early_audio(self):
        path=self.root/'long-pulse.wav';rate=8000
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,rate,0,'NONE','not compressed'))
            for second in range(1802):
                value=10000 if second==1801 else 0
                stream.writeframes(array.array('h',[value]*rate).tobytes())
        peaks=engine.waveform(path,max_points=1802)
        self.assertEqual(max(peaks[:-1]),0)
        self.assertAlmostEqual(peaks[-1],10000/32768,delta=.0001)

    def test_split_audio_preserves_the_end_and_join_in_rendered_sound(self):
        path=self.root/'split-pulses.wav';rate=48000
        samples=array.array('h')
        for frame in range(rate*4):
            at=frame/rate
            amplitude=.3 if .1<=at<3.98 else 0
            samples.append(round(amplitude*32767*math.sin(2*math.pi*440*at)))
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,rate,0,'NONE','not compressed'));stream.writeframes(samples.tobytes())
        for speed,effects in ((1,{}),(1,{'echo_enabled':True,'echo_delay_ms':240,'echo_level':.35}),
                              (.5,{}),(2,{'compressor_enabled':True})):
            with self.subTest(speed=speed,effects=effects):
                project=model.new_project(160,90);project['clips']=[model.new_card(duration=10)]
                track=model.new_audio(path);track.update(start=.2,audio_effects=effects,speed=speed,fade_in=.05,fade_out=.01)
                project['audio']=[track]
                before=copy.deepcopy(project)
                split=model.split_item(project,'audio',0,.2+1.92345/speed)
                self.assertEqual(split['audio'][1]['out'],track['out'])
                self.assertAlmostEqual(sum(model.clip_duration(a) for a in split['audio']),model.clip_duration(track))
                def render(p,name):
                    output=self.root/name;engine.render_audio(p,output)
                    with wave.open(str(output),'rb') as stream:
                        data=array.array('h');data.frombytes(stream.readframes(stream.getnframes()));return data
                whole=render(project,'whole.wav');pieces=render(split,'pieces.wav')
                self.assertEqual(len(whole),len(pieces))
                self.assertEqual(whole,pieces,'Splitting alone must preserve every audible sample, including the join and tail')
                again=model.split_item(split,'audio',1,.2+3.1/speed)
                self.assertEqual(whole,render(again,'three-pieces.wav'))
                self.assertEqual(project,before)

    def test_audio_cuts_with_gaps_or_changed_settings_stay_independent(self):
        project=self.project();project['audio']=project['audio'][:1]
        split=model.split_item(project,'audio',0,.8)
        for field,value in [('volume',.1),('speed',2),('start',1.1),('in',1.3),
                            ('fade_in',.1),('lane_id','another-lane'),
                            ('audio_effects',{'gain_db':6})]:
            with self.subTest(field=field):
                edited=copy.deepcopy(split);edited['audio'][1][field]=value
                before=copy.deepcopy(edited)
                self.assertEqual(len(engine._continuous_audio(edited['audio'])),2)
                self.assertEqual(edited,before)


    def test_wav_layers_trim_offsets_and_fades_in_export(self):
        project = self.project()
        output = self.root / "wav-layers.mp4"
        engine.render(project, output, quality="high")
        metadata = engine.probe(output)
        self.assertTrue(metadata["has_audio"])
        self.assertTrue(metadata["has_video"])
        self.assertAlmostEqual(metadata["duration"], 3.0, delta=0.07)
        raw = subprocess.run([
            self.ffmpeg, "-v", "error", "-i", str(output), "-vn", "-ar", "8000",
            "-ac", "1", "-f", "f32le", "pipe:1",
        ], env=self.env, check=True, capture_output=True).stdout
        samples = array.array("f")
        samples.frombytes(raw)
        self.assertGreaterEqual(len(samples), 3 * 8000)

        def amplitude(at, frequency, duration=0.1):
            segment = samples[round(at * 8000):round((at + duration) * 8000)]
            real = sum(value * math.cos(2 * math.pi * frequency * i / 8000)
                       for i, value in enumerate(segment))
            imag = sum(value * math.sin(2 * math.pi * frequency * i / 8000)
                       for i, value in enumerate(segment))
            return 2 * math.hypot(real, imag) / len(segment)

        for index, (name, _, _, _, frequency) in enumerate(self.FORMATS):
            with self.subTest(wav=name):
                start = project["audio"][index]["start"]
                # All three WAV encodings remain audible when layered together.
                steady = amplitude(1.2, frequency)
                self.assertGreater(steady, 0.035)
                self.assertLess(amplitude(start - 0.15, frequency), 0.002)
                self.assertLess(amplitude(start + 0.02, frequency), steady * 0.65)
                self.assertLess(amplitude(start + 1.38, frequency), steady * 0.65)
                self.assertLess(amplitude(start + 1.55, frequency), 0.002)
                # The doubled opening tone must have been removed by the trim.
                self.assertLess(amplitude(start + 0.2, frequency * 2), 0.003)
        tail = samples[round(2.7 * 8000):round(2.9 * 8000)]
        self.assertLess(math.sqrt(sum(value * value for value in tail) / len(tail)),
                        0.0001)


if __name__ == "__main__":
    unittest.main()
