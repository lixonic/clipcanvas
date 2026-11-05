"""Bounded picture windows with one continuous soundtrack producer."""
import copy
import math
from pathlib import Path
import subprocess
import tempfile
import threading

from . import engine, model, preview_cache


def supported(project):
    # Overlapping transitions retain the established composition path.
    first = next((clip for clip in project['clips'] if not model.is_still(clip)), None)
    if first is not None and not first.get('source_fps'):
        return False
    return len(project['clips']) > 2 and not project.get('layers') and not project.get('captions') and all(
        not model.is_image(clip) and clip.get('transition', 'none') == 'none' for clip in project['clips'])


def _read(arguments, scratch, cancel, block):
    if cancel.is_set():return
    binary, env = engine._tool('ffmpeg')
    with open(Path(scratch)/'decoder.log', 'wb') as log:
        process = subprocess.Popen([binary, '-nostdin', '-v', 'error',
            '-filter_complex_threads', '1'] + arguments, stdout=subprocess.PIPE,
            stderr=log, stdin=subprocess.DEVNULL, env=env)
        finished = threading.Event()
        def watch():
            while not finished.wait(.02):
                if cancel.is_set():
                    engine._stop(process, immediate=True)
                    return
        watcher = threading.Thread(target=watch, daemon=True);watcher.start()
        try:
            while not cancel.is_set():
                data = process.stdout.read(block)
                if not data:break
                yield data
            if not cancel.is_set() and process.wait():
                log.flush()
                raise engine.RenderError(Path(log.name).read_text(errors='replace')[-1600:])
        finally:
            finished.set();engine._stop(process, immediate=True)
            process.stdout.close();watcher.join()


def audio(project, start, cancel):
    with tempfile.TemporaryDirectory(prefix='clipcanvas-lazy-audio-') as scratch:
        args, graph, total = engine._graph(project, True, scratch, audio_only=True, cancel=cancel)
        # Disable picture decoding for the continuous audio mix.
        args = [value for arg in args for value in (['-vn', arg] if arg == '-i' else [arg])]
        path = Path(scratch)/'filters.txt';path.write_text(graph)
        yield from _read(args + ['-filter_complex_script', str(path), '-ss', engine._fmt(start),
            '-map', '[outaudio]', '-vn', '-threads', '1', '-c:a', 'pcm_s16le',
            '-ar', '48000', '-ac', '2', '-t', engine._fmt(total-start), '-f', 's16le', 'pipe:1'],
            scratch, cancel, 4096)


def video(project, start, edge, cancel):
    fps = project['fps'];width, height = engine.preview_dimensions(project, edge)
    size = width*height*3//2
    counts = [math.floor(model.clip_duration(clip)*fps+.5+1e-7) for clip in project['clips']]
    offsets = [0]
    for count in counts:offsets.append(offsets[-1]+count)
    first = math.ceil(start*fps-1e-7)
    last = min(offsets[-1], math.ceil(model.project_duration(project)*fps-1e-7))
    index = next((i for i in range(len(counts)) if offsets[i+1]>first),len(counts))
    while index<len(counts) and first<last and not cancel.is_set():
        end = min(len(counts),(index//2+1)*2)
        stop = min(last,offsets[end])
        window = copy.deepcopy(project)
        window['clips'] = window['clips'][index:end]
        window['audio']=[]
        with tempfile.TemporaryDirectory(prefix='clipcanvas-lazy-video-') as scratch:
            proxies = {}
            for clip in window['clips']:
                if not model.is_still(clip):
                    proxy=preview_cache.lease_available(clip['path'],edge,scratch)
                    if proxy:proxies[clip['path']]=proxy
            local = (first-offsets[index])/fps
            args, graph, _ = engine._graph(window, True, scratch, video_only=True,
                cancel=cancel, preview_edge=edge, picture_start=local, proxies=proxies)
            path=Path(scratch)/'filters.txt';path.write_text(graph)
            delivered=0
            for data in _read(args+['-filter_complex_script',str(path),'-ss',engine._fmt(local),
                '-map','[outvideo]','-an','-frames:v',str(stop-first),'-threads','1',
                '-c:v','rawvideo','-pix_fmt','yuv420p','-f','rawvideo','pipe:1'],scratch,cancel,size):
                if len(data)!=size:raise engine.RenderError('Incomplete preview picture.')
                delivered+=1
                yield data
            if not cancel.is_set() and delivered!=stop-first:
                raise engine.RenderError('Preview window ended before its final picture.')
        first=stop;index=end
