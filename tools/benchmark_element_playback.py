#!/usr/bin/env python3
"""Measure 1080p animated titles/captions with continuously clocked WAV audio.

The audio sink consumes the PCM mix silently and reports arrival lateness.
This measures native delivery rather than speakers/GTK painting. No lossy
proxy, preview encoding or lowered resolution is involved.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from benchmark_transition_playback import measure, Gst
from clipcanvas import engine, model


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();Gst.init(None)
    binary,env=engine._tool('ffmpeg');runs=[]
    with tempfile.TemporaryDirectory(prefix='clipcanvas-element-playback-') as directory:
        voice=Path(directory)/'voice.wav'
        subprocess.run([binary,'-v','error','-f','lavfi','-i','sine=f=600:r=48000:d=4',
                        '-c:a','pcm_s16le',str(voice)],env=env,check=True)
        for group in ('layers','captions'):
            p=model.new_project(1920,1080);p['clips']=[model.new_card('#19384a',4)]
            p['audio']=[model.new_audio(voice)]
            title=model.new_text('Editable മലയാളം',1,4) if group=='layers' else model.new_caption('Editable മലയാളം',1,4)
            p[group]=[title]
            for effect in ('rise','pop','fade','wipe','blur'):
                title['animation'].update(enter=effect,duration=.5)
                result=measure(p,effect,window=(.9,3.9),audio_probe=True)
                result['group']=group;runs.append(result)
                print(json.dumps(result),flush=True)
    report=json.dumps({'resolution':[1920,1080],'fps':30,'runs':runs},indent=2)+'\n'
    if args.output:args.output.write_text(report)


if __name__=='__main__':main()
