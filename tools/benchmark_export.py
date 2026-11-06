#!/usr/bin/env python3
"""Compare full export speed and sampled FFmpeg peak RSS on the same timeline.

Generated media and exports are temporary. --project reads an existing project
without changing it or its sources. Run without competing CPU-heavy workloads.
The legacy case uses the previous medium/one-filter-thread export settings.
"""
import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipcanvas import color_grading, engine, model
from benchmark_crop_memory import resident_kib


def measure(project, output, speed, legacy=False, encoding='software'):
    stopped = threading.Event()
    peak = 0
    children = Path(f'/proc/{os.getpid()}/task/{threading.get_native_id()}/children')
    def sample():
        nonlocal peak
        while not stopped.wait(.01):
            try:
                pids = children.read_text().split()
            except FileNotFoundError:
                continue
            peak = max(peak, sum(resident_kib(pid) for pid in pids))
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.monotonic()
    messages = []
    def progress(value, message):
        if not messages or messages[-1] != message:
            messages.append(message)
    try:
        budget = mock.patch.object(engine, '_render_filter_threads', return_value=1) if legacy else contextlib.nullcontext()
        with budget:
            engine.render(project, output, quality='master', speed=speed, encoding=encoding, progress=progress)
        elapsed = time.monotonic()-started
    finally:
        stopped.set();monitor.join()
    info = engine.probe(output)
    backend = next((message for message in reversed(messages) if message.startswith('Rendering with ')), '')
    return {'seconds': round(elapsed, 3), 'peak_ffmpeg_rss_mib': round(peak/1024, 2),
            'bytes': output.stat().st_size,
            'encoding': encoding, 'rendering_status': backend,
            'video': {key: info[key] for key in ('width', 'height', 'fps', 'duration')}}


def generated_project(root, layered):
    binary, env = engine._tool('ffmpeg')
    source = root/'source.mp4'
    subprocess.run([binary, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'testsrc2=s=1920x1080:r=24:d=1.5', '-f', 'lavfi', '-i',
        'sine=f=440:r=48000:d=1.5', '-c:v', 'libx264', '-preset', 'ultrafast',
        '-crf', '18', '-threads', '2', '-c:a', 'aac', str(source)], env=env, check=True)
    project = model.new_project(1080, 1080)
    for index in range(3):
        clip = model.new_clip(source)
        clip.update(color_grade=color_grading.PRESETS['Adventure'],
                    saturation=color_grading.PRESET_SATURATION['Adventure'])
        if layered:
            clip.update(transition='fade', transition_duration=.25, zoom=1.2, offset_x=.1)
        project['clips'].append(clip)
    if layered:
        project['layers'] = [model.new_text('ClipCanvas export', .2, 2.5)]
        project['layers'][0]['animation'].update(enter='fade', exit='fade')
        project['captions'] = [model.new_caption('Generated benchmark', 1, 2.8)]
        track = model.new_audio(source)
        track.update(volume=.3, audio_effects={'echo_enabled': True, 'echo_delay_ms': 100})
        project['audio'] = [track]
    engine._source_rate(project)
    return project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, help='Read a saved project without modifying it')
    parser.add_argument('--layered', action='store_true', help='Add crops, transitions, title, caption and echo to generated media')
    parser.add_argument('--hardware', action='store_true', help='Compare automatic hardware encoding with software Fast')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('Repeats must be positive.')
    if args.project and args.layered:
        parser.error('--layered applies only to generated media.')
    runs = []
    with tempfile.TemporaryDirectory(prefix='clipcanvas-export-benchmark-') as directory:
        root = Path(directory)
        project = model.load_project(args.project) if args.project else generated_project(root, args.layered)
        engine._source_rate(project)
        original = copy.deepcopy(project)
        # Match the application after media import rather than timing cold probes.
        sources = [clip for clip in project['clips'] if not model.is_still(clip)]
        sources += project['audio'] + [layer for layer in project['layers'] if layer['kind'] == 'video']
        for item in sources:
            engine.probe(item['path'])
        cases = ([('software-fast', 'fast', 'software'), ('automatic-fast', 'fast', 'auto')] if args.hardware else
                 [('legacy', 'balanced', 'software'), ('fast', 'fast', 'software'),
                  ('balanced', 'balanced', 'software'), ('compact', 'compact', 'software')])
        for repeat in range(args.repeats):
            for name, speed, encoding in (cases if repeat % 2 == 0 else list(reversed(cases))):
                output = root/f'{repeat}-{name}.mp4'
                result = measure(project, output, speed, legacy=name == 'legacy', encoding=encoding)
                result.update(mode=name, repeat=repeat+1)
                runs.append(result)
                output.unlink()
                print(json.dumps(result), flush=True)
        if project != original:
            raise RuntimeError('Export modified the in-memory project.')
        summary = []
        for name, _, _ in cases:
            selected = [run for run in runs if run['mode'] == name]
            summary.append({'mode': name,
                'median_seconds': round(statistics.median(run['seconds'] for run in selected), 3),
                'max_peak_ffmpeg_rss_mib': max(run['peak_ffmpeg_rss_mib'] for run in selected)})
        report = {'fixture': 'saved project' if args.project else 'layered' if args.layered else 'graded cuts',
                  'timeline_seconds': model.project_duration(project), 'quality': 'master',
                  'export_filter_threads': engine._render_filter_threads(False),
                  'runs': runs, 'summary': summary}
        if args.output:
            args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'summary': summary}), flush=True)


if __name__ == '__main__':
    main()
