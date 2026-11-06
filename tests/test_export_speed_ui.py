"""Native export choices, cancellation and remembered desktop settings."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from clipcanvas import model
from clipcanvas.app import ClipCanvasWindow


@unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
class ExportSpeedUITests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='clipcanvas-speed-ui-')
        self.root = Path(self.scratch.name)
        self.window = ClipCanvasWindow(recovery_dir=self.root/'recovery')
        self.window.project['clips'] = [model.new_card('#123456', 1)]

    def tearDown(self):
        self.window.cleanup()
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.scratch.cleanup()

    def choose_export(self, inspect, response=Gtk.ResponseType.OK, save_response=Gtk.ResponseType.ACCEPT):
        chooser = mock.Mock()
        chooser.run.return_value = save_response
        chooser.get_filename.return_value = str(self.root/'finished.mp4')
        def run(dialog):
            inspect(self.window)
            return response
        with mock.patch.object(Gtk.Dialog, 'run', run), \
                mock.patch('clipcanvas.app.file_chooser', return_value=chooser), \
                mock.patch.object(self.window, 'render_job') as render:
            self.window.export()
        return render

    def test_fast_default_and_selected_speed_reaches_export_and_survives_reopen(self):
        def choose(w):
            self.assertEqual(w.export_quality_combo.get_active_id(), 'master')
            self.assertEqual(w.export_speed_combo.get_active_id(), 'fast')
            self.assertEqual(w.export_encoding_combo.get_active_id(), 'software')
            w.export_quality_combo.set_active_id('high')
            w.export_speed_combo.set_active_id('balanced')
        render = self.choose_export(choose)
        self.assertEqual(render.call_args.kwargs, {'quality': 'high', 'speed': 'balanced', 'encoding': 'software'})
        self.assertEqual(self.window.preferences.get('export_speed'), 'balanced')
        reopened = ClipCanvasWindow(recovery_dir=self.root/'recovery')
        try:
            self.assertEqual(reopened.preferences.get('export_speed'), 'balanced')
        finally:
            reopened.cleanup();reopened.destroy()
        self.choose_export(lambda w: self.assertEqual(w.export_speed_combo.get_active_id(), 'balanced'),
                           response=Gtk.ResponseType.CANCEL)

    def test_lossless_disables_speed_then_restores_it_and_keeps_mp4_preference(self):
        self.window.preferences.set('export_speed', 'balanced')
        def choose(w):
            w.export_speed_combo.set_active_id('compact')
            w.export_quality_combo.set_active_id('lossless')
            self.assertFalse(w.export_speed_combo.get_sensitive())
            w.export_quality_combo.set_active_id('master')
            self.assertTrue(w.export_speed_combo.get_sensitive())
            self.assertEqual(w.export_speed_combo.get_active_id(), 'compact')
            w.export_quality_combo.set_active_id('lossless')
        render = self.choose_export(choose)
        self.assertEqual(render.call_args.kwargs['quality'], 'lossless')
        self.assertEqual(self.window.preferences.get('export_speed'), 'balanced')

    def test_cancelling_either_dialog_keeps_preference_and_does_not_render(self):
        self.window.preferences.set('export_speed', 'balanced')
        choose = lambda w: w.export_speed_combo.set_active_id('fast')
        self.choose_export(choose, response=Gtk.ResponseType.CANCEL).assert_not_called()
        self.choose_export(choose, save_response=Gtk.ResponseType.CANCEL).assert_not_called()
        self.assertEqual(self.window.preferences.get('export_speed'), 'balanced')

    def test_invalid_saved_speed_falls_back_to_fast(self):
        self.window.preferences.set('export_speed', ['bad setting'])
        render = self.choose_export(lambda w: self.assertEqual(w.export_speed_combo.get_active_id(), 'fast'))
        self.assertEqual(render.call_args.kwargs['speed'], 'fast')

    def test_unwritable_preferences_do_not_prevent_export(self):
        with mock.patch.object(self.window.preferences, 'set', side_effect=OSError('Read-only config')):
            render = self.choose_export(lambda w: w.export_speed_combo.set_active_id('compact'))
        self.assertEqual(render.call_args.kwargs['speed'], 'compact')

    def test_encoding_is_remembered_and_lossless_does_not_replace_it(self):
        render = self.choose_export(lambda w: w.export_encoding_combo.set_active_id('auto'))
        self.assertEqual(render.call_args.kwargs['encoding'], 'auto')
        self.assertEqual(self.window.preferences.get('export_encoding'), 'auto')
        def choose_master(w):
            self.assertEqual(w.export_encoding_combo.get_active_id(), 'auto')
            w.export_encoding_combo.set_active_id('software')
            w.export_quality_combo.set_active_id('lossless')
            self.assertFalse(w.export_encoding_combo.get_sensitive())
            w.export_quality_combo.set_active_id('master')
            self.assertTrue(w.export_encoding_combo.get_sensitive())
            self.assertEqual(w.export_encoding_combo.get_active_id(), 'software')
            w.export_quality_combo.set_active_id('lossless')
        self.choose_export(choose_master)
        self.assertEqual(self.window.preferences.get('export_encoding'), 'auto')

    def test_invalid_encoding_defaults_to_software_and_cancellation_keeps_saved_choice(self):
        self.window.preferences.set('export_encoding', {'invalid': True})
        self.choose_export(lambda w: self.assertEqual(w.export_encoding_combo.get_active_id(), 'software'))
        self.window.preferences.set('export_encoding', 'software')
        choose = lambda w: w.export_encoding_combo.set_active_id('auto')
        self.choose_export(choose, response=Gtk.ResponseType.CANCEL).assert_not_called()
        self.choose_export(choose, save_response=Gtk.ResponseType.CANCEL).assert_not_called()
        self.assertEqual(self.window.preferences.get('export_encoding'), 'software')
