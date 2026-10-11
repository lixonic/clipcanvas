"""Native desktop integration test; skipped when no display is available."""
import copy
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from unittest import mock
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

    def test_live_play_pause_seek_replay_edit_and_cleanup_without_mp4(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'live-recovery')
        w.canvas._pipeline.set_property('volume',0)
        errors=[];w.canvas.on_error=errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and len(w.waves)==1)
            w.project['clips'][0].update(transition='fade',transition_duration=.4)
            w.project['layers']=[model.new_text('Live',0,3)];w.project['layers'][0]['font_size']=24
            w.project['captions']=[model.new_caption('Caption',0,3)]
            w.changed()
            self.assertFalse(hasattr(w,'fps_combo'))
            with mock.patch.object(engine,'render',side_effect=AssertionError('Live play must not render an MP4')):
                w.toggle_play()
                self.assertFalse(w.busy)
                self.pump(lambda:w.canvas.live and w.canvas.position()>.15 and w.canvas.frame is not None)
                self.assertTrue(w.canvas.composited)
                self.assertIsNone(w.preview_key)
                self.assertFalse(list(w.temp.glob('*.mp4')))
                first=w.canvas._live
                w.toggle_play();at=w.canvas.position()
                deadline=time.monotonic()+.15
                self.pump(lambda:time.monotonic()>=deadline)
                self.assertAlmostEqual(w.canvas.position(),at,places=2)
                w.seek(1.8)
                self.pump(lambda:not w.scrub_timer and w.canvas._live is not first)
                second=w.canvas._live
                self.pump(lambda:second.pipeline.get_state(0)[1].value_nick=='paused')
                self.assertFalse(w.canvas.playing)
                self.assertAlmostEqual(w.canvas.position(),1.8,places=2)
                w.toggle_play();self.pump(lambda:w.canvas.position()>2)
                # Delayed asset completion must never replace the live stream.
                w.asset_ready(w.project['clips'][0]['path'],'clips',next(str(p) for p in w.temp.glob('*.jpg')))
                self.assertIs(w.canvas._live,second)
                self.pump(lambda:not w.canvas.playing)
                self.assertAlmostEqual(w.playhead,model.project_duration(w.project),places=2)
                w.toggle_play();self.pump(lambda:.05<w.canvas.position()<.5)
                w.select('clips',1);self.pump(lambda:not w.scrub_timer)
                self.assertFalse(w.canvas.playing)
                self.assertAlmostEqual(w.canvas.position(),model.timeline(w.project)[1]['start'],places=2)
                last=w.canvas._live
                w.changed()
                self.assertFalse(w.canvas.live)
                last.thread.join(timeout=5)
                self.assertFalse(last.thread.is_alive())
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_frame_rate_adapts_after_import_reorder_and_old_project_probe(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'fps-recovery')
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('portrait.mp4','landscape.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2)
            self.assertEqual(w.project['fps'],25)
            w.select('clips',1,seek=False);w.move(-1)
            self.assertEqual(w.project['fps'],30)
            w.undo();self.assertEqual(w.project['fps'],25)
            legacy=copy.deepcopy(w.project);legacy['fps']=60
            for clip in legacy['clips']:clip.pop('source_fps')
            saved=self.fixtures.root/'legacy-fps.clipcanvas';model.save_project(legacy,saved)
            w.saved=json.dumps(w.project,sort_keys=True);w.open_project(str(saved))
            self.assertEqual(w.project['fps'],25)
            w.media_metadata.clear();w.project['fps']=60
            for clip in w.project['clips']:clip.pop('source_fps',None)
            w.media_info_ready(w.project['clips'][0]['path'],engine.probe(w.project['clips'][0]['path']))
            self.assertEqual(w.project['fps'],25)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_drag_playhead_line_updates_frames_before_release_and_resumes_playback(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'drag-preview-recovery')
        w.canvas._pipeline.set_property('volume',0)
        errors=[];w.canvas.on_error=errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and bool(w.timeline.hit))
            w.zoom(100);w.seek(.3)
            self.pump(lambda:w.canvas.live and not w.scrub_timer and abs(w.canvas.position()-.3)<.01)
            before=copy.deepcopy(w.project);selected=w.selected
            y=w.timeline.hit[0][3]+40
            # Grab the line below the ruler, through the video thumbnail.
            w.timeline.press(None,SimpleNamespace(button=1,x=130,y=y))
            self.assertEqual(w.timeline.drag['mode'],'seek')
            self.assertTrue(w._drag_scrubbing)
            seen=set()
            for step in range(40):
                at=.45+.45*step/39
                w.timeline.motion(None,SimpleNamespace(x=100+at*100,y=y))
                deadline=time.monotonic()+.03
                self.pump(lambda:time.monotonic()>=deadline)
                if not w.canvas.live:seen.add(round(w.canvas.position(),2))
            self.assertGreaterEqual(len(seen),2,'Continuous pointer movement must not starve preview frames')
            w.timeline.motion(None,SimpleNamespace(x=190,y=y))
            self.pump(lambda:w.canvas.composited and not w.canvas.live and abs(w.canvas.position()-.9)<.04)
            first=bytes(w.canvas.frame.get_pixels())
            self.assertAlmostEqual(w.playhead,.9)
            # A stale tick must not pull the pointer back.
            w.on_position(.3,3.5);self.assertAlmostEqual(w.playhead,.9)
            w.timeline.motion(None,SimpleNamespace(x=330,y=y))
            self.pump(lambda:not w.canvas.live and abs(w.canvas.position()-2.3)<.04)
            self.assertNotEqual(first,bytes(w.canvas.frame.get_pixels()))
            self.assertIsNotNone(w.timeline.drag)
            self.assertEqual(w.project,before)
            self.assertEqual(w.selected,selected)
            # Zoom changes remain while seeking and only affect the viewport.
            w.preview_zoom_combo.set_active_id('2')
            w.timeline.release(None,SimpleNamespace(x=330,y=y))
            self.pump(lambda:not w._scrub_settling and abs(w.canvas.position()-2.3)<.01)
            self.assertFalse(w.canvas.live)
            self.assertFalse(w.canvas.playing)
            self.assertEqual(w.canvas.preview_zoom,2)
            self.assertEqual(w.project,before)
            # Dragging a playing timeline pauses audio until release, then
            # resumes from the new time rather than from the original player.
            w.seek(.2);self.pump(lambda:not w.scrub_timer)
            w.toggle_play();self.pump(lambda:w.canvas.position()>.3)
            x=100+w.playhead*w.timeline.scale
            w.timeline.press(None,SimpleNamespace(button=1,x=x,y=28))
            self.assertFalse(w.canvas.playing)
            w.timeline.motion(None,SimpleNamespace(x=220,y=y))
            self.pump(lambda:not w.canvas.live and abs(w.canvas.position()-1.2)<.04)
            w.timeline.release(None,SimpleNamespace(x=220,y=y))
            self.pump(lambda:w.canvas.playing and w.canvas.position()>1.3)
            self.assertFalse(list(w.temp.glob('*.mp4')))
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_preview_zoom_pan_fit_and_layer_coordinates_leave_project_unchanged(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'zoom-preview-recovery')
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/'landscape.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and bool(w.thumbs) and w.canvas.canvas_rect is not None)
            before=copy.deepcopy(w.project);history=len(w.history)
            fit=w.canvas.canvas_rect
            w.preview_zoom_in.clicked()
            self.pump(lambda:w.canvas.canvas_rect[2]>fit[2]*1.2)
            self.assertEqual(w.preview_zoom_combo.get_active_id(),'1.25')
            w.preview_zoom_combo.set_active_id('2')
            self.pump(lambda:abs(w.canvas.canvas_rect[2]-2*fit[2])<.1)
            w.canvas._pan_press(None,SimpleNamespace(button=1,state=Gdk.ModifierType.SHIFT_MASK,x=200,y=100))
            self.assertTrue(w.canvas._pan_motion(None,SimpleNamespace(x=260,y=140)))
            self.assertNotEqual(w.canvas.pan_y,0)
            w.canvas._pan_release(None,SimpleNamespace(button=1))
            w.canvas._scroll_view(None,SimpleNamespace(state=Gdk.ModifierType.CONTROL_MASK,direction=Gdk.ScrollDirection.DOWN))
            self.assertEqual(w.canvas.preview_zoom,1.5)
            self.assertEqual(w.project,before);self.assertEqual(len(w.history),history)
            w.preview_zoom_combo.set_active_id('1')
            self.pump(lambda:abs(w.canvas.canvas_rect[2]-fit[2])<.1)
            self.assertEqual((w.canvas.pan_x,w.canvas.pan_y),(0,0))
            # Direct overlay manipulation uses the zoomed rectangle, so a
            # tenth of its width/height still moves by a tenth of the canvas.
            w.layer_tools.add_text();w.apply_fields()
            w.preview_zoom_combo.set_active_id('2')
            self.pump(lambda:not w.scrub_timer and w.canvas.canvas_rect[2]>fit[2]*1.9)
            x,y,width,height=w.canvas.canvas_rect
            w.layer_tools.canvas_press(SimpleNamespace(button=1,x=x+width*.5,y=y+height*.5))
            w.layer_tools.canvas_motion(SimpleNamespace(x=x+width*.6,y=y+height*.6))
            w.layer_tools.canvas_release(SimpleNamespace(button=1))
            self.assertAlmostEqual(w.project['layers'][0]['x'],.6,places=2)
            self.assertAlmostEqual(w.project['layers'][0]['y'],.6,places=2)
            self.assertEqual(w.canvas.preview_zoom,2)
            w.start_project(1080,1920)
            self.assertEqual(w.preview_zoom_combo.get_active_id(),'1')
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_import_edit_preview_save_and_reopen(self):
        w = ClipCanvasWindow(recovery_dir=self.fixtures.root/'recovery')
        errors = []
        w.error = errors.append
        # Automated playback is muted without changing exported audio.
        w.canvas._pipeline.set_property('volume', 0)
        try:
            w.start_project(1080, 1920)
            sources = [str(self.fixtures.root / n) for n in ('landscape.mp4', 'portrait.mp4', 'music.wav')]
            w.import_paths(sources,add_to_timeline=True)
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
            w.render_preview()
            self.pump(lambda: not w.busy and w.preview_key is not None and w.canvas.frame is not None)
            self.assertFalse(errors)
            preview = w.temp / (w.preview_key + '.mp4')
            info = engine.probe(preview)
            self.assertEqual((info['width'], info['height']), (1080, 1920))
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
            self.pump(lambda:not w._scrub_settling and w.canvas.frame is not None)
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
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')],add_to_timeline=True)
            self.pump(lambda: not w.busy and len(w.thumbs)==2 and len(w.waves)==1)
            w.select('clips',0)
            w.fields['speed'][0].set_value(2)
            w.rotation.set_active_id('90');w.flip_x.set_active(True);w.apply_fields()
            self.assertEqual(model.clip_duration(w.project['clips'][0]),.75)
            self.assertEqual(w.project['clips'][0]['rotation'],90)
            w.seek(.3)
            self.pump(lambda: not w.scrub_timer and w.canvas.live and w.canvas.frame is not None)
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

    def test_quality_picker_refreshes_still_live_and_export_defaults(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'quality-ui-recovery');w.error=lambda message:self.fail(message)
        try:
            w.start_project(960,540);w.import_paths([str(self.fixtures.root/'landscape.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and not w._scrub_settling and w.canvas.frame is not None)
            self.assertEqual(w.preview_quality.get_active_id(),'0')
            self.assertEqual((w.canvas.frame.get_width(),w.canvas.frame.get_height()),(960,540))
            before=copy.deepcopy(w.project);history=len(w.history)
            w.preview_quality.set_active_id('640')
            self.pump(lambda:not w._scrub_settling and w.canvas.frame.get_width()==640)
            w.preview_quality.set_active_id('1280')
            self.pump(lambda:not w._scrub_settling and w.canvas.frame.get_width()==960)
            w.toggle_play();self.pump(lambda:w.canvas.live and w.canvas.position()>.1)
            w.preview_quality.set_active_id('0')
            self.pump(lambda:w.canvas.playing and w.canvas._live.preview_edge==0)
            self.assertEqual(w.project,before);self.assertEqual(len(w.history),history)
            seen=[]
            def cancel_export():
                dialog=next(d for d in Gtk.Window.list_toplevels() if isinstance(d,Gtk.Dialog) and d.get_title()=='Export your video')
                seen.append(w.export_quality_combo.get_active_id());w.export_quality_combo.set_active_id('lossless');seen.append(w.export_quality_combo.get_active_text());dialog.response(Gtk.ResponseType.CANCEL);return False
            GLib.timeout_add(50,cancel_export);w.export()
            self.assertEqual(seen[0],'master');self.assertIn('MKV',seen[1])
            w.canvas.pause();w.begin_scrub();w.end_scrub(.3);w.seek(.5)
            self.pump(lambda:not w.scrub_timer and w.canvas.live and w.canvas.frame is not None)
            self.assertFalse(w._scrub_settling)
            self.assertAlmostEqual(w.canvas.position(),.5,delta=.1)
        finally:w.cleanup();w.destroy()

    def test_insert_edit_split_reorder_cards_with_visible_drag_and_undo(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'cards-ui-recovery');w.error=lambda message:self.fail(message)
        try:
            w.start_project(320,180);w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2)
            w.select('clips',0,seek=False)
            def insert():
                d=next(d for d in Gtk.Window.list_toplevels() if isinstance(d,Gtk.Dialog) and d.get_title()=='Insert color card')
                color=Gdk.RGBA();color.parse('#cc4422');w.new_card_color.set_rgba(color);w.new_card_duration.set_value(2.5);d.response(Gtk.ResponseType.OK);return False
            GLib.timeout_add(50,insert);w.card_button.clicked()
            self.pump(lambda:not w._scrub_settling and w.canvas.composited)
            self.assertTrue(model.is_card(w.project['clips'][1]));self.assertEqual(w.project['clips'][1]['color'],'#cc4422')
            self.assertAlmostEqual(model.clip_duration(w.project['clips'][1]),2.5)
            self.assertFalse(w.sound_button.get_sensitive())
            names=[i.get_label() for i in w.context_menu('clips',1).get_children() if isinstance(i,Gtk.MenuItem)]
            self.assertIn('Insert color card before…',names);self.assertNotIn('Extract audio',names);w.active_context_menu.popdown()
            w.open_properties();color=Gdk.RGBA();color.parse('#2244cc');w.card_color.set_rgba(color);w.card_duration.set_value(4);w.apply_fields()
            self.assertEqual(w.project['clips'][1]['color'],'#2244cc');self.assertEqual(model.clip_duration(w.project['clips'][1]),4)
            before=copy.deepcopy(w.project);w.zoom_control.set_value(100)
            self.pump(lambda:len(w.timeline.hit)>=3)
            self.pump(lambda:not w._scrub_settling)
            hit=next(h for h in w.timeline.hit if h[:2]==('clips',1));_,_,x,y,width,height=hit
            w.timeline.press(w.timeline,SimpleNamespace(button=1,x=x+width/2,y=y+height/3))
            self.assertEqual(w.timeline.drag['mode'],'edit')
            w.timeline.motion(w.timeline,SimpleNamespace(x=110,y=y+height/3))
            self.assertTrue(w.timeline.drag['active']);self.assertEqual(w.timeline.drag['target'],0)
            self.assertEqual(w.project,before)
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,w.timeline.get_allocated_width(),w.timeline.get_allocated_height())
            w.timeline.draw(w.timeline,cairo.Context(surface))
            self.assertEqual(w.project,before)
            history=len(w.history);w.timeline.release(w.timeline,SimpleNamespace(button=1,x=110,y=y+height/3))
            self.assertEqual(w.selected,('clips',0));self.assertTrue(model.is_card(w.project['clips'][0]));self.assertEqual(len(w.history),history+1)
            w.undo();self.assertEqual(w.project,before)
            w.select('clips',1,seek=False);w.playhead=model.timeline(w.project)[1]['start']+2;w.split_selected()
            self.assertEqual([model.clip_duration(c) for c in w.project['clips'][1:3]],[2,2])
            w.project_path=str(self.fixtures.root/'card-ui.clipcanvas');self.assertTrue(w.save())
            saved=copy.deepcopy(w.project);w.open_project(w.project_path);self.assertEqual(w.project,saved)
            # A held drag at the viewport edge scrolls without editing the model.
            w.project['clips']=[model.new_card(color,10) for color in ('#aa2222','#22aa22','#2222aa')]
            w.selected=('clips',0);w.changed();w.zoom_control.set_value(100)
            self.pump(lambda:w.timeline_scroll.get_hadjustment().get_upper()>w.timeline_scroll.get_hadjustment().get_page_size())
            self.pump(lambda:not w._scrub_settling)
            adjust=w.timeline_scroll.get_hadjustment();adjust.set_value(0);self.pump(lambda:len(w.timeline.hit)==3)
            _,_,x,y,width,height=next(h for h in w.timeline.hit if h[:2]==('clips',0))
            w.timeline.press(None,SimpleNamespace(button=1,x=x+width/3,y=y+20))
            w.timeline.motion(None,SimpleNamespace(x=adjust.get_page_size()-8,y=y+20))
            before=copy.deepcopy(w.project);self.pump(lambda:adjust.get_value()>20)
            self.assertEqual(w.project,before)
            w.timeline.release(None,SimpleNamespace(x=w.timeline.drag['pointer_x'],y=y+20))
            self.assertEqual(w.timeline.drag_scroll_timer,0)
        finally:w.cleanup();w.destroy()

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
                            for n in ('portrait.mp4', 'landscape.mp4')],add_to_timeline=True)
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
            w.start_project(320,180);w.import_paths([str(self.fixtures.root/'landscape.mp4')],add_to_timeline=True)
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
            self.pump(lambda:not w.scrub_timer and w.canvas.live and w.canvas.frame is not None)
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
            w.import_paths([str(logo)],add_to_timeline=True)
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
            w.render_preview();self.pump(lambda:not w.busy and w.preview_key is not None)
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
            w.start_project(320,180);w.import_paths([str(source)],add_to_timeline=True)
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
            w.start_project(320,180);w.import_paths([str(self.fixtures.root/'landscape.mp4')],add_to_timeline=True)
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
            w.import_paths([str(logo)],add_to_timeline=True);self.pump(lambda:not w.busy and str(logo) in w.layer_tools.assets)
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
            w.render_preview();self.pump(lambda:not w.busy and w.preview_key is not None)
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
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and bool(w.waves))
            w.project['clips'][0].update(transition='fade',transition_duration=.2)
            w.layer_tools.add_text();w.layer_tools.font.set_font_name('Sans 24');w.apply_fields()
            w.project['captions']=[model.new_caption('Editable words',0,.7),model.new_caption('Second',.8,1.2)]
            w.changed();w.render_preview();self.pump(lambda:not w.busy and w.preview_key is not None)
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
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','music.wav')],add_to_timeline=True)
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
            w.render_preview();self.pump(lambda:not w.busy and w.preview_key is not None)
            self.assertTrue(w.canvas.composited);self.assertFalse(errors)
        finally:
            if getattr(w,'voice_dialog',None):w.voice_dialog.destroy()
            if getattr(w,'active_context_menu',None):w.active_context_menu.popdown()
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)


    def test_library_click_group_selection_keyboard_and_timeline_wheel(self):
        from clipcanvas import workflow
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'new-workflow-recovery')
        errors=[];w.error=errors.append
        clipboard=SimpleNamespace(text=None)
        clipboard.set_text=lambda text,length:setattr(clipboard,'text',text)
        clipboard.wait_for_text=lambda:clipboard.text
        def key(name,ctrl=False,shift=False):
            state=(Gdk.ModifierType.CONTROL_MASK if ctrl else 0)|(Gdk.ModifierType.SHIFT_MASK if shift else 0)
            return w.keypress(w,SimpleNamespace(keyval=Gdk.keyval_from_name(name),state=state))
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')])
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and len(w.waves)==1)
            self.assertEqual((len(w.project['clips']),len(w.project['audio'])),(0,0))
            self.assertEqual(len(w.project['assets']),3)
            # Click the actual library buttons to create independent copies.
            for row in w.assets.get_children()[:3]:row.get_child().clicked()
            self.assertEqual((len(w.project['clips']),len(w.project['audio'])),(2,1))
            self.assertEqual(len(w.project['assets']),3)
            w.cancel_scrub();w.timeline.grab_focus()
            w.select('audio',0,seek=False)
            menu=w.context_menu('clips',0,SimpleNamespace(state=Gdk.ModifierType.SHIFT_MASK))
            menu.popdown()
            self.assertEqual({kind for kind,_,_ in w.selection_items()},{'clips'})
            self.assertEqual(len(w.selection_items()),2)
            original=copy.deepcopy(w.project)
            with mock.patch.object(Gtk.Clipboard,'get',return_value=clipboard):
                self.assertTrue(key('c',ctrl=True))
                self.assertTrue(clipboard.text.startswith(workflow.CLIPBOARD_PREFIX))
                w.playhead=model.project_duration(w.project)
                self.assertTrue(key('v',ctrl=True))
                self.assertEqual(len(w.project['clips']),4)
                self.assertEqual(len(w.selection_items()),2)
                self.assertTrue(key('Delete'))
                self.assertEqual(w.project,original)
                w.undo();self.assertEqual(len(w.project['clips']),4)
                w.redo();self.assertEqual(w.project,original)
                # Ctrl+A selects every timeline kind; Shift+right-click audio is scoped.
                key('a',ctrl=True);self.assertEqual(len(w.selection_items()),3)
                menu=w.context_menu('audio',0,SimpleNamespace(state=Gdk.ModifierType.SHIFT_MASK));menu.popdown()
                self.assertEqual([kind for kind,_,_ in w.selection_items()],['audio'])
                # Entry shortcuts must remain text operations.
                w.name.grab_focus();before=copy.deepcopy(w.project)
                self.assertFalse(key('Delete'));self.assertFalse(key('c',ctrl=True));self.assertFalse(key('v',ctrl=True))
                self.assertEqual(w.project,before)
            w.timeline.grab_focus();w.select('clips',0,seek=False)
            w.select_modified('clips',1,Gdk.ModifierType.SHIFT_MASK);self.assertEqual(len(w.selection_items()),2)
            w.select_modified('clips',1,Gdk.ModifierType.CONTROL_MASK);self.assertEqual(len(w.selection_items()),1)
            scale=w.timeline.scale;preview_zoom=w.canvas.preview_zoom
            self.assertTrue(w.timeline.scroll(None,SimpleNamespace(state=Gdk.ModifierType.CONTROL_MASK,direction=Gdk.ScrollDirection.UP,x=200)))
            self.assertGreater(w.timeline.scale,scale);self.assertEqual(w.canvas.preview_zoom,preview_zoom)
            self.assertGreaterEqual(w.play_button.get_size_request()[1],52)
            self.assertEqual(w.play_button.get_label(),'Play')
            # Store unused imported assets too, then restore an editable project.
            w.project_path=str(self.fixtures.root/'new-workflow.clipcanvas');self.assertTrue(w.save())
            saved=copy.deepcopy(w.project);w.open_project(w.project_path)
            self.assertEqual(w.project,saved);self.assertFalse(w.selection)
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_native_chooser_and_audio_defaults_persist_only_after_accept_apply(self):
        from clipcanvas.audio_effects_ui import VoiceEffectsDialog,edit_voice_effects
        prefs=self.fixtures.root/'remembered-settings';w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'remembered-recovery',preferences_dir=prefs)
        chooser=None;dialog=None
        try:
            folder=self.fixtures.root/'chosen-media';folder.mkdir(exist_ok=True)
            chooser=file_chooser(w,'Import clips',Gtk.FileChooserAction.OPEN,'Import')
            chooser.set_current_folder(str(folder));self.pump(lambda:chooser.get_current_folder()==str(folder))
            chooser.emit('response',Gtk.ResponseType.CANCEL)
            self.assertIsNone(w.preferences.get('folder_media'))
            chooser.emit('response',Gtk.ResponseType.ACCEPT);chooser.destroy();chooser=None
            from clipcanvas.preferences import Preferences
            self.assertEqual(Preferences(prefs).folder('media'),str(folder))
            chooser=file_chooser(w,'Add soundtracks',Gtk.FileChooserAction.OPEN,'Import')
            self.pump(lambda:chooser.get_current_folder()==str(folder));chooser.destroy();chooser=None
            w.start_project(320,180);w.project['clips']=[model.new_card('#222222',3)]
            w.project['audio']=[model.new_audio(self.fixtures.root/'music.wav')];w.select('audio',0,seek=False)
            before=w.preferences.get('last_voice_effects')
            with mock.patch.object(VoiceEffectsDialog,'run',return_value=Gtk.ResponseType.CANCEL):edit_voice_effects(w)
            self.assertEqual(w.preferences.get('last_voice_effects'),before)
            def apply_once(d):
                d.controls['echo_enabled'][0].set_active(True)
                d.controls['echo_delay_ms'][0].set_value(730)
                d.controls['gain_db'][0].set_value(4)
                return Gtk.ResponseType.OK
            with mock.patch.object(VoiceEffectsDialog,'run',apply_once):edit_voice_effects(w)
            self.assertEqual(Preferences(prefs).get('last_voice_effects')['echo_delay_ms'],730)
            fresh=model.new_audio(self.fixtures.root/'music.wav')
            dialog=VoiceEffectsDialog(w,fresh,2);self.assertEqual(dialog.read()['echo_delay_ms'],730);dialog.destroy();dialog=None
            fresh['audio_effects']={'echo_delay_ms':360,'gain_db':-2}
            dialog=VoiceEffectsDialog(w,fresh,2);self.assertEqual(dialog.read()['echo_delay_ms'],360);self.assertEqual(dialog.read()['gain_db'],-2);dialog.destroy();dialog=None
            w.cancel_scrub();w.select('audio',0,seek=False)
            w.fields['volume'][0].set_value(55);w.fields['fade_in'][0].set_value(.25);w.apply_fields()
            self.assertEqual(Preferences(prefs).get('last_audio_settings')['volume'],.55)
            w.select('audio',0,seek=False);w.fields['volume'][0].set_value(100);w.use_last_audio_settings()
            self.assertAlmostEqual(w.fields['volume'][0].get_value(),55)
            self.assertEqual(w.fields['fade_in'][0].get_value(),.25)
        finally:
            if dialog:dialog.destroy()
            if chooser:chooser.destroy()
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_playhead_position_is_notified_with_each_presented_live_frame(self):
        from gi.repository import Gst
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'frame-clock-recovery')
        try:
            w.start_project(320,180)
            canvas=w.canvas;canvas._live=SimpleNamespace(start=5,total=10,last_position=5,stop=lambda:None)
            canvas._loaded=True
            picture=Gst.Sample.new(Gst.Buffer.new_wrapped(bytes(4*2*3)),Gst.Caps.from_string('video/x-raw,format=RGB,width=4,height=2'),None,None)
            picture.get_buffer().pts=int(.625*Gst.SECOND)
            canvas._receive_sample(picture)
            self.pump(lambda:canvas.frame is not None)
            self.assertAlmostEqual(w.playhead,5.625);self.assertAlmostEqual(canvas.position(),5.625)
            # No timer/query clock may advance the line past this visible frame.
            canvas._tick();self.assertAlmostEqual(w.playhead,5.625)
            canvas._live=None
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_shift_right_click_bulk_volume_mute_unmute_undo_and_project_save(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'group-volume-recovery')
        errors=[];w.error=errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and bool(w.waves))
            w.cancel_scrub();w.project['clips'][0].update({'volume':.35,'in':.2,'out':1.3,'speed':1.5,'fade_out':.15})
            w.project['clips'][1].update(volume=.8,video_fade_in=.1,transition='fade')
            w.project['audio'][0].update(volume=.55,fade_in=.2,audio_effects={'echo_enabled':True})
            w.project['clips'].append(model.new_card('#123456',.5));w.changed();before=copy.deepcopy(w.project)
            # Start from a non-first clip; the panel must describe the clicked clip.
            menu=w.context_menu('clips',1,SimpleNamespace(state=Gdk.ModifierType.SHIFT_MASK))
            actions={item.get_label():item for item in menu.get_children() if isinstance(item,Gtk.MenuItem)}
            self.assertIn('Mute selected videos (2)',actions)
            self.assertIn('Volume for 2 selected items…',actions)
            menu.popdown();actions['Volume for 2 selected items…'].activate()
            self.assertEqual(w.properties_stack.get_visible_child_name(),'Sound')
            self.assertEqual(w.apply_button.get_label(),'Apply volume to 2 items')
            self.assertAlmostEqual(w.fields['volume'][0].get_value(),80)
            self.assertNotIn('fade_in',w.fields)
            history=len(w.history);w.fields['volume'][0].set_value(0);w.apply_button.clicked()
            expected=copy.deepcopy(before)
            for item in expected['clips']:
                if not model.is_card(item):item['volume']=0
            self.assertEqual(w.project,expected);self.assertEqual(len(w.history),history+1)
            self.assertEqual(len(w.selection_items()),3)
            w.undo();self.assertEqual(w.project,before);w.redo();self.assertEqual(w.project,expected)
            menu=w.context_menu('clips',0,SimpleNamespace(state=Gdk.ModifierType.SHIFT_MASK))
            actions={item.get_label():item for item in menu.get_children() if isinstance(item,Gtk.MenuItem)}
            menu.popdown();actions['Unmute selected videos (2)'].activate();self.assertEqual(w.project,before)
            # Direct menu mute gives the same group operation.
            menu=w.context_menu('clips',0,SimpleNamespace(state=Gdk.ModifierType.SHIFT_MASK))
            actions={item.get_label():item for item in menu.get_children() if isinstance(item,Gtk.MenuItem)}
            menu.popdown();actions['Mute selected videos (2)'].activate();self.assertEqual(w.project,expected)
            history=len(w.history);w.apply_selected_volume(0);self.assertEqual(len(w.history),history)
            w.project_path=str(self.fixtures.root/'group-muted.clipcanvas');self.assertTrue(w.save())
            w.open_project(w.project_path);self.assertEqual(w.project,expected)
            # Arbitrary Ctrl-selection can span video/audio, excluding other media.
            w.select('clips',1,seek=False);w.select_modified('audio',0,Gdk.ModifierType.CONTROL_MASK)
            w.open_properties('Sound');w.fields['volume'][0].set_value(25);w.apply_fields()
            mixed=copy.deepcopy(expected);mixed['clips'][1]['volume']=.25;mixed['audio'][0]['volume']=.25
            self.assertEqual(w.project,mixed)
            w.select('audio',0,seek=False);w.open_properties('Sound')
            self.assertEqual(w.apply_button.get_label(),'Apply changes');self.assertIn('fade_in',w.fields)
            w.project['layers']=[model.new_text('Caption title',0,.5)]
            w.select('clips',0,seek=False);w.select_modified('audio',0,Gdk.ModifierType.CONTROL_MASK)
            w.select_modified('layers',0,Gdk.ModifierType.CONTROL_MASK);w.open_properties('Text')
            self.assertEqual(w.apply_button.get_label(),'Apply changes')
            self.assertFalse(errors)
        finally:
            if getattr(w,'active_context_menu',None):w.active_context_menu.popdown()
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

if __name__ == '__main__':
    unittest.main()
