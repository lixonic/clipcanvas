"""Bounded picture and audio windows for responsive long-form playback.

Picture windows prepare only the clips, transitions, layers and captions active
around the playhead, keeping a bounded horizon even on multi-hour timelines.
Audio windows open only active clips/soundtracks/element cues and use exact
filter-aware preroll on deep seeks so minute 179 starts as fast as minute 0.
"""
from collections import deque
import copy
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from . import animations, audio_effects, engine, model, preview_cache, transition_render

RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE_FRAME = CHANNELS * 2

FIRST_COMPOSED_VIDEO_WINDOW_SECONDS = 8.0
COMPOSED_VIDEO_WINDOW_SECONDS = 16.0
MIN_STARTUP_VIDEO_WINDOW_SECONDS = 2.0
MIN_STEADY_VIDEO_WINDOW_SECONDS = 10.0
MAX_STARTUP_WINDOW_CLIPS = 4
MAX_STEADY_WINDOW_CLIPS = 6
PREFETCH_MAX_BYTES = 96 * 1024 * 1024
PREFETCH_MIN_FRAMES = 12
PREFETCH_MAX_FRAMES = 48
FIRST_AUDIO_WINDOW_SECONDS = 12.0
AUDIO_WINDOW_SECONDS = 30.0
MIX_PREROLL_SECONDS = 0.25


def _video_decode_threads():
    return min(2, os.cpu_count() or 1)


def _video_filter_threads():
    return min(4, os.cpu_count() or 1)


def route_reason(project):
    """Return ``(use_windowed, reason)`` for live preview scheduling."""
    clips = project.get('clips') or []
    if not clips:
        return False, 'empty-project'
    first = next((clip for clip in clips if not model.is_still(clip)), None)
    if first is not None and not first.get('source_fps'):
        return False, 'unknown-source-fps'
    if len(clips) > 2:
        return True, 'multi-clip-windowed'
    if model.project_duration(project) > 20.0:
        return True, 'long-timeline-windowed'
    if len(project.get('captions', [])) > 8:
        return True, 'captioned-windowed'
    if len(project.get('layers', [])) > 4:
        return True, 'layered-windowed'
    if len(project.get('audio', [])) > 2:
        return True, 'multi-audio-windowed'
    return False, 'short-continuous'


def supported(project):
    return route_reason(project)[0]


class _TimelineIndex:
    """Immutable per-playback time index so windows never deep-copy the project."""

    def __init__(self, project):
        self.project = project
        self.clips = project['clips']
        self.fps = float(project['fps'])
        self.rows = model.timeline(project)
        self.total = model.project_duration(project)
        self.clip_frames = [0]
        for index in range(1, len(self.clips)):
            if self.rows[index - 1]['overlap'] > 0:
                self.clip_frames.append(transition_render._first_frame(self.rows[index]['start'], self.fps))
            else:
                self.clip_frames.append(
                    self.clip_frames[-1] + math.floor(self.rows[index - 1]['duration'] * self.fps + 0.5 + 1e-7)
                )
        self.clip_end_frames = [
            self.clip_frames[index] + math.floor(self.rows[index]['duration'] * self.fps + 0.5 + 1e-7)
            for index in range(len(self.clips))
        ]
        last_end = self.clip_end_frames[-1] if self.clip_end_frames else 0
        self.total_frames = min(last_end, math.ceil(self.total * self.fps - 1e-7))
        self.has_match_move = any(clip.get('transition') == 'match_move' for clip in self.clips)
        self.layers = animations.prepare_layers(project) if self.has_match_move else list(project.get('layers', []))
        self.captions = list(project.get('captions', []))
        self.has_overlays = bool(self.layers or self.captions)
        self.base_project = {
            key: value for key, value in project.items()
            if key not in ('clips', 'audio', 'layers', 'captions')
        }
        self.base_project['audio'] = []

    def window_project(self, clip_start, clip_end, time_start, time_end):
        """Shallow window view containing only active clips, layers and captions."""
        pad = 2.0 / self.fps
        lo = max(0.0, time_start - pad)
        hi = time_end + pad
        layers = [
            item for item in self.layers
            if item['start'] < hi and item['end'] > lo and float(item.get('_suppress_until', 0)) < hi
        ]
        captions = [
            cue for cue in self.captions
            if cue['start'] < hi and cue['end'] > lo
        ]
        window = dict(self.base_project)
        window['clips'] = self.clips[clip_start:clip_end]
        window['layers'] = layers
        window['captions'] = captions
        window['audio'] = []
        return window


