"""Spoken-word timing, saved appearance and real preview/export parity."""
import copy
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from clipcanvas import animations, captions, caption_styles, engine, model, visuals
from clipcanvas.preferences import Preferences


class CaptionHighlightTests(unittest.TestCase):
    def cue(self):
        cue = model.new_caption('Hello മലയാളം', 1, 3)
        cue['word_timings'] = [dict(text='Hello',start=0.,end=.15),
                               dict(text='മലയാളം',start=.55,end=1.)]
        return cue

    def test_recognition_retains_words_and_combines_tokens_into_shaped_words(self):
        group = [SimpleNamespace(word=' മല',start=1,end=1.2),
                 SimpleNamespace(word='യാളം',start=1.2,end=1.4),
                 SimpleNamespace(word=' വാക്ക്',start=2,end=3)]
        timings = captions.timed_words(group, 1, 3)
        self.assertEqual([w['text'] for w in timings], ['മലയാളം','വാക്ക്'])
        self.assertAlmostEqual(timings[0]['end'], .2)
        self.assertEqual(timings[1]['start'], .5)
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'speech.wav';source.touch()
            cue=self.cue()
            with mock.patch.object(captions,'runtime_status',return_value=dict(ready=True,python='python',model_path=d)), \
                 mock.patch.object(captions,'_run_worker',return_value=dict(language='en',captions=[cue])):
                result=captions.generate_from_audio(source,language='en')
            self.assertEqual(result['captions'][0]['word_timings'],cue['word_timings'])

    def test_timing_handles_silence_moving_stretching_edits_and_reopening(self):
        cue=self.cue()
        self.assertEqual([animations.text_step(cue,t) for t in (1.1,1.5,2.2)], [0,-1,1])
        moved=copy.deepcopy(cue);moved.update(start=10,end=14)
        self.assertEqual([animations.text_step(moved,t) for t in (10.2,11,12.5)], [0,-1,1])
        moved['text']='Corrected words'
        self.assertEqual(animations.text_step(moved,11),0)
        with tempfile.TemporaryDirectory() as d:
            p=model.new_project();p['captions']=[cue]
            loaded=model.load_project(model.save_project(p,Path(d)/'timed.clipcanvas'))
            self.assertEqual(loaded['captions'],p['captions'])
            self.assertEqual(captions.parse_srt(captions.to_srt([cue]))[0]['text'],cue['text'])
        for value in ([dict(text='a',start=float('nan'),end=1)],
                      [dict(text='a',start=.8,end=.2)],
                      [dict(text='a',start=0,end=.7),dict(text='b',start=.5,end=1)]):
            p=model.new_project();p['captions']=[dict(cue,word_timings=value)]
            with self.assertRaises(ValueError):model.validate_project(p,check_files=False)

    def test_presets_persist_across_projects_without_cue_data(self):
        with tempfile.TemporaryDirectory() as d:
            preferences=Preferences(d)
            style=caption_styles.spoken_word(model.caption_style())
            style.update(x=.3,y=.2,width=.65,height=.3,text='Do not save',word_timings=[{}])
            caption_styles.save_preset(preferences,'My Malayalam',style)
            loaded=caption_styles.saved_presets(Preferences(d))['My Malayalam']
            self.assertTrue(loaded['word_highlight']);self.assertEqual(loaded['y'],.2)
            self.assertNotIn('text',loaded);self.assertNotIn('word_timings',loaded)
            p=model.new_project();p['caption_style']=loaded
            self.assertTrue(model.validate_project(p,check_files=False))
            loaded['y']=.7
            self.assertEqual(caption_styles.saved_presets(preferences)['My Malayalam']['y'],.2)
            with self.assertRaises(ValueError):caption_styles.save_preset(preferences,' ',style)

    def test_yellow_word_native_export_and_seek_match_with_silent_gap(self):
        p=model.new_project(320,180);p['clips']=[model.new_card('#122638',3)]
        p['captions']=[self.cue()]
        p['caption_style']=caption_styles.spoken_word(model.caption_style())
        p['caption_style'].update(font_size=25,x=.47,y=.25,height=.4,outline_width=1.5)
        before=copy.deepcopy(p)
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'highlight.mkv';engine.render(p,output,quality='lossless')
            ffmpeg,env=engine._tool('ffmpeg')
            yellow_counts=[]
            for at in (1.1,1.5,2.2):
                exported=subprocess.run([ffmpeg,'-v','error','-i',str(output),'-ss',str(at),'-frames:v','1',
                    '-pix_fmt','rgb24','-f','rawvideo','pipe:1'],env=env,capture_output=True,check=True).stdout
                frame=engine.preview_frame(p,at,preview_edge=0)['pixels']
                self.assertLess(sum(abs(a-b) for a,b in zip(frame,exported))/len(exported),2)
                surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,320,180);cr=cairo.Context(surface)
                cr.set_source_rgb(18/255,38/255,56/255);cr.paint()
                visuals.draw(cr,p,at,(0,0,320,180))
                data=bytes(surface.get_data())
                native=bytes(channel for i in range(0,len(data),4) for channel in (data[i+2],data[i+1],data[i]))
                # Export rounds the sprite origin and converts the card to YUV.
                self.assertLess(sum(abs(a-b) for a,b in zip(native,exported))/len(exported),6)
                exported_yellow=sum(exported[i]>200 and exported[i+1]>180 and exported[i+2]<50 for i in range(0,len(exported),3))
                if at == 1.5:self.assertEqual(exported_yellow,0)
                else:self.assertGreater(exported_yellow,100)
                yellow_counts.append(sum(native[i]>200 and native[i+1]>180 and native[i+2]<50 for i in range(0,len(native),3)))
            self.assertGreater(yellow_counts[0],100);self.assertEqual(yellow_counts[1],0);self.assertGreater(yellow_counts[2],100)
        self.assertEqual(p,before)

    @unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
    def test_editor_saves_loads_and_applies_position_and_colors(self):
        from clipcanvas.text_style_ui import CaptionStyleEditor
        with tempfile.TemporaryDirectory() as d:
            parent=Gtk.Box();style=model.caption_style()
            editor=CaptionStyleEditor(parent,style,Preferences(d))
            editor.set_style(caption_styles.spoken_word(style))
            editor.position['x'].set_value(42);editor.position['y'].set_value(18)
            editor.preset_name.set_text('Top speech');editor.save_preset()
            editor.word_highlight.set_active(False);editor.position['y'].set_value(85)
            editor.load_preset()
            result=editor.read_into(copy.deepcopy(style))
            self.assertTrue(result['word_highlight']);self.assertEqual(result['x'],.42);self.assertEqual(result['y'],.18)
            self.assertEqual(result['highlight_color'],'#ffe500ff')
            self.assertEqual(style,model.caption_style())
