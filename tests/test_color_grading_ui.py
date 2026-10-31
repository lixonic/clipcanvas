"""Native grading presets, asynchronous A/B preview and one-step group edits."""
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

from clipcanvas import color_grading, model
from clipcanvas.app import ClipCanvasWindow
from clipcanvas.color_grading_ui import ColorGradingDialog


@unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
class ColorGradingUITests(unittest.TestCase):
    def pump(self, condition, timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            while Gtk.events_pending():Gtk.main_iteration_do(False)
            if condition():return
            time.sleep(.015)
        self.fail('Color grading preview did not finish')

    def ready(self, dialog):
        return not dialog.timer and not dialog.rendering and dialog.picture.get_pixbuf() is not None

    def test_presets_reset_bypass_comparison_and_cancel_keep_project_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            window=ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
            window.start_project(320,180)
            window.project['clips']=[model.new_card('#787060',1)]
            window.refresh();window.select('clips',0,seek=False)
            before=copy.deepcopy(window.project)
            dialog=ColorGradingDialog(window,window.project,0,.3)
            try:
                for name in color_grading.PRESET_DESCRIPTIONS:
                    dialog.presets.set_active_id(name)
                    self.pump(lambda:self.ready(dialog))
                    grade,saturation=dialog.read()
                    self.assertTrue(grade['enabled'])
                    self.assertEqual(saturation,color_grading.PRESET_SATURATION[name])
                    self.assertEqual(dialog.presets.get_active_id(),name)
                    self.assertTrue(dialog.look_hint.get_text())
                graded=bytes(dialog.picture.get_pixbuf().get_pixels())
                dialog.before.set_active(True);self.pump(lambda:self.ready(dialog))
                original=bytes(dialog.picture.get_pixbuf().get_pixels())
                self.assertNotEqual(graded,original)
                dialog.before.set_active(False);dialog.enabled.set_active(False)
                self.pump(lambda:self.ready(dialog))
                self.assertEqual(bytes(dialog.picture.get_pixbuf().get_pixels()),original)
                dialog.load(color_grading.defaults(),1);self.pump(lambda:self.ready(dialog))
                self.assertEqual(dialog.read(),(color_grading.defaults(),1))
                self.assertEqual(window.project,before)
                dialog.response(Gtk.ResponseType.CANCEL)
                self.assertTrue(dialog.closed)
                self.assertEqual(window.project,before)
            finally:
                dialog.destroy();window.cleanup();window.destroy()

    def test_group_apply_undo_redo_save_and_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            window=ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
            window.start_project(320,180)
            window.project['clips']=[model.new_card('#787060',1),model.new_card('#607080',1)]
            window.project['layers']=[model.new_text('Title',0,1)]
            window.refresh();window.select('clips',0,seek=False)
            window.selection={('clips',clip['id']) for clip in window.project['clips']}
            window.selection.add(('layers',window.project['layers'][0]['id']))
            before=copy.deepcopy(window.project);history=len(window.history)
            try:
                self.assertTrue(window.color_button.get_sensitive())
                menu=window.context_menu('clips',0);menu.popdown()
                self.assertIn('Color grading…',[action.get_label() for action in menu.get_children()])
                # Restore the multi-selection after opening the single-item context menu.
                window.selection={('clips',clip['id']) for clip in window.project['clips']}
                def apply(dialog):
                    dialog.presets.set_active_id('Horror')
                    self.assertTrue(dialog.apply_selection.get_visible())
                    dialog.apply_selection.set_active(True)
                    self.pump(lambda:self.ready(dialog))
                    return Gtk.ResponseType.OK
                with mock.patch.object(ColorGradingDialog,'run',apply):window.edit_color_grading()
                self.assertEqual(len(window.history),history+1)
                for clip in window.project['clips']:
                    self.assertEqual(clip['saturation'],.65)
                    self.assertEqual(clip['color_grade']['temperature'],-.3)
                self.assertEqual(window.project['layers'],before['layers'])
                graded=copy.deepcopy(window.project)
                window.undo();self.assertEqual(window.project,before)
                window.redo();self.assertEqual(window.project,graded)
                path=Path(directory)/'grade.clipcanvas';model.save_project(window.project,path)
                self.assertEqual(model.load_project(path),graded)
                window.select('clips',0,seek=False);history=len(window.history)
                with mock.patch.object(ColorGradingDialog,'run',lambda _:Gtk.ResponseType.OK):window.edit_color_grading()
                self.assertEqual(len(window.history),history)
                def cancel(dialog):
                    dialog.presets.set_active_id('Romantic')
                    return Gtk.ResponseType.CANCEL
                with mock.patch.object(ColorGradingDialog,'run',cancel):window.edit_color_grading()
                self.assertEqual(window.project,graded)
                self.assertEqual(len(window.history),history)
            finally:
                if getattr(window,'active_context_menu',None):window.active_context_menu.popdown()
                window.cleanup();window.destroy()
                while Gtk.events_pending():Gtk.main_iteration_do(False)


if __name__=='__main__':unittest.main()