def _read(arguments, scratch, cancel, block, on_launch=None, filter_threads=1):
    if cancel.is_set():
        return
    binary, env = engine._tool('ffmpeg')
    with open(Path(scratch) / 'decoder.log', 'wb') as log:
        process = subprocess.Popen(
            [binary, '-nostdin', '-v', 'error', '-filter_complex_threads', str(max(1, int(filter_threads)))] + arguments,
            stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL, env=env,
        )
        if on_launch is not None:
            on_launch()
        finished = threading.Event()

        def watch():
            while not finished.wait(.02):
                if cancel.is_set():
                    engine._stop(process, immediate=True)
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            while not cancel.is_set():
                data = process.stdout.read(block)
                if not data:
                    break
                yield data
            if not cancel.is_set() and process.wait():
                log.flush()
                raise engine.RenderError(Path(log.name).read_text(errors='replace')[-1600:])
        finally:
            finished.set()
            engine._stop(process, immediate=True)
            process.stdout.close()
            watcher.join()


def _stop_for_end(index, c_end, last):
    if c_end >= len(index.clips):
        return last
    return min(last, index.clip_frames[c_end])


def _plan_video_windows(index, first, last):
    """Yield ``(win_first, win_stop, clip_start, clip_end)`` on clean boundaries.

    No window boundary ever splits strictly inside a transition overlap, so
    every transition sees both adjacent clips. Startup opens minimal clips for
    fast first-frame latency, steady-state packs short clips/cards to avoid
    micro-window restarts, and long clips with distant overlays or captions are
    time-sliced so distant sprites never block startup.
    """
    n_clips = len(index.clips)
    fps = index.fps
    cursor = first
    window_number = 0
    while cursor < last:
        c_start = next((i for i in range(n_clips) if index.clip_end_frames[i] > cursor), n_clips)
        if c_start >= n_clips:
            break
        c_end = min(n_clips, c_start + 1)
        while c_end < n_clips and _stop_for_end(index, c_end, last) <= cursor:
            c_end += 1

        if window_number == 0:
            while c_end < n_clips and (c_end - c_start) < MAX_STARTUP_WINDOW_CLIPS:
                cur_dur = (_stop_for_end(index, c_end, last) - cursor) / fps
                if cur_dur >= MIN_STARTUP_VIDEO_WINDOW_SECONDS:
                    break
                c_end += 1
        else:
            while c_end < n_clips and (c_end - c_start) < MAX_STEADY_WINDOW_CLIPS:
                cur_dur = (_stop_for_end(index, c_end, last) - cursor) / fps
                if cur_dur >= MIN_STEADY_VIDEO_WINDOW_SECONDS:
                    break
                next_dur = (_stop_for_end(index, c_end + 1, last) - cursor) / fps
                if cur_dur >= 4.0 and next_dur > COMPOSED_VIDEO_WINDOW_SECONDS + 2.0:
                    break
                c_end += 1

        natural_stop = _stop_for_end(index, c_end, last)
        win_stop = natural_stop
        # Time-slice long windows when overlays/captions or drifting sparkles exist beyond the horizon.
        max_seconds = FIRST_COMPOSED_VIDEO_WINDOW_SECONDS if window_number == 0 else COMPOSED_VIDEO_WINDOW_SECONDS
        max_frames = max(1, round(max_seconds * fps))
        if win_stop - cursor > max_frames:
            t_horizon = (cursor + max_frames) / fps
            needs_time_slice = False
            if index.has_overlays:
                needs_time_slice = any(
                    item['end'] > t_horizon for item in index.layers
                ) or any(
                    cue['end'] > t_horizon for cue in index.captions
                )
            if not needs_time_slice:
                needs_time_slice = any(
                    clip.get('sparkle_patch') for clip in index.clips[c_start:c_end]
                )
            if needs_time_slice:
                cap_stop = cursor + max_frames
                # Keep cap_stop outside any transition overlap inside [c_start, c_end).
                for join in range(c_start + 1, c_end):
                    if index.rows[join - 1]['overlap'] > 0:
                        ov_begin = index.clip_frames[join]
                        ov_end = index.clip_end_frames[join - 1]
                        if ov_begin < cap_stop < ov_end:
                            cap_stop = ov_begin if ov_begin > cursor else min(natural_stop, ov_end)
                            break
                if cap_stop > cursor:
                    win_stop = min(natural_stop, cap_stop)
                    while c_end > c_start + 1 and index.clip_frames[c_end - 1] >= win_stop:
                        c_end -= 1

        if win_stop <= cursor:
            break
        yield cursor, win_stop, c_start, c_end
        cursor = win_stop
        window_number += 1


