"""Original sound cues, determinism and attachment timing."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import wave

from clipcanvas import sound_effects


class SoundEffectTests(unittest.TestCase):
    def test_generated_cues_are_deterministic_quiet_stereo_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            for preset, duration in sound_effects.DURATIONS.items():
                first = sound_effects.generate(preset, Path(directory) / (preset + '.wav'))
                second = sound_effects.generate(preset, Path(directory) / (preset + '-again.wav'))
                self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
                with wave.open(str(first)) as audio:
                    self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (2, 2, 48000))
                    self.assertAlmostEqual(audio.getnframes() / 48000, duration, places=4)
                    values = struct.unpack('<' + 'h' * audio.getnframes() * 2, audio.readframes(audio.getnframes()))
                self.assertGreater(max(map(abs, values)), 500)
                self.assertLess(max(map(abs, values)), 26000)
                self.assertEqual(values[:2], (0, 0))
                self.assertLess(max(map(abs, values[-2:])), 30)

    def test_sound_follows_layer_entry_and_finishes_at_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = sound_effects.generate('pop', Path(directory) / 'pop.wav')
            item = {'start': 5, 'end': 9, 'sound_effect': {'preset': 'file', 'path': str(path), 'volume': .35, 'trigger': 'both'}}
            self.assertEqual(sound_effects.events(item), [(5, path, .35), (8.81, path, .35)])
            item['start'] = 12; item['end'] = 12.1
            self.assertEqual(sound_effects.events(item), [(12, path, .35)])
            item['sound_effect']['volume'] = 0
            self.assertEqual(sound_effects.events(item), [])

    def test_none_is_lazy_missing_files_report_and_cache_reused(self):
        with mock.patch.object(sound_effects, 'generate', side_effect=AssertionError('No sound should be generated')):
            self.assertIsNone(sound_effects.resolve({'preset': 'none'}))
            self.assertEqual(sound_effects.events({'start': 0, 'end': 3}), [])
        with self.assertRaisesRegex(ValueError, 'missing'):
            sound_effects.resolve({'preset': 'file', 'path': '/not/a/clipcanvas/sound.wav'})
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict('os.environ', {'XDG_CACHE_HOME': directory}):
            path = sound_effects.generate('click'); before = path.stat().st_mtime_ns
            self.assertEqual(sound_effects.generate('click'), path)
            self.assertEqual(path.stat().st_mtime_ns, before)

