"""Bundled Malayalam text must shape and export without system font installs."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import cairo
from clipcanvas import bundled_fonts, engine, model, visuals
from gi.repository import Pango, PangoCairo


class MalayalamFontTests(unittest.TestCase):
    def test_bundled_sources_licenses_and_real_glyph_coverage(self):
        root=Path(bundled_fonts.__file__).parent/'fonts'
        manifest=json.loads((root/'sources.json').read_text())
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
        text='മലയാളം അടിക്കുറിപ്പുകൾ കേരളം കൃഷ്ണൻ നമസ്കാരം ൻ ൽ ൾ ർ ൺ ൿ'
        surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,1280,200);cr=cairo.Context(surface)
        for family,_ in bundled_fonts.MALAYALAM_FONTS:
            with self.subTest(family=family):
                for bold in (False,True):
                    style=model.text_style();style.update(text=text,font_family=family,bold=bold)
                    layout=visuals._layout(cr,{},style,1200)
                    self.assertEqual(layout.get_unknown_glyphs_count(),0)
                    font=layout.get_context().load_font(layout.get_font_description())
                    self.assertEqual(font.describe().get_family(),family)
                    self.assertTrue(font.has_char('മ'))
                    self.assertGreater(layout.get_pixel_size()[0],100)

    def test_malayalam_preview_and_video_export_match_for_each_font(self):
        with tempfile.TemporaryDirectory(prefix='clipcanvas-ml-fonts-') as d:
            root=Path(d);binary,env=engine._tool('ffmpeg');frames=[]
            for index,(family,_) in enumerate(bundled_fonts.MALAYALAM_FONTS):
                p=model.new_project(640,360);p['clips']=[model.new_card('#182238',1)]
                p['captions']=[model.new_caption('മലയാളം അടിക്കുറിപ്പുകൾ',.1,.9)]
                p['caption_style'].update(font_family=family,font_size=34,bold=True)
                original=copy.deepcopy(p);saved=root/'project.clipcanvas';model.save_project(p,saved)
                self.assertEqual(model.load_project(saved),p)
                preview=engine.preview_frame(p,.3,preview_edge=0)['pixels']
                output=root/f'font-{index}.mkv';engine.render(p,output,quality='lossless')
                exported=subprocess.check_output([binary,'-v','error','-i',str(output),'-ss','0.3',
                    '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],env=env)
                self.assertEqual(preview,exported)
                plain=copy.deepcopy(p);plain['captions']=[]
                self.assertNotEqual(preview,engine.preview_frame(plain,.3,preview_edge=0)['pixels'])
                self.assertEqual(p,original);frames.append(preview)
            self.assertEqual(len(set(frames)),3,'Each font must produce its own rendered glyphs')
