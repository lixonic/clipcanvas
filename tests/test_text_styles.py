"""Text presets preserve editable content and migrate older style records."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from clipcanvas import model, text_styles


class TextStyleTests(unittest.TestCase):
    def test_four_presets_preserve_content_geometry_font_and_timing(self):
        item=model.new_text('Editable — مرحبا',1.2,4.5)
        item.update(font_family='DejaVu Sans',font_size=32,x=.2,y=.8,width=.4,rotation=16,opacity=.7,align='left')
        original=copy.deepcopy(item)
        self.assertEqual(len(text_styles.PRESETS),4)
        for key,_,_ in text_styles.PRESETS:
            style=text_styles.apply_preset(item,key)
            for field in ('id','text','start','end','font_family','font_size','x','y','width','height','rotation','opacity','align'):
                self.assertEqual(style[field],item[field])
            p=model.new_project();p['layers']=[style];p['caption_style']=text_styles.apply_preset(p['caption_style'],key)
            self.assertTrue(model.validate_project(p,check_files=False))
            style['text']='Corrected after styling'
            self.assertEqual(item,original)
        with self.assertRaises(ValueError):text_styles.apply_preset(item,'unknown')

    def test_styles_roundtrip_and_old_format3_keeps_its_appearance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=model.new_project();p['layers']=[text_styles.apply_preset(model.new_text('Title'),'shadow')]
            p['captions']=[model.new_caption('Editable caption',0,2)]
            p['caption_style']=text_styles.apply_preset(p['caption_style'],'highlight')
            path=root/'styles.clipcanvas';model.save_project(p,path)
            self.assertEqual(model.load_project(path),p)
            old=copy.deepcopy(p)
            for style in (old['layers'][0],old['caption_style']):
                for key in text_styles.effect_defaults():style.pop(key,None)
            path.write_text(json.dumps(old));loaded=model.load_project(path)
            for before,after in zip((old['layers'][0],old['caption_style']),(loaded['layers'][0],loaded['caption_style'])):
                for key in before:self.assertEqual(after[key],before[key])
                self.assertEqual(after['shadow_color'],'#00000000');self.assertEqual(after['background_mode'],'box')

    def test_invalid_shadow_and_highlight_parameters_rejected(self):
        for field,value in (('shadow_color','bad'),('shadow_x',float('nan')),('shadow_y',300),
                            ('shadow_blur',-1),('background_mode','bad'),('background_radius',float('inf')),
                            ('background_padding',True)):
            with self.subTest(field=field):
                p=model.new_project();p['layers']=[model.new_text()];p['layers'][0][field]=value
                with self.assertRaises(ValueError):model.validate_project(p,check_files=False)


if __name__=='__main__':unittest.main()
