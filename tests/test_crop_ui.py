"""Mouse cropping changes saved clip placement without changing timeline time."""
import copy
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk

from clipcanvas import engine, model
from clipcanvas.app import ClipCanvasWindow
import test_engine


@unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
class MouseCropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def pump(self, condition, timeout=15):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            if condition():
                return
            time.sleep(.01)
        self.fail('Crop interaction did not finish')

    def window(self, name, width=320, height=180):
        w = ClipCanvasWindow(recovery_dir=self.fixtures.root/name)
        self.addCleanup(self.close, w)
        w.start_project(width, height)
        w.project['clips'] = [model.new_clip(self.fixtures.root/'landscape.mp4')]
        w.changed();w.seek(.5)
        self.pump(lambda: not w.scrub_timer and not w._scrub_settling and w.canvas.frame is not None)
        return w

    @staticmethod
    def close(w):
        w.cleanup();w.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    @staticmethod
    def event(x, y, **extra):
        return SimpleNamespace(button=1, state=0, x=x, y=y, **extra)

    def activate(self, w):
        vx,vy,vw,vh = w.canvas.viewport_rect
        self.assertTrue(w.canvas_press(self.event(vx+vw*.5, vy+vh*.5)))
        self.pump(lambda: w.crop_tools.active and not w.crop_tools.loading)
        self.assertTrue(w.crop_done_button.get_visible())
        self.assertFalse(w.canvas.playing)
        return w.crop_tools.bounds()

    def test_left_click_drag_at_100_percent_and_corner_zoom_are_saved_and_undoable(self):
        w = self.window('mouse-crop-move')
        w.canvas.set_preview_zoom(2)
        before = copy.deepcopy(w.project);history = len(w.history)
        x,y,width,height = self.activate(w)
        self.assertEqual(w.canvas.preview_zoom, 1)
        crop = w.crop_tools
        crop.press(self.event(x+width*.5, y+height*.5))
        for fraction in (.05, .1, .2):
            crop.motion(self.event(x+width*(.5+fraction), y+height*.6))
        self.assertEqual(w.project, before, 'Draft motion is not saved until release')
        self.assertEqual(len(w.history), history)
        self.assertAlmostEqual(w.canvas.offset_x, .2)
        self.assertTrue(crop.release())
        self.assertAlmostEqual(w.project['clips'][0]['offset_x'], .2)
        self.assertAlmostEqual(w.project['clips'][0]['offset_y'], .1)
        self.assertEqual(w.project['clips'][0]['zoom'], 1)
        self.assertEqual(len(w.history), history+1)
        self.assertAlmostEqual(w.playhead, .5)
        crop.press(self.event(x+width-5, y+height-5))
        crop.motion(self.event(x+width*1.4-5, y+height*1.4-5));crop.release()
        self.assertGreater(w.project['clips'][0]['zoom'], 1.3)
        self.assertEqual(len(w.history), history+2)
        saved = copy.deepcopy(w.project)
        w.crop_done_button.clicked()
        self.pump(lambda: not w._scrub_settling and not w.scrub_timer)
        self.assertFalse(crop.active)
        self.assertEqual(w.canvas.preview_zoom, 2)
        self.assertAlmostEqual(w.playhead, .5)
        expected = engine.preview_frame(w.project, .5, preview_edge=0)
        self.assertEqual((w.canvas.frame.get_width(),w.canvas.frame.get_height()), (expected['width'],expected['height']))
        w.undo();self.assertEqual(w.project['clips'][0]['zoom'], 1)
        self.assertAlmostEqual(w.playhead,.5)
        self.assertAlmostEqual(w.project['clips'][0]['offset_x'], .2)
        w.undo();self.assertEqual(w.project,before)
        self.assertAlmostEqual(w.playhead,.5)
        w.redo();w.redo();self.assertEqual(w.project,saved)
        self.assertAlmostEqual(w.playhead,.5)
        path = self.fixtures.root/'mouse-crop.clipcanvas'
        w.project_path = str(path);self.assertTrue(w.save())
        w.open_project(str(path));self.assertEqual(w.project,saved)

    def test_cancel_draft_noop_click_and_seek_during_source_load_preserve_project(self):
        w = self.window('mouse-crop-cancel')
        before = copy.deepcopy(w.project);history = len(w.history)
        x,y,width,height = self.activate(w)
        crop=w.crop_tools
        crop.press(self.event(x+width*.5,y+height*.5));crop.release()
        self.assertEqual(len(w.history),history)
        crop.press(self.event(x+width*.5,y+height*.5))
        crop.motion(self.event(x+width*.8,y+height*.5))
        self.assertAlmostEqual(w.canvas.offset_x,.3)
        w.keypress(w,SimpleNamespace(state=0,keyval=Gdk.KEY_Escape))
        self.pump(lambda:not w._scrub_settling and not w.scrub_timer)
        self.assertFalse(crop.active);self.assertEqual(w.project,before)
        self.assertEqual(len(w.history),history)
        queued=[]
        with mock.patch.object(w.pool,'submit',side_effect=lambda run:queued.append(run)):
            self.assertTrue(crop.start())
        self.assertTrue(crop.loading)
        w.seek(.8)
        self.assertFalse(crop.active)
        queued[0]()
        self.pump(lambda:not w._scrub_settling and not w.scrub_timer)
        self.assertEqual(w.project,before)
        self.assertAlmostEqual(w.playhead,.8)
        self.assertTrue(w.canvas.composited,'A late crop image must not replace a newer composed seek')

    def test_visible_overlay_wins_click_and_hidden_workspace_does_not_start_crop(self):
        w = self.window('mouse-crop-layer',180,320)
        layer=model.new_text('Keep overlay',0,1);layer.update(width=.6,height=.3)
        w.project['layers']=[layer];w.changed();w.seek(.5)
        self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
        vx,vy,vw,vh=w.canvas.viewport_rect
        self.assertFalse(w.canvas_press(self.event(vx-15,vy+vh/2)))
        self.assertFalse(w.crop_tools.active)
        self.assertTrue(w.canvas_press(self.event(vx+vw/2,vy+vh/2)))
        self.assertEqual(w.selected,('layers',0))
        self.assertFalse(w.crop_tools.active)
        w.layer_tools.canvas_release(self.event(vx+vw/2,vy+vh/2))
        self.assertTrue(w.canvas_press(self.event(vx+vw*.1,vy+vh*.1)))
        self.pump(lambda:w.crop_tools.active and not w.crop_tools.loading)
        self.assertEqual(w.selected,('clips',0))
        w.crop_tools.finish()

    def test_click_during_playback_uses_active_clip_and_save_finishes_crop(self):
        w=self.window('mouse-crop-playback')
        w.project['clips'].append(model.new_clip(self.fixtures.root/'portrait.mp4'))
        w.changed();w.select('clips',0,seek=False)
        w.seek(1.8)
        self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
        w.toggle_play()
        self.pump(lambda:w.canvas.playing and w.canvas.live and w.canvas.position()>1.8)
        x,y,width,height=self.activate(w)
        self.assertEqual(w.selected,('clips',1))
        self.assertFalse(w.canvas.live)
        at=w.playhead;first=copy.deepcopy(w.project['clips'][0])
        crop=w.crop_tools
        crop.press(self.event(x+width/2,y+height/2))
        crop.motion(self.event(x+width*.6,y+height/2));crop.release()
        w.project_path=str(self.fixtures.root/'mouse-crop-active-save.clipcanvas')
        self.assertTrue(w.save());self.assertFalse(crop.active)
        saved=model.load_project(w.project_path)
        self.assertEqual(saved['clips'][0],first)
        self.assertAlmostEqual(saved['clips'][1]['offset_x'],.1)
        self.assertAlmostEqual(w.playhead,at)

    def test_play_from_crop_skips_redundant_composed_seek_and_keeps_saved_position(self):
        w=self.window('mouse-crop-preview-memory')
        x,y,width,height=self.activate(w)
        crop=w.crop_tools
        crop.press(self.event(x+width/2,y+height/2))
        crop.motion(self.event(x+width*.65,y+height/2));crop.release()
        at=w.playhead
        with mock.patch.object(w,'seek',side_effect=AssertionError('Play must not also request a paused composed frame')):
            w.toggle_play()
        self.assertFalse(crop.active)
        self.assertAlmostEqual(w.project['clips'][0]['offset_x'],.15)
        self.pump(lambda:w.canvas.live and w.canvas.playing and w.canvas.position()>at)

    def test_large_photo_crop_source_is_bounded_before_grading(self):
        import cairo
        w=self.window('mouse-crop-photo-memory')
        photo=self.fixtures.root/'large-crop-photo.png'
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,2560,1440)
        cr=cairo.Context(surface);cr.set_source_rgb(.2,.4,.6);cr.paint();surface.write_to_png(str(photo))
        surface.finish()
        w.project['clips']=[model.new_image(photo,.8)]
        w.changed();w.seek(.2)
        self.pump(lambda:not w.scrub_timer and not w._scrub_settling)
        original=copy.deepcopy(w.project)
        self.activate(w)
        picture=w.crop_tools.picture
        self.assertLessEqual(max(picture.get_width(),picture.get_height()),1920)
        self.assertAlmostEqual(picture.get_width()/picture.get_height(),2560/1440,places=2)
        self.assertEqual(w.project,original)
