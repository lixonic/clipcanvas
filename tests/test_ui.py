"""Native desktop integration test; skipped when no display is available."""
import copy
import json
import time
import unittest
from types import SimpleNamespace

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from clipcanvas.app import ClipCanvasWindow, CSS
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


if __name__ == '__main__':
    unittest.main()
