"""Desaturation is an editable scene effect shared by preview and export."""
import copy
import hashlib
import json
import subprocess
import unittest

from clipcanvas import engine, model, workflow
from clipcanvas.scrubber import ScrubSession
import test_engine


def chroma(pixels):
    return sum(max(pixel)-min(pixel) for pixel in zip(pixels[::3], pixels[1::3], pixels[2::3]))/(len(pixels)//3)


class DesaturationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = test_engine.EngineIntegrationTests
        cls.fixtures.setUpClass()
        cls.root = cls.fixtures.root
        cls.photo = cls.root/'photo.png'
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

    def test_partial_and_full_saturation_agree_in_native_preview_live_and_export(self):
        p = self.project();source = self.root/'landscape.mp4'
        digest = hashlib.sha256(source.read_bytes()).digest()
        original = engine.preview_frame(p, .4, preview_edge=0)['pixels']
        session = ScrubSession()
        try:
            for saturation in (0, .5, 1):
                with self.subTest(saturation=saturation):
                    p['clips'][0]['saturation'] = saturation
                    picture = engine.preview_frame(p, .4, preview_edge=0)['pixels']
                    self.assertAlmostEqual(chroma(picture)/chroma(original), saturation, delta=.03)
                    native = session.frame(p, .4)['pixels']
                    self.assertAlmostEqual(chroma(native)/chroma(original), saturation, delta=.04)
                    self.assertLess(sum(abs(a-b) for a,b in zip(picture,native))/len(picture), 10)
                    self.assertEqual(session.last_backend, 'native')
                    out = self.root/f'saturation-{saturation}.mkv'
                    engine.render(p, out, quality='lossless')
                    exported = self.pixels(out)
                    self.assertLess(sum(abs(a-b) for a,b in zip(picture,exported))/len(picture), 1)
            p['clips'][0]['saturation'] = 0
            live = self.root/'desaturated-live.mkv'
            with live.open('wb') as stream:
                for data in engine.live_stream(p, .2):stream.write(data)
            self.assertLess(chroma(self.pixels(live, .2)), 1)
            self.assertTrue(engine.probe(live)['has_audio'])
            self.assertEqual(session.open_count, 1)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
        finally:session.close()

    def test_images_cards_transitions_and_overlays_keep_their_own_color(self):
        for scene in (model.new_image(self.photo, 1), model.new_card('#dd4422', 1)):
            with self.subTest(kind=scene['kind']):
                p = model.new_project(320, 180);p['clips'] = [scene]
                scene['saturation'] = 0
                gray = engine.preview_frame(p, .3, preview_edge=0)['pixels']
                self.assertLess(chroma(gray), 1)
                if model.is_card(scene):
                    session = ScrubSession()
                    try:
                        native = session.frame(p, .3)['pixels']
                        self.assertLess(chroma(native), 1)
                        self.assertLess(sum(abs(a-b) for a,b in zip(gray,native))/len(gray), 3)
                    finally:session.close()
                p['layers'] = [model.new_text('COLOR', 0, 1)]
                p['layers'][0].update(color='#ff0000', font_size=48)
                with_text = engine.preview_frame(p, .3, preview_edge=0)['pixels']
                self.assertGreater(chroma(with_text), 2)
                p['layers'] = []
                out = self.root/f"gray-{scene['kind']}.mp4";engine.render(p, out)
                self.assertLess(chroma(self.pixels(out, .3)), 1)
        p['clips'] = [model.new_card('#dd4422', 1), model.new_card('#2255ee', 1)]
        p['clips'][0].update(saturation=0, transition='fade', transition_duration=.4)
        p['clips'][1]['saturation'] = 0
        self.assertLess(chroma(engine.preview_frame(p, .8)['pixels']), 1)
        p['clips'][1]['saturation'] = 1
        self.assertGreater(chroma(engine.preview_frame(p, .8)['pixels']), 20)

    def test_selection_saving_splitting_clipboard_and_legacy_defaults(self):
        p = self.project();p['clips'] += [model.new_image(self.photo), model.new_card('#dd4422')]
        p['audio'] = [model.new_audio(self.root/'music.wav')]
        p['layers'] = [model.new_text('Keep color', 0, 1)]
        selected = {(group, item['id']) for group in workflow.GROUPS for item in p[group]}
        selected.remove(('clips', p['clips'][2]['id']))
        before = copy.deepcopy(p);gray = workflow.with_saturation(p, selected, .35)
        expected = copy.deepcopy(before)
        for item in expected['clips'][:2]:item['saturation'] = .35
        self.assertEqual(gray, expected);self.assertEqual(p, before)
        path = self.root/'desaturation.clipcanvas';model.save_project(gray, path)
        self.assertEqual(model.load_project(path), gray)
        split = model.split_item(gray, 'clips', 0, .6)
        self.assertEqual([item['saturation'] for item in split['clips'][:2]], [.35, .35])
        pasted, _ = workflow.paste_items(gray, workflow.copy_items(gray, selected), 0, 0)
        self.assertEqual(pasted['clips'][0]['saturation'], .35)
        for item in before['clips']:item.pop('saturation')
        path.write_text(json.dumps(before))
        self.assertEqual([item['saturation'] for item in model.load_project(path)['clips']], [1, 1, 1])
        for value in (-.01, 2.01, True, float('nan'), float('inf'), '0', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workflow.with_saturation(p, selected, value)
        self.assertEqual([item['saturation'] for item in p['clips']], [1, 1, 1])


if __name__ == '__main__':unittest.main()
