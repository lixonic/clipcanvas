"""Effects are portable project data and must never rewrite source media."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from clipcanvas import model,storage


class EffectsProjectTests(unittest.TestCase):
    def test_effect_settings_roundtrip_collect_recovery_and_old_project_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sound=root/'accent.wav';sound.write_bytes(b'local sound fixture')
            p=model.new_project(320,180);p['clips']=[model.new_card('#123456',4),model.new_card('#345678',4)]
            p['clips'][0].update(transition='colour_wipe',transition_duration=.6,transition_direction='up',transition_colour='#cc5533')
            layer=model.new_text('Hello — തുടരൂ...',.4,3.6)
            layer.update(match_id='title');layer['animation'].update(enter='pop',exit='blur',text_effect='highlight',colour='#aa33ee')
            layer['sound_effect'].update(preset='file',path=str(sound),volume=.35,trigger='both')
            p['layers']=[layer];p['captions']=[model.new_caption('Edit these words',1,3)]
            p['captions'][0]['animation']['text_effect']='snake'
            before=copy.deepcopy(p);saved=root/'story.clipcanvas';model.save_project(p,saved)
            self.assertEqual(model.load_project(saved),p);self.assertEqual(p,before)
            data=json.loads(saved.read_text());self.assertEqual(data['layers'][0]['sound_effect']['path'],'accent.wav')
            with self.assertRaisesRegex(ValueError,'original media'):model.save_project(p,sound)
            recovery=storage.Recovery(root/'recovery');recovery.write(p,saved)
            restored,original=recovery.read(recovery.path);self.assertEqual(restored,p);self.assertEqual(original,str(saved))
            portable=storage.collect_project(p,root/'portable');sound.unlink();loaded=model.load_project(portable)
            self.assertTrue(model.validate_project(loaded));self.assertEqual(Path(loaded['layers'][0]['sound_effect']['path']).read_bytes(),b'local sound fixture')
            old=copy.deepcopy(before);old['version']=6
            for clip in old['clips']:
                clip.pop('transition_direction');clip.pop('transition_colour');clip['transition']='fade'
            for item in old['layers']+old['captions']:
                item.pop('animation');item.pop('sound_effect');item.pop('match_id',None)
            old['caption_style'].pop('animation');saved.write_text(json.dumps(old))
            migrated=model.load_project(saved);self.assertEqual(migrated['version'],7)
            self.assertEqual(migrated['layers'][0]['animation'],model.animation_defaults())
            self.assertEqual(migrated['layers'][0]['sound_effect']['preset'],'none')
            self.assertEqual(migrated['clips'][0]['transition'],'fade')
            self.assertEqual(migrated['audio'],old['audio']);self.assertEqual(migrated['layers'][0]['text'],layer['text'])

    def test_invalid_effect_data_rejected_and_defaults_do_not_leak(self):
        p=model.new_project(320,180);p['clips']=[model.new_card()];p['layers']=[model.new_text()]
        cases=[('enter','eval(x)'),('exit','zoom'),('duration',float('nan')),('duration',True),('direction','diagonal'),('colour','red'),('text_effect','arbitrary')]
        for key,value in cases:
            bad=copy.deepcopy(p);bad['layers'][0]['animation'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):model.validate_project(bad,check_files=False)
        for key,value in [('preset','remote'),('volume',-1),('volume',True),('trigger','loop')]:
            bad=copy.deepcopy(p);bad['layers'][0]['sound_effect'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):model.validate_project(bad,check_files=False)
        for key,value in [('transition_direction','diagonal'),('transition_colour','ff00ff')]:
            bad=copy.deepcopy(p);bad['clips'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):model.validate_project(bad,check_files=False)
        p['layers'][0]['animation']['enter']='rise';p['layers'][0]['sound_effect']['volume']=0
        self.assertEqual(model.new_text()['animation']['enter'],'none');self.assertEqual(model.new_caption()['sound_effect']['volume'],.5)
        visual=model.new_visual('/tmp/image.png');visual['animation']['text_effect']='reveal';p['layers']=[visual]
        with self.assertRaisesRegex(ValueError,'text animation'):model.validate_project(p,check_files=False)
