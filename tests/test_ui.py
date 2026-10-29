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
    def test_container_timestamp_offset_and_pending_eos_frame_keep_media_time(self):
        from gi.repository import Gst
        from clipcanvas.player import VideoCanvas
        canvas=VideoCanvas();positions=[];canvas.on_position=lambda at,total:positions.append(at)
        try:
            canvas._last_duration=2
            segment=Gst.Segment();segment.init(Gst.Format.TIME)
            segment.start=2*Gst.SECOND;segment.time=0
            buffer=Gst.Buffer.new_wrapped(bytes(24));buffer.pts=2500000000
            sample=Gst.Sample.new(buffer,Gst.Caps.from_string('video/x-raw,format=RGB,width=4,height=2'),segment,None)
            canvas._receive_sample(sample);canvas._deliver_frame()
            self.assertAlmostEqual(canvas.position(),.5)
            buffer=Gst.Buffer.new_wrapped(bytes([200])*24);buffer.pts=3900000000
            canvas._receive_sample(Gst.Sample.new(buffer,sample.get_caps(),segment,None))
            canvas._message(None,Gst.Message.new_eos(canvas._pipeline))
            canvas._deliver_frame()
            self.assertEqual(canvas.position(),2);self.assertEqual(positions[-1],2)
            self.assertEqual(bytes(canvas.frame.get_pixels())[0],200)
            self.assertTrue(canvas._freeze_frame)
        finally:canvas.destroy()

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
                self.pump(lambda:not w.scrub_timer and not w._scrub_settling and not w.canvas.live)
                first.thread.join(timeout=5);self.assertFalse(first.thread.is_alive())
                self.assertFalse(w.canvas.playing)
                self.assertAlmostEqual(w.canvas.position(),1.8,places=2)
                w.toggle_play();self.pump(lambda:w.canvas.position()>2)
                second=w.canvas._live
                # Delayed asset completion must never replace the live stream.
                w.asset_ready(w.project['clips'][0]['path'],'clips',next(str(p) for p in w.temp.glob('*.jpg')))
                self.assertIs(w.canvas._live,second)
                self.pump(lambda:not w.canvas.playing)
                self.assertAlmostEqual(w.playhead,model.project_duration(w.project),places=2)
                w.toggle_play();self.pump(lambda:.05<w.canvas.position()<.5)
                w.select('clips',1);self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
                self.assertFalse(w.canvas.playing)
                self.assertAlmostEqual(w.canvas.position(),model.timeline(w.project)[1]['start'],places=2)
                last=w.canvas._live or second
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

    def test_waveform_pulses_follow_trim_speed_zoom_and_split(self):
        import array
        import wave
        path=self.fixtures.root/'waveform-pulses.wav';rate=48000
        data=array.array('h',[0])*(rate*3)
        for at in (.737,2.917):
            for n in range(round(at*rate),round((at+.002)*rate)):data[n]=26000
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,rate,0,'NONE','not compressed'));stream.writeframes(data.tobytes())
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'waveform-paint-recovery')
        try:
            w.start_project(320,180);w.project['clips']=[model.new_card(duration=6)]
            track=model.new_audio(path);track.update(start=.2)
            w.project['audio']=[track];w.changed();w.load_asset(track,'audio')
            self.pump(lambda:str(path) in w.waves and len(w.timeline.hit)==2)
            for scale,speed,trim in ((28,1,0),(140,1,0),(140,.5,.4)):
                with self.subTest(scale=scale,speed=speed,trim=trim):
                    track.update(speed=speed,**{'in':trim});w.zoom(scale)
                    def check():
                        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1400,500)
                        w.timeline.draw(w.timeline,cairo.Context(surface))
                        picture=Gdk.pixbuf_get_from_surface(surface,0,0,1400,500)
                        pixels=picture.get_pixels();stride=picture.get_rowstride();channels=picture.get_n_channels()
                        def pixel(x,y):
                            offset=int(y)*stride+int(x)*channels;return tuple(pixels[offset:offset+3])
                        for at in (.737,2.917):
                            index=next(i for i,a in enumerate(w.project['audio']) if a['in']<=at<a['out'])
                            item=w.project['audio'][index]
                            _,_,x,y,_,_=next(h for h in w.timeline.hit if h[:2]==('audio',index))
                            column=3*int(((at-item['in'])/item['speed']*scale)//3)
                            quiet=pixel(x+12,y+24);pulse=pixel(x+column+1,y+24)
                            self.assertGreater(pulse[0],quiet[0]+20,'The drawn waveform must show the real short pulse at its source time')
                    check()
                    if scale==140 and speed==.5:
                        w.project=model.split_item(w.project,'audio',0,2)
                        w.timeline.refresh();check()
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_video_and_audio_body_clicks_seek_to_pointer_time(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'clip-click-recovery')
        w.canvas._pipeline.set_property('volume',0)
        errors=[];w.canvas.on_error=errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4','music.wav')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and bool(w.waves) and not w._scrub_settling)
            w.project['clips'][1].update(**{'in':.2,'out':1.8,'speed':2})
            w.project['audio'][0].update(start=.3,**{'in':.5,'out':3.5,'speed':2})
            w.changed();w.zoom(100)
            self.pump(lambda:len(w.timeline.hit)==3 and not w._scrub_settling)
            before=copy.deepcopy(w.project);history=len(w.history)

            def click(kind,index,at):
                _,_,_,y,_,height=next(h for h in w.timeline.hit if h[:2]==(kind,index))
                x=100+at*w.timeline.scale;y+=height/2
                w.timeline.press(None,SimpleNamespace(button=1,x=x,y=y))
                # Selection must not replace pointer time with a clip start.
                self.assertAlmostEqual(w.playhead,at)
                self.assertEqual(w.selected,(kind,index))
                w.on_position(0,model.project_duration(w.project))
                self.assertAlmostEqual(w.playhead,at,'A stale playback tick must not pull the line back')
                w.timeline.release(None,SimpleNamespace(x=x,y=y))
                self.pump(lambda:not w.scrub_timer and not w._scrub_settling and abs(w.canvas.position()-at)<1/w.project['fps']+.001)
                self.assertAlmostEqual(w.playhead,at,delta=1/w.project['fps']+.001)
                self.assertIsNotNone(w.canvas.frame)
                self.assertFalse(w.canvas.playing)

            for kind,index,at in [('clips',0,.8),('clips',1,1.9),('audio',0,1.1),('clips',1,2.1),('audio',0,.7)]:
                with self.subTest(kind=kind,at=at):click(kind,index,at)
            self.assertEqual(w.project,before);self.assertEqual(len(w.history),history)

            # Modifier selection and trim handles remain edit controls.
            for state in (Gdk.ModifierType.SHIFT_MASK,Gdk.ModifierType.CONTROL_MASK):
                _,_,x,y,width,height=next(h for h in w.timeline.hit if h[:2]==('clips',1))
                at=w.playhead
                w.timeline.press(None,SimpleNamespace(button=1,x=x+width/2,y=y+height/2,state=state))
                w.timeline.release(None,SimpleNamespace(x=x+width/2,y=y+height/2))
                self.assertAlmostEqual(w.playhead,at)
            _,_,x,y,width,height=next(h for h in w.timeline.hit if h[:2]==('clips',1))
            at=w.playhead
            w.timeline.press(None,SimpleNamespace(button=1,x=x+width-2,y=y+height/2))
            self.assertEqual(w.timeline.drag['edge'],'right')
            w.timeline.release(None,SimpleNamespace(x=x+width-2,y=y+height/2))
            self.assertAlmostEqual(w.playhead,at)
            self.assertEqual(w.project,before);self.assertEqual(len(w.history),history)

            # Seeking from a playing clip must replace the old live position
            # and keep playback running from the requested point.
            w.seek(.1);self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
            w.toggle_play();self.pump(lambda:w.canvas.playing and w.canvas.position()>.2)
            for kind,index,at in [('clips',1,1.9),('audio',0,.9)]:
                previous=w.canvas._live
                _,_,_,y,_,height=next(h for h in w.timeline.hit if h[:2]==(kind,index))
                x=100+at*w.timeline.scale;y+=height/2
                w.timeline.press(None,SimpleNamespace(button=1,x=x,y=y))
                self.assertAlmostEqual(w.playhead,at);self.assertTrue(w.canvas.playing)
                w.timeline.release(None,SimpleNamespace(x=x,y=y))
                self.pump(lambda:w.canvas._live is not previous and w.canvas.playing and w.canvas.position()>at)
            w.canvas.pause();w.update_play_button(False)

            # The prepared-preview seek path uses the same pointer position.
            w.render_preview();self.pump(lambda:not w.busy and w.preview_key is not None)
            w.canvas.pause();w.update_play_button(False)
            click('clips',1,1.9);click('audio',0,1.1)
            self.assertEqual(w.project,before);self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_slow_drag_frames_are_shown_while_new_pointer_requests_wait(self):
        import threading
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'slow-drag-recovery')
        entered=threading.Event();release=threading.Event();second=threading.Event();finish=threading.Event()
        calls=[]
        def frame(project,at,cancel,preview_edge):
            calls.append(at)
            if len(calls)==1:entered.set();release.wait(5)
            elif len(calls)==2:second.set();finish.wait(5)
            return {'pixels':bytes([int(at*100),0,0])*16*9,'width':16,'height':9,'position':at}
        try:
            w.start_project(160,90);w.project['clips']=[model.new_card(duration=3)]
            w.refresh();w.cancel_scrub()
            with mock.patch.object(w.scrub_session,'frame',side_effect=frame):
                w.begin_scrub();w.seek(.2);self.pump(entered.is_set)
                w.seek(.8);release.set();self.pump(second.is_set)
                self.assertAlmostEqual(w.canvas.position(),.2,'Keep showing completed frames during movement')
                self.assertAlmostEqual(w.playhead,.8,'The playhead must still follow the pointer')
                w.end_scrub(1.1);finish.set()
                self.pump(lambda:not w._drag_frame_busy and not w._scrub_settling)
                self.assertAlmostEqual(w.canvas.position(),1.1,'Release must settle at the newest target')
                self.assertAlmostEqual(w.playhead,1.1)
        finally:
            release.set();finish.set();w.cleanup();w.destroy()

    def test_drag_playhead_line_updates_frames_before_release_and_resumes_playback(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'drag-preview-recovery')
        w.canvas._pipeline.set_property('volume',0)
        errors=[];w.canvas.on_error=errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/n) for n in ('landscape.mp4','portrait.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and len(w.thumbs)==2 and bool(w.timeline.hit))
            w.zoom(100);w.seek(.3)
            self.pump(lambda:w.canvas.composited and not w._scrub_settling and abs(w.canvas.position()-.3)<.01)
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

    def test_zoomed_preview_wheel_and_trackpad_scroll_without_editing_or_seeking(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'scroll-preview-recovery')
        try:
            for width,height in ((1080,1920),(1920,1080)):
                with self.subTest(canvas=(width,height)):
                    w.start_project(width,height)
                    w.project['clips']=[model.new_card('#16384b',2)]
                    w.changed()
                    self.pump(lambda:not w.scrub_timer and not w._scrub_settling and w.canvas.frame is not None)
                    canvas=w.canvas;before=copy.deepcopy(w.project);history=len(w.history)
                    position=canvas.position();playhead=w.playhead;frame=canvas.frame
                    def scroll(direction,state=0,dx=0,dy=0):
                        event=Gdk.Event.new(Gdk.EventType.SCROLL)
                        event.window=canvas.get_window();event.send_event=True
                        event.direction=direction;event.state=state
                        event.x=canvas.get_allocated_width()/2;event.y=canvas.get_allocated_height()/2
                        event.delta_x=dx;event.delta_y=dy
                        return canvas.emit('scroll-event',event)
                    self.assertFalse(scroll(Gdk.ScrollDirection.DOWN))
                    canvas.set_preview_zoom(4)
                    original=canvas._view_rect()
                    self.assertTrue(scroll(Gdk.ScrollDirection.DOWN))
                    self.assertLess(canvas.canvas_rect[1],original[1])
                    self.assertEqual(canvas.preview_zoom,4)
                    self.assertTrue(scroll(Gdk.ScrollDirection.UP))
                    self.assertAlmostEqual(canvas.pan_y,0)
                    self.assertTrue(scroll(Gdk.ScrollDirection.SMOOTH,dx=.4,dy=.7))
                    self.assertLess(canvas.pan_y,0)
                    # Scroll all the way to either end without overshooting
                    # the picture and exposing empty space inside the canvas.
                    scroll(Gdk.ScrollDirection.SMOOTH,dy=10000)
                    _,y,_,h=canvas.canvas_rect
                    self.assertLessEqual(y+h,canvas.get_allocated_height())
                    self.assertGreaterEqual(y+h,canvas.get_allocated_height()-28.1)
                    scroll(Gdk.ScrollDirection.SMOOTH,dy=-10000)
                    self.assertGreaterEqual(canvas.canvas_rect[1],0)
                    self.assertLessEqual(canvas.canvas_rect[1],28.1)
                    if width>height:
                        canvas.set_preview_zoom(4,reset_pan=True)
                        self.assertTrue(scroll(Gdk.ScrollDirection.DOWN,Gdk.ModifierType.SHIFT_MASK))
                        self.assertLess(canvas.pan_x,0);self.assertEqual(canvas.pan_y,0)
                        self.assertTrue(scroll(Gdk.ScrollDirection.RIGHT))
                    scroll(Gdk.ScrollDirection.DOWN,Gdk.ModifierType.CONTROL_MASK)
                    self.assertEqual(canvas.preview_zoom,3)
                    canvas.reset_view()
                    self.assertEqual((canvas.pan_x,canvas.pan_y),(0,0))
                    self.assertFalse(scroll(Gdk.ScrollDirection.DOWN))
                    self.assertEqual(w.project,before);self.assertEqual(len(w.history),history)
                    self.assertEqual(canvas.position(),position);self.assertEqual(w.playhead,playhead)
                    self.assertIs(canvas.frame,frame)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_image_import_independent_duration_motion_trim_undo_and_reopen(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'photo-ui-recovery')
        errors=[];w.error=errors.append;w.canvas.on_error=errors.append
        photo=self.fixtures.root/'scene.png'
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,320,180);cr=cairo.Context(surface)
        cr.set_source_rgb(.1,.3,.5);cr.paint();cr.set_source_rgb(1,0,0);cr.rectangle(140,70,40,40);cr.fill()
        surface.write_to_png(str(photo))
        try:
            w.start_project(320,180);w.import_paths([str(photo)],kind='image')
            self.pump(lambda:not w.busy and str(photo) in w.thumbs)
            self.assertFalse(w.project['clips']);self.assertFalse(w.project['layers'])
            self.assertEqual(w.project['assets'][0]['kind'],'image')
            w.assets.get_children()[0].add_button.clicked()
            self.assertEqual(w.selected,('clips',0))
            self.assertEqual(model.clip_duration(w.selected_item()),5)
            self.assertFalse(w.sound_button.get_sensitive());self.assertNotIn('Sound',w.property_pages)
            w.open_properties('Timing');w.fields['image_duration'][0].set_value(2.4)
            w.image_motion.set_active_id('zoom_in');w.fields['zoom_amount'][0].set_value(25)
            w.apply_fields()
            self.assertAlmostEqual(model.project_duration(w.project),2.4)
            self.assertEqual(w.project['clips'][0]['motion'],'zoom_in')
            self.assertEqual(w.project['clips'][0]['zoom_amount'],.25)
            w.assets.get_children()[0].add_button.clicked()
            self.assertEqual(model.clip_duration(w.project['clips'][1]),5)
            self.assertNotEqual(w.project['clips'][0]['id'],w.project['clips'][1]['id'])
            w.fields['image_duration'][0].set_value(1.3);w.image_motion.set_active_id('zoom_out');w.apply_fields()
            self.assertAlmostEqual(model.project_duration(w.project),3.7)
            w.select('clips',0,seek=False);w.transition.set_active_id('fade')
            w.fields['transition_duration'][0].set_value(.2);w.apply_fields()
            self.assertAlmostEqual(model.project_duration(w.project),3.5)
            menu=w.context_menu('clips',0);menu.popdown()
            titles=[action.get_label() for action in menu.get_children()]
            self.assertIn('Image motion…',titles);self.assertNotIn('Extract audio',titles)
            self.assertFalse(w.selected_sound_items())
            w.select('clips',1,seek=False);w.zoom(100);w.snap_check.set_active(False)
            self.pump(lambda:any(hit[:2]==('clips',1) for hit in w.timeline.hit))
            _,_,x,y,width,height=next(hit for hit in w.timeline.hit if hit[:2]==('clips',1))
            w.timeline.press(None,SimpleNamespace(button=1,x=x+width-2,y=y+height/2))
            w.timeline.release(None,SimpleNamespace(x=x+width-2+.5*w.timeline.scale))
            self.assertAlmostEqual(model.clip_duration(w.project['clips'][1]),1.8,delta=1/30)
            w.undo();self.assertAlmostEqual(model.clip_duration(w.project['clips'][1]),1.3)
            w.redo();self.assertAlmostEqual(model.clip_duration(w.project['clips'][1]),1.8,delta=1/30)
            w.select('clips',1,seek=False);w.seek(2.6)
            self.pump(lambda:not w.scrub_timer and not w._scrub_settling and w.canvas.frame is not None)
            self.assertFalse(w.preview_key)
            w.preview();self.pump(lambda:w.canvas.playing and w.canvas.position()>2.7)
            w.canvas.pause()
            w.project_path=str(self.fixtures.root/'image-scenes.clipcanvas');self.assertTrue(w.save())
            saved=copy.deepcopy(w.project);w.open_project(w.project_path)
            self.assertEqual(w.project,saved)
            w.select('clips',1,seek=False)
            self.assertAlmostEqual(w.fields['image_duration'][0].get_value(),model.clip_duration(saved['clips'][1]))
            self.assertEqual(w.image_motion.get_active_id(),'zoom_out')
            w.cancel_scrub();w.scrub_session.close()
            relocated=photo.with_name('relocated-scene.png');photo.rename(relocated)
            with mock.patch('clipcanvas.app.file_chooser') as chooser:
                chooser.return_value.run.return_value=Gtk.ResponseType.ACCEPT
                chooser.return_value.get_filename.return_value=str(relocated)
                w.relink_media()
            self.pump(lambda:not w.busy)
            expected=copy.deepcopy(saved)
            for item in model.media_items(expected):item['path']=str(relocated)
            self.assertEqual(w.project,expected)
            self.assertFalse(errors)
        finally:
            if getattr(w,'active_context_menu',None):w.active_context_menu.popdown()
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
            self.pump(lambda: not w.scrub_timer and not w._scrub_settling and w.canvas.frame is not None)
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
            self.pump(lambda:not w.scrub_timer and not w._scrub_settling and w.canvas.frame is not None)
            self.assertFalse(w._scrub_settling)
            self.assertAlmostEqual(w.canvas.position(),.5,delta=.1)
        finally:w.cleanup();w.destroy()

    def test_export_releases_preview_and_pending_scrub_then_can_replay(self):
        import threading
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'export-memory-recovery')
        w.canvas._pipeline.set_property('volume',0);errors=[];w.error=errors.append
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/'landscape.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and bool(w.thumbs) and not w._scrub_settling and not w._drag_frame_busy)
            w.preview();self.pump(lambda:w.canvas.live and w.canvas.frame is not None and w.canvas.position()>.1)
            w.canvas.pause();live=w.canvas._live;before=copy.deepcopy(w.project);frame=w.canvas.frame;at=w.playhead
            seen=[];execute=engine.render
            def render(project,path,**options):
                self.assertFalse(live.thread.is_alive(),'Paused preview FFmpeg must exit before export starts')
                self.assertFalse(w.canvas.live);self.assertEqual(w.scrub_timer,0)
                self.assertIs(w.canvas.frame,frame);self.assertEqual(w.playhead,at)
                self.assertEqual(project,before);self.assertEqual(options['quality'],'high')
                seen.append(path);return execute(project,path,**options)
            output=self.fixtures.root/'released-preview-export.mp4'
            with mock.patch.object(engine,'render',render):
                w.render_job(str(output),False,lambda path:w.set_status('Export completed'),quality='high')
                self.assertTrue(w.busy);self.pump(lambda:not w.busy)
            self.assertEqual(seen,[str(output)]);self.assertTrue(output.is_file());self.assertEqual(w.project,before)
            self.assertIsNone(w.preview_key);self.assertEqual(w.playhead,at)
            w.toggle_play();self.pump(lambda:w.canvas.live and w.canvas.playing and w.canvas.position()>at)
            w.canvas.pause();w.seek(.5);self.assertTrue(w.scrub_timer)
            # Starting export cancels a scheduled seek before it can spawn a
            # second preview decoder after export has already begun.
            completed=threading.Event();entered=threading.Event()
            def cancel_render(project,path,**options):
                self.assertEqual(w.scrub_timer,0);self.assertFalse(w.canvas.live);entered.set()
                completed.wait(timeout=5)
                raise engine.RenderCancelled('Cancelled')
            with mock.patch.object(engine,'render',cancel_render):
                w.render_job(str(output),False,lambda path:self.fail('Cancelled export must not finish'))
                self.pump(entered.is_set)
                deadline=time.monotonic()+.15;self.pump(lambda:time.monotonic()>=deadline)
                self.assertEqual(w.scrub_timer,0);self.assertFalse(w.canvas.live)
                completed.set();self.pump(lambda:not w.busy)
            self.assertEqual(w.project,before);self.assertFalse(errors)
            w.toggle_play();self.pump(lambda:w.canvas.live and w.canvas.playing)
            w.render_preview();self.pump(lambda:not w.busy and w.preview_key and w.canvas.frame is not None)
            # Export must clear the prepared-preview key as well, so Play can
            # recreate a valid player after it was stopped for export.
            with mock.patch.object(engine,'render',side_effect=engine.RenderCancelled('Cancelled')):
                w.render_job(str(output),False,lambda path:None)
                self.pump(lambda:not w.busy)
            self.assertIsNone(w.preview_key);w.toggle_play();self.pump(lambda:w.canvas.live and w.canvas.playing)
            self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

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

    def test_zoomed_timeline_pointer_edits_keep_the_scrolled_viewport(self):
        import wave
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'timeline-viewport-recovery')
        sound=self.fixtures.root/'timeline-viewport.wav'
        with wave.open(str(sound),'wb') as stream:
            stream.setparams((1,2,8000,0,'NONE','not compressed'))
            stream.writeframes(bytes(210*8000*2))
        try:
            w.start_project(320,180)
            w.project['clips']=[model.new_card('#224466',30) for _ in range(7)]
            w.project['audio']=[model.new_audio(sound)]
            w.project['layers']=[model.new_text(str(i),0,210) for i in range(5)]
            w.project['captions']=[model.new_caption('Late caption',175,190)]
            # Decoding is covered by playback tests; here use real GTK event,
            # focus, popup and adjustment handling on a long timeline.
            with mock.patch.object(w,'show_selected_still'),mock.patch.object(w,'seek') as seek:
                w.changed();w.zoom_control.set_value(100)
                self.pump(lambda:len(w.timeline.hit)==14 and w.timeline_scroll.get_hadjustment().get_upper()>20000)
                horizontal=w.timeline_scroll.get_hadjustment();vertical=w.timeline_scroll.get_vadjustment()
                pointer=w.get_display().get_default_seat().get_pointer()
                def settle():
                    deadline=time.monotonic()+.08;self.pump(lambda:time.monotonic()>=deadline)
                def position(kind,index,at=17500):
                    w.media_search.grab_focus();horizontal.set_value(at)
                    hit=next(h for h in w.timeline.hit if h[:2]==(kind,index))
                    vertical.set_value(max(0,hit[3]+hit[5]/2-vertical.get_page_size()/2))
                    settle();return hit
                def event(event_type,x,y,button=1,state=0):
                    e=Gdk.Event.new(event_type);e.window=w.timeline.get_window();e.send_event=True
                    e.time=Gdk.CURRENT_TIME;e.x=x;e.y=y;e.state=state;e.set_device(pointer)
                    origin=e.window.get_origin();e.x_root=origin[1]+x;e.y_root=origin[2]+y
                    if event_type!=Gdk.EventType.MOTION_NOTIFY:e.button=button
                    w.timeline.event(e)
                def stable(at,top):
                    settle();self.assertEqual(horizontal.get_value(),at);self.assertEqual(vertical.get_value(),top)

                before=copy.deepcopy(w.project);position('clips',5)
                at=horizontal.get_value();top=vertical.get_value()
                w.timeline.grab_focus();self.assertTrue(w.timeline.is_focus());stable(at,top)
                # Hover and a quick press/release must not reveal the hidden
                # beginning of the long clip after the event loop goes idle.
                event(Gdk.EventType.MOTION_NOTIFY,17800,60);stable(at,top)
                w.media_search.grab_focus()
                event(Gdk.EventType.BUTTON_PRESS,17800,60);event(Gdk.EventType.BUTTON_RELEASE,17800,60)
                seek.assert_called_once_with(177.0);seek.reset_mock()
                self.assertEqual(w.selected,('clips',5));stable(at,top)
                w.media_search.grab_focus()
                event(Gdk.EventType.BUTTON_PRESS,17800,60)
                event(Gdk.EventType.MOTION_NOTIFY,17820,60);stable(at,top)
                self.assertTrue(w.timeline.drag['active'])
                event(Gdk.EventType.BUTTON_RELEASE,17820,60);stable(at,top)
                for state in (Gdk.ModifierType.SHIFT_MASK,Gdk.ModifierType.CONTROL_MASK):
                    event(Gdk.EventType.BUTTON_PRESS,18400,60,state=state)
                    event(Gdk.EventType.BUTTON_RELEASE,18400,60,state=state);stable(at,top)
                # The preceding ordinary drag can seek at its grab position;
                # modifier clicks do not add another seek.
                self.assertEqual(seek.call_count,1);seek.reset_mock()
                # Right-click an unselected clip and return focus from its menu.
                event(Gdk.EventType.BUTTON_PRESS,18400,60,button=3)
                self.assertEqual(w.selected,('clips',6));stable(at,top)
                w.active_context_menu.popdown();w.timeline.grab_focus();stable(at,top)
                # Transition popovers use the clicked join without recentering.
                _,x,y,_=next(h for h in w.timeline.join_hits if h[0]==5)
                event(Gdk.EventType.MOTION_NOTIFY,x,y);stable(at,top)
                event(Gdk.EventType.BUTTON_PRESS,x,y)
                self.assertEqual(w.properties_stack.get_visible_child_name(),'Transition');stable(at,top)
                w.properties_popover.popdown();settle()
                self.assertEqual(w.project,before);self.assertFalse(w.history)

                for kind in ('layers','captions'):
                    _,_,_,y,_,height=position(kind,0);at=horizontal.get_value();top=vertical.get_value()
                    event(Gdk.EventType.BUTTON_PRESS,18000,y+height/2)
                    event(Gdk.EventType.BUTTON_RELEASE,18000,y+height/2)
                    self.assertEqual(w.selected,(kind,0));stable(at,top)
                # Moving a soundtrack whose start is far offscreen retains the
                # drop location rather than scrolling back to its first sample.
                _,_,_,y,_,height=position('audio',0);at=horizontal.get_value();top=vertical.get_value()
                w.snap_check.set_active(False)
                seek.reset_mock()
                event(Gdk.EventType.BUTTON_PRESS,18000,y+height/2)
                seek.assert_called_once_with(179.0)
                event(Gdk.EventType.MOTION_NOTIFY,18020,y+height/2);stable(at,top)
                self.assertTrue(w.timeline.drag['active'])
                event(Gdk.EventType.BUTTON_RELEASE,18020,y+height/2);stable(at,top)
                self.assertEqual(w.project['audio'][0]['start'],.2);self.assertEqual(w.timeline.drag_scroll_timer,0)
                position('audio',0,horizontal.get_upper()-horizontal.get_page_size())
                at=horizontal.get_value();top=vertical.get_value()
                _,edge,x,y,_=next(h for h in w.timeline.fade_hits if h[1]=='out')
                event(Gdk.EventType.BUTTON_PRESS,x,y);self.assertEqual(w.timeline.drag['mode'],'fade')
                event(Gdk.EventType.BUTTON_RELEASE,x,y);stable(at,top)
                # Navigation from outside the timeline still reveals its item.
                w.select('clips',0,seek=False);self.pump(lambda:horizontal.get_value()<100)
                w.select('captions',0,seek=False);self.pump(lambda:horizontal.get_value()>16000)
                row=next(h for h in w.timeline.hit if h[:2]==('captions',0))
                self.assertLessEqual(vertical.get_value(),row[3])
                self.assertGreaterEqual(vertical.get_value()+vertical.get_page_size(),row[3]+row[5])
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
            with mock.patch.object(w,'seek',wraps=w.seek) as seek:
                w.timeline.press(None, SimpleNamespace(button=3, x=x+width/2,
                                                       y=y+height/2))
                seek.assert_not_called()
            # The paused 25-fps stream may present the next frame while GTK
            # opens the menu; that must not be mistaken for a selection seek.
            self.assertAlmostEqual(w.playhead, .7, delta=1/w.project['fps']+.001)
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
            self.pump(lambda:not w.scrub_timer and not w._scrub_settling and w.canvas.frame is not None)
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
            with mock.patch('clipcanvas.app.file_chooser') as chooser:
                chooser.return_value.run.return_value=Gtk.ResponseType.ACCEPT
                chooser.return_value.get_filename.return_value=str(logo)
                w.layer_tools.import_visual('image')
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

    def test_caption_sidebar_hover_delete_is_individual_undoable_and_saved(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'hover-delete-captions');errors=[];w.error=errors.append
        try:
            w.start_project(320,180);w.import_paths([str(self.fixtures.root/'landscape.mp4'),str(self.fixtures.root/'music.wav')],add_to_timeline=True)
            self.pump(lambda:not w.busy and bool(w.thumbs) and bool(w.waves))
            w.project['layers']=[model.new_text('Keep this title',0,1)]
            w.project['captions']=[model.new_caption(text,start,start+.3) for text,start in [('First',.1),('Delete me',.5),('Third',.9),('Last',1.3)]]
            w.changed();w.media_stack.set_visible_child_name('Captions')
            row=w.layer_tools.captions_list.get_children()[1];button=row.remove_button
            opacity=lambda:button.get_style_context().get_property('opacity',button.get_state_flags())
            row.edit_button.grab_focus();row.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            self.pump(lambda:opacity()<.01 and row.get_allocated_width()>100)
            size=(row.get_allocated_width(),row.edit_button.get_allocated_width())
            row.set_state_flags(Gtk.StateFlags.PRELIGHT,False);self.pump(lambda:opacity()>.99)
            self.assertEqual((row.get_allocated_width(),row.edit_button.get_allocated_width()),size)
            row.unset_state_flags(Gtk.StateFlags.PRELIGHT);self.pump(lambda:opacity()<.01)
            button.grab_focus();self.pump(lambda:opacity()>.99)
            self.assertIn('Delete me',button.get_accessible().get_name())
            row.edit_button.clicked();self.assertEqual(w.selected,('captions',1))
            self.assertEqual(w.properties_stack.get_visible_child_name(),'Text')
            w.properties_popover.popdown();w.select_all();w.selected=('captions',3);w.playhead=1.4
            before=copy.deepcopy(w.project);selection=set(w.selection);history=len(w.history)
            last_id=before['captions'][3]['id'];deleted_id=row.caption_id
            with mock.patch.object(w.layer_tools,'select') as edit:
                button.clicked();edit.assert_not_called()
            expected=copy.deepcopy(before);expected['captions'].pop(1)
            self.assertEqual(w.project,expected);self.assertEqual(len(w.history),history+1)
            self.assertEqual(w.selected,('captions',2));self.assertEqual(w.selected_item()['id'],last_id)
            self.assertEqual(w.selection,selection-{('captions',deleted_id)})
            self.assertAlmostEqual(w.playhead,1.4,delta=1/30)
            w.undo();self.assertEqual(w.project,before)
            w.redo();self.assertEqual(w.project,expected)
            history=len(w.history)
            self.assertFalse(w.layer_tools.remove_caption(deleted_id));self.assertEqual(len(w.history),history)
            w.busy=True;self.assertFalse(w.layer_tools.remove_caption(last_id));w.busy=False
            self.assertEqual(w.project,expected)
            saved=self.fixtures.root/'sidebar-caption-delete.clipcanvas'
            w.project_path=str(saved);self.assertTrue(w.save())
            w.open_project(str(saved));self.assertEqual(w.project,expected)
            while w.project['captions']:
                row=w.layer_tools.captions_list.get_children()[0]
                w.select('captions',0,seek=False);row.remove_button.clicked()
            self.assertFalse(w.layer_tools.captions_list.get_children());self.assertIsNone(w.selected)
            self.assertEqual(w.project['clips'],before['clips']);self.assertEqual(w.project['audio'],before['audio'])
            self.assertEqual(w.project['layers'],before['layers']);self.assertFalse(errors)
        finally:
            w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_caption_button_starts_small_directly_and_cancel_keeps_edits(self):
        import threading
        from clipcanvas import captions
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'direct-captions-recovery');errors=[];w.error=errors.append
        release=threading.Event()
        try:
            w.start_project(320,180)
            with mock.patch.object(captions,'setup_transcription') as setup:
                w.layer_tools.generate_button.clicked();setup.assert_not_called();self.assertFalse(w.busy)
            w.import_paths([str(self.fixtures.root/'landscape.mp4')],add_to_timeline=True)
            self.pump(lambda:not w.busy and bool(w.thumbs))
            w.project['clips'][0]['volume']=0
            before=copy.deepcopy(w.project);history=len(w.history)
            result={'captions':[model.new_caption('Editable speech',.1,1)],'language':'en'}
            with mock.patch.object(captions,'setup_transcription') as setup, \
                 mock.patch.object(engine,'render_audio',wraps=engine.render_audio) as render, \
                 mock.patch.object(captions,'generate_from_audio',return_value=result) as transcribe, \
                 mock.patch.object(Gtk.Dialog,'run',side_effect=AssertionError('Generate must not open an options dialog')):
                w.layer_tools.generate_button.clicked();self.assertTrue(w.busy)
                self.pump(lambda:not w.busy)
                self.assertEqual(setup.call_args.kwargs['model'],'small')
                self.assertEqual(transcribe.call_args.kwargs['model'],'small')
                self.assertIsNone(transcribe.call_args.kwargs['language'])
                self.assertEqual(render.call_args.kwargs['source'],'video')
            expected=copy.deepcopy(before);expected['captions']=result['captions']
            self.assertEqual(w.project,expected);self.assertEqual(len(w.history),history+1)
            w.project['captions'][0]['text']='My corrected words'
            before=copy.deepcopy(w.project);history=len(w.history);entered=threading.Event()
            def delayed_result(*args,**kwargs):
                entered.set();release.wait(5);return result
            with mock.patch.object(captions,'setup_transcription'), \
                 mock.patch.object(captions,'generate_from_audio',side_effect=delayed_result), \
                 mock.patch.object(w.layer_tools,'replace_captions',return_value=False) as replace:
                w.layer_tools.generate_button.clicked();self.pump(entered.is_set)
                dialog=next(d for d in Gtk.Window.list_toplevels() if isinstance(d,Gtk.Dialog) and d.get_title()=='Generating captions')
                dialog.response(Gtk.ResponseType.CANCEL);release.set();self.pump(lambda:not w.busy)
                replace.assert_not_called()
            self.assertEqual(w.project,before);self.assertEqual(len(w.history),history);self.assertFalse(errors)
        finally:
            release.set();w.cleanup();w.destroy()
            while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_malayalam_language_generation_fonts_undo_and_reopen(self):
        from clipcanvas import captions,bundled_fonts
        from clipcanvas.text_style_ui import TextStyleEditor
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'malayalam-captions-recovery');reopened=None
        errors=[];w.error=errors.append
        try:
            w.start_project(640,360);w.project['clips']=[model.new_card(duration=5)]
            w.refresh();w.cancel_scrub();before=copy.deepcopy(w.project)
            tools=w.layer_tools;tools.caption_language.set_active_id('ml')
            self.assertEqual(tools.generation_settings(),{'source':'video','model_name':'large-v3-turbo','language':'ml'})
            self.assertEqual(w.project,before)
            result={'captions':[model.new_caption('മലയാളം അടിക്കുറിപ്പുകൾ',.2,2.2)],'language':'ml'}
            with mock.patch.object(captions,'setup_transcription'), \
                 mock.patch.object(captions,'generate_from_audio',return_value=result) as transcribe:
                tools.generate_button.clicked();self.pump(lambda:not w.busy)
                self.assertEqual(transcribe.call_args.kwargs['language'],'ml')
                self.assertEqual(transcribe.call_args.kwargs['model'],'large-v3-turbo')
            self.assertEqual(w.project['captions'][0]['text'],result['captions'][0]['text'])
            self.assertEqual(w.project['caption_style']['font_family'],'Noto Sans Malayalam')
            w.undo();self.assertEqual(w.project,before);w.redo()
            container=Gtk.Box();editor=TextStyleEditor(container,w.project['caption_style'])
            editor.set_sample(result['captions'][0]['text'])
            for family,_ in bundled_fonts.MALAYALAM_FONTS:
                editor.malayalam_font.set_active_id(family)
                style=editor.read_into(copy.deepcopy(w.project['caption_style']))
                self.assertEqual(style['font_family'],family)
                self.assertEqual(editor.sample,result['captions'][0]['text'])
                w.project['caption_style']=style
            saved=self.fixtures.root/'malayalam.clipcanvas';model.save_project(w.project,saved)
            self.assertEqual(model.load_project(saved),w.project)
            reopened=ClipCanvasWindow(recovery_dir=self.fixtures.root/'malayalam-captions-reopened',preferences_dir=w.preferences.path.parent)
            self.assertEqual(reopened.layer_tools.caption_language.get_active_id(),'ml')
            self.assertEqual(reopened.layer_tools.generation_settings()['language'],'ml')
            with mock.patch.object(tools,'generate_job') as generate:
                tools.caption_language.set_active_id('en');tools.generate_button.clicked()
                self.assertEqual(generate.call_args.kwargs['language'],'en')
            self.assertFalse(errors)
        finally:
            if reopened is not None:reopened.cleanup();reopened.destroy()
            w.cleanup();w.destroy()

    def test_optional_caption_settings_cancel_validate_and_survive_restart(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'caption-settings-recovery');reopened=None
        try:
            tools=w.layer_tools;before=copy.deepcopy(w.project);defaults={'source':'video','model_name':'small','language':None}
            self.assertEqual(tools.generation_settings(),defaults)
            def controls(dialog):
                self.assertEqual(dialog.get_title(),'Caption settings')
                children=dialog.get_content_area().get_children()
                combos=[child for child in children if isinstance(child,Gtk.ComboBoxText)]
                entry=next(child for child in children if isinstance(child,Gtk.Entry))
                return children,combos,entry
            def cancel(dialog):
                _,combos,entry=controls(dialog)
                self.assertEqual([c.get_active_id() for c in combos],['video','small'])
                combos[0].set_active_id('mix');combos[1].set_active_id('tiny');entry.set_text('en')
                return Gtk.ResponseType.CANCEL
            with mock.patch.object(Gtk.Dialog,'run',cancel),mock.patch.object(tools,'generate_job') as generate:
                tools.caption_settings_button.clicked();generate.assert_not_called()
            self.assertEqual(tools.generation_settings(),defaults)
            self.assertIsNone(w.preferences.get('caption_generation'))
            attempts=[]
            def apply(dialog):
                children,combos,entry=controls(dialog);attempts.append(True)
                if len(attempts)==1:
                    entry.set_text('English')
                else:
                    self.assertTrue(any(isinstance(child,Gtk.Label) and child.get_text().startswith('Use a language code') for child in children))
                    combos[0].set_active_id('mix');combos[1].set_active_id('tiny');entry.set_text(' EN ')
                return Gtk.ResponseType.OK
            with mock.patch.object(Gtk.Dialog,'run',apply),mock.patch.object(tools,'generate_job') as generate:
                tools.caption_settings_button.clicked();generate.assert_not_called()
            saved={'source':'mix','model_name':'tiny','language':'en'}
            self.assertEqual(len(attempts),2);self.assertEqual(tools.generation_settings(),saved)
            reopened=ClipCanvasWindow(recovery_dir=self.fixtures.root/'caption-settings-reopened',preferences_dir=w.preferences.path.parent)
            self.assertEqual(reopened.layer_tools.generation_settings(),saved)
            with mock.patch.object(reopened.layer_tools,'generate_job') as generate:
                reopened.layer_tools.generate_button.clicked();generate.assert_called_once_with(**saved)
            w.preferences.set('caption_generation',{'source':'bad','model_name':'missing','language':123})
            self.assertEqual(tools.generation_settings(),defaults)
            self.assertEqual(w.project,before);self.assertFalse(w.history)
        finally:
            if reopened is not None:reopened.cleanup();reopened.destroy()
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
            # This is the same background job used by the Generate button:
            # private runtime -> timeline-aware audio -> real offline ASR -> editable cues.
            w.layer_tools.generate_job(runtime_dir=os.environ['CLIPCANVAS_TEST_SPEECH_RUNTIME'],model_name=os.environ.get('CLIPCANVAS_TEST_SPEECH_MODEL','tiny'))
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


    def test_short_text_layer_stretch_and_full_video_watermark(self):
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'watermark-ui-recovery')
        errors=[];w.error=errors.append
        try:
            w.start_project(320,180)
            w.project['clips']=[model.new_card('#112244',295.7)]
            layer=model.new_text('@airadio_malayalam',0,3)
            layer.update(font_size=22,opacity=.7,y=.8)
            w.project['layers']=[layer]
            w.project['timeline_order']=['layers:'+layer['id'],'video']
            pointer=w.get_display().get_default_seat().get_pointer()
            def event(kind,x,y):
                e=Gdk.Event.new(kind);e.window=w.timeline.get_window();e.send_event=True
                e.time=Gdk.CURRENT_TIME;e.x=x;e.y=y;e.state=0;e.set_device(pointer)
                if kind!=Gdk.EventType.MOTION_NOTIFY:e.button=1
                w.timeline.event(e)
            def draw():
                surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,2100,400)
                w.timeline.draw(w.timeline,cairo.Context(surface))
                return next(h for h in w.timeline.hit if h[:2]==('layers',0))
            with mock.patch.object(w,'show_selected_still'),mock.patch.object(w,'seek') as seek:
                w.changed();w.zoom_control.set_value(6)
                self.pump(lambda:len(w.timeline.hit)==2)
                for end in (.2,3,4.5):
                    with self.subTest(initial_duration=end):
                        w.project['layers'][0].update(start=0,end=end)
                        w.playhead=0;w.select('layers',0,seek=False)
                        _,_,x,y,width,height=draw();point=x+width-2;middle=y+height/2
                        before=copy.deepcopy(w.project);history=len(w.history)
                        event(Gdk.EventType.MOTION_NOTIFY,point,middle)
                        self.assertIsNotNone(w.timeline.get_window().get_cursor())
                        event(Gdk.EventType.BUTTON_PRESS,point,middle)
                        self.assertEqual(w.timeline.drag['edge'],'right')
                        target=point+(295.7-end)*w.timeline.scale
                        event(Gdk.EventType.MOTION_NOTIFY,target,middle)
                        self.assertEqual(w.project,before)
                        self.assertAlmostEqual(draw()[4],295.7*w.timeline.scale)
                        event(Gdk.EventType.BUTTON_RELEASE,target,middle)
                        self.assertEqual(w.project['layers'][0]['end'],295.7)
                        self.assertEqual(len(w.history),history+1)
                        expected=copy.deepcopy(before);expected['layers'][0]['end']=295.7
                        self.assertEqual(w.project,expected)
                        w.undo();self.assertEqual(w.project,before);w.redo();self.assertEqual(w.project,expected)
                # The layer edge wins over the playhead at zero, while the
                # ruler remains available for scrubbing. Body drags still move.
                w.project['layers'][0].update(start=0,end=3);w.selected=('layers',0);w.playhead=0
                _,_,x,y,width,height=draw();middle=y+height/2
                event(Gdk.EventType.BUTTON_PRESS,x+2,middle)
                self.assertEqual(w.timeline.drag['mode'],'edit');self.assertEqual(w.timeline.drag['edge'],'left')
                event(Gdk.EventType.MOTION_NOTIFY,x+2+6,middle)
                event(Gdk.EventType.BUTTON_RELEASE,x+2+6,middle)
                self.assertEqual(w.project['layers'][0]['start'],1)
                w.playhead=20;w.snap_check.set_active(False)
                _,_,x,y,width,height=draw();point=x+width/2
                event(Gdk.EventType.BUTTON_PRESS,point,middle);self.assertIsNone(w.timeline.drag['edge'])
                event(Gdk.EventType.MOTION_NOTIFY,point+12,middle)
                event(Gdk.EventType.BUTTON_RELEASE,point+12,middle)
                self.assertEqual((w.project['layers'][0]['start'],w.project['layers'][0]['end']),(3,5))
                w.select('layers',0,seek=False)
                self.assertTrue(w.layer_tools.overlay_controls.get_visible())
                w.playhead=180;history=len(w.history);before=copy.deepcopy(w.project)
                w.layer_tools.full_video_button.clicked()
                expected=copy.deepcopy(before);expected['layers'][0].update(start=0,end=295.7)
                self.assertEqual(w.project,expected);seek.assert_called_with(180)
                self.assertEqual(len(w.history),history+1)
                w.layer_tools.full_video_button.clicked();self.assertEqual(len(w.history),history+1)
                w.undo();self.assertEqual(w.project,before);w.redo();self.assertEqual(w.project,expected)
                path=self.fixtures.root/'watermark.clipcanvas';w.project_path=str(path);self.assertTrue(w.save())
                w.open_project(str(path));self.assertEqual(w.project,expected)
                # Both numeric timing and right-click offer the same action.
                w.project['layers'][0]['end']=3;w.select('layers',0,seek=False)
                timing=w.properties_stack.get_child_by_name('Timing')
                fit=next(child for child in timing.get_children() if isinstance(child,Gtk.Button) and child.get_label()=='Fit to full video')
                fit.clicked();self.assertEqual(w.project['layers'][0]['end'],295.7)
                w.project['layers'][0]['end']=3
                menu=w.context_menu('layers',0)
                fit=next(child for child in menu.get_children() if child.get_label()=='Fit to full video')
                menu.popdown();fit.activate();self.assertEqual(w.project['layers'][0]['end'],295.7)
            # Export a short actual video after fitting through the UI. Check
            # its last frame, well beyond the watermark's original endpoint.
            w.project['clips']=[model.new_card('#112244',1.5)]
            w.project['layers'][0].update(start=.2,end=.4,opacity=1)
            w.select('layers',0,seek=False);w.playhead=.3;w.layer_tools.full_video_button.clicked()
            self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
            self.assertAlmostEqual(w.playhead,.3)
            self.assertEqual((w.project['layers'][0]['start'],w.project['layers'][0]['end']),(0,1.5))
            output=self.fixtures.root/'watermark-export.mp4'
            w.render_job(str(output),False,lambda _:None,quality='master')
            self.pump(lambda:not w.busy)
            ffmpeg,env=engine._tool('ffmpeg')
            for at in (.1,1.4):
                preview=engine.preview_frame(w.project,at,preview_edge=320)['pixels']
                self.assertGreater(sum(min(preview[i:i+3])>150 for i in range(0,len(preview),3)),60)
                raw=subprocess.run([ffmpeg,'-v','error','-i',str(output),'-ss',str(at),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
                                   env=env,check=True,stdout=subprocess.PIPE).stdout
                self.assertGreater(sum(min(raw[i:i+3])>150 for i in range(0,len(raw),3)),60)
            w.project['clips']=[];w.select('layers',0,seek=False)
            self.assertFalse(w.layer_tools.full_video_button.get_sensitive())
            before=copy.deepcopy(w.project);history=len(w.history);w.layer_tools.overlay_full_video()
            self.assertEqual(w.project,before);self.assertEqual(len(w.history),history)
            self.assertFalse(errors)
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
            with mock.patch('clipcanvas.app.file_chooser') as chooser:
                chooser.return_value.run.return_value=Gtk.ResponseType.ACCEPT
                chooser.return_value.get_filename.return_value=str(logo)
                w.layer_tools.import_visual('image')
            self.pump(lambda:not w.busy and str(logo) in w.layer_tools.assets)
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


    def test_shared_audio_lanes_drag_split_menu_clipboard_and_save(self):
        from clipcanvas import track_layout
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'shared-lane-recovery')
        errors=[];w.error=errors.append;w.canvas._pipeline.set_property('volume',0)
        try:
            w.start_project(320,180)
            w.import_paths([str(self.fixtures.root/'landscape.mp4'),str(self.fixtures.root/'music.wav')],add_to_timeline=True)
            self.pump(lambda:not w.busy and bool(w.thumbs) and bool(w.waves))
            a=w.project['audio'][0];a.update(out=.5,volume=.35,fade_in=.1,audio_effects={'echo_enabled':True,'echo_delay_ms':200})
            b=model.new_audio(self.fixtures.root/'music.wav');b.update(start=.5,**{'in':1,'out':1.5,'volume':.7,'fade_out':.1})
            music=model.new_audio(self.fixtures.root/'music.wav');music.update(volume=.2)
            w.project['audio'].extend([b,music]);w.changed();w.zoom(100)
            w.snap_check.set_active(False);w.select('audio',1,seek=False)
            self.pump(lambda:len(w.timeline.hit)==4)
            before=copy.deepcopy(w.project);history=len(w.history)
            _,_,x,y,width,height=next(h for h in w.timeline.hit if h[:2]==('audio',1))
            target_y=next(r[3] for r in track_layout.rows(w.project) if r[0]=='audio:'+a['lane_id'])
            w.timeline.press(None,SimpleNamespace(button=1,x=x+20,y=y+20))
            w.timeline.motion(None,SimpleNamespace(x=x+20,y=target_y+20))
            self.assertTrue(w.timeline.drag['active']);self.assertTrue(w.timeline.drag['drop_valid'])
            self.assertEqual(w.timeline.drag['lane_id'],a['lane_id'])
            self.assertEqual(w.project,before)
            w.timeline.draw(w.timeline,cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,1200,400)))
            w.timeline.release(None,SimpleNamespace(x=x+20,y=target_y+20))
            expected=copy.deepcopy(before);expected['audio'][1]['lane_id']=a['lane_id']
            self.assertEqual(w.project,expected);self.assertEqual(len(w.history),history+1)
            self.pump(lambda:len(w.timeline.hit)==4 and next(h[3] for h in w.timeline.hit if h[:2]==('audio',0))==next(h[3] for h in w.timeline.hit if h[:2]==('audio',1)))
            self.assertEqual(len([r for r in track_layout.rows(w.project) if r[1]=='audio']),2)
            w.undo();self.assertEqual(w.project,before);w.redo();self.assertEqual(w.project,expected)
            # An overlapping drop is red/invalid and commits no history.
            self.pump(lambda:len(w.timeline.hit)==4)
            _,_,x,y,_,_=next(h for h in w.timeline.hit if h[:2]==('audio',1))
            history=len(w.history)
            w.timeline.press(None,SimpleNamespace(button=1,x=x+20,y=y+20))
            w.timeline.motion(None,SimpleNamespace(x=x-30,y=y+20))
            self.assertFalse(w.timeline.drag['drop_valid'])
            w.timeline.release(None,SimpleNamespace(x=x-30,y=y+20))
            self.assertEqual(w.project,expected);self.assertEqual(len(w.history),history)
            # The menu appends on a occupied lane, with the new start visible.
            menu=w.context_menu('audio',2)
            submenu=next(c for c in menu.get_children() if c.get_label()=='Move to audio lane').get_submenu()
            append=next(c for c in submenu.get_children() if c.get_label().startswith('Audio 1 · after last clip'))
            menu.popdown();append.activate()
            self.assertEqual(w.project['audio'][2]['start'],1)
            self.assertEqual(w.project['audio'][2]['lane_id'],a['lane_id'])
            w.undo();self.assertEqual(w.project,expected)
            # Drag a clip back out into the new-lane area without retiming.
            w.select('audio',1,seek=False)
            self.pump(lambda:len(w.timeline.hit)==4)
            _,_,x,y,_,_=next(h for h in w.timeline.hit if h[:2]==('audio',1))
            new_y=w.timeline.new_audio_row_y()
            w.timeline.press(None,SimpleNamespace(button=1,x=x+20,y=y+20))
            w.timeline.motion(None,SimpleNamespace(x=x+20,y=new_y+20))
            self.assertTrue(w.timeline.drag['drop_valid'])
            w.timeline.release(None,SimpleNamespace(x=x+20,y=new_y+20))
            self.assertNotEqual(w.project['audio'][1]['lane_id'],a['lane_id']);self.assertEqual(w.project['audio'][1]['start'],.5)
            w.undo();self.assertEqual(w.project,expected)
            # Both parts of a split remain in one row and remain editable.
            w.select('audio',1,seek=False);w.playhead=.75;w.split_selected()
            self.assertEqual(w.project['audio'][1]['lane_id'],w.project['audio'][2]['lane_id'])
            self.assertEqual(len([r for r in track_layout.rows(w.project) if r[1]=='audio']),2)
            w.select('audio',0,seek=False);order_before=copy.deepcopy(w.project)
            w.row_up_button.clicked()
            self.assertEqual(track_layout.rows(w.project)[0][0],'audio:'+a['lane_id'])
            self.assertEqual(w.project['audio'],order_before['audio'])
            w.select('audio',1,seek=False);w.duplicate()
            self.assertNotEqual(w.project['audio'][1]['lane_id'],w.project['audio'][2]['lane_id'])
            self.assertTrue(model.validate_project(w.project));w.undo()
            # Asset clicks reuse a selected lane only when the playhead fits.
            snapshot=copy.deepcopy(w.project);asset=next(i for i,a in enumerate(w.project['assets']) if a['kind']=='audio')
            w.select('audio',0,seek=False);w.playhead=1;w.add_asset(asset)
            self.assertEqual(w.project['audio'][-1]['lane_id'],a['lane_id'])
            self.assertEqual(w.project['audio'][-1]['start'],1)
            w.undo();self.assertEqual(w.project,snapshot)
            w.select('audio',0,seek=False);w.playhead=.1;w.add_asset(asset)
            self.assertNotEqual(w.project['audio'][-1]['lane_id'],a['lane_id'])
            w.undo();self.assertEqual(w.project,snapshot)
            saved=copy.deepcopy(w.project);w.project_path=str(self.fixtures.root/'shared-lanes.clipcanvas')
            self.assertTrue(w.save());w.open_project(w.project_path);self.assertEqual(w.project,saved)
            # Delete the first member: its lane remains for the other clips.
            w.select('audio',0,seek=False);w.remove()
            self.assertEqual(track_layout.rows(w.project)[0][0],'audio:'+a['lane_id'])
            # Sample-accurate WAV edges can sit between video frames. Snapping
            # must retain that exact boundary, not round down into an overlap.
            left=copy.deepcopy(a);left['out']=.513
            right=copy.deepcopy(b);right['start']=.9
            w.project['audio']=[left,right];w.changed();w.snap_check.set_active(True)
            self.pump(lambda:len(w.timeline.hit)==3)
            _,_,x,y,_,_=next(h for h in w.timeline.hit if h[:2]==('audio',1))
            target_y=next(h[3] for h in w.timeline.hit if h[:2]==('audio',0))
            w.timeline.press(None,SimpleNamespace(button=1,x=x+20,y=y+20))
            to_x=x+20+(.52-.9)*w.timeline.scale
            w.timeline.motion(None,SimpleNamespace(x=to_x,y=target_y+20))
            self.assertTrue(w.timeline.drag['drop_valid'])
            w.timeline.release(None,SimpleNamespace(x=to_x,y=target_y+20))
            self.assertAlmostEqual(w.project['audio'][1]['start'],.513,places=9)
            self.assertEqual(w.project['audio'][1]['lane_id'],a['lane_id'])
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
            for row in w.assets.get_children()[:3]:row.add_button.clicked()
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

    def test_remove_sidebar_media_keeps_files_timeline_playback_and_supports_undo(self):
        import hashlib
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'remove-library-recovery')
        errors=[];w.error=errors.append
        logo=self.fixtures.root/'library-logo.png'
        cairo.ImageSurface(cairo.FORMAT_ARGB32,16,16).write_to_png(str(logo))
        paths=[self.fixtures.root/name for name in ('landscape.mp4','portrait.mp4','music.wav')]+[logo]
        originals={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        try:
            w.start_project(320,180);w.import_paths([str(path) for path in paths])
            self.pump(lambda:not w.busy and len(w.project['assets'])==4 and len(w.thumbs)==3 and len(w.waves)==1)
            for row in w.assets.get_children():
                if w.project['assets'][w.asset_index(row.media_asset_id)]['path'].endswith(('landscape.mp4','music.wav','library-logo.png')):
                    row.add_button.clicked()
            self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
            self.assertEqual((len(w.project['clips']),len(w.project['audio']),len(w.project['layers'])),(2,1,0))
            w.select('clips',0,seek=False);w.preview()
            self.pump(lambda:w.canvas.playing and w.canvas.position()>.1)
            before=copy.deepcopy(w.project);selected=w.selected;selection=set(w.selection)
            history=len(w.history);playing=w.canvas._live;position=w.playhead;frame=w.canvas.frame
            row=next(row for row in w.assets.get_children() if row.media_asset_id==before['assets'][0]['id'])
            row.remove_button.clicked()
            expected=copy.deepcopy(before);expected['assets'].pop(0)
            self.assertEqual(w.project,expected);self.assertEqual(len(w.history),history+1)
            self.assertEqual(w.selected,selected);self.assertEqual(w.selection,selection)
            self.assertIs(w.canvas._live,playing);self.assertTrue(w.canvas.playing)
            self.assertEqual(w.playhead,position);self.assertIs(w.canvas.frame,frame)
            self.assertTrue(w.undo_button.get_sensitive())
            w.undo();self.assertEqual(w.project,before)
            w.redo();self.assertEqual(w.project,expected)
            self.assertFalse(w.remove_asset(before['assets'][0]['id']))
            self.assertEqual(w.project,expected)
            # Filtering must remove the chosen stable asset ID, not the row's
            # position in the unfiltered model. Right-click never adds a clip.
            w.media_tabs.set_active_id('audio');w.media_search.set_text('music');w.refresh_assets()
            row=w.assets.get_children()[0];asset_id=row.media_asset_id
            self.assertTrue(w.media_button_press(row.add_button,SimpleNamespace(button=3),asset_id))
            self.assertEqual(w.project,expected)
            menu=w.active_media_menu;menu.popdown()
            next(item for item in menu.get_children() if item.get_label()=='Remove from media library').activate()
            expected['assets']=[item for item in expected['assets'] if item['id']!=asset_id]
            self.assertEqual(w.project,expected)
            # Delete belongs to the focused library row even if the timeline
            # still has a selected clip. Empty filtered results focus Search.
            w.media_search.set_text('');w.media_tabs.set_active_id('image');w.refresh_assets()
            row=w.assets.get_children()[0];asset_id=row.media_asset_id;row.add_button.grab_focus()
            self.assertTrue(w.keypress(w,SimpleNamespace(keyval=Gdk.KEY_Delete,state=0)))
            expected['assets']=[item for item in expected['assets'] if item['id']!=asset_id]
            self.assertEqual(w.project,expected)
            self.assertFalse(w.keypress(w,SimpleNamespace(keyval=Gdk.KEY_Delete,state=0)))
            self.assertEqual(w.project,expected)
            history=len(w.history);w.busy=True
            self.assertFalse(w.remove_asset(expected['assets'][0]['id']))
            w.busy=False;self.assertEqual(len(w.history),history)
            saved=self.fixtures.root/'removed-library.clipcanvas'
            model.save_project(w.project,saved);self.assertEqual(model.load_project(saved),expected)
            self.assertEqual({str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},originals)
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
            canvas._start_frame_updates()
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

    def test_preview_and_playhead_paint_together_for_live_and_prepared_frames(self):
        from gi.repository import Gst
        w=ClipCanvasWindow(recovery_dir=self.fixtures.root/'shared-paint-recovery')
        try:
            w.start_project(320,180);w.project['clips']=[model.new_card('#16384a',10)];w.refresh();w.cancel_scrub()
            canvas=w.canvas;draws=[]
            def drawn(kind):
                def record(widget,cr):
                    draws.append((kind,widget.get_frame_clock().get_frame_counter(),w.playhead,canvas.position(),bytes(canvas.frame.get_pixels())[0] if canvas.frame else None))
                return record
            canvas.connect_after('draw',drawn('preview'));w.timeline.connect_after('draw',drawn('timeline'))
            def sample(at,color):
                picture=Gst.Sample.new(Gst.Buffer.new_wrapped(bytes([color])*24),
                    Gst.Caps.from_string('video/x-raw,format=RGB,width=4,height=2'),None,None)
                picture.get_buffer().pts=int(at*Gst.SECOND);return picture
            for live in (True,False):
                with self.subTest(live=live):
                    canvas.stop();canvas.frame=None;canvas._last_duration=10;w.playhead=0
                    if live:canvas._live=SimpleNamespace(start=2,total=10,last_position=2,stop=lambda:None,pause=lambda:None)
                    canvas._loaded=True;draws.clear()
                    # A busy UI retains the newest picture only. The streaming
                    # callback must not change either displayed state yet.
                    for at,color in ((.25,25),(.5,50),(.625,62)):canvas._receive_sample(sample(at,color))
                    self.assertIsNone(canvas.frame);self.assertEqual(w.playhead,0)
                    canvas._start_frame_updates();target=2.625 if live else .625
                    def matched():
                        previews={row[1]:row for row in draws if row[0]=='preview' and abs(row[2]-target)<1e-8}
                        return any(row[0]=='timeline' and row[1] in previews and row[2:]==previews[row[1]][2:] for row in draws)
                    self.pump(matched)
                    self.assertEqual(bytes(canvas.frame.get_pixels())[0],62)
                    self.assertAlmostEqual(canvas.position(),target)
                    # A query-clock timer cannot move a prepared-preview line
                    # away from its painted picture either.
                    with mock.patch.object(canvas._pipeline,'query_position',side_effect=AssertionError('Use presented frame time')):
                        canvas._tick();self.assertAlmostEqual(canvas.position(),target)
                    canvas.playing=True;canvas.pause();canvas._receive_sample(sample(.9,90))
                    deadline=time.monotonic()+.05;self.pump(lambda:time.monotonic()>=deadline)
                    self.assertAlmostEqual(w.playhead,target);self.assertEqual(bytes(canvas.frame.get_pixels())[0],62)
                    canvas.stop();self.assertEqual(canvas._frame_tick_id,0)
                    canvas._receive_sample(sample(1,100),canvas._generation-1)
                    self.assertIsNone(canvas._latest_frame)
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
