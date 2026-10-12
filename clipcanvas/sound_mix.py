"""Bounded local premix for sounds attached to text, images and captions.

Each distinct file is decoded once into seekable float PCM. A small block is
mixed at a time, so hundreds of late cues do not create hundreds of decoders
or long in-memory delay buffers. Float samples retain headroom for the main
composition's existing limiter.
"""

from __future__ import annotations

import array
import contextlib
import copy
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

from . import sound_effects

RATE = 48000
CHANNELS = 2
BLOCK_FRAMES = 4096
BYTES_PER_FRAME = CHANNELS * 4


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        from .engine import RenderCancelled
        raise RenderCancelled('Export cancelled.')


def _header(output, frames):
    size = frames * BYTES_PER_FRAME
    fmt = struct.pack('<HHIIHH', 3, CHANNELS, RATE, RATE * BYTES_PER_FRAME, BYTES_PER_FRAME, 32)
    if size + 36 <= 0xffffffff:
        output.write(b'RIFF' + struct.pack('<I', size + 36) + b'WAVEfmt ' + struct.pack('<I', 16) + fmt)
        output.write(b'data' + struct.pack('<I', size))
    else:
        # RF64 also supports long projects without wrapping 32-bit WAV sizes.
        output.write(b'RF64\xff\xff\xff\xffWAVEds64' + struct.pack('<IQQQI', 28, size + 72, size, frames, 0))
        output.write(b'fmt ' + struct.pack('<I', 16) + fmt + b'data\xff\xff\xff\xff')


def _decode(source, target, frames, cancel):
    from . import engine
    binary, env = engine._tool('ffmpeg')
    log_path = target.with_suffix('.log')
    with log_path.open('wb') as log:
        process = subprocess.Popen([binary, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                                    '-threads', '1', '-i', str(source), '-vn', '-sn', '-dn',
                                    '-t', format((frames + 1) / RATE, '.9f'),
                                    '-c:a', 'pcm_f32le', '-ar', str(RATE), '-ac', str(CHANNELS),
                                    '-threads', '1', '-f', 'f32le', str(target)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=log, env=env)
        try:
            while process.poll() is None:
                _cancelled(cancel)
                if cancel is None:
                    time.sleep(.025)
                else:
                    cancel.wait(.025)
            _cancelled(cancel)
            if process.returncode:
                detail = log_path.read_text(errors='replace')[-1000:]
                raise engine.RenderError(f'Could not read the layer sound {Path(source).name}.\n{detail}')
        finally:
            engine._stop(process)
    available = min(frames, target.stat().st_size // BYTES_PER_FRAME)
    with target.open('r+b') as output:
        output.truncate(available * BYTES_PER_FRAME)
    return available


def _cues(project, directory, cancel):
    """Cache equal sound/timing templates, retaining the events API's rules."""
    from .model import project_duration
    total = round(project_duration(project) * RATE)
    cues = []
    templates = {}
    durations = {}
    for item in project.get('layers', []) + project.get('captions', []):
        _cancelled(cancel)
        config = item.get('sound_effect', {})
        if config.get('preset', 'none') == 'none' or config.get('volume', .5) <= 0:
            continue
        item_start, item_end = float(item['start']), float(item['end'])
        if item_start * RATE >= total or item_end <= 0:
            continue
        # Relative templates avoid repeated ffprobe calls when multiple cues
        # use the same custom sound and duration, without global monkeypatches.
        key = (config.get('preset'), config.get('path', ''), config.get('volume', .5),
               config.get('trigger', 'enter'), item_end - item_start)
        if key not in templates:
            relative = {'start': 0., 'end': item_end - item_start,
                        'sound_effect': copy.deepcopy(config)}
            templates[key] = sound_effects.events(relative, directory, durations)
        for at, path, volume in templates[key]:
            begin = max(0, round((item_start + at) * RATE))
            end = min(total, round(item_end * RATE))
            if end > begin:
                cues.append((begin, end, Path(path).resolve(), float(volume)))
    return total, cues


def mix(project, scratch, cancel=None):
    """Return one timed 48 kHz stereo float WAV, or None for no audible cues.

    Temporary files live only in the caller-owned scratch folder. Original
    paths are read, never modified. Entry and exit sounds stop at the element
    end and the project end. The caller skips this for silent picture requests
    and for original-video speech extraction.
    """
    directory = Path(scratch)
    total, cues = _cues(project, directory, cancel)
    if not cues:
        return None
    needed = {}
    for begin, end, source, volume in cues:
        needed[source] = max(needed.get(source, 0), end - begin)
    decoded = {}
    for index, (source, frames) in enumerate(needed.items()):
        _cancelled(cancel)
        path = directory / f'element-sound-source-{index}.f32'
        decoded[source] = path, _decode(source, path, frames, cancel)
    events = sorted((begin, min(end, begin + decoded[source][1]), source, volume)
                    for begin, end, source, volume in cues if decoded[source][1])
    if not events:
        return None
    target = directory / 'element-sounds.wav'
    try:
        with contextlib.ExitStack() as files:
            readers = {source: files.enter_context(path.open('rb')) for source, (path, frames) in decoded.items()}
            output = files.enter_context(target.open('wb'))
            _header(output, total)
            active = []
            next_event = 0
            for block_start in range(0, total, BLOCK_FRAMES):
                _cancelled(cancel)
                block_end = min(total, block_start + BLOCK_FRAMES)
                while next_event < len(events) and events[next_event][0] < block_end:
                    active.append(events[next_event])
                    next_event += 1
                active = [event for event in active if event[1] > block_start]
                result = array.array('f', [0.]) * ((block_end - block_start) * CHANNELS)
                for begin, end, source, volume in active:
                    _cancelled(cancel)
                    first, last = max(begin, block_start), min(end, block_end)
                    if last <= first:
                        continue
                    reader = readers[source]
                    reader.seek((first - begin) * BYTES_PER_FRAME)
                    samples = array.array('f')
                    samples.frombytes(reader.read((last - first) * BYTES_PER_FRAME))
                    if sys.byteorder != 'little':
                        samples.byteswap()
                    offset = (first - block_start) * CHANNELS
                    for index, sample in enumerate(samples):
                        result[offset + index] += sample * volume
                if sys.byteorder != 'little':
                    result.byteswap()
                output.write(result.tobytes())
            _cancelled(cancel)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise
