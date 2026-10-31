"""Generated-media checks for primary grades, persistence and render agreement."""
import copy
import hashlib
import json
import subprocess
import unittest

from clipcanvas import color_grading as grading, engine, model, workflow, visuals
from clipcanvas.scrubber import ScrubSession
import test_engine


def mean_rgb(pixels):
    return tuple(sum(pixels[channel::3])/(len(pixels)//3) for channel in range(3))


def difference(a, b):
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)


class ColorGradingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root = cls.fixtures.root
        cls.photo = cls.root/'grading-photo.png'
        engine.thumbnail(cls.root/'landscape.mp4', cls.photo, at=.4)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()

    def project(self):
        p = model.new_project(320, 180)
        p['clips'] = [model.new_clip(self.root/'landscape.mp4')]
        return p

    def pixels(self, path, at=.4):
        return subprocess.run([self.fixtures.ffmpeg, '-v', 'error', '-i', str(path),
            '-ss', str(at), '-frames:v', '1', '-threads', '1', '-pix_fmt', 'rgb24',
            '-f', 'rawvideo', 'pipe:1'], env=self.fixtures.env, capture_output=True, check=True).stdout

    def card_pixels(self, color, **grade):
        p = model.new_project(320, 180)
        p['clips'] = [model.new_card(color, .5)]
        p['clips'][0]['color_grade'] = grade
        return engine.preview_frame(p, .2)['pixels']

    def test_controls_change_the_expected_tones_and_white_balance(self):
        base = mean_rgb(self.card_pixels('#606060'))
        for key in ('exposure', 'gamma'):
            with self.subTest(key=key):
                value = 1 if key == 'exposure' else 1.5
                self.assertGreater(mean_rgb(self.card_pixels('#606060', **{key:value}))[0], base[0]+20)
        dark = mean_rgb(self.card_pixels('#404040'))[0]
        light = mean_rgb(self.card_pixels('#b0b0b0'))[0]
        self.assertLess(mean_rgb(self.card_pixels('#404040', contrast=1.5))[0], dark-15)
        self.assertGreater(mean_rgb(self.card_pixels('#b0b0b0', contrast=1.5))[0], light+15)
        self.assertGreater(mean_rgb(self.card_pixels('#404040', shadows=.6))[0]-dark,
                           mean_rgb(self.card_pixels('#b0b0b0', shadows=.6))[0]-light+10)
        self.assertGreater(light-mean_rgb(self.card_pixels('#b0b0b0', highlights=-.6))[0],
                           dark-mean_rgb(self.card_pixels('#404040', highlights=-.6))[0]+10)
        warm = mean_rgb(self.card_pixels('#606060', temperature=.8))
        cool = mean_rgb(self.card_pixels('#606060', temperature=-.8))
        magenta = mean_rgb(self.card_pixels('#606060', tint=.8))
        self.assertGreater(warm[0], warm[2]+30)
        self.assertGreater(cool[2], cool[0]+30)
        self.assertGreater(magenta[0], magenta[1]+20)

    def test_native_scrub_live_preview_and_lossless_export_agree(self):
        p = self.project()
        source = self.root/'landscape.mp4'
        digest = hashlib.sha256(source.read_bytes()).digest()
        original = engine.preview_frame(p, .4)['pixels']
        p['clips'][0].update(color_grade={**grading.defaults(), 'exposure':.2, 'contrast':1.15,
            'gamma':1.1, 'shadows':.1, 'highlights':-.2, 'temperature':.3, 'tint':-.1}, saturation=1.3)
        graded = engine.preview_frame(p, .4)['pixels']
        self.assertGreater(difference(original, graded), 3)
        session = ScrubSession()
        try:
            native = session.frame(p, .4)['pixels']
            self.assertEqual(session.last_backend, 'native')
            self.assertLess(difference(graded, native), 10)
            out = self.root/'grade.mkv'
            engine.render(p, out, quality='lossless')
            self.assertLess(difference(graded, self.pixels(out)), 1)
            live = self.root/'grade-live.mkv'
            with live.open('wb') as stream:
                for chunk in engine.live_stream(p, .2):stream.write(chunk)
            self.assertLess(difference(engine.preview_frame(p, .6)['pixels'], self.pixels(live, .4)), 3)
            self.assertTrue(engine.probe(live)['has_audio'])
            p['clips'][0]['color_grade']['strength'] = 0
            self.assertEqual(engine.preview_frame(p, .4)['pixels'], original)
            p['clips'][0]['color_grade']['strength'] = .5
            half = engine.preview_frame(p, .4)['pixels']
            self.assertGreater(difference(half,original), .5)
            self.assertLess(difference(half,original), difference(graded,original))
            self.assertLess(difference(half,session.frame(p, .4)['pixels']), 10)
            p['clips'][0]['color_grade']['enabled'] = False
            bypass = engine.preview_frame(p, .4)['pixels']
            self.assertEqual(bypass, original)
            self.assertLess(difference(bypass, session.frame(p, .4)['pixels']), 10)
            self.assertEqual(session.open_count, 1)
        finally:session.close()
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)

    def test_images_cards_transitions_overlays_and_fit_borders(self):
        for scene in (model.new_image(self.photo, .8), model.new_card('#606060', .8)):
            with self.subTest(kind=scene['kind']):
                p = model.new_project(320, 180);p['clips'] = [scene]
                scene['color_grade'] = {'temperature':.8, 'exposure':.3}
                pixels = engine.preview_frame(p, .3)['pixels']
                out = self.root/f"grade-{scene['kind']}.mkv"
                engine.render(p, out, quality='lossless')
                self.assertLess(difference(pixels, self.pixels(out, .3)), 1)
                session = ScrubSession()
                try:self.assertLess(difference(pixels, session.frame(p, .3)['pixels']), 8)
                finally:session.close()
        p['clips'] = [model.new_card('#606060', 1), model.new_card('#606060', 1)]
        p['clips'][0].update(color_grade={'temperature':1}, transition='fade', transition_duration=.4)
        p['clips'][1]['color_grade'] = {'temperature':-1}
        warm = mean_rgb(engine.preview_frame(p, .3)['pixels'])
        cool = mean_rgb(engine.preview_frame(p, 1.2)['pixels'])
        self.assertGreater(warm[0], warm[2]+40)
        self.assertGreater(cool[2], cool[0]+40)
        join = engine.preview_frame(p, .8)['pixels']
        self.assertGreater(difference(join, engine.preview_frame(p, .3)['pixels']), 10)
        p['layers'] = [model.new_text('TITLE', 0, 1.6)]
        p['layers'][0].update(color='#ff0000', font_size=55)
        overlay = engine.preview_frame(p, .3)['pixels']
        self.assertGreater(sum(r>200 and g<50 and b<50 for r,g,b in zip(overlay[::3],overlay[1::3],overlay[2::3])), 100)
        p = self.project();p['width'],p['height'] = 180,320
        p['clips'][0].update(fit='fit', color_grade={'temperature':1, 'shadows':1})
        self.assertLess(max(engine.preview_frame(p, .4)['pixels'][:180*30*3]), 4)

    def test_selection_persistence_split_clipboard_defaults_and_validation(self):
        p = self.project();p['clips'] += [model.new_image(self.photo), model.new_card('#606060')]
        p['audio'] = [model.new_audio(self.root/'music.wav')]
        p['layers'] = [model.new_text('Title', 0, 1)]
        before = copy.deepcopy(p)
        selected = {(group,item['id']) for group in workflow.GROUPS for item in p[group]}
        selected.remove(('clips',p['clips'][2]['id']))
        grade = {**grading.defaults(), **grading.PRESETS['Warm']}
        candidate = workflow.with_color_grade(p, selected, grade, 1.4)
        self.assertEqual(p, before)
        self.assertEqual(candidate['audio'], before['audio'])
        self.assertEqual(candidate['layers'], before['layers'])
        self.assertEqual(candidate['clips'][2], before['clips'][2])
        path = self.root/'grading.clipcanvas';model.save_project(candidate,path)
        self.assertEqual(model.load_project(path), candidate)
        split = model.split_item(candidate,'clips',0,.6)
        self.assertEqual([item['color_grade'] for item in split['clips'][:2]],[grade,grade])
        pasted,_ = workflow.paste_items(candidate,workflow.copy_items(candidate,selected),0,0)
        self.assertEqual(pasted['clips'][0]['color_grade'], grade)
        self.assertEqual(pasted['clips'][0]['saturation'],1.4)
        path.write_text(json.dumps(before))
        loaded = model.load_project(path)
        self.assertTrue(all(not grading.active(clip) for clip in loaded['clips']))
        for value in (None, [], {'enabled':1}, {'temperature':float('nan')}, {'contrast':True},
                      {'gamma':0}, {'exposure':4}, {'unexpected':1}, {'tint':'1'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                invalid=copy.deepcopy(p);invalid['clips'][0]['color_grade']=value
                model.validate_project(invalid,check_files=False)

    def test_thumbnail_alpha_padding_and_shared_source_are_preserved(self):
        from gi.repository import GdkPixbuf, GLib
        # Three RGBA pixels plus row padding: alpha must never be graded.
        data = bytes([30,60,90,40,100,120,150,80,200,210,220,120,0,0,0,0]*2)
        source = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data),GdkPixbuf.Colorspace.RGB,True,8,3,2,16)
        clip = {'color_grade':{'temperature':.5,'gamma':1.2}}
        picture = visuals.graded_picture(source,clip)
        self.assertEqual(bytes(source.get_pixels()), data[:28])
        self.assertNotEqual(bytes(picture.get_pixels()),bytes(source.get_pixels()))
        self.assertEqual(bytes(picture.get_pixels())[3::4],bytes(source.get_pixels())[3::4])

    def test_cinematic_presets_produce_distinct_editable_looks(self):
        pictures = {}
        for name in grading.PRESET_DESCRIPTIONS:
            with self.subTest(preset=name):
                p = self.project()
                grade = {**grading.defaults(), **grading.PRESETS[name]}
                p['clips'][0].update(color_grade=grade, saturation=grading.PRESET_SATURATION[name])
                model.validate_project(p)
                pictures[name] = engine.preview_frame(p, .4)['pixels']
        self.assertEqual(len({hashlib.sha256(data).digest() for data in pictures.values()}),5)
        animated = mean_rgb(pictures['Pixar-inspired animation'])
        horror = mean_rgb(pictures['Horror'])
        self.assertGreater(sum(animated)/3, sum(horror)/3+20)
        def chroma(data):
            return sum(max(pixel)-min(pixel) for pixel in zip(data[::3],data[1::3],data[2::3]))/(len(data)//3)
        self.assertGreater(chroma(pictures['Pixar-inspired animation']),chroma(pictures['Horror'])+20)


if __name__ == '__main__':unittest.main()
