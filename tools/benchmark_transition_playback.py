#!/usr/bin/env python3
"""Measure frame delivery through a full-resolution native preview transition.

Uses original H.264/AAC test footage, ClipCanvas's raw live stream and the same
GStreamer queues as the app. No display or speakers are needed. Records sample
delivery, not GTK paint timing; results depend on system load and decoding.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipcanvas import engine, model, transition_render
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst


def measure(project, effect, timeout=30, window=None, audio_probe=False):
    audio_sink = ('audio/x-raw,format=S16LE,rate=48000,channels=2 ! '
                  'appsink name=audio emit-signals=true sync=true max-buffers=1 drop=false'
                  if audio_probe else 'fakesink sync=true')
    pipeline = Gst.parse_launch(
        'appsrc name=source block=true max-bytes=1048576 caps=video/x-matroska '
        '! matroskademux name=demux '
        'demux.video_0 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=300000000 '
        '! videoconvert ! video/x-raw,format=RGB,pixel-aspect-ratio=1/1 '
        '! appsink name=picture emit-signals=true sync=true max-buffers=1 drop=true '
        'demux.audio_0 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=300000000 '
        '! audioconvert ! audioresample ! '+audio_sink)
    cancel = threading.Event()
    samples, errors = [], []
    audio_samples = []
    begun = time.perf_counter()

    def sample(sink):
        value = sink.emit('pull-sample')
        if value is not None:
            caps = value.get_caps().get_structure(0)
            if (caps.get_value('width'), caps.get_value('height')) != (project['width'], project['height']):
                errors.append('Preview unexpectedly changed resolution.')
            buffer = value.get_buffer()
            samples.append((buffer.pts/Gst.SECOND, time.perf_counter()-begun))
        return Gst.FlowReturn.OK

    pipeline.get_by_name('picture').connect('new-sample', sample)
    if audio_probe:
        def audio(sink):
            value=sink.emit('pull-sample')
            if value is not None:
                buffer=value.get_buffer()
                audio_samples.append((buffer.pts/Gst.SECOND,time.perf_counter()-begun,
                                      any(buffer.extract_dup(0,buffer.get_size()))))
            return Gst.FlowReturn.OK
        pipeline.get_by_name('audio').connect('new-sample',audio)
    source = pipeline.get_by_name('source')

    def feed():
        stream = engine.live_stream(project, cancel=cancel, preview_edge=0)
        try:
            for data in stream:
                if cancel.is_set() or source.emit('push-buffer', Gst.Buffer.new_wrapped(data)) != Gst.FlowReturn.OK:
                    break
            if not cancel.is_set():
                source.emit('end-of-stream')
        except Exception as problem:
            errors.append(str(problem))
        finally:
            stream.close()

    pipeline.set_state(Gst.State.PLAYING)
    worker = threading.Thread(target=feed, daemon=True)
    worker.start()
    bus = pipeline.get_bus()
    try:
        while time.perf_counter()-begun < timeout:
            message = bus.timed_pop_filtered(int(.05*Gst.SECOND), Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if errors:
                break
            if message is not None:
                if message.type == Gst.MessageType.ERROR:
                    problem, detail = message.parse_error()
                    errors.append(f'{problem.message}: {detail or ""}')
                break
        else:
            errors.append('Preview did not finish before timeout.')
    finally:
        cancel.set()
        pipeline.set_state(Gst.State.NULL)
        worker.join(timeout=8)
        if worker.is_alive():
            errors.append('Preview worker failed to stop.')
    if errors or len(samples) < 2:
        raise RuntimeError('; '.join(errors) or 'No preview pictures received.')
    row = model.timeline(project)[0]
    start, end = window or (row['duration']-row['overlap'],row['duration'])
    gaps = [(point[1]-previous[1])*1000 for previous, point in zip(samples, samples[1:])
            if start-.1 <= point[0] <= end+.1]
    result = {'effect': effect, 'frames': len(samples),
            'first_picture_ms': round(samples[0][1]*1000, 2),
            'transition_max_gap_ms': round(max(gaps), 2),
            'transition_median_gap_ms': round(statistics.median(gaps), 2),
            'transition_pts': [round(start, 3), round(end, 3)],
            'large_gaps': [[round(point[0],3),round((point[1]-previous[1])*1000,2)]
                           for previous,point in zip(samples,samples[1:]) if point[1]-previous[1]>.06]}
    if audio_probe:
        if len(audio_samples)<2:raise RuntimeError('Audio sink did not receive the mix.')
        first=audio_samples[0]
        result.update(audible_audio_buffers=sum(point[2] for point in audio_samples),
                      max_audio_lateness_ms=round(max(max(0,(point[1]-first[1])-(point[0]-first[0]))
                                                      for point in audio_samples)*1000,2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    args = parser.parse_args()
    if min(args.width, args.height) <= 0 or args.width % 2 or args.height % 2:
        parser.error('Use positive even dimensions.')
    Gst.init(None)
    binary, env = engine._tool('ffmpeg')
    runs = []
    with tempfile.TemporaryDirectory(prefix='clipcanvas-transition-delivery-') as directory:
        path = Path(directory)/'source.mp4'
        subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i',
                        f'testsrc2=s={args.width}x{args.height}:r=30:d=1.5',
                        '-f', 'lavfi', '-i', 'sine=f=430:r=48000:d=1.5',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-threads', '2',
                        '-c:a', 'aac', str(path)], env=env, check=True)
        for effect in transition_render.FAST_KEYS:
            project = model.new_project(args.width, args.height)
            project['clips'] = [model.new_clip(path), model.new_clip(path)]
            project['clips'][0].update(transition=effect, transition_duration=.5)
            run = measure(project, effect)
            runs.append(run)
            print(json.dumps(run), file=sys.stderr, flush=True)
    report = json.dumps({'resolution': [args.width, args.height], 'fps': 30,
                         'gstreamer': Gst.version_string(), 'runs': runs}, indent=2)+'\n'
    print(report, end='')
    if args.output:
        args.output.write_text(report)


if __name__ == '__main__':
    main()
