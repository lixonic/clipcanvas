#!/usr/bin/env python3
"""Measure first native picture/audio on a long timeline, then cancel playback.

The generated timeline has 21 distinct 15-second 1080p H.264/AAC source paths.
Its Fast preview cache is isolated from the user's cache. The warm run explicitly
prepares all proxy files before measurement. No project or source is modified.
Run without competing CPU-heavy jobs when comparing startup measurements.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipcanvas import engine, live_player, model, preview_cache, color_grading
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst


def measure(project, start, edge, timeout=180):
    events, audio, pictures, errors = [], [], [], []
    begun = time.monotonic()
    threads_before = set(threading.enumerate())
    description = live_player.pipeline_description
    def picture(sample):
        pictures.append(time.monotonic()-begun)
        return Gst.FlowReturn.OK
    with mock.patch.object(live_player, 'pipeline_description', side_effect=lambda:description(
            'audio/x-raw,format=S16LE,rate=48000,channels=2 ! appsink name=audio emit-signals=true sync=true')):
        player = live_player.StreamPlayer(project, start, True, picture, errors.append, lambda:None,
            preview_edge=edge, buffering=lambda active,message:events.append((time.monotonic()-begun,active,message)))
    def sound(sink):
        sink.emit('pull-sample')
        audio.append(time.monotonic()-begun)
        return Gst.FlowReturn.OK
    player.pipeline.get_by_name('audio').connect('new-sample', sound)
    context = GLib.MainContext.default()
    try:
        while time.monotonic()-begun<timeout and not errors and not (pictures and audio):
            while context.pending():context.iteration(False)
            time.sleep(.005)
        if errors or not pictures or not audio:
            raise RuntimeError('; '.join(errors) or 'Preview did not start before timeout.')
        result = {'preview_edge':edge, 'start_seconds':start,
            'route':player.metrics.route, 'route_reason':player.metrics.route_reason,
            'first_picture_ms':round(pictures[0]*1000,2),
            'first_audio_ms':round(audio[0]*1000,2),
            'events':[[round(at,3),active,message] for at,active,message in events]}
    finally:
        stopping = time.monotonic()
        player.stop();player.thread.join(10)
        if player.thread.is_alive():raise RuntimeError('Preview producer did not stop after cancellation.')
        remaining = [thread.name for thread in threading.enumerate() if thread not in threads_before]
        if remaining:raise RuntimeError('Preview left Python workers running: '+', '.join(remaining))
    result['stop_ms'] = round((time.monotonic()-stopping)*1000,2)
    result['metrics'] = player.metrics.snapshot()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,help='Read an existing project without modifying it')
    parser.add_argument('--grade',choices=list(color_grading.PRESETS),help='Grade every generated clip')
    parser.add_argument('--clips',type=int,default=21)
    parser.add_argument('--clip-seconds',type=float,default=15)
    parser.add_argument('--width',type=int,default=1920)
    parser.add_argument('--height',type=int,default=1080)
    parser.add_argument('--start',type=float,default=0)
    parser.add_argument('--edge',type=int,default=640)
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--dissolve-last',action='store_true',help='Add a 0.5 s dissolve at the final join')
    parser.add_argument('--captions',type=int,default=0,help='Distribute N word-highlight captions across the timeline')
    parser.add_argument('--layer-sound',action='store_true',help='Add a distant layer sound effect near the end')
    parser.add_argument('--reuse-source',action='store_true',help='Reuse one generated source file across clips (for 60m/180m runs)')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args();Gst.init(None)
    if args.clips<1 or args.clip_seconds<=0 or args.repeats<1:parser.error('Clip count, duration and repeats must be positive.')
    with tempfile.TemporaryDirectory(prefix='clipcanvas-long-preview-benchmark-') as folder:
        root = Path(folder)
        if args.project:
            project = model.load_project(args.project)
        else:
            source = root/'source.mp4';binary,env = engine._tool('ffmpeg')
            subprocess.run([binary,'-v','error','-f','lavfi','-i',
                f'testsrc2=s={args.width}x{args.height}:r=24:d={args.clip_seconds}',
                '-f','lavfi','-i',f'sine=f=430:r=48000:d={args.clip_seconds}',
                '-c:v','libx264','-preset','ultrafast','-g','240','-threads','2','-c:a','aac',str(source)],
                env=env,check=True)
            project = model.new_project(args.width,args.height)
            for index in range(args.clips):
                if args.reuse_source:
                    target = source
                else:
                    target = root/f'clip-{index:02}.mp4'
                    shutil.copyfile(source,target)
                clip = model.new_clip(target)
                if args.grade:
                    clip.update(color_grade=color_grading.PRESETS[args.grade],
                                saturation=color_grading.PRESET_SATURATION.get(args.grade,1))
                project['clips'].append(clip)
            if args.dissolve_last and len(project['clips']) >= 2:
                project['clips'][-2].update(transition='dissolve', transition_duration=0.5)
            total = model.project_duration(project)
            if args.captions > 0:
                step = total / args.captions
                for i in range(args.captions):
                    cue = model.new_caption(f'Caption line {i} benchmark text', i * step, min(total - 0.1, i * step + min(2.0, step * 0.8)))
                    cue['animation']['text_effect'] = 'highlight'
                    project['captions'].append(cue)
            if args.layer_sound and total > 2:
                item = model.new_text('Ping', max(0.0, total - 10.0), max(1.0, total - 9.0))
                item['sound_effect'].update(preset='pop', volume=0.5, trigger='enter')
                project['layers'].append(item)
        # Match the application after media import, including for saved projects.
        for clip in project['clips']:
            if not model.is_still(clip):engine.probe(clip['path'])
        duration = model.project_duration(project)
        if not 0<=args.start<duration:parser.error('Choose a start inside the project.')
        runs = []
        for repeat in range(args.repeats):
            cache = root/f'cache-{repeat}'
            with mock.patch.object(preview_cache,'directory',return_value=cache):
                for mode in (('cold','warm') if args.edge else ('originals',)):
                    if mode=='warm':
                        with tempfile.TemporaryDirectory(prefix='warm-leases-',dir=root) as leases:
                            for clip in project['clips']:
                                if not model.is_still(clip):preview_cache.prepare(clip['path'],args.edge,leases)
                    result = measure(project,args.start,args.edge)
                    result.update(mode=mode,repeat=repeat+1,clips=len(project['clips']),timeline_seconds=duration,
                                  canvas_dimensions=[project['width'],project['height']],
                                  graded_clips=sum(color_grading.active(clip) for clip in project['clips']),
                                  cached_files_after=len(list(cache.glob('*.mp4'))))
                    runs.append(result);print(json.dumps(result),flush=True)
        if args.output:args.output.write_text(json.dumps(runs,indent=2)+'\n')


if __name__=='__main__':main()
