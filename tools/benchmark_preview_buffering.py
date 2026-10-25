#!/usr/bin/env python3
"""Measure native preview startup and rebuffering, with silent PCM capture.

Optionally read a saved project without modifying it. Defaults generate a
long-GOP source, split near its end and a continuous independent soundtrack.
Run warm repeats after first-use cache preparation. Avoid running CPU-heavy
tests at the same time when comparing timings.
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipcanvas import engine, live_player, model
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst


def measure(project, start, edge, timeout=120):
    events, audio, pictures, errors, ended = [], [], [], [], []
    begun = time.monotonic()
    description = live_player.pipeline_description
    def picture(sample):
        buffer = sample.get_buffer()
        pictures.append((buffer.pts/Gst.SECOND,time.monotonic()-begun))
        return Gst.FlowReturn.OK
    with mock.patch.object(live_player,'pipeline_description',side_effect=lambda:description(
            'audio/x-raw,format=S16LE,rate=48000,channels=2 ! appsink name=audio emit-signals=true sync=true')):
        player = live_player.StreamPlayer(project,start,True,picture,errors.append,lambda:ended.append(True),
            preview_edge=edge,buffering=lambda active,message:events.append((time.monotonic()-begun,active,message)))
    def sound(sink):
        sample = sink.emit('pull-sample');buffer = sample.get_buffer()
        audio.append((buffer.pts/Gst.SECOND,buffer.duration/Gst.SECOND,time.monotonic()-begun))
        return Gst.FlowReturn.OK
    player.pipeline.get_by_name('audio').connect('new-sample',sound)
    context = GLib.MainContext.default()
    try:
        while time.monotonic()-begun<timeout and not ended and not errors:
            while context.pending():context.iteration(False)
            time.sleep(.005)
        if errors or not ended or not audio:
            raise RuntimeError('; '.join(errors) or 'Preview did not finish within the timeout.')
        first_play = next((at for at,active,_ in events if not active),None)
        waits = [event for event in events if event[1] and first_play is not None and event[0]>first_play]
        holes = [b[0]-a[0]-a[1] for a,b in zip(audio,audio[1:])]
        return {'preview_edge':edge,'first_picture_ms':round(pictures[0][1]*1000,2),
            'first_audio_ms':round(audio[0][2]*1000,2),'audio_buffers':len(audio),
            'max_audio_timestamp_gap_ms':round(max([0]+holes)*1000,2),
            'rebuffer_events':len(waits),'wall_seconds':round(time.monotonic()-begun,3),
            'events':[[round(at,3),active,message] for at,active,message in events]}
    finally:
        player.stop();player.thread.join(8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path)
    parser.add_argument('--start',type=float,default=5)
    parser.add_argument('--end',type=float,default=9)
    parser.add_argument('--split',type=float,default=6)
    parser.add_argument('--transition',default='none',choices=('none','fade','circle_wipe','flow'))
    parser.add_argument('--edges',type=int,nargs='+',default=[640,640,0])
    parser.add_argument('--output',type=Path)
    args = parser.parse_args();Gst.init(None)
    with tempfile.TemporaryDirectory(prefix='clipcanvas-preview-benchmark-') as directory:
        if args.project:p = model.load_project(args.project)
        else:
            source = Path(directory)/'long-gop.mp4';binary,env = engine._tool('ffmpeg')
            subprocess.run([binary,'-v','error','-f','lavfi','-i','testsrc2=s=1920x1080:r=24:d=10',
                '-f','lavfi','-i','sine=f=430:r=48000:d=10','-c:v','libx264','-preset','ultrafast',
                '-g','240','-threads','2','-c:a','aac',str(source)],env=env,check=True)
            p = model.new_project();p['clips'] = [model.new_clip(source)];p['clips'][0]['volume'] = 0
            p['audio'] = [model.new_audio(source)]
        p = copy.deepcopy(p)
        if not 0<=args.start<args.end<=model.project_duration(p):parser.error('Choose a range inside the project.')
        original_rows = model.timeline(p)
        p['clips'] = [clip for clip,row in zip(p['clips'],original_rows) if row['start']<args.end]
        last = model.timeline(p)[-1]
        p['clips'][-1]['out'] = p['clips'][-1]['in']+(args.end-last['start'])*p['clips'][-1].get('speed',1)
        index = next((i for i,row in enumerate(model.timeline(p)) if row['start']<args.split<row['start']+row['duration']),None)
        if index is not None:p = model.split_item(p,'clips',index,args.split)
        row_index = next((i for i,row in enumerate(model.timeline(p)) if abs(row['start']+row['duration']-args.split)<1e-6),None)
        if row_index is not None:p['clips'][row_index].update(transition=args.transition,transition_duration=.5)
        runs = []
        for edge in args.edges:
            result = measure(p,args.start,edge);runs.append(result);print(json.dumps(result),flush=True)
        if args.output:args.output.write_text(json.dumps(runs,indent=2)+'\n')


if __name__ == '__main__':main()
