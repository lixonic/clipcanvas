"""Loading feedback follows committed pictures, including canceled generations."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import gi
gi.require_version('Gtk','3.0')
gi.require_version('Gst','1.0')
from gi.repository import Gtk, Gst
from clipcanvas import model
from clipcanvas.app import ClipCanvasWindow


class DeferredStream:
    """Hold the first native picture until the test explicitly supplies it."""
    def __init__(self,project,start,autoplay,sample,error,end,**kwargs):
        self.project=project;self.start=start;self.total=model.project_duration(project)
        self.sample=sample;self.error=error;self.end=end;self.last_position=start
        self.ended=False;self.stopped=False
        self.buffering=False;self.on_buffering=kwargs.get('buffering')

    def buffer(self,active,message='Buffering preview…'):
        self.buffering=active
        if self.on_buffering:self.on_buffering(active,message)

    def picture(self,at=0):
        buffer=Gst.Buffer.new_wrapped(bytes([180])*24);buffer.pts=int(at*Gst.SECOND)
        caps=Gst.Caps.from_string('video/x-raw,format=RGB,width=4,height=2')
        segment=Gst.Segment();segment.init(Gst.Format.TIME)
        self.sample(Gst.Sample.new(buffer,caps,segment,None))

    def play(self):pass
    def pause(self):pass
    def stop(self):self.stopped=True
    def position(self):return self.last_position


@unittest.skipUnless(Gtk.init_check()[0],'A desktop display is required')
class PlayStartupFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='clipcanvas-play-ui-')
        self.patch=mock.patch('clipcanvas.player.StreamPlayer',DeferredStream);self.patch.start()
        self.window=ClipCanvasWindow(recovery_dir=Path(self.folder.name))
        self.window.project['clips']=[model.new_card('#183b56',5)]

    def tearDown(self):
        self.window.cleanup();self.window.destroy();self.patch.stop();self.folder.cleanup()
        while Gtk.events_pending():Gtk.main_iteration_do(False)

    def test_play_responds_then_loading_ends_only_when_picture_is_committed(self):
        w=self.window;w.preview()
        self.assertTrue(w.canvas.loading);self.assertEqual(w.play_button.get_label(),'Pause')
        self.assertEqual(w.preview_label.get_text(),'Loading preview…')
        w.canvas._live.picture(.1)
        self.assertTrue(w.canvas.loading)
        w.canvas._deliver_frame()
        self.assertFalse(w.canvas.loading);self.assertEqual(w.preview_label.get_text(),'Live preview')
        self.assertAlmostEqual(w.canvas.position(),.1)

    def test_pause_before_first_frame_keeps_loading_for_resume(self):
        w=self.window;w.preview();w.toggle_play()
        self.assertFalse(w.canvas.playing);self.assertTrue(w.canvas.loading)
        self.assertEqual(w.preview_label.get_text(),'Paused preview')
        w.canvas._live.picture(.1);w.canvas._deliver_frame()
        self.assertTrue(w.canvas.loading)
        w.toggle_play()
        self.assertEqual(w.preview_label.get_text(),'Loading preview…')
        w.canvas._live.picture(.2);w.canvas._deliver_frame()
        self.assertFalse(w.canvas.loading);self.assertEqual(w.preview_label.get_text(),'Live preview')

    def test_old_seek_generation_cannot_clear_loading_or_report_error(self):
        w=self.window;w.preview();old=w.canvas._live
        w.canvas.seek(2);self.assertTrue(old.stopped)
        old.picture(.1);w.canvas._deliver_frame();old.error('Stale error')
        self.assertTrue(w.canvas.loading);self.assertTrue(w.canvas.playing)
        self.assertEqual(w.preview_label.get_text(),'Loading preview…')
        w.canvas._live.picture(.1);w.canvas._deliver_frame()
        self.assertFalse(w.canvas.loading);self.assertAlmostEqual(w.canvas.position(),2.1)

    def test_buffering_status_waits_for_ready_picture_and_keeps_manual_pause(self):
        w=self.window;w.preview();stream=w.canvas._live
        stream.buffer(True,'Preparing Fast preview · video.mp4…')
        self.assertEqual(w.preview_label.get_text(),'Preparing Fast preview · video.mp4…')
        stream.picture(.1);w.canvas._deliver_frame()
        self.assertTrue(w.canvas.loading)
        stream.buffer(False)
        self.assertFalse(w.canvas.loading);self.assertEqual(w.preview_label.get_text(),'Live preview')
        stream.buffer(True);w.toggle_play()
        stream.buffer(False)
        self.assertFalse(w.canvas.playing);self.assertEqual(w.preview_label.get_text(),'Paused preview')
        w.canvas.seek(2)
        stream.buffer(False);stream.picture(.2);w.canvas._deliver_frame()
        self.assertTrue(w.canvas.loading,'A cancelled generation must not clear current buffering feedback')

    def test_current_error_or_constructor_failure_clears_loading(self):
        w=self.window;w.preview();w.canvas._live.error('Broken preview')
        self.assertFalse(w.canvas.loading);self.assertFalse(w.canvas.playing)
        self.assertEqual(w.preview_label.get_text(),'Preview unavailable')
        self.assertEqual(w.play_button.get_label(),'Play')
        with mock.patch('clipcanvas.player.StreamPlayer',side_effect=RuntimeError('Unavailable decoder')):
            w.preview()
        self.assertFalse(w.canvas.loading);self.assertFalse(w.canvas.playing)
        self.assertEqual(w.canvas._frame_tick_id,0)
        self.assertEqual(w.preview_label.get_text(),'Preview unavailable')
