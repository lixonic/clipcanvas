"""Subtitle I/O and optional real local speech-recognition checks."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import sys
import time
import unittest
from unittest import mock

from clipcanvas import captions,model


class CaptionTests(unittest.TestCase):
    def test_unicode_multiline_bom_crlf_roundtrip_and_order(self):
        source='\ufeff2\r\n00:00:03,250 --> 00:00:05,000\r\nCafé — مرحبا\r\nSecond line\r\n\r\n1\r\n00:00:00,500 --> 00:00:01,800\r\nFirst\r\n'
        cues=captions.parse_srt(source)
        self.assertEqual([c['start'] for c in cues],[.5,3.25])
        encoded=captions.to_srt(cues)
        decoded=captions.parse_srt(encoded)
        self.assertEqual([{k:c[k] for k in ('start','end','text')} for c in cues],
                         [{k:c[k] for k in ('start','end','text')} for c in decoded])
        self.assertIn('Café — مرحبا\nSecond line',encoded)

    def test_malformed_input_rejected_as_a_whole(self):
        for source in ('1\n00:00:02,000 --> 00:00:01,000\nBackwards',
                       '1\n00:99:00,000 --> 00:00:03,000\nBad minute',
                       '1\n00:00:00,000 --> 00:00:01,000\nGood\n\n2\nBad\nBroken',
                       '1\n00:00:00,000 --> 00:00:01,000\n\x00',
                       '1\n00:00:00,000 --> 00:00:01,000\nA\n\n1\n00:00:02,000 --> 00:00:03,000\nB'):
            with self.subTest(source=source),self.assertRaises(ValueError):captions.parse_srt(source)
        for end in (float('nan'),float('inf'),True,0):
            with self.assertRaises(ValueError):captions.to_srt([{'start':0,'end':end,'text':'Bad'}])

    def test_atomic_srt_export_protects_media_project_and_hardlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'logo.png';image.write_bytes(b'original image')
            project=model.new_project();project['layers']=[model.new_visual(image)]
            project['captions']=[model.new_caption('Hello',.2,1.5)]
            saved=root/'project.clipcanvas';model.save_project(project,saved);project['_project_path']=str(saved)
            before=copy.deepcopy(project);link=root/'hardlink.srt';os.link(image,link)
            for path in (image,saved,link):
                with self.assertRaises(ValueError):captions.save_srt(project,path)
            output=root/'captions.srt';captions.save_srt(project,output)
            self.assertEqual(captions.load_srt(output)[0]['text'],'Hello')
            self.assertEqual(project,before);self.assertEqual(image.read_bytes(),b'original image')
            self.assertFalse(list(root.glob('.clipcanvas-captions-*')))

    def test_ready_setup_is_offline_and_cancel_does_not_start_worker(self):
        ready={'ready':True,'model':'tiny'}
        with mock.patch.object(captions,'runtime_status',return_value=ready),mock.patch.object(captions,'_run_worker') as worker:
            self.assertEqual(captions.setup_transcription(model='tiny'),ready);worker.assert_not_called()
        event=threading.Event();event.set()
        with mock.patch.object(captions,'_run_worker') as worker:
            with self.assertRaises(captions.CaptionCancelled):captions.setup_transcription(model='tiny',cancel=event)
            with self.assertRaises(captions.CaptionCancelled):captions.generate_from_audio('unused',model='tiny',cancel=event)
            worker.assert_not_called()

    def test_incompatible_runtime_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);python=root/'venv/bin/python';python.parent.mkdir(parents=True);python.touch()
            model_dir=root/'models/tiny';model_dir.mkdir(parents=True)
            for name in ('model.bin','config.json','tokenizer.json','ready.json'):(model_dir/name).touch()
            marker=root/'runtime.json'
            marker.write_text(json.dumps({'faster_whisper':'1.2.1','av':'19.0.1'}))
            self.assertFalse(captions.runtime_status(root,'tiny')['ready'])
            marker.write_text(json.dumps({'faster_whisper':'1.2.1','av':'16.1.0'}))
            self.assertTrue(captions.runtime_status(root,'tiny')['ready'])

    def test_cancellation_stops_running_worker_promptly(self):
        event=threading.Event();timer=threading.Timer(.3,event.set);timer.start();start=time.monotonic()
        try:
            with self.assertRaises(captions.CaptionCancelled):
                captions._run_worker([sys.executable,'-c','import time; time.sleep(30)'],cancel=event)
            self.assertLess(time.monotonic()-start,5)
        finally:timer.cancel()

    def test_missing_runtime_and_invalid_worker_results_leave_input_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/'audio.wav';audio.write_bytes(b'original audio')
            with self.assertRaises(captions.CaptionError):captions.generate_from_audio(audio,runtime_dir=root,model='tiny')
            status={'ready':True,'python':'python','model_path':str(root)}
            with mock.patch.object(captions,'runtime_status',return_value=status),mock.patch.object(captions,'_run_worker',return_value={'language':'en','captions':[{'text':'Bad','start':2,'end':1}]}):
                with self.assertRaises(captions.CaptionError):captions.generate_from_audio(audio,model='tiny')
            self.assertEqual(audio.read_bytes(),b'original audio')

    @unittest.skipUnless(os.environ.get('CLIPCANVAS_TEST_SPEECH_RUNTIME') and os.environ.get('CLIPCANVAS_TEST_SPEECH_AUDIO'),
                         'Set a downloaded local runtime and generated speech sample for actual recognition')
    def test_real_offline_speech_produces_editable_timed_captions(self):
        result=captions.generate_from_audio(os.environ['CLIPCANVAS_TEST_SPEECH_AUDIO'],
             runtime_dir=os.environ['CLIPCANVAS_TEST_SPEECH_RUNTIME'],model='tiny',language=None)
        self.assertEqual(result['language'],'en');self.assertGreaterEqual(len(result['captions']),2)
        words=' '.join(c['text'].lower() for c in result['captions'])
        self.assertIn('video',words);self.assertIn('computer',words)
        for cue in result['captions']:
            self.assertGreater(cue['end'],cue['start']);self.assertGreaterEqual(cue['start'],0)
        result['captions'][0]['text']='Corrected words'
        self.assertIn('Corrected words',captions.to_srt(result['captions']))


if __name__=='__main__':unittest.main()
