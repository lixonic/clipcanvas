"""Animation changes retain real audio and the previous sprite appearance."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from clipcanvas import animations, audio_effects, element_render, engine, model, sound_effects


def reference_sprite(filters,source,output,item,width,height,fps,length,scratch,
                     blurred=None,cues=None,project=None,cancel=None):
    """Former per-pixel compositor, used only as a decoded-picture reference."""
    values = engine._cue_expressions(dict(project,captions=cues)) if cues is not None else animations.expressions(item)
    placement = animations.placement_expressions(item)
    current = source
    if blurred is not None:
        weight = engine._expression_time(values['blur'],'T')
        current = output+'oldsoft'
        filters.append(f"[{source}][{blurred}]blend=all_expr='A*(1-({weight}))+B*({weight})'[{current}]")
    alpha = engine._expression_time(f'({values["opacity"]})*({placement["opacity"]})','T')
    if values['wipe'] != '1':
        progress = engine._expression_time(values['wipe'],'T')
        mask = engine._expression_time(values['mask'],'T') if 'mask' in values else engine._wipe_mask(item,progress)
        alpha += f'*({mask})'
    filters.append(f"[{current}]format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({alpha})'[{output}]")
    return output


class AnimatedAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='clipcanvas-element-tests-')
        cls.root=Path(cls.folder.name);cls.ffmpeg,cls.env=engine._tool('ffmpeg')
        cls.voice=cls.root/'voice.wav'
        subprocess.run([cls.ffmpeg,'-v','error','-f','lavfi','-i','sine=f=480:r=48000:d=3',
                        '-c:a','pcm_s16le',str(cls.voice)],env=cls.env,check=True)
        cls.cue=sound_effects.generate('pop',cls.root/'cue.wav')

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def project(self,group):
        p=model.new_project(160,90);p['fps']=24;p['clips']=[model.new_card('#19384a',3)]
        track=model.new_audio(self.voice);track.update(volume=.2,fade_in=.1)
        track['audio_effects']=audio_effects.preset('Echo');p['audio']=[track]
        item=model.new_text('Editable മലയാളം',.27,2.73) if group=='layers' else model.new_caption('Editable മലയാളം',.27,2.73)
        item['sound_effect'].update(preset='file',path=str(self.cue),volume=.3,trigger='both')
        if group=='layers':item.update(font_size=18,opacity=.6,rotation=9)
        else:p['caption_style'].update(font_size=14,opacity=.6,height=.3)
        p[group]=[item];return p

    def stream(self,p,name,start=0):
        path=self.root/name
        with path.open('wb') as output:
            for packet in engine.live_stream(p,start,preview_edge=0):output.write(packet)
        return path

    def decode(self,path,pictures=False):
        options=['-an','-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo'] if pictures else [
                 '-vn','-c:a','pcm_s16le','-ar','48000','-ac','2','-f','s16le']
        return subprocess.run([self.ffmpeg,'-v','error','-i',str(path),*options,'-threads','1','pipe:1'],
                              env=self.env,capture_output=True,check=True).stdout

    def test_all_animations_keep_soundtrack_attached_file_seek_and_reopen(self):
        for group in ('layers','captions'):
            p=self.project(group);plain=self.decode(self.stream(p,f'{group}-plain.mkv'))
            plain_seek=self.decode(self.stream(p,f'{group}-plain-seek.mkv',2.25))
            self.assertTrue(any(plain));sound=copy.deepcopy(p[group][0]['sound_effect'])
            audio=copy.deepcopy(p['audio'])
            for effect in animations.GENERAL[1:]+animations.TEXT[1:]:
                with self.subTest(group=group,effect=effect):
                    config=model.animation_defaults()
                    if effect in animations.GENERAL:config.update(enter=effect,exit=effect,duration=.4)
                    else:config['text_effect']=effect
                    p[group][0]['animation']=config
                    before=copy.deepcopy(p)
                    full=self.stream(p,f'{group}-{effect}.mkv')
                    self.assertEqual(self.decode(full),plain)
                    seek=self.stream(p,f'{group}-{effect}-seek.mkv',2.25)
                    self.assertEqual(self.decode(seek),plain_seek)
                    reopened=model.load_project(model.save_project(p,self.root/'edit.clipcanvas'))
                    self.assertEqual(reopened[group][0]['sound_effect'],sound)
                    self.assertEqual(reopened[group][0]['animation'],config)
                    self.assertEqual(reopened['audio'],audio)
                    self.assertEqual(p,before)

    def test_native_opacity_wipes_blur_and_mixed_captions_match_previous_frames(self):
        cases=[('layers','rise','left'),('layers','pop','right'),('layers','blur','left'),
               *[('layers','wipe',direction) for direction in animations.DIRECTIONS],
               ('captions','wipe','up'),('captions','blur','left')]
        for group,effect,direction in cases:
            with self.subTest(group=group,effect=effect,direction=direction):
                p=self.project(group)
                p[group][0]['animation'].update(enter=effect,exit=effect,direction=direction,text_effect='highlight')
                if group=='captions':
                    p[group][0]['end']=1.2
                    p['captions'].append(model.new_caption('Next words',1.4,2.7))
                    p['captions'][1]['animation'].update(enter='pop',exit='fade',text_effect='snake')
                native=self.stream(p,'native-look.mkv')
                with mock.patch.object(element_render,'append',side_effect=reference_sprite):
                    reference=self.stream(p,'reference-look.mkv')
                actual,expected=self.decode(native,True),self.decode(reference,True)
                self.assertEqual(len(actual),len(expected))
                size=160*90*3
                for at in range(0,len(actual),size):
                    error=sum(abs(a-b) for a,b in zip(actual[at:at+size],expected[at:at+size]))/size
                    self.assertLess(error,1.5,f'Animation frame {at//size} differs: {error}')
                self.assertEqual(self.decode(native),self.decode(reference))
