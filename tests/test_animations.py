"""Real composed frames for layer entry/exit, word effects and matched movement."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from clipcanvas import animations, engine, model


class AnimationRenderTests(unittest.TestCase):
    def project(self):
        p=model.new_project(160,90);p['clips']=[model.new_card('#122638',2)]
        title=model.new_text('One two three',0,2);title.update(font_size=16,outline_width=0,width=.9,height=.5)
        p['layers']=[title];return p

    def pixels(self,path,at):
        binary,env=engine._tool('ffmpeg')
        return subprocess.run([binary,'-v','error','-i',str(path),'-ss',str(at),'-frames:v','1',
            '-pix_fmt','rgb24','-f','rawvideo','pipe:1'],env=env,capture_output=True,check=True).stdout

    def test_six_general_effects_and_three_word_effects_render_seek_and_preserve_project(self):
        with tempfile.TemporaryDirectory() as directory:
            for effect in animations.GENERAL[1:]+animations.TEXT[1:]:
                with self.subTest(effect=effect):
                    p=self.project();config=p['layers'][0]['animation']
                    if effect in animations.GENERAL:config.update(enter=effect,exit=effect,duration=.5)
                    else:config['text_effect']=effect
                    before=copy.deepcopy(p);output=Path(directory)/(effect+'.mkv')
                    engine.render(p,output,quality='lossless')
                    frames=[]
                    for at in (.1,.8,1.8):
                        picture=engine.preview_frame(p,at,preview_edge=0)['pixels']
                        expected=self.pixels(output,at)
                        self.assertEqual(len(picture),len(expected))
                        self.assertLess(sum(abs(a-b) for a,b in zip(picture,expected))/len(expected),2)
                        frames.append(picture)
                    self.assertNotEqual(frames[0],frames[1]);self.assertEqual(p,before)

    def test_caption_word_motion_and_explicit_match_move_survive_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            p=self.project();p['layers']=[]
            p['captions']=[model.new_caption('Editable മലയാളം caption',0,2)]
            p['caption_style'].update(font_size=14,y=.6,height=.4)
            p['captions'][0]['animation'].update(enter='rise',exit='fade',text_effect='snake')
            output=Path(directory)/'caption.mkv';engine.render(p,output,quality='lossless')
            for at in (.1,.8,1.8):
                actual=engine.preview_frame(p,at)['pixels'];expected=self.pixels(output,at)
                self.assertLess(sum(abs(a-b) for a,b in zip(actual,expected))/len(expected),2)
            p['captions']=[];p['clips']=[model.new_card('#122638',2),model.new_card('#352612',2)]
            p['clips'][0].update(transition='match_move',transition_duration=.5)
            first=model.new_text('Move me',0,2);first.update(match_id='title',font_size=16,outline_width=0,x=.25)
            second=copy.deepcopy(first);second.update(id='incoming',start=1.5,end=3.5,x=.75)
            p['layers']=[first,second];paired=animations.prepare_layers(p)
            self.assertEqual(paired[0]['_match_to']['id'],'incoming')
            self.assertAlmostEqual(animations.placement(paired[0],1.75)['x'],.5)
            before=copy.deepcopy(p);output=Path(directory)/'matched.mkv';engine.render(p,output,quality='lossless')
            for at in (1.6,1.8,2.1):
                actual=engine.preview_frame(p,at)['pixels'];expected=self.pixels(output,at)
                self.assertLess(sum(abs(a-b) for a,b in zip(actual,expected))/len(expected),2)
            self.assertEqual(p,before)
