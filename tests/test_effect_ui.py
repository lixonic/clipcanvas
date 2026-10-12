"""Native effect choices and one-step layer editing, without remote media."""
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from clipcanvas import model, transitions, sound_effects, engine
from clipcanvas.app import ClipCanvasWindow
from clipcanvas.effect_ui import AnimationEditor, TransitionEditor


@unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
class NativeEffectTests(unittest.TestCase):
    def finish(self, window):
        window.cleanup(); window.destroy()
        while Gtk.events_pending(): Gtk.main_iteration_do(False)

    def pump(self, condition):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            while Gtk.events_pending():Gtk.main_iteration_do(False)
            if condition():return
            time.sleep(.01)
        self.fail('Native effect workflow did not finish')

    def test_transition_tiles_direction_colour_duration_and_legacy_preservation(self):
        host = Gtk.Window(); host.get_style_context().add_class('clipcanvas')
        editor = TransitionEditor({'transition': 'none', 'transition_duration': .25})
        host.add(editor); host.show_all()
        try:
            self.assertEqual(list(editor.tiles.buttons), list(transitions.KEYS))
            self.assertFalse(editor.direction_row.get_visible())
            self.assertFalse(editor.colour_row.get_visible())
            self.assertFalse(editor.duration.get_sensitive())
            editor.tiles.buttons['line_wipe'].clicked()
            self.assertTrue(editor.direction_row.get_visible())
            self.assertTrue(editor.colour_row.get_visible())
            self.assertTrue(editor.tiles.buttons['line_wipe'].get_style_context().has_class('selected'))
            self.assertFalse(editor.tiles.buttons['none'].get_style_context().has_class('selected'))
            editor.duration.set_value(.75); editor.direction.set_active_id('down')
            colour = Gdk.RGBA(); colour.parse('#ee3355'); editor.colour.set_rgba(colour)
            self.assertEqual(editor.read_into({}), {'transition': 'line_wipe', 'transition_duration': .75,
                                                  'transition_direction': 'down', 'transition_colour': '#ee3355'})
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 90, 64)
            for key, button in editor.tiles.buttons.items():
                drawing = button.get_child().get_children()[0]
                editor.tiles.draw_icon(drawing, cairo.Context(surface), key)
            legacy = TransitionEditor({'transition': 'wipeleft', 'transition_duration': .3})
            self.assertEqual(legacy.read_into({})['transition'], 'wipeleft')
            legacy.destroy()
        finally:
            host.destroy()

    def test_animation_pickers_clear_and_match_key(self):
        editor = AnimationEditor({'enter': 'rise', 'exit': 'fade', 'duration': .3,
                                  'text_effect': 'highlight', 'colour': '#ffbb22'}, text=True, match_id='title')
        try:
            self.assertEqual(editor.match_id.get_text(), 'title')
            editor.exit.set_active_id('pop'); editor.direction.set_active_id('left')
            self.assertEqual(editor.read()['exit'], 'pop')
            self.assertEqual(editor.read()['direction'], 'left')
            self.assertEqual(editor.read()['text_effect'], 'highlight')
            self.assertEqual(editor.read()['colour'], '#ffbb22')
            editor.clear()
            self.assertEqual([editor.read()[key] for key in ('enter', 'exit', 'text_effect')], ['none']*3)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 90, 64)
            for picker in (editor.enter, editor.text_effect):
                for key, button in picker.buttons.items():
                    picker.draw_icon(button.get_child().get_children()[0], cairo.Context(surface), key)
        finally:
            editor.destroy()

    def test_text_and_caption_effects_apply_with_editable_words_and_one_undo(self):
        with tempfile.TemporaryDirectory() as directory:
            window = ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
            try:
                window.start_project(320, 180)
                window.project['clips'] = [model.new_card('#334455', 5)]
                window.project['layers'] = [model.new_text('Title', 0, 3)]
                window.project['captions'] = [model.new_caption('Words', 1, 4)]
                for kind in ('layers', 'captions'):
                    window.select(kind, 0, seek=False)
                    self.assertIn('Animate', window.property_pages)
                    self.assertIn('Sound', window.property_pages)
                    self.assertTrue(window.sound_button.get_sensitive())
                    tools = window.layer_tools; tools.text.get_buffer().set_text('Corrected ' + kind)
                    tools.animation_editor.enter.set_active_id('pan')
                    tools.animation_editor.exit.set_active_id('blur')
                    tools.animation_editor.text_effect.set_active_id('reveal')
                    window.open_properties('Animate')
                    self.assertEqual(window.properties_stack.get_visible_child_name(),'Animate')
                    if kind=='captions':tools.all_caption_animation.set_active(True)
                    tools.sound_editor.preset.set_active_id('pop'); tools.sound_editor.trigger.set_active_id('both')
                    tools.sound_editor.volume.set_value(35)
                    if kind == 'layers': tools.animation_editor.match_id.set_text('shared-title')
                    before = copy.deepcopy(window.project); history = len(window.history)
                    with mock.patch.object(window, 'seek'):
                        tools.apply()
                    self.assertEqual(len(window.history), history+1)
                    item = window.project[kind][0]
                    self.assertEqual(item['text'], 'Corrected ' + kind)
                    self.assertEqual(item['animation']['enter'], 'pan')
                    self.assertEqual(item['animation']['text_effect'], 'reveal')
                    self.assertEqual(item['sound_effect']['preset'], 'pop')
                    self.assertEqual(item['sound_effect']['trigger'], 'both')
                    self.assertAlmostEqual(item['sound_effect']['volume'], .35)
                    if kind == 'layers': self.assertEqual(item['match_id'], 'shared-title')
                    else:self.assertEqual(window.project['caption_style']['animation'],item['animation'])
                    self.assertEqual(window.history[-1], before)
            finally:
                self.finish(window)

    def test_image_effects_keep_opacity_and_sound_file_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'logo.png'
            cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8).write_to_png(str(path))
            window = ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
            try:
                window.start_project(320, 180); window.project['clips'] = [model.new_card('#111111', 5)]
                item = model.new_visual(path, 'image', 0, 4, {'width': 8, 'height': 8}); item['opacity'] = .4
                window.project['layers'] = [item]; window.select('layers', 0, seek=False)
                tools = window.layer_tools
                self.assertIsNone(tools.animation_editor.text_effect)
                tools.animation_editor.enter.set_active_id('pop'); tools.animation_editor.match_id.set_text('logo')
                tools.sound_editor.preset.set_active_id('file')
                self.assertTrue(tools.sound_editor.file_row.get_visible())
                tools.sound_editor.preset.set_active_id('click')
                self.assertFalse(tools.sound_editor.file_row.get_visible())
                with mock.patch.object(window, 'seek'): tools.apply()
                item = window.project['layers'][0]
                self.assertAlmostEqual(item['opacity'], .4)
                self.assertEqual(item['animation']['enter'], 'pop')
                self.assertEqual(item['match_id'], 'logo')
                self.assertEqual(item['sound_effect']['preset'], 'click')
            finally:
                self.finish(window)

    def test_relink_nested_layer_and_caption_sound_requires_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            window=ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
            replacement=sound_effects.generate('pop',Path(directory)/'replacement.wav')
            missing=str(Path(directory)/'missing.wav');errors=[];window.error=errors.append
            chooser=mock.Mock();chooser.run.return_value=Gtk.ResponseType.ACCEPT
            chooser.get_filename.return_value=str(replacement)
            try:
                window.start_project(320,180);window.project['clips']=[model.new_card('#111111',5)]
                window.project['layers']=[model.new_text('Title',0,3)]
                window.project['captions']=[model.new_caption('Words',1,4)]
                for group in ('layers','captions'):
                    window.project[group][0]['sound_effect'].update(preset='file',path=missing)
                before=copy.deepcopy(window.project);history=len(window.history)
                with mock.patch('clipcanvas.app.file_chooser',return_value=chooser):
                    window.relink_media();self.pump(lambda:not window.busy)
                self.assertFalse(errors);self.assertEqual(len(window.history),history+1)
                for group in ('layers','captions'):
                    self.assertEqual(window.project[group][0]['sound_effect']['path'],str(replacement.resolve()))
                self.assertEqual(window.history[-1],before)
                window.project=copy.deepcopy(before);history=len(window.history)
                with mock.patch('clipcanvas.app.file_chooser',return_value=chooser),mock.patch.object(engine,'probe',return_value={'has_audio':False}):
                    window.relink_media();self.pump(lambda:not window.busy)
                self.assertEqual(window.project,before);self.assertEqual(len(window.history),history)
                self.assertTrue(any('no audio stream' in error for error in errors))
            finally:self.finish(window)
