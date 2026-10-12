#!/usr/bin/env python3
"""Measure live Play startup on synthetic original media without opening GTK.

Run with /usr/bin/python3 tools/benchmark_startup.py --output /tmp/startup.json.
The reference disables the graph's input video seek; both variants otherwise use
the current live_stream transport. GStreamer consumes raw frames and PCM through
the native appsrc/Matroska path, with a silent fake audio sink. Generated media
and sprites stay in temporary folders and are removed after the run.
"""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipcanvas import audio_effects, engine, model

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst


def measure(project, start, variant, timeout):
    """Time the first complete native sample, including warm graph preparation."""
    pipeline = Gst.parse_launch(
        'appsrc name=source block=true max-bytes=1048576 caps=video/x-matroska '
        '! matroskademux name=demux '
        'demux.video_0 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=300000000 '
        '! videoconvert ! video/x-raw,format=RGB,pixel-aspect-ratio=1/1 '
        '! appsink name=picture emit-signals=true sync=true max-buffers=1 drop=true '
        'demux.audio_0 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=300000000 '
        '! audioconvert ! audioresample ! fakesink sync=true')
    cancel = threading.Event()
    finished = threading.Event()
    errors = []
    first_read = []
    first_sample = []
    graph_times = []
    graph_builder = engine._graph
    begun = None

    def build_graph(*args, **kwargs):
        if variant == 'reference':
            kwargs['picture_start'] = None
        graph_start = time.perf_counter()
        try:
            return graph_builder(*args, **kwargs)
        finally:
            graph_times.append((time.perf_counter()-graph_start)*1000)

    def sample(sink, signal):
        value = sink.emit(signal)
        if value is not None and not first_sample:
            caps = value.get_caps().get_structure(0)
            buffer = value.get_buffer()
            first_sample.append({
                'first_native_sample_ms': (time.perf_counter()-begun)*1000,
                'width': caps.get_value('width'),
                'height': caps.get_value('height'),
                'format': caps.get_value('format'),
                'frame_bytes': buffer.get_size(),
                'pts_seconds': buffer.pts/Gst.SECOND
                    if buffer.pts != Gst.CLOCK_TIME_NONE else None,
            })
            finished.set()
        return Gst.FlowReturn.OK

    sink = pipeline.get_by_name('picture')
    sink.connect('new-preroll', lambda s: sample(s, 'pull-preroll'))
    sink.connect('new-sample', lambda s: sample(s, 'pull-sample'))
    source = pipeline.get_by_name('source')
    bus = pipeline.get_bus()

    def feed():
        stream = engine.live_stream(project, start, cancel, preview_edge=0)
        try:
            for data in stream:
                if cancel.is_set():
                    break
                if not first_read:
                    first_read.append((time.perf_counter()-begun)*1000)
                if source.emit('push-buffer', Gst.Buffer.new_wrapped(data)) != Gst.FlowReturn.OK:
                    break
            if not cancel.is_set():
                source.emit('end-of-stream')
        except Exception as problem:
            errors.append(str(problem))
            finished.set()
        finally:
            stream.close()

    with mock.patch.object(engine, '_graph', build_graph):
        pipeline.set_state(Gst.State.PLAYING)
        begun = time.perf_counter()
        worker = threading.Thread(target=feed, daemon=True)
        worker.start()
        try:
            while not finished.wait(.02):
                message = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message is not None:
                    if message.type == Gst.MessageType.ERROR:
                        problem, detail = message.parse_error()
                        errors.append(f'{problem.message}: {detail or ""}')
                    else:
                        errors.append('Stream ended before its first video sample.')
                    break
                if time.perf_counter()-begun >= timeout:
                    errors.append(f'No video sample within {timeout:g} seconds.')
                    break
        finally:
            cancel.set()
            pipeline.set_state(Gst.State.NULL)
            worker.join(timeout=8)
            if worker.is_alive():
                errors.append('Live stream worker did not stop within 8 seconds.')

    if errors or not first_sample:
        raise RuntimeError(f'{variant} at {start:g}s: {"; ".join(errors) or "No video sample"}')
    result = first_sample[0]
    expected = (project['width'], project['height'])
    if (result['width'], result['height']) != expected:
        raise RuntimeError(f'Expected full resolution {expected}, got {result["width"]}×{result["height"]}.')
    if result['format'] != 'RGB' or result['frame_bytes'] < expected[0]*expected[1]*3:
        raise RuntimeError('Native sample did not contain a complete RGB frame.')
    result.update(variant=variant, start_seconds=start,
                  first_read_ms=first_read[0], graph_ms=sum(graph_times))
    return {key: round(value, 2) if isinstance(value, float) else value
            for key, value in result.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--duration', type=float, default=50)
    parser.add_argument('--starts', type=float, nargs='+', default=[0, 15, 45])
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--echo-track', action='store_true',
                        help='Include an independent stereo WAV with echo history.')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0 or args.width % 2 or args.height % 2:
        parser.error('width and height must be positive even numbers')
    if args.duration <= 0 or any(at < 0 or at >= args.duration-1/30 for at in args.starts):
        parser.error('starts must be inside the generated source duration')
    if args.repeats < 1 or args.timeout <= 0:
        parser.error('repeats and timeout must be positive')
    Gst.init(None)
    binary, env = engine._tool('ffmpeg')
    runs = []
    with tempfile.TemporaryDirectory(prefix='clipcanvas-startup-benchmark-') as directory:
        root = Path(directory)
        source = root/'source.mp4'
        subprocess.run([
            binary, '-v', 'error', '-f', 'lavfi', '-i',
            f'testsrc2=s={args.width}x{args.height}:r=30:d={args.duration}',
            '-f', 'lavfi', '-i', f'sine=f=430:r=48000:d={args.duration}',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-g', '60',
            '-threads', '2', '-c:a', 'aac', str(source)], env=env, check=True)
        project = model.new_project(args.width, args.height)
        project['clips'] = [model.new_clip(source)]
        if args.echo_track:
            audio = root/'echo.wav'
            subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i',
                            f'sine=f=880:r=48000:d={args.duration}',
                            '-ac', '2', '-c:a', 'pcm_s16le', str(audio)], env=env, check=True)
            track = model.new_audio(audio)
            track.update(start=.3, volume=.25, fade_in=.2)
            track['audio_effects'] = audio_effects.preset('Echo')
            project['audio'] = [track]
        # new_clip/new_audio have already populated the size/mtime probe cache.
        for repeat in range(args.repeats):
            for start in args.starts:
                # Alternate order across repetitions to reduce warm-up bias.
                variants = ('reference', 'optimized') if repeat % 2 == 0 else ('optimized', 'reference')
                for variant in variants:
                    result = measure(project, start, variant, args.timeout)
                    result['repeat'] = repeat+1
                    runs.append(result)
    summary = []
    for start in args.starts:
        values = {}
        for variant in ('reference', 'optimized'):
            values[variant] = statistics.median(
                run['first_native_sample_ms'] for run in runs
                if run['variant'] == variant and run['start_seconds'] == start)
        summary.append({'start_seconds': start,
                        'reference_median_ms': round(values['reference'], 2),
                        'optimized_median_ms': round(values['optimized'], 2),
                        'speedup': round(values['reference']/values['optimized'], 2)})
    data = json.dumps({'resolution': [args.width, args.height], 'fps': 30,
                       'source_duration_seconds': args.duration,
                       'independent_echo_track': args.echo_track,
                       'warm_probe_cache': True, 'gstreamer': Gst.version_string(),
                       'runs': runs, 'summary': summary}, indent=2)+'\n'
    print(data, end='')
    if args.output:
        args.output.write_text(data, encoding='utf-8')


if __name__ == '__main__':
    main()