class _CompositeCancel:
    """Cancel signal active when either external cancel or local close fires."""

    def __init__(self, external, local):
        self._external = external
        self._local = local

    def is_set(self):
        return self._local.is_set() or self._external.is_set()


def video(project, start, edge, cancel, metrics=None):
    index = _TimelineIndex(project)
    fps = index.fps
    width, height = engine.preview_dimensions(project, edge)
    size = width * height * 3 // 2
    first = math.ceil(start * fps - 1e-7)
    last = index.total_frames
    if first >= last or not index.clips:
        return

    decode_threads = _video_decode_threads()
    filter_threads = _video_filter_threads()
    max_queued = max(PREFETCH_MIN_FRAMES, min(PREFETCH_MAX_FRAMES, PREFETCH_MAX_BYTES // max(1, size)))
    stop_local = threading.Event()
    effective_cancel = _CompositeCancel(cancel, stop_local)
    buffer = deque()
    cond = threading.Condition()
    state = {'done': False, 'error': None}

    def produce():
        try:
            for win_idx, (win_first, win_stop, c_start, c_end) in enumerate(_plan_video_windows(index, first, last)):
                if effective_cancel.is_set():
                    break
                prep_start = time.monotonic()
                t_first = win_first / fps
                t_stop = win_stop / fps
                clip_origin = index.clip_frames[c_start] / fps
                window = index.window_project(c_start, c_end, t_first, t_stop)
                slice_rows = index.rows[c_start:c_end]
                with tempfile.TemporaryDirectory(prefix='clipcanvas-lazy-video-') as scratch:
                    proxies = {}
                    if edge:
                        for clip in window['clips']:
                            if not model.is_still(clip):
                                proxy = preview_cache.lease_available(clip['path'], edge, scratch)
                                if proxy:
                                    proxies[clip['path']] = proxy
                    args, graph, _ = engine._graph(
                        window, True, scratch, video_only=True,
                        cancel=effective_cancel, preview_edge=edge, picture_start=t_first,
                        proxies=proxies, clip_origin=clip_origin, window_end=t_stop,
                        prepared_layers=True, global_rows=slice_rows,
                        frame_range=(win_first, win_stop),
                        video_threads=decode_threads,
                    )
                    path = Path(scratch) / 'filters.txt'
                    path.write_text(graph, encoding='utf-8')
                    if metrics is not None:
                        metrics.record_video_window(win_idx, (time.monotonic() - prep_start) * 1000.0)
                    delivered = 0
                    expected = win_stop - win_first
                    on_launch = (lambda: metrics.mark_video_ffmpeg()) if (metrics is not None and win_idx == 0) else None
                    for data in _read(
                        args + ['-filter_complex_script', str(path),
                                '-map', '[outvideo]', '-an', '-frames:v', str(expected),
                                '-threads', str(decode_threads),
                                '-c:v', 'rawvideo', '-pix_fmt', 'yuv420p', '-f', 'rawvideo', 'pipe:1'],
                        scratch, effective_cancel, size, on_launch=on_launch,
                        filter_threads=filter_threads,
                    ):
                        if len(data) != size:
                            raise engine.RenderError('Incomplete preview picture.')
                        delivered += 1
                        with cond:
                            while len(buffer) >= max_queued and not effective_cancel.is_set():
                                cond.wait(timeout=0.05)
                            if effective_cancel.is_set():
                                break
                            buffer.append(data)
                            cond.notify()
                    if not effective_cancel.is_set() and delivered != expected:
                        raise engine.RenderError('Preview window ended before its final picture.')
        except Exception as problem:
            with cond:
                state['error'] = problem
                cond.notify_all()
        finally:
            with cond:
                state['done'] = True
                cond.notify_all()

    worker = threading.Thread(target=produce, daemon=True)
    worker.start()
    try:
        while True:
            with cond:
                while not buffer and not state['done'] and not effective_cancel.is_set():
                    cond.wait(timeout=0.05)
                if effective_cancel.is_set():
                    break
                if buffer:
                    frame = buffer.popleft()
                    cond.notify()
                elif state['error'] is not None:
                    raise state['error']
                else:
                    break
            yield frame
    finally:
        stop_local.set()
        with cond:
            buffer.clear()
            cond.notify_all()
        worker.join()


def _audio_preroll(item):
    """Minimum seconds of source audio history required before a seek point."""
    preroll = 0.25  # AAC MDCT codec priming + resampler + alimiter settling
    speed = float(item.get('speed', 1.0))
    if abs(speed - 1.0) > 1e-6:
        preroll += 0.5
    if audio_effects.active(item):
        cfg = audio_effects.settings(item)
        if any(cfg[key] for key in ('highpass_hz', 'lowpass_hz', 'bass_db', 'treble_db')):
            preroll += 0.1
        if cfg['compressor_enabled']:
            preroll += (cfg['compressor_attack_ms'] + cfg['compressor_release_ms'] * 2.0) / 1000.0 + 0.25
        if cfg['echo_enabled'] and cfg['echo_level'] > 0:
            preroll += (cfg['echo_delay_ms'] * int(cfg['echo_repeats'])) / 1000.0 + 0.25
    return preroll


def _shifted_audio_fades(item, audible_length, seek_out):
    """Adjust fade_in/fade_out timestamps when seeking into an audio item."""
    filters = []
    remaining = max(0.0, audible_length - seek_out)
    for prefix in ('', 'transition_'):
        fade_in = min(float(item.get(prefix + 'fade_in', 0)), audible_length)
        fade_out = min(float(item.get(prefix + 'fade_out', 0)), audible_length)
        if fade_in > seek_out:
            filters.append(f"afade=t=in:st=0:d={engine._fmt(fade_in - seek_out)}")
        if fade_out > 0:
            fade_out_start = audible_length - fade_out - seek_out
            if fade_out_start <= 0:
                filters.append(f"afade=t=out:st=0:d={engine._fmt(max(0.001, remaining))}")
            elif fade_out_start < remaining:
                filters.append(f"afade=t=out:st={engine._fmt(fade_out_start)}:d={engine._fmt(fade_out)}")
    return filters


def _processed_audio_shifted(item, length, audible_length, seek_out, max_output_duration):
    """Process a seeked span of an audio clip/track while preserving fades and effects."""
    rem_length = max(0.001, length - seek_out)
    out_dur = min(rem_length, max_output_duration) if max_output_duration is not None else rem_length
    filters = engine._tempo(item.get('speed', 1))
    filters.extend(['apad', f'atrim=duration={engine._fmt(out_dur)}'])
    filters.extend(audio_effects.filters(item))
    filters.extend([f'atrim=duration={engine._fmt(out_dur)}', f'volume={engine._fmt(item.get("volume", 1))}'])
    filters.extend(_shifted_audio_fades(item, audible_length, seek_out))
    return filters


def _build_audio_window_graph(project, rows, merged_tracks, total, s0, s1, scratch, cancel):
    """Build a bounded FFmpeg audio graph emitting exact samples ``[s0, s1)``."""
    t0 = s0 / RATE
    t1 = s1 / RATE
    mix_preroll = MIX_PREROLL_SECONDS if s0 > 0 else 0.0
    # Align mix_s0 down to FFmpeg atempo's 1024-sample WSOLA grid so interval
    # cue mixes retain bit-exact phase parity with continuous playback.
    mix_s0 = (max(0, s0 - round(mix_preroll * RATE)) // 1024) * 1024
    mix_t0 = mix_s0 / RATE
    mix_span = max(1 / RATE, t1 - mix_t0)

    args = []
    filters = []
    clips = project['clips']

    # 1. Main timeline clips intersecting [mix_t0, t1).
    active_clip_indices = [
        i for i, row in enumerate(rows)
        if row['start'] + row['duration'] > mix_t0 + 1e-9 and row['start'] < t1 - 1e-9
    ]
    mix_labels = []
    if active_clip_indices:
        c_start, c_end = active_clip_indices[0], active_clip_indices[-1] + 1
        first_seek_out = 0.0
        for local_idx, global_idx in enumerate(range(c_start, c_end)):
            clip = clips[global_idx]
            row = rows[global_idx]
            duration = row['duration']
            speed = float(clip.get('speed', 1.0))
            c_time = row['start']
            seek_out = 0.0
            if local_idx == 0 and c_time < mix_t0:
                local_offset = mix_t0 - c_time
                preroll = _audio_preroll(clip)
                fade_in_max = max(float(clip.get('fade_in', 0)), float(clip.get('transition_fade_in', 0)))
                # Don't seek past the start of an outgoing acrossfade.
                safe_limit = max(0.0, duration - row['overlap'] - 0.5)
                if not model.is_still(clip) and clip.get('volume', 1) > 0:
                    metadata = engine.probe(clip['path'])
                    if (metadata['has_audio'] and metadata.get('format_name') != 'wav'
                            and local_offset > max(preroll, fade_in_max) + 1.5
                            and local_offset - preroll <= safe_limit):
                        seek_out = math.floor((local_offset - preroll) * RATE / 1024) * 1024 / RATE if abs(speed - 1.0) <= 1e-6 else local_offset - preroll
                elif model.is_still(clip) or clip.get('volume', 1) <= 0:
                    if local_offset > 0.5 and local_offset - 0.25 <= safe_limit:
                        seek_out = local_offset - 0.25
                first_seek_out = seek_out

            effective_dur = duration - seek_out
            if not model.is_still(clip) and clip.get('volume', 1) > 0:
                metadata = engine.probe(clip['path'])
                if metadata['has_audio']:
                    audio_input = args.count('-i')
                    source_in = clip.get('in', 0.0) + seek_out * speed
                    source_rem = clip['out'] - source_in
                    # Only decode through the end of this audio window unless an outgoing crossfade needs the clip tail.
                    if local_idx == (c_end - c_start - 1):
                        needed_timeline = max(0.05, (t1 - (c_time + seek_out)) + 0.5)
                        read_dur = min(source_rem, needed_timeline * speed)
                        max_out = min(effective_dur, needed_timeline)
                    else:
                        read_dur = source_rem
                        max_out = effective_dur
                    args += ['-threads', '1', '-vn', '-ss', engine._fmt(source_in),
                             '-t', engine._fmt(max(0.01, read_dur)), '-i', clip['path']]
                    parts = [
                        f"[{audio_input}:{metadata['audio_index']}]aresample={RATE}",
                        'aformat=sample_fmts=fltp:channel_layouts=stereo',
                        'asetpts=PTS-STARTPTS',
                    ]
                    if seek_out > 0:
                        parts.extend(_processed_audio_shifted(clip, duration, duration, seek_out, max_out))
                    else:
                        parts.extend(engine._processed_audio(clip, effective_dur))
                        if max_out < effective_dur - 0.05:
                            parts.append(f'atrim=duration={engine._fmt(max_out)}')
                    filters.append(','.join(parts) + f'[a{local_idx}]')
                else:
                    out_dur = min(effective_dur, max(0.05, t1 - (c_time + seek_out) + 0.25)) if local_idx == (c_end - c_start - 1) else effective_dur
                    filters.append(f"anullsrc=r={RATE}:cl=stereo,atrim=duration={engine._fmt(out_dur)},asetpts=PTS-STARTPTS[a{local_idx}]")
            else:
                out_dur = min(effective_dur, max(0.05, t1 - (c_time + seek_out) + 0.25)) if local_idx == (c_end - c_start - 1) else effective_dur
                filters.append(f"anullsrc=r={RATE}:cl=stereo,atrim=duration={engine._fmt(out_dur)},asetpts=PTS-STARTPTS[a{local_idx}]")

        chain_label = 'a0'
        for local_idx in range(1, c_end - c_start):
            overlap = rows[c_start + local_idx - 1]['overlap']
            if overlap:
                filters.append(f"[{chain_label}][a{local_idx}]acrossfade=d={engine._fmt(overlap)}:c1=tri:c2=tri[joina{local_idx}]")
            else:
                filters.append(f"[{chain_label}][a{local_idx}]concat=n=2:v=0:a=1[joina{local_idx}]")
            chain_label = f'joina{local_idx}'

        chain_start = rows[c_start]['start'] + first_seek_out
        align = [f'[{chain_label}]']
        if chain_start > mix_t0 + 1e-9:
            delay_samples = round((chain_start - mix_t0) * RATE)
            if delay_samples > 0:
                align.append(f'adelay={delay_samples}S:all=1,asetpts=N/SR/TB')
        elif chain_start < mix_t0 - 1e-9:
            skip_samples = round((mix_t0 - chain_start) * RATE)
            if skip_samples > 0:
                align.append(f'atrim=start_sample={skip_samples},asetpts=N/SR/TB')
        align.extend([f'apad=whole_dur={engine._fmt(mix_span)}', f'atrim=duration={engine._fmt(mix_span)}'])
        filters.append(align[0] + ','.join(align[1:]) + '[mainaudio]')
        mix_labels.append('mainaudio')
    else:
        filters.append(f"anullsrc=r={RATE}:cl=stereo,atrim=duration={engine._fmt(mix_span)},asetpts=N/SR/TB[mainaudio]")
        mix_labels.append('mainaudio')

    # 2. Independent audio lanes intersecting [mix_t0, t1).
    for offset, track in enumerate(merged_tracks):
        t_start = float(track.get('start', 0.0))
        length = model.clip_duration(track)
        if t_start >= t1 - 1e-9 or t_start + length <= mix_t0 + 1e-9:
            continue
        metadata = engine.probe(track['path'])
        if not metadata['has_audio']:
            raise engine.RenderError(f"{Path(track['path']).name} has no audio stream.")
        speed = float(track.get('speed', 1.0))
        audible_length = max(0.0, min(length, total - t_start))
        local_offset = max(0.0, mix_t0 - t_start)
        preroll = _audio_preroll(track)
        fade_in_max = max(float(track.get('fade_in', 0)), float(track.get('transition_fade_in', 0)))
        seek_out = 0.0
        if (metadata.get('format_name') != 'wav'
                and local_offset > max(preroll, fade_in_max) + 1.5):
            seek_out = math.floor((local_offset - preroll) * RATE / 1024) * 1024 / RATE if abs(speed - 1.0) <= 1e-6 else local_offset - preroll

        eff_start = t_start + seek_out
        needed_timeline = max(0.05, (t1 - eff_start) + 0.5)
        input_idx = args.count('-i')
        if seek_out > 0:
            source_in = track.get('in', 0.0) + seek_out * speed
            read_dur = min(track['out'] - source_in, needed_timeline * speed)
            args += ['-threads', '1', '-vn', '-ss', engine._fmt(source_in),
                     '-t', engine._fmt(max(0.01, read_dur)), '-i', track['path']]
            source_trim = ''
            parts = [
                f"[{input_idx}:{metadata['audio_index']}]aresample={RATE}",
                'aformat=sample_fmts=fltp:channel_layouts=stereo',
                'asetpts=PTS-STARTPTS',
            ]
            parts.extend(_processed_audio_shifted(track, length, audible_length, seek_out, min(length - seek_out, needed_timeline)))
        else:
            clamped = dict(track)
            if metadata.get('format_name') != 'wav':
                clamped['out'] = min(track['out'], track.get('in', 0.0) + needed_timeline * speed)
            else:
                clamped['out'] = min(track['out'], track.get('in', 0.0) + needed_timeline * speed)
            source_args, source_trim = engine._audio_input(clamped, metadata)
            # Ensure video stream decoding is disabled if audio came from a video file.
            source_args = [value for arg in source_args for value in (['-vn', arg] if arg == '-i' else [arg])]
            args += source_args
            if source_trim:
                source_trim += 'asetpts=PTS-STARTPTS,'
            parts = [
                f"[{input_idx}:{metadata['audio_index']}]{source_trim}aresample={RATE}",
                'aformat=sample_fmts=fltp:channel_layouts=stereo',
                'asetpts=PTS-STARTPTS',
            ]
            # Preserve exact fade_out timing using original length/audible_length, then bound output duration.
            process_len = min(length, needed_timeline)
            parts.extend(_processed_audio_shifted(track, length, audible_length, 0.0, process_len))

        if eff_start > mix_t0 + 1e-9:
            delay_samples = round((eff_start - mix_t0) * RATE)
            if delay_samples > 0:
                parts.extend([f'adelay={delay_samples}S:all=1', 'asetpts=N/SR/TB'])
        elif eff_start < mix_t0 - 1e-9:
            skip_samples = round((mix_t0 - eff_start) * RATE)
            if skip_samples > 0:
                parts.extend([f'atrim=start_sample={skip_samples}', 'asetpts=N/SR/TB'])
        parts.extend([f'apad=whole_dur={engine._fmt(mix_span)}', f'atrim=duration={engine._fmt(mix_span)}'])
        filters.append(','.join(parts) + f'[music{offset}]')
        mix_labels.append(f'music{offset}')

    # 3. Attached element sounds intersecting [mix_t0, t1) — never creates multi-GB WAVs.
    if not project.get('_original_speech_only'):
        has_any_sound = any(
            item.get('sound_effect', {}).get('preset', 'none') != 'none'
            and float(item['start']) < t1 and float(item['end']) > mix_t0
            for item in project.get('layers', []) + project.get('captions', [])
        )
        if has_any_sound:
            from . import sound_mix
            cue_wav = sound_mix.mix(project, scratch, cancel, start=mix_t0, end=t1)
            if cue_wav is not None:
                cue_idx = args.count('-i')
                args += ['-threads', '1', '-vn', '-i', str(cue_wav)]
                filters.append(
                    f"[{cue_idx}:a]aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"asetpts=N/SR/TB,apad=whole_dur={engine._fmt(mix_span)},atrim=duration={engine._fmt(mix_span)}[cuesound]"
                )
                mix_labels.append('cuesound')

    trim_start = s0 - mix_s0
    trim_end = s1 - mix_s0
    norm_labels = []
    for idx, label in enumerate(mix_labels):
        norm = f'amixin{idx}'
        filters.append(f'[{label}]asetnsamples=n=1024:p=0[{norm}]')
        norm_labels.append(norm)
    filters.append(
        ''.join(f'[{label}]' for label in norm_labels)
        + f'amix=inputs={len(norm_labels)}:duration=longest:dropout_transition=0:normalize=0,'
        + f'alimiter=limit=0.95:level=0:latency=1,apad,atrim=start_sample={trim_start}:end_sample={trim_end},asetpts=N/SR/TB,asetnsamples=n=1024:p=0[outaudio]'
    )
    return args, ';\n'.join(filters)


def audio(project, start, cancel, metrics=None):
    rows = model.timeline(project)
    total = model.project_duration(project)
    total_samples = round(total * RATE)
    cursor = max(0, min(total_samples, round(float(start) * RATE)))
    if cursor >= total_samples:
        return
    merged_tracks = engine._continuous_audio(project.get('audio', []))
    win_idx = 0
    while cursor < total_samples and not cancel.is_set():
        window_seconds = FIRST_AUDIO_WINDOW_SECONDS if win_idx == 0 else AUDIO_WINDOW_SECONDS
        span_samples = round(window_seconds * RATE)
        s1 = min(total_samples, cursor + span_samples)
        # Avoid cutting an audio window inside a clip acrossfade overlap when possible.
        t1 = s1 / RATE
        for i in range(len(rows) - 1):
            overlap = rows[i]['overlap']
            if overlap > 0:
                ov_begin = rows[i + 1]['start']
                ov_end = rows[i]['start'] + rows[i]['duration']
                if ov_begin < t1 < ov_end:
                    before = round(ov_begin * RATE)
                    after = min(total_samples, round(ov_end * RATE))
                    s1 = before if before > cursor else after
                    break
        if s1 <= cursor:
            break
        prep_start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='clipcanvas-lazy-audio-') as scratch:
            args, graph = _build_audio_window_graph(
                project, rows, merged_tracks, total, cursor, s1, scratch, cancel,
            )
            path = Path(scratch) / 'filters.txt'
            path.write_text(graph, encoding='utf-8')
            if metrics is not None:
                metrics.record_audio_window(win_idx, (time.monotonic() - prep_start) * 1000.0)
            on_launch = (lambda: metrics.mark_audio_ffmpeg()) if (metrics is not None and win_idx == 0) else None
            expected_bytes = (s1 - cursor) * BYTES_PER_SAMPLE_FRAME
            delivered_bytes = 0
            for data in _read(
                args + ['-filter_complex_script', str(path),
                        '-map', '[outaudio]', '-vn', '-threads', '1',
                        '-c:a', 'pcm_s16le', '-ar', str(RATE), '-ac', str(CHANNELS),
                        '-f', 's16le', 'pipe:1'],
                scratch, cancel, 4096, on_launch=on_launch,
            ):
                if delivered_bytes + len(data) > expected_bytes:
                    data = data[:expected_bytes - delivered_bytes]
                if not data:
                    break
                delivered_bytes += len(data)
                yield data
                if delivered_bytes >= expected_bytes:
                    break
            if not cancel.is_set() and delivered_bytes < expected_bytes:
                # Pad any tiny rounding tail with silence so A/V sample counts stay exact.
                yield bytes(expected_bytes - delivered_bytes)
        cursor = s1
        win_idx += 1
