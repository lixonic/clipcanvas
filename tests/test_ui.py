"""Native desktop integration test; skipped when no display is available."""
import copy
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from types import SimpleNamespace

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

from clipcanvas.app import ClipCanvasWindow, CSS, file_chooser
from clipcanvas import engine, model
import test_engine


@unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
class DesktopWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.provider = Gtk.CssProvider()
        cls.provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), cls.provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def pump(self, condition, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            if condition():
                return
            time.sleep(.015)
        self.fail('The desktop workflow did not finish in time')

    def test_import_edit_preview_save_and_reopen(self):
        w = ClipCanvasWindow(recovery_dir=self.fixtures.root/'recovery')
        errors = []
        w.error = errors.append
        # Automated playback is muted without changing exported audio.
        w.canvas._pipeline.set_property('volume', 0)
        try:
            w.start_project(1080, 1920)
            sources = [str(self.fixtures.root / n) for n in ('landscape.mp4', 'portrait.mp4', 'music.wav')]
            w.import_paths(sources)
            self.pump(lambda: not w.busy and len(w.thumbs) == 2 and len(w.waves) == 1)
            self.assertFalse(errors)
            self.assertEqual(len(w.project['clips']), 2)
            self.assertEqual(len(w.project['audio']), 1)
            w.select('clips', 0)
            w.transition.set_active_id('fade')
            w.fields['transition_duration'][0].set_value(.4)
            w.fields['volume'][0].set_value(50)
            w.apply_fields()
            self.assertEqual(w.project['clips'][0]['volume'], .5)
            w.undo()
            self.assertEqual(w.project['clips'][0]['volume'], 1)
            w.redo()
            w.select('clips', 0)
            w.move(1)
            self.assertTrue(w.project['clips'][1]['path'].endswith('landscape.mp4'))
            w.undo()
            w.select('audio', 0)
            w.fields['start'][0].set_value(.25)
            w.fields['volume'][0].set_value(35)
            w.apply_fields()
            w.project_path = str(self.fixtures.root / 'desktop.clipcanvas')
            self.assertTrue(w.save())
            self.assertEqual(model.load_project(w.project_path), w.project)
            w.preview()
            self.pump(lambda: not w.busy and w.preview_key is not None and w.canvas.frame is not None)
            self.assertFalse(errors)
            preview = w.temp / (w.preview_key + '.mp4')
            info = engine.probe(preview)
            self.assertEqual((info['width'], info['height']), (360, 640))
            self.pump(lambda: w.canvas.position() > .15)
            w.select('clips', 1)
            self.assertFalse(w.canvas.playing)
            self.assertTrue(w.canvas._loaded)
            w.toggle_play()
            self.assertTrue(w.canvas.playing)
            # Late thumbnails must not interrupt a running prepared preview.
            w.asset_ready(sources[0], 'clips', next(str(p) for p in w.temp.glob('*.jpg')))
            self.assertTrue(w.canvas.playing)
            w.canvas.pause()
            w.open_project(w.project_path)
            self.assertIsNotNone(w.canvas.frame)
            w.start_project(1920, 1080)
            self.assertIsNone(w.canvas.frame)
            self.assertFalse(w.canvas.playing)
            self.assertFalse(errors)
        finally:
            w.cleanup()
            w.destroy()
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    def test_split_trim_snap_speed_scrub_and_recovery(self):
        w = ClipCanvasWindow(recovery_dir=self.fixtures.root/'editing-recovery')
        errors = [];w.error = errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')])
            self.pump(lambda: not w.busy and len(w.thumbs)==2 and len(w.waves)==1)
            w.select('clips',0)
            w.fields['speed'][0].set_value(2)
            w.rotation.set_active_id('90');w.flip_x.set_active(True);w.apply_fields()
            self.assertEqual(model.clip_duration(w.project['clips'][0]),.75)
            self.assertEqual(w.project['clips'][0]['rotation'],90)
            w.seek(.3)
            self.pump(lambda: not w.scrub_timer and 'Scrub frame' in w.preview_label.get_text())
            self.assertAlmostEqual(w.playhead,.3)
            self.assertIsNone(w.preview_key)
            w.split_selected()
            self.assertEqual(len(w.project['clips']),3)
            self.assertAlmostEqual(w.project['clips'][0]['out'],.6)
            w.undo();self.assertEqual(len(w.project['clips']),2)
            w.select('audio',0);w.seek(.5);w.split_selected()
            self.assertEqual(len(w.project['audio']),2)
            self.assertEqual(w.project['audio'][1]['start'],.5)
            w.undo()
            # Move a soundtrack near the next video boundary: snapping must align it.
            self.pump(lambda: len(w.timeline.hit)==3)
            hit=next(h for h in w.timeline.hit if h[0]=='audio')
            _,_,x,y,width,height=hit
            w.timeline.press(None,SimpleNamespace(button=1,x=x+15,y=y+20))
            w.timeline.release(None,SimpleNamespace(x=x+15+.72*w.timeline.scale))
            self.assertAlmostEqual(w.project['audio'][0]['start'],.75,delta=1/30)
            w.undo();w.snap_check.set_active(False)
            # Drag the right video edge, preserving the source-time conversion at 2x speed.
            w.zoom(100)
            self.pump(lambda: w.timeline.hit[0][4]>60)
            _,_,x,y,width,height=w.timeline.hit[0]
            w.timeline.press(None,SimpleNamespace(button=1,x=x+width-2,y=y+20))
            w.timeline.release(None,SimpleNamespace(x=x+width-2-.2*w.timeline.scale))
            self.assertAlmostEqual(w.project['clips'][0]['out'],1.1,delta=.07)
            self.assertTrue(w.autosave())
            recovered,original=w.recovery.read(w.recovery.path)
            self.assertEqual(recovered,w.project)
            self.assertIsNone(original)
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_clean_canvas_and_native_file_chooser(self):
        w = ClipCanvasWindow(recovery_dir=self.fixtures.root/'native-ui-recovery')
        chooser = None
        try:
            w.resize(1600, 900)
            w.start_project(1920, 1080)
            self.pump(lambda: w.canvas.get_allocation().width > 500)
            # The preview occupies the main editor rather than sharing its
            # width with a permanently visible properties panel.
            self.assertGreater(w.canvas.get_allocation().width,
                               w.body.get_allocation().width * .65)
            self.assertFalse(w.properties_popover.get_visible())
            self.assertIs(w.get_titlebar(), w.header)
            self.assertIsNone(w.inspector.get_ancestor(Gtk.Paned))
            chooser = file_chooser(w, 'Add local videos',
                                   Gtk.FileChooserAction.OPEN, 'Add videos')
            self.assertIsInstance(chooser, Gtk.FileChooserNative)
            self.assertIs(chooser.get_transient_for(), w)
            self.assertEqual(chooser.get_action(), Gtk.FileChooserAction.OPEN)
        finally:
            if chooser is not None:
                chooser.destroy()
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_join_context_audio_extraction_and_fade_drag(self):
        w = ClipCanvasWindow(recovery_dir=self.fixtures.root/'context-ui-recovery')
        errors = []
        w.error = errors.append
        w.canvas._pipeline.set_property('volume', 0)
        try:
            w.start_project(320, 180)
            # Place the clip with sound second, so extraction must preserve a
            # nonzero offset as well as its source trim and playback speed.
            w.import_paths([str(self.fixtures.root/n)
                            for n in ('portrait.mp4', 'landscape.mp4')])
            self.pump(lambda: not w.busy and len(w.thumbs) == 2
                      and bool(w.timeline.join_hits))
            w.seek(.7)
            _, x, y, _ = w.timeline.join_hits[0]
            w.timeline.press(None, SimpleNamespace(button=1, x=x, y=y))
            self.assertEqual(w.selected, ('clips', 0))
            self.assertAlmostEqual(w.playhead, .7)
            self.assertTrue(w.properties_popover.get_visible())
            self.assertEqual(w.properties_stack.get_visible_child_name(), 'Transition')
            w.transition.set_active_id('fade')
            w.fields['transition_duration'][0].set_value(.25)
            w.apply_fields()
            self.assertEqual(w.project['clips'][0]['transition'], 'fade')
            self.assertAlmostEqual(w.project['clips'][0]['transition_duration'], .25)

            w.select('clips', 1, seek=False)
            for key, value in [('in', .2), ('out', 1.4), ('speed', 2),
                               ('volume', 40), ('fade_in', .1), ('fade_out', .15),
                               ('video_fade_in', .1), ('video_fade_out', .2)]:
                w.fields[key][0].set_value(value)
            w.open_properties('Picture')
            w.apply_fields()
            clip = w.project['clips'][1]
            self.assertAlmostEqual(clip['video_fade_in'], .1)
            self.assertAlmostEqual(clip['video_fade_out'], .2)
            self.assertAlmostEqual(clip['fade_in'], .1)
            self.assertAlmostEqual(clip['fade_out'], .15)

            before_extract = copy.deepcopy(w.project)
            expected_start = model.timeline(w.project)[1]['start']
            w.seek(.7)
            self.pump(lambda: len(w.timeline.hit) == 2)
            _, _, x, y, width, height = next(
                hit for hit in w.timeline.hit if hit[:2] == ('clips', 1))
            w.timeline.press(None, SimpleNamespace(button=3, x=x+width/2,
                                                   y=y+height/2))
            self.assertAlmostEqual(w.playhead, .7)
            extract = next(item for item in w.active_context_menu.get_children()
                           if isinstance(item, Gtk.MenuItem)
                           and item.get_label() == 'Extract audio')
            self.assertTrue(extract.get_sensitive())
            w.active_context_menu.popdown()
            extract.activate()
            self.pump(lambda: not w.busy and len(w.project['audio']) == 1
                      and len(w.waves) == 1)
            self.assertFalse(errors)
            track = w.project['audio'][0]
            self.assertAlmostEqual(track['start'], expected_start)
            self.assertEqual(track['path'], clip['path'])
            self.assertAlmostEqual(track['in'], .2)
            self.assertAlmostEqual(track['out'], 1.4)
            self.assertAlmostEqual(track['speed'], 2)
            self.assertAlmostEqual(track['volume'], .4)
            self.assertEqual(w.project['clips'][1]['volume'], 0)
            self.assertEqual(w.selected, ('audio', 0))

            w.zoom(100)
            fade_x = 100 + (expected_start + .1) * w.timeline.scale
            self.pump(lambda: len(w.timeline.fade_hits) == 2
                      and any(hit[1] == 'in' and abs(hit[2]-fade_x) < 1
                              for hit in w.timeline.fade_hits))
            _, edge, x, y, _ = next(hit for hit in w.timeline.fade_hits
                                    if hit[1] == 'in')
            self.assertEqual(edge, 'in')
            w.timeline.press(None, SimpleNamespace(button=1, x=x, y=y))
            self.assertEqual(w.timeline.drag['mode'], 'fade')
            target = x + .15 * w.timeline.scale
            w.timeline.motion(None, SimpleNamespace(x=target, y=y))
            # Invoke the drawing function directly: errors in a GTK draw
            # callback can otherwise be printed without failing the test.
            allocation = w.timeline.get_allocation()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                         allocation.width, allocation.height)
            w.timeline.draw(w.timeline, cairo.Context(surface))
            w.timeline.release(None, SimpleNamespace(x=target, y=y))
            self.assertAlmostEqual(w.project['audio'][0]['fade_in'], .25)
            w.undo()
            self.assertAlmostEqual(w.project['audio'][0]['fade_in'], .1)
            w.undo()
            self.assertEqual(w.project, before_extract)
            self.assertFalse(errors)
        finally:
            if getattr(w, 'active_context_menu', None) is not None:
                w.active_context_menu.popdown()
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)


    def test_text_image_layers_caption_edit_drag_trim_and_reopen(self):
        from clipcanvas import captions
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'layers-ui-recovery')
        errors=[];w.error=errors.append
        w.canvas._pipeline.set_property('volume',0)
        try:
            w.start_project(320,180);w.import_paths([str(self.fixtures.root/'landscape.mp4')])
            self.pump(lambda:not w.busy and bool(w.thumbs) and w.canvas.canvas_rect is not None)
            w.layer_tools.add_text()
            self.assertEqual(w.selected,('layers',0))
            self.assertEqual(w.properties_stack.get_visible_child_name(),'Text')
            w.layer_tools.text.get_buffer().set_text('Café — title')
            w.layer_tools.font.set_font_name('Sans 24');w.layer_tools.bold.set_active(True)
            w.fields['end'][0].set_value(1.2);w.fields['height'][0].set_value(35);w.apply_fields()
            self.assertEqual(w.project['layers'][0]['text'],'Café — title')
            self.assertTrue(w.project['layers'][0]['bold'])
            self.assertEqual(w.project['layers'][0]['background'],'#00000000')
            # Applying an already saved RGBA style must preserve transparency.
            w.select('layers',0,seek=False);w.apply_fields()
            self.assertEqual(w.project['layers'][0]['background'],'#00000000')
            self.pump(lambda:not w.scrub_timer and 'Scrub frame' in w.preview_label.get_text())
            x,y,width,height=w.canvas.canvas_rect
            w.layer_tools.canvas_press(SimpleNamespace(button=1,x=x+width*.5,y=y+height*.5))
            w.layer_tools.canvas_motion(SimpleNamespace(x=x+width*.6,y=y+height*.6))
            w.layer_tools.canvas_release(SimpleNamespace(button=1))
            self.assertAlmostEqual(w.project['layers'][0]['x'],.6,places=2)
            w.undo();self.assertAlmostEqual(w.project['layers'][0]['x'],.5)
            w.redo();self.assertAlmostEqual(w.project['layers'][0]['x'],.6,places=2)
            logo=self.fixtures.root/'ui-logo.png'
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,50,50);cr=cairo.Context(surface)
            cr.set_source_rgba(1,0,0,.7);cr.paint();surface.write_to_png(str(logo))
            w.import_paths([str(logo)])
            self.pump(lambda:not w.busy and str(logo) in w.layer_tools.assets)
            self.assertEqual(len(w.project['layers']),2)
            w.layer_tools.replace_captions(captions.parse_srt('1\n00:00:00,100 --> 00:00:01,000\nRecognized words\n'))
            w.layer_tools.select('captions',0,edit=True)
            self.assertEqual(w.properties_stack.get_visible_child_name(),'Text')
            w.layer_tools.text.get_buffer().set_text('Corrected words')
            w.fields['end'][0].set_value(1.3);w.apply_fields()
            self.assertEqual(w.project['captions'][0]['text'],'Corrected words')
            w.zoom(100);w.snap_check.set_active(False)
            self.pump(lambda:any(h[:2]==('layers',0) for h in w.timeline.hit))
            _,_,x,y,width,height=next(h for h in w.timeline.hit if h[:2]==('layers',0))
            w.timeline.press(None,SimpleNamespace(button=1,x=x+width-2,y=y+height/2))
            w.timeline.release(None,SimpleNamespace(x=x+width-2-.2*w.timeline.scale))
            self.assertAlmostEqual(w.project['layers'][0]['end'],1,delta=1/30)
            w.undo();self.assertAlmostEqual(w.project['layers'][0]['end'],1.2)
            w.project_path=str(self.fixtures.root/'layers-desktop.clipcanvas');self.assertTrue(w.save())
            saved=copy.deepcopy(w.project)
            srt=self.fixtures.root/'edited.srt';captions.save_srt(saved,srt)
            self.assertEqual(captions.load_srt(srt)[0]['text'],'Corrected words')
            w.preview();self.pump(lambda:not w.busy and w.preview_key is not None)
            self.assertTrue(w.canvas.composited)
            w.canvas.pause();w.open_project(w.project_path)
            self.assertEqual(w.project,saved);self.assertFalse(w.canvas.composited)
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    @unittest.skipUnless(os.environ.get('CLIPCANVAS_TEST_SPEECH_RUNTIME') and os.environ.get('CLIPCANVAS_TEST_SPEECH_AUDIO'),
                         'A provisioned local speech runtime is required')
    def test_generate_from_speech_then_edit_and_export_desktop(self):
        from clipcanvas import captions
        source=self.fixtures.root/'spoken.mp4'
        subprocess.run([self.fixtures.ffmpeg,'-v','error','-y','-f','lavfi','-i',
                        'color=c=navy:s=320x180:r=30','-i',os.environ['CLIPCANVAS_TEST_SPEECH_AUDIO'],
                        '-shortest','-c:v','libx264','-preset','ultrafast','-threads','1','-c:a','aac',str(source)],
                        env=self.fixtures.env,check=True)
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'speech-ui-recovery');errors=[];w.error=errors.append
        w.canvas._pipeline.set_property('volume',0)
        try:
            w.start_project(320,180);w.import_paths([str(source)])
            self.pump(lambda:not w.busy and bool(w.thumbs))
            # This is the same background job used by the Generate dialog:
            # private runtime -> timeline-aware audio -> real offline ASR -> editable cues.
            w.layer_tools.generate_job(runtime_dir=os.environ['CLIPCANVAS_TEST_SPEECH_RUNTIME'])
            self.pump(lambda:not w.busy,timeout=60)
            self.assertFalse(errors);self.assertGreaterEqual(len(w.project['captions']),2)
            self.assertIn('computer',' '.join(c['text'].lower() for c in w.project['captions']))
            w.layer_tools.select('captions',0,edit=True)
            w.layer_tools.text.get_buffer().set_text('Welcome to ClipCanvas.');w.apply_fields()
            w.project['caption_style'].update(font_size=18,height=.25)
            w.layer_tools.add_text();w.layer_tools.text.get_buffer().set_text('Local video')
            w.layer_tools.font.set_font_name('Sans 26');w.apply_fields()
            output=self.fixtures.root/'speech-captioned.mp4'
            w.render_job(str(output),False,lambda path:w.set_status('Export test finished'))
            self.pump(lambda:not w.busy,timeout=60)
            self.assertFalse(errors);self.assertTrue(output.is_file())
            self.assertAlmostEqual(engine.probe(output)['duration'],model.project_duration(w.project),delta=.1)
            srt=self.fixtures.root/'speech-edited.srt';captions.save_srt(w.project,srt)
            self.assertEqual(captions.load_srt(srt)[0]['text'],'Welcome to ClipCanvas.')
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)


    def test_shared_text_presets_and_logo_opacity_workflow(self):
        from clipcanvas import text_styles
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'presets-ui-recovery')
        errors=[];w.error=errors.append;w.canvas._pipeline.set_property('volume',0)
        try:
            w.start_project(320,180);w.import_paths([str(self.fixtures.root/'landscape.mp4')])
            self.pump(lambda:not w.busy and bool(w.thumbs))
            w.layer_tools.add_text();w.layer_tools.text.get_buffer().set_text('Keep my words')
            w.layer_tools.font.set_font_name('Sans 24');w.fields['height'][0].set_value(35)
            w.layer_tools.style_editor.preset_buttons['highlight'].clicked();w.apply_fields()
            self.assertEqual(w.project['layers'][0]['text'],'Keep my words')
            self.assertEqual(w.project['layers'][0]['background_mode'],'text')
            self.assertEqual(w.project['layers'][0]['font_size'],24)
            for key,_,_ in text_styles.PRESETS:
                w.select('layers',0,seek=False);w.layer_tools.style_editor.preset_buttons[key].clicked()
                w.layer_tools.text.get_buffer().set_text('Still editable');w.apply_fields()
                self.assertEqual(w.project['layers'][0]['text'],'Still editable')
            w.project['captions']=[model.new_caption('First cue',0,.6),model.new_caption('Second cue',.7,1.4)]
            w.changed();w.layer_tools.select('captions',0,edit=True)
            w.layer_tools.style_editor.preset_buttons['shadow'].clicked()
            w.layer_tools.text.get_buffer().set_text('Corrected first cue');w.apply_fields()
            self.assertEqual(w.project['captions'][0]['text'],'Corrected first cue')
            self.assertEqual(w.project['captions'][1]['text'],'Second cue')
            self.assertEqual(w.project['caption_style']['shadow_color'],'#000000d9')
            w.undo();self.assertEqual(w.project['captions'][0]['text'],'First cue');w.redo()
            logo=self.fixtures.root/'preset-logo.png'
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,40,40);cr=cairo.Context(surface)
            cr.set_source_rgba(1,0,0,.5);cr.paint();surface.write_to_png(str(logo))
            w.import_paths([str(logo)]);self.pump(lambda:not w.busy and str(logo) in w.layer_tools.assets)
            w.select('layers',1,seek=False)
            self.assertTrue(w.layer_tools.overlay_controls.get_visible())
            w.project['layers'][1].update(start=.2,end=.8);w.layer_tools.overlay_full_video()
            self.assertEqual(w.project['layers'][1]['start'],0)
            self.assertEqual(w.project['layers'][1]['end'],model.project_duration(w.project))
            w.seek(.7);history=len(w.history)
            w.layer_tools.opacity_begin();w.layer_tools.opacity.set_value(50);w.layer_tools.opacity.set_value(35);w.layer_tools.opacity_end()
            self.assertEqual(len(w.history),history+1)
            self.assertAlmostEqual(w.project['layers'][1]['opacity'],.35)
            self.assertAlmostEqual(w.playhead,.7)
            w.undo();self.assertEqual(w.project['layers'][1]['opacity'],1);w.redo()
            w.preview();self.pump(lambda:not w.busy and w.preview_key is not None)
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)


    def test_timeline_row_arrows_move_tracks_without_retiming(self):
        from clipcanvas import track_layout
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'row-layout-recovery')
        errors=[];w.error=errors.append;w.canvas._pipeline.set_property('volume',0)
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')])
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and bool(w.waves))
            w.project['clips'][0].update(transition='fade',transition_duration=.2)
            w.layer_tools.add_text();w.layer_tools.font.set_font_name('Sans 24');w.apply_fields()
            w.project['captions']=[model.new_caption('Editable words',0,.7),model.new_caption('Second',.8,1.2)]
            w.changed();w.preview();self.pump(lambda:not w.busy and w.preview_key is not None)
            w.canvas.pause();w.seek(.5);key=w.preview_key
            before=copy.deepcopy(w.project);w.select('captions',0,seek=False)
            for _ in range(3):w.row_up_button.clicked()
            self.assertEqual(track_layout.rows(w.project)[0][0],'captions')
            self.assertFalse(w.row_up_button.get_sensitive())
            self.assertTrue(w.row_down_button.get_sensitive())
            self.assertEqual(w.preview_key,key);self.assertAlmostEqual(w.playhead,.5)
            for field in before:
                if field!='timeline_order':self.assertEqual(w.project[field],before[field])
            w.select('clips',0,seek=False)
            w.row_down_button.clicked();w.row_down_button.clicked()
            self.assertFalse(w.row_down_button.get_sensitive())
            self.pump(lambda:bool(w.timeline.join_hits) and w.timeline.join_hits[0][2]>100)
            video_y=next(row[3] for row in track_layout.rows(w.project) if row[0]=='video')
            self.assertEqual(w.timeline.join_hits[0][2],video_y+40)
            self.assertEqual(w.timeline.add_hit[1],video_y+15)
            _,cx,cy,_=w.timeline.join_hits[0]
            w.timeline.press(None,SimpleNamespace(button=1,x=cx,y=cy))
            self.assertEqual(w.properties_stack.get_visible_child_name(),'Transition')
            w.properties_popover.popdown()
            w.select('audio',0,seek=False)
            self.pump(lambda:len(w.timeline.fade_hits)==2)
            audio_y=next(row[3] for row in track_layout.rows(w.project) if row[1]=='audio')
            self.assertTrue(all(hit[3]==audio_y+4 for hit in w.timeline.fade_hits))
            saved=copy.deepcopy(w.project)
            w.project_path=str(self.fixtures.root/'row-order.clipcanvas');self.assertTrue(w.save())
            w.open_project(w.project_path);self.assertEqual(w.project,saved)
            w.select('captions',0,seek=False);w.row_down_button.clicked()
            moved=copy.deepcopy(w.project);w.undo();self.assertEqual(w.project,saved)
            w.redo();self.assertEqual(w.project,moved)
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)


    def test_right_click_voice_effects_audition_cancel_apply_undo_and_save(self):
        from clipcanvas import audio_effects
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'voice-ui-recovery')
        errors=[];w.error=errors.append;w.canvas._pipeline.set_property('volume',0)
        callback_errors=[];audition_paths=[]
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','music.wav')])
            self.pump(lambda:not w.busy and bool(w.thumbs) and bool(w.waves))
            w.project['clips'][0]['volume']=0;w.changed();w.seek(.3)
            before=copy.deepcopy(w.project)
            def cancel_dialog():
                try:
                    d=w.voice_dialog;d.presets.set_active_id('Echo')
                    d.controls['gain_db'][0].set_value(6)
                    d.response(Gtk.ResponseType.CANCEL)
                except Exception as error:
                    callback_errors.append(error)
                    if w.voice_dialog:w.voice_dialog.response(Gtk.ResponseType.CANCEL)
                return False
            GLib.timeout_add(100,cancel_dialog)
            menu=w.context_menu('audio',0)
            entry=next(item for item in menu.get_children() if isinstance(item,Gtk.MenuItem) and item.get_label()=='Voice effects…')
            menu.popdown();entry.activate()
            self.assertEqual(w.project,before)
            def apply_dialog():
                try:
                    d=w.voice_dialog;d.player.set_property('volume',0)
                    d.presets.set_active_id('Narration');d.presets.set_active_id('Echo')
                    d.controls['gain_db'][0].set_value(6)
                    d.controls['echo_delay_ms'][0].set_value(200)
                    d.listen.clicked();self.pump(lambda:d.audition_path is not None or d.audition_error is not None)
                    self.assertIsNone(d.audition_error)
                    wet=d.audition_path;audition_paths.append(wet);self.assertTrue(wet.is_file())
                    wet_hash=wet.read_bytes()
                    d.compare.clicked();self.pump(lambda:d.audition_path is not None or d.audition_error is not None)
                    self.assertIsNone(d.audition_error);audition_paths.append(d.audition_path)
                    self.assertNotEqual(d.audition_path.read_bytes(),wet_hash)
                    d.response(Gtk.ResponseType.OK)
                except Exception as error:
                    callback_errors.append(error)
                    if w.voice_dialog:w.voice_dialog.response(Gtk.ResponseType.CANCEL)
                return False
            GLib.timeout_add(100,apply_dialog);w.edit_voice_effects()
            self.assertFalse(callback_errors,callback_errors)
            self.assertTrue(all(not path.exists() for path in audition_paths))
            effects=w.project['audio'][0]['audio_effects']
            self.assertEqual(effects['gain_db'],6);self.assertEqual(effects['echo_delay_ms'],200)
            self.assertTrue(audio_effects.active(w.project['audio'][0]))
            self.assertAlmostEqual(w.playhead,.3)
            after=copy.deepcopy(w.project);w.undo();self.assertEqual(w.project,before)
            w.redo();self.assertEqual(w.project,after)
            w.project_path=str(self.fixtures.root/'voice-effects.clipcanvas');self.assertTrue(w.save())
            w.open_project(w.project_path);self.assertEqual(w.project,after)
            w.preview();self.pump(lambda:not w.busy and w.preview_key is not None)
            self.assertTrue(w.canvas.composited);self.assertFalse(errors)
        finally:
            if getattr(w,'voice_dialog',None):w.voice_dialog.destroy()
            if getattr(w,'active_context_menu',None):w.active_context_menu.popdown()
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)


if __name__ == '__main__':
    unittest.main()
