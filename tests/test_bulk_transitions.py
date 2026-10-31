"""Apply-to-all must change only existing joins, with native undo and rendering."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

from clipcanvas import engine, model, workflow
from clipcanvas.app import ClipCanvasWindow
import test_engine


SETTINGS = {'transition': 'colour_wipe', 'transition_duration': .8,
            'transition_direction': 'up', 'transition_colour': '#ee3355'}


class BulkTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root = cls.fixtures.root
        cls.photo = cls.root/'bulk-photo.png'
        engine.thumbnail(cls.root/'landscape.mp4', cls.photo, at=.3)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def project(self):
        p = model.new_project(320,180)
        p['clips'] = [model.new_clip(self.root/'landscape.mp4'),
                      model.new_image(self.photo,.4), model.new_card('#2266aa',1.2)]
        p['clips'][0].update(saturation=.7,color_grade={'temperature':.2},volume=.3)
        p['clips'][-1].update(transition='stack',transition_duration=.3,transition_direction='right')
        p['audio'] = [model.new_audio(self.root/'music.wav')]
        p['layers'] = [model.new_text('Title',0,1)]
        p['captions'] = [model.new_caption('Words',.2,.8)]
        return p

    def test_mixed_clips_copy_only_join_settings_save_reopen_and_reject_invalid(self):
        p = self.project();before = copy.deepcopy(p)
        actual = workflow.with_all_transitions(p,{**SETTINGS,'volume':0,'saturation':0})
        expected = copy.deepcopy(before)
        for clip in expected['clips'][:-1]:clip.update(SETTINGS)
        self.assertEqual(actual,expected)
        self.assertEqual(p,before)
        self.assertEqual(actual['clips'][-1],before['clips'][-1])
        self.assertEqual([row['overlap'] for row in model.timeline(actual)],[.2,.2,0])
        self.assertEqual(model.load_project(model.save_project(actual,self.root/'all-joins.clipcanvas')),actual)
        for key,value in (('transition','bad'),('transition_duration',float('nan')),
                          ('transition_duration',11),('transition_duration',True),
                          ('transition_direction','bad'),('transition_colour','bad')):
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                workflow.with_all_transitions(p,{**SETTINGS,key:value})
        for clips in ([],[before['clips'][0]]):
            single = model.new_project();single['clips'] = copy.deepcopy(clips)
            self.assertEqual(workflow.with_all_transitions(single,SETTINGS),single)

    def test_every_join_renders_matching_preview_export_and_none_restores_cuts(self):
        p = model.new_project(320,180)
        p['clips'] = [model.new_card(color,1) for color in ('#ff0000','#0000ff','#00ff00')]
        p['audio'] = [model.new_audio(self.root/'music.wav')]
        joined = workflow.with_all_transitions(p,{**SETTINGS,'transition':'fade','transition_duration':.4})
        out = self.root/'all-joins.mkv';engine.render(joined,out,quality='lossless')
        self.assertAlmostEqual(engine.probe(out)['duration'],2.2,delta=.05)
        self.assertTrue(engine.probe(out)['has_audio'])
        for at,channels in ((.8,(0,2)),(1.4,(1,2))):
            preview = engine.preview_frame(joined,at)['pixels']
            pixel = preview[:3]
            self.assertTrue(all(pixel[channel]>60 for channel in channels),pixel)
            exported = subprocess.run([self.fixtures.ffmpeg,'-v','error','-i',str(out),'-ss',str(at),
                '-frames:v','1','-threads','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
                env=self.fixtures.env,capture_output=True,check=True).stdout
            self.assertLess(sum(abs(a-b) for a,b in zip(preview,exported))/len(preview),1)
        cut = workflow.with_all_transitions(joined,{'transition':'none'})
        self.assertEqual(model.project_duration(cut),3)
        self.assertTrue(all(row['overlap']==0 for row in model.timeline(cut)))

    @unittest.skipUnless(Gtk.init_check()[0], 'A desktop display is required')
    def test_native_button_for_video_image_card_and_last_clip_undo_noop_and_page_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            window = ClipCanvasWindow(recovery_dir=Path(directory)/'recovery')
            window.error = self.fail
            window.start_project(320,180)
            window.project = self.project();window.refresh()
            try:
                with mock.patch.object(window,'seek'), mock.patch.object(window,'show_selected_still'):
                    for index in (0,1,2):
                        with self.subTest(index=index):
                            window.select('clips',index,seek=False)
                            window.open_properties('Transition')
                            self.assertTrue(window.apply_all_transitions_button.get_visible())
                            self.assertTrue(window.apply_all_transitions_button.get_sensitive())
                            editor = window.card_transition_editor if index==2 else window.transition_editor
                            self.assertTrue(editor.get_sensitive())
                            editor.tiles.set_active_id('colour_wipe');editor.duration.set_value(.8)
                            editor.direction.set_active_id('up')
                            color = Gdk.RGBA();color.parse('#ee3355');editor.colour.set_rgba(color)
                            # Other pending inspector edits must not be applied by the new button.
                            if index==0:window.fields['volume'][0].set_value(0)
                            before = copy.deepcopy(window.project);history = len(window.history)
                            window.apply_all_transitions_button.clicked()
                            self.assertEqual(window.project,workflow.with_all_transitions(before,SETTINGS))
                            self.assertEqual(len(window.history),history+1)
                            applied = copy.deepcopy(window.project)
                            window.undo();self.assertEqual(window.project,before)
                            window.redo();self.assertEqual(window.project,applied)
                            window.select('clips',0,seek=False);window.open_properties('Transition')
                            history = len(window.history);window.apply_all_transitions_button.clicked()
                            self.assertEqual(len(window.history),history)
                            window.undo()
                    window.select('clips',0,seek=False);window.open_properties('Transition')
                    window.properties_stack.set_visible_child_name('Picture')
                    self.assertFalse(window.apply_all_transitions_button.get_visible())
                    window.select('audio',0,seek=False);window.open_properties('Sound')
                    self.assertFalse(window.apply_all_transitions_button.get_visible())
                    window.select('layers',0,seek=False);window.open_properties('Timing')
                    self.assertFalse(window.apply_all_transitions_button.get_visible())
                    window.project['clips'] = window.project['clips'][:1]
                    window.select('clips',0,seek=False);window.open_properties('Transition')
                    self.assertFalse(window.apply_all_transitions_button.get_sensitive())
                    before = copy.deepcopy(window.project);history = len(window.history)
                    window.apply_all_transitions()
                    self.assertEqual(window.project,before);self.assertEqual(len(window.history),history)
            finally:
                window.cleanup();window.destroy()
                while Gtk.events_pending():Gtk.main_iteration_do(False)


if __name__=='__main__':unittest.main()
