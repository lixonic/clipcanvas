#!/usr/bin/env python3
"""Compare composed process-per-seek and reusable native seeks on original 1080p."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clipcanvas import engine,model
from clipcanvas.scrubber import ScrubSession


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='clipcanvas-seek-benchmark-') as directory:
        root=Path(directory);source=root/'1080p.mp4';binary,env=engine._tool('ffmpeg')
        subprocess.run([binary,'-v','error','-f','lavfi','-i','testsrc2=s=1920x1080:r=30:d=3',
                        '-c:v','libx264','-preset','fast','-g','60','-threads','2',str(source)],env=env,check=True)
        p=model.new_project(1920,1080);p['clips']=[model.new_clip(source)]
        title=model.new_text('Full resolution · original media',0,3);title.update(font_size=56,y=.2)
        p['layers']=[title];session=ScrubSession();results={}
        try:
            for name,read in (('previous_process_per_seek',lambda at:engine.preview_frame(p,at,preview_edge=0)),
                              ('reused_native_decoder',lambda at:session.frame(p,at,preview_edge=0))):
                samples=[]
                for at in (.2,.6,1.3,2.6,1.5,.4,1.8,.7,2.4):
                    start=time.perf_counter();picture=read(at);samples.append(round((time.perf_counter()-start)*1000,2))
                    assert (picture['width'],picture['height'])==(1920,1080)
                results[name]={'first_ms':samples[0],'repeat_median_ms':round(statistics.median(samples[1:]),2),
                               'repeat_max_ms':max(samples[1:]),'samples_ms':samples}
                samples=[]
                for at in (.2,.233333334,.266666667,.3,.333333334,.366666667,.4,.433333334,.466666667,.5):
                    start=time.perf_counter();read(at);samples.append(round((time.perf_counter()-start)*1000,2))
                results[name]['continuous_drag_median_ms']=round(statistics.median(samples[1:]),2)
            results['native_decoder_opens']=session.open_count
        finally:session.close()
    data=json.dumps(results,indent=2)+'\n';print(data)
    if args.output:args.output.write_text(data,encoding='utf-8')


if __name__=='__main__':main()
