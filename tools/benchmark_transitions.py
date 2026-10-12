#!/usr/bin/env python3
"""Compare old and native full-resolution transition computation, without GTK.

Both paths use generated colour sources, identical pixel format and one filter
thread. Times include FFmpeg launch and conversion. Geometry preparation is
reported separately. This isolates effects, not end-to-end playback latency.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clipcanvas import engine, transitions, transition_render


def measure(effect, variant, width, height, fps, duration):
    binary,env=engine._tool('ffmpeg')
    with tempfile.TemporaryDirectory(prefix='clipcanvas-transition-benchmark-') as root:
        args=['-threads','1','-f','lavfi','-i',f'color=red:s={width}x{height}:r={fps}:d={duration}',
              '-threads','1','-f','lavfi','-i',f'color=blue:s={width}x{height}:r={fps}:d={duration}']
        filters=['[0:v]format=yuv444p,settb=AVTB[a]','[1:v]format=yuv444p,settb=AVTB[b]']
        started=time.perf_counter()
        if variant=='reference':
            filters.append('[a][b]'+transitions.join_filter({'transition':effect},duration,0)+'[out]')
            output='out'
        else:
            output=transition_render.append(args,filters,'a','b',{'transition':effect},duration,0,
                                            width,height,fps,duration,root,1)
        prep=(time.perf_counter()-started)*1000
        graph=Path(root)/'graph.txt';graph.write_text(';\n'.join(filters))
        begun=time.perf_counter()
        result=subprocess.run([binary,'-v','error','-nostdin','-filter_complex_threads','1']+args+
            ['-filter_complex_script',str(graph),'-map',f'[{output}]','-an','-t',str(duration),
             '-threads','1','-progress','pipe:1','-f','null','-'],env=env,capture_output=True,text=True,timeout=180)
        result.check_returncode()
        elapsed=(time.perf_counter()-begun)*1000
        frames=[int(line.split('=')[1]) for line in result.stdout.splitlines() if line.startswith('frame=')]
        expected=round(duration*fps)
        if not frames or frames[-1]!=expected:
            raise RuntimeError(f'{effect}: expected {expected} frames, got {frames}')
        return {'effect':effect,'variant':variant,'prepare_ms':round(prep,2),
                'process_ms':round(elapsed,2),'total_ms':round(prep+elapsed,2),'frames':frames[-1]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width',type=int,default=1920);parser.add_argument('--height',type=int,default=1080)
    parser.add_argument('--fps',type=int,default=30);parser.add_argument('--duration',type=float,default=.5)
    parser.add_argument('--repeats',type=int,default=1);parser.add_argument('--output',type=Path)
    parser.add_argument('--effects',nargs='+',choices=transitions.KEYS[1:],default=['fade','circle_wipe','colour_wipe','line_wipe','flow'])
    args=parser.parse_args()
    if min(args.width,args.height,args.fps,args.duration,args.repeats)<=0 or abs(args.duration*args.fps-round(args.duration*args.fps))>1e-6:
        parser.error('Use positive values and a whole number of transition frames.')
    runs=[]
    for effect in args.effects:
        for repeat in range(args.repeats):
            for variant in (('reference','native') if repeat%2==0 else ('native','reference')):
                result=measure(effect,variant,args.width,args.height,args.fps,args.duration)
                result['repeat']=repeat+1;runs.append(result)
                print(json.dumps(result),file=sys.stderr,flush=True)
    summary=[]
    for effect in args.effects:
        values={variant:statistics.median(r['process_ms'] for r in runs if r['effect']==effect and r['variant']==variant)
                for variant in ('reference','native')}
        summary.append({'effect':effect,'reference_process_ms':round(values['reference'],2),
                        'native_process_ms':round(values['native'],2),
                        'speedup':round(values['reference']/values['native'],2)})
    report=json.dumps({'resolution':[args.width,args.height],'fps':args.fps,'duration':args.duration,
                       'filter_threads':1,'runs':runs,'summary':summary},indent=2)+'\n'
    print(report,end='')
    if args.output:args.output.write_text(report)


if __name__=='__main__':main()
