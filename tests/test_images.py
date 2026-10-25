"""Photo scenes retain their timing and motion throughout the local workflow."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import cairo

from clipcanvas import engine, model, storage, workflow
from clipcanvas.scrubber import ScrubSession


class ImageSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='clipcanvas-images-')
        cls.root = Path(cls.scratch.name)
        cls.photo = cls.root/'photo.png'
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 320, 180)
        cr = cairo.Context(surface)
        cr.set_source_rgb(.1, .2, .4); cr.paint()
        cr.set_source_rgb(1, 0, 0); cr.rectangle(140, 70, 40, 40); cr.fill()
        surface.write_to_png(str(cls.photo))
        cls.ffmpeg, cls.env = engine._tool('ffmpeg')

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def project(self, motion='none', duration=1):
        p = model.new_project(320, 180)
        p['clips'] = [model.new_image(self.photo, duration)]
        p['clips'][0].update(motion=motion, zoom_amount=.5)
        return p

    def decode(self, path):
        return subprocess.check_output([self.ffmpeg, '-v', 'error', '-i', str(path),
            '-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', 'pipe:1'], env=self.env)

    def test_duration_edits_split_clipboard_save_portability_and_old_projects(self):
        p = self.project('zoom_in', 5)
        original = copy.deepcopy(p); digest = hashlib.sha256(self.photo.read_bytes()).digest()
        split = model.split_item(p, 'clips', 0, 2)
        self.assertEqual([model.clip_duration(c) for c in split['clips']], [2, 3])
        self.assertEqual(split['clips'][1]['in'], 2)
        self.assertEqual(split['clips'][1]['motion'], 'zoom_in')
        extended = model.trim_item(split, 'clips', 1, 'right', 4)
        self.assertEqual(model.project_duration(extended), 9)
        shortened = model.trim_item(extended, 'clips', 1, 'left', 1)
        self.assertEqual(model.project_duration(shortened), 8)
        selected = {('clips', p['clips'][0]['id'])}
        pasted, selection = workflow.paste_items(p, workflow.copy_items(p, selected), 5, 1)
        self.assertEqual(len(selection), 1)
        self.assertEqual(model.project_duration(pasted), 10)
        self.assertEqual(workflow.with_volume(p, selected, 1), p)
        with self.assertRaises(ValueError):model.detach_audio(p, 0)
        p['assets'] = [model.new_asset(p['clips'][0], 'image')]
        saved = self.root/'photos.clipcanvas'; model.save_project(p, saved)
        self.assertEqual(model.load_project(saved), p)
        self.assertFalse(Path(json.loads(saved.read_text())['clips'][0]['path']).is_absolute())
        portable = model.load_project(storage.collect_project(p, self.root/'portable'))
        self.assertEqual(portable['clips'][0]['path'], portable['assets'][0]['path'])
        self.assertEqual(Path(portable['clips'][0]['path']).read_bytes(), self.photo.read_bytes())
        recovery = storage.Recovery(self.root/'recovery'); recovery.write(p)
        self.assertEqual(recovery.read(recovery.path)[0], p)
        old = model.new_project();old['version'] = 7;old['clips'] = [model.new_card()]
        saved.write_text(json.dumps(old))
        self.assertEqual(model.load_project(saved)['version'], model.PROJECT_VERSION)
        self.assertEqual(hashlib.sha256(self.photo.read_bytes()).digest(), digest)
        self.assertEqual(original['clips'], p['clips'])

    def test_invalid_image_duration_and_motion_are_rejected(self):
        for duration in (0, -.1, float('nan'), float('inf'), True):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                model.new_image(self.photo, duration)
        p = self.project()
        for key, value in (('motion','pan'), ('zoom_amount',float('nan')), ('zoom_amount',1.1),
                           ('zoom_amount',-.1), ('volume',1), ('speed',2)):
            bad = copy.deepcopy(p);bad['clips'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                model.validate_project(bad)
        broken = self.root/'broken.png';broken.write_text('not an image')
        with self.assertRaises(engine.RenderError):model.new_image(broken)
        with self.assertRaises(engine.RenderError):engine.render(p, self.photo)

    def test_zoom_directions_duration_preview_playback_export_and_split_agree(self):
        frame_size = 320*180*3
        for motion in model.IMAGE_MOTIONS:
            with self.subTest(motion=motion):
                p = self.project(motion);before = copy.deepcopy(p)
                output = self.root/(motion+'.mkv');engine.render(p, output, quality='lossless')
                metadata = engine.probe(output)
                self.assertEqual((metadata['width'],metadata['height']), (320,180))
                self.assertAlmostEqual(metadata['duration'], 1, delta=.04)
                raw = self.decode(output)
                self.assertEqual(len(raw), 30*frame_size)
                widths = []
                for index in (0, 15, 29):
                    expected = raw[index*frame_size:(index+1)*frame_size]
                    picture = engine.preview_frame(p, index/30, preview_edge=0)
                    error = sum(abs(a-b) for a,b in zip(expected,picture['pixels']))/frame_size
                    self.assertLess(error, .5, 'Seeking must retain the animation phase')
                    row = expected[90*320*3:91*320*3]
                    widths.append(sum(row[x]>180 and row[x+1]<50 for x in range(0,len(row),3)))
                if motion=='zoom_in':self.assertLess(widths[0], widths[-1])
                elif motion=='zoom_out':self.assertGreater(widths[0], widths[-1])
                else:self.assertEqual(widths[0], widths[-1])
                live = self.root/(motion+'-live.mkv')
                with live.open('wb') as stream:
                    for chunk in engine.live_stream(p, preview_edge=0):stream.write(chunk)
                self.assertEqual(self.decode(live), raw)
                resumed = self.root/(motion+'-resumed.mkv')
                with resumed.open('wb') as stream:
                    for chunk in engine.live_stream(p, start=.5, preview_edge=0):stream.write(chunk)
                self.assertEqual(self.decode(resumed), raw[15*frame_size:])
                if motion!='none':
                    split = model.split_item(p, 'clips', 0, .5)
                    target = self.root/(motion+'-split.mkv');engine.render(split,target,quality='lossless')
                    self.assertEqual(self.decode(target), raw, 'Splitting must keep motion continuous')
                self.assertEqual(p, before)

    def test_mixed_photos_follow_first_video_fps_and_keep_soundtrack_and_transitions(self):
        video = self.root/'video.mp4';music = self.root/'music.wav'
        subprocess.run([self.ffmpeg,'-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x180:r=25:d=1',
            '-c:v','libx264','-threads','1',str(video)],env=self.env,check=True)
        subprocess.run([self.ffmpeg,'-v','error','-y','-f','lavfi','-i','sine=frequency=200:duration=4',str(music)],env=self.env,check=True)
        p = self.project('zoom_out')
        p['clips'] += [model.new_clip(video), model.new_image(self.photo, .8)]
        p['clips'][0].update(transition='fade', transition_duration=.2)
        p['clips'][1].update(transition='slide', transition_duration=.2)
        p['audio'] = [model.new_audio(music)]
        model.adapt_frame_rate(p);self.assertEqual(p['fps'],25)
        target = self.root/'mixed.mp4';engine.render(p, target)
        metadata = engine.probe(target)
        self.assertEqual(metadata['fps'],25)
        self.assertAlmostEqual(metadata['duration'],2.4,delta=.05)
        for source in ('video','mix'):
            output = self.root/(source+'.wav');engine.render_audio(p,output,source=source)
            raw = subprocess.check_output([self.ffmpeg,'-v','error','-i',str(output),'-f','s16le','pipe:1'],env=self.env)
            self.assertEqual(any(raw), source=='mix')
        session = ScrubSession()
        try:
            for at in (.3,.9,1.9):
                frame = session.frame(p,at)
                self.assertEqual(frame['pixels'],engine.preview_frame(p,at,preview_edge=0)['pixels'])
        finally:session.close()

    def test_image_formats_alpha_rotation_fit_and_fades(self):
        from gi.repository import GdkPixbuf
        formats = [('png','.png'), ('jpeg','.JPG'), ('bmp','.bmp'), ('tiff','.tiff')]
        picture = engine.image_picture(self.photo)
        for kind, suffix in formats:
            path = self.root/('format'+suffix);picture.savev(str(path),kind,[],[])
            p = self.project();p['clips'] = [model.new_image(path,.3)]
            frame = engine.preview_frame(p,.1,preview_edge=0)
            self.assertEqual(len(frame['pixels']),320*180*3)
        webp = self.root/'format.webp'
        subprocess.run([self.ffmpeg,'-v','error','-y','-i',str(self.photo),'-frames:v','1',
            '-c:v','libwebp','-lossless','1','-threads','1',str(webp)],env=self.env,check=True)
        p = self.project();p['clips'] = [model.new_image(webp,.3)]
        frame = engine.preview_frame(p,.1,preview_edge=0)
        self.assertEqual(len(frame['pixels']),320*180*3)
        svg = self.root/'transparent.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect x="25" y="25" width="50" height="50" fill="red"/></svg>')
        p = self.project();p['clips'] = [model.new_image(svg,1)]
        p['clips'][0].update(fit='fit',rotation=90,flip_x=True,video_fade_in=.2,video_fade_out=.2)
        start = engine.preview_frame(p,0,preview_edge=0)['pixels']
        self.assertFalse(any(start))
        middle = engine.preview_frame(p,.5,preview_edge=0)['pixels']
        self.assertGreater(middle[(90*320+160)*3],240)
        self.assertFalse(any(middle[:3]), 'Transparent corners and borders must stay black')


if __name__ == '__main__':
    unittest.main()
