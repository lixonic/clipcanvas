#!/usr/bin/env python3
"""Measure generated crop playback graphs with a hard resident-memory watchdog.

This does not open user projects or retain rendered video. Each short FFmpeg
process is killed if it exceeds the requested memory ceiling or timeout.
Peak RSS covers the complete rendering graph, excluding the GTK/GStreamer app.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipcanvas import engine, model


def resident_kib(pid):
    """Read Linux's process high-water mark without adding a dependency."""
    try:
        fields = Path(f'/proc/{pid}/status').read_text().splitlines()
    except (FileNotFoundError, ProcessLookupError):
        return 0
    return max([int(line.split()[1]) for line in fields
                if line.startswith(('VmRSS:', 'VmHWM:'))] or [0])


def measure(project, memory_limit_mib=300, timeout=30, renderer=engine):
    """Drain the actual full-resolution crop graph; return bounded peak RSS."""
    with tempfile.TemporaryDirectory(prefix='clipcanvas-crop-memory-') as directory:
        root = Path(directory)
        args, graph, duration = renderer._graph(project, True, root, preview_edge=0)
        script = root/'filters.txt'; script.write_text(graph)
        binary, env = renderer._tool('ffmpeg')
        command = [binary, '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-filter_complex_threads', '1']+args+[
                   '-filter_complex_script', str(script), '-map', '[outvideo]',
                   '-map', '[outaudio]', '-c:v', 'rawvideo', '-pix_fmt', 'yuv420p',
                   '-threads', '1', '-c:a', 'pcm_s16le', '-t', str(duration),
                   '-progress', 'pipe:1', '-f', 'null', '-']
        started = time.monotonic(); peak = 0; stopped = None
        with (root/'output.log').open('wb') as output, (root/'error.log').open('wb') as errors:
            process = subprocess.Popen(command, stdout=output, stderr=errors,
                                       stdin=subprocess.DEVNULL, env=env)
            try:
                while process.poll() is None:
                    peak = max(peak, resident_kib(process.pid))
                    if peak > memory_limit_mib*1024:
                        stopped = 'memory_limit'
                        process.kill()
                        break
                    if time.monotonic()-started > timeout:
                        stopped = 'timeout'
                        process.kill()
                        break
                    time.sleep(.005)
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=5)
        progress = (root/'output.log').read_text()
        frames = [int(line.split('=', 1)[1]) for line in progress.splitlines()
                  if line.startswith('frame=')]
        return {'peak_rss_mib': round(peak/1024, 2),
                'wall_seconds': round(time.monotonic()-started, 3),
                'frames': max(frames or [0]), 'returncode': process.returncode,
                'stopped': stopped, 'error': (root/'error.log').read_text()[-1000:]}


def generated_project(source, width=960, height=540, clips=1, zoom=4, offset=.3,
                      transition='none'):
    project = model.new_project(width, height)
    project['clips'] = [model.new_clip(source) for _ in range(clips)]
    for clip in project['clips']:
        clip.update(zoom=zoom, offset_x=offset, offset_y=-offset/2,
                    transition=transition, transition_duration=.2)
    return project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory-limit-mib', type=int, default=300)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--width', type=int, default=960)
    parser.add_argument('--height', type=int, default=540)
    parser.add_argument('--clips', type=int, nargs='+', default=[1, 10])
    parser.add_argument('--zooms', type=float, nargs='+', default=[1, 4])
    parser.add_argument('--offsets', type=float, nargs='+', default=[.3])
    parser.add_argument('--transitions', nargs='+', default=['none', 'fade'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='clipcanvas-crop-source-') as directory:
        source = Path(directory)/'source.mp4'; binary, env = engine._tool('ffmpeg')
        subprocess.run([binary, '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=s=320x180:r=30:d=0.5', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-threads', '1', str(source)], env=env, check=True)
        results = []
        for clips in args.clips:
            for zoom in args.zooms:
                for offset in args.offsets:
                    for transition in args.transitions:
                        if clips == 1 and transition != 'none':
                            continue
                        p = generated_project(source, args.width, args.height, clips, zoom,
                                              offset=offset, transition=transition)
                        result = {'clips': clips, 'zoom': zoom, 'offset': offset,
                                  'transition': transition,
                                  **measure(p, args.memory_limit_mib, args.timeout)}
                        results.append(result); print(json.dumps(result), flush=True)
        if args.output:
            args.output.write_text(json.dumps(results, indent=2)+'\n')


if __name__ == '__main__':
    main()
