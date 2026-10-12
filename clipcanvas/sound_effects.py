"""Small, original sound cues for timed text and visual layers.

The bundled presets are procedural waveforms, not samples from an online
library.  Generation uses the standard library and a fixed noise seed so
projects sound the same on another computer.
"""
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import wave

PRESETS = (('none', 'None'), ('pop', 'Pop'), ('crisp_pop', 'Crisp pop'),
           ('click', 'Click'), ('whoosh', 'Whoosh'), ('file', 'Choose local sound…'))
RATE = 48000
DURATIONS = {'pop': .19, 'crisp_pop': .13, 'click': .07, 'whoosh': .46}
SYNTH_VERSION = 1


def generate(preset, path=None):
    """Return a deterministic stereo PCM WAV, optionally at a requested path."""
    if preset not in DURATIONS:
        raise ValueError('Choose Pop, Crisp pop, Click or Whoosh.')
    target = Path(path) if path is not None else Path(os.environ.get(
        'XDG_CACHE_HOME', Path.home() / '.cache')) / 'clipcanvas' / 'sound-effects' / f'{preset}-v{SYNTH_VERSION}.wav'
    if path is None and target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    noise = random.Random(7391)
    samples = bytearray()
    previous = 0.0
    duration = DURATIONS[preset]
    for index in range(round(duration * RATE)):
        t = index / RATE
        u = t / duration
        n = noise.uniform(-1, 1)
        previous = previous * .83 + n * .17
        # Smooth both ends; every cue starts and ends at silence.
        edge = min(1, t / .003, (duration - t) / .012)
        if preset == 'pop':
            phase = math.tau * (480 * t - 160 / duration * t * t)
            value = math.sin(phase) * math.exp(-22 * t) * .6
        elif preset == 'crisp_pop':
            phase = math.tau * (1300 * t - 450 / duration * t * t)
            value = (math.sin(phase) * .45 + n * .2) * math.exp(-38 * t)
        elif preset == 'click':
            value = (n - previous) * math.exp(-80 * t) * .55
        else:
            envelope = math.sin(math.pi * u) ** 2
            tone = math.sin(math.tau * (170 * t + 300 / duration * t * t))
            value = (previous * 1.4 + tone * .12) * envelope * .7
        pcm = round(max(-1, min(1, value * max(0, edge))) * 32767)
        samples.extend(struct.pack('<hh', pcm, pcm))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.wav', delete=False) as handle:
            temporary = Path(handle.name)
        with wave.open(str(temporary), 'wb') as output:
            output.setnchannels(2); output.setsampwidth(2); output.setframerate(RATE)
            output.writeframes(samples)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def resolve(config, directory=None):
    """Find the selected local audio or synthesize its original preset."""
    preset = config.get('preset', 'none')
    if preset == 'none':
        return None
    if preset == 'file':
        path = Path(config.get('path', ''))
        if not path.is_file():
            raise ValueError('The layer sound file is missing. Choose it again in Sound.')
        return path
    target = Path(directory) / f'{preset}-v{SYNTH_VERSION}.wav' if directory is not None else None
    if target is not None and target.is_file():
        return target
    return generate(preset, target)


def duration(path):
    """Read WAV duration directly; other local formats use the installed ffprobe."""
    try:
        with wave.open(str(path), 'rb') as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, EOFError):
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                 'format=duration', '-of', 'default=nw=1:nk=1', str(path)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError('This sound file could not be opened.')
        try:
            seconds = float(result.stdout.strip())
        except ValueError as error:
            raise ValueError('This sound has no readable duration.') from error
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('This sound has no readable duration.')
        return seconds


def events(item, directory=None, durations=None):
    """Return timed (start, local path, volume) cues that follow a layer."""
    config = item.get('sound_effect', {})
    volume = config.get('volume', .5)
    if volume <= 0:
        return []
    path = resolve(config, directory)
    if path is None:
        return []
    start, end = float(item['start']), float(item['end'])
    trigger = config.get('trigger', 'enter')
    times = []
    if trigger in ('enter', 'both'):
        times.append(start)
    if trigger in ('exit', 'both'):
        if durations is not None:
            if path not in durations:durations[path]=duration(path)
            length=durations[path]
        else:length=duration(path)
        times.append(max(start, end - length))
    return [(at, path, volume) for at in dict.fromkeys(times)]
