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

    def test_malayalam_generation_stays_offline_and_retains_unicode_timing(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'speech.wav';source.touch()
            expected={'language':'ml','captions':[{'text':'കേരളത്തിലെ മലയാളം വാർത്തകൾ', 'start':.4,'end':2.7},
                                                {'text':'നമസ്കാരം ലോകമേ', 'start':2.8,'end':4.1}]}
            status={'ready':True,'python':'private-python','model_path':d}
            with mock.patch.object(captions,'runtime_status',return_value=status) as runtime, \
                 mock.patch.object(captions,'_run_worker',return_value=expected) as worker:
                result=captions.generate_from_audio(source,model='small',language='ml',runtime_dir=d)
                command,_,_,environment=worker.call_args.args
                self.assertEqual(command[-1],'ml');self.assertEqual(environment['HF_HUB_OFFLINE'],'1')
                runtime.assert_called_once_with(d,captions.MALAYALAM_MODEL)
                self.assertEqual(result['language'],'ml')
                self.assertEqual([{k:item[k] for k in ('start','end','text')} for item in result['captions']],expected['captions'])
                exported=Path(d)/'Malayalam.srt';captions.save_srt(dict(captions=result['captions']),exported)
                self.assertEqual(captions.load_srt(exported)[0]['text'],expected['captions'][0]['text'])
                worker.reset_mock()
                with self.assertRaisesRegex(ValueError,'multilingual'):
                    captions.generate_from_audio(source,model='small.en',language='ml')
                worker.assert_not_called()

    def test_malayalam_rejects_wrong_language_and_script_without_replacing_captions(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'speech.wav';source.write_bytes(b'original recording')
            status={'ready':True,'python':'private-python','model_path':d}
            for language,text in [('hi','हमें बस'),('en','As soon as the mother comes'),
                                  ('ml','As soon as the mother comes'),('ml','हमें बस')]:
                with self.subTest(language=language,text=text):
                    result={'language':language,'captions':[{'text':text,'start':0,'end':2}]}
                    with mock.patch.object(captions,'runtime_status',return_value=status), \
                         mock.patch.object(captions,'_run_worker',return_value=result):
                        with self.assertRaisesRegex(captions.CaptionError,'Existing captions were kept'):
                            captions.generate_from_audio(source,model='small',language='ml')
            captions.validate_language([{'text':'അമ്മ വരുമ്പോൾ KSRTC ബസ്'}],'ml','ml')
            captions.validate_language([],'ml','ml')
            self.assertEqual(source.read_bytes(),b'original recording')

    def test_malayalam_worker_forces_transcription_and_short_timed_windows(self):
        from types import SimpleNamespace
        speech=mock.Mock()
        speech.transcribe.return_value=([SimpleNamespace(words=[
            SimpleNamespace(word=' മലയാളം',start=.2,end=1.2),
            SimpleNamespace(word=' വാർത്തകൾ',start=1.2,end=2.4)],text='',start=.2,end=2.4)],
            SimpleNamespace(language='ml',duration=3))
        with mock.patch.dict(sys.modules,{'faster_whisper':SimpleNamespace(WhisperModel=mock.Mock(return_value=speech))}), \
             mock.patch.object(sys,'argv',['captions.py','--worker','speech.wav','local-model','ml']), \
             mock.patch.object(captions,'_emit') as emit:
            self.assertEqual(captions._worker(),0)
        options=speech.transcribe.call_args.kwargs
        self.assertEqual(options['language'],'ml');self.assertEqual(options['task'],'transcribe')
        self.assertEqual(options['chunk_length'],7);self.assertTrue(options['word_timestamps'])
        self.assertEqual(options['beam_size'],1);self.assertEqual(options['temperature'],0.0)
        self.assertEqual(speech.max_length,888);self.assertEqual(options['max_new_tokens'],884)
        result=emit.call_args.args[0]['result']
        self.assertEqual(result['captions'],[{'start':.2,'end':2.4,'text':'മലയാളം വാർത്തകൾ'}])

    def test_malayalam_setup_requires_the_pinned_model_and_tokenizer(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('setup_transcription',Path(__file__).resolve().parents[1]/'tools/setup_transcription.py')
        setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);python=root/'venv/bin/python';python.parent.mkdir(parents=True);python.touch()
            with mock.patch.object(setup.subprocess,'run') as run,mock.patch.object(setup,'emit'):
                setup.run_setup(root,captions.MALAYALAM_MODEL)
            command=run.call_args.args[0]
            self.assertIn(captions.MALAYALAM_REPO,command)
            self.assertIn(captions.MALAYALAM_REVISION,command)
            self.assertIn(captions.MALAYALAM_TOKENIZER_REVISION,command)
            model_path=root/'models'/captions.MALAYALAM_MODEL
            for name in ('model.bin','config.json','tokenizer.json'):(model_path/name).touch()
            self.assertTrue(captions.runtime_status(root,captions.MALAYALAM_MODEL)['ready'])
            (model_path/'ready.json').write_text('{}')
            self.assertFalse(captions.runtime_status(root,captions.MALAYALAM_MODEL)['ready'])

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
