"""Native desktop integration test; skipped when no display is available."""
import copy
import json
import time
import unittest
from types import SimpleNamespace

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

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


if __name__ == '__main__':
    unittest.main()
