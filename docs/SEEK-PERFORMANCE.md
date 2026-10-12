# Dragging and seeking — ClipCanvas 0.10.0

The 0.9.4 timeline measurement (76–217 ms down to about 2.6 ms) covered drawing
the timeline only. It did not measure decoding, opening a process, seeking in
a compressed clip, composing the picture or waiting for GTK to display it.

0.10.0 keeps one native source decoder for ordinary paused seeks. Its bounded
two-frame buffer supplies nearby forward frames without restarting decoding
from the previous keyframe. Cairo draws timed text, images and captions from
the same saved layer settings. Original resolution stays selected by default;
no proxy or lossy preview file is made. Other source formats can fall back to
the established renderer if native seeking is unavailable.

The UI replaces the pending target with the newest pointer time. An obsolete
request cannot replace the picture after a newer request has arrived. Clicking
the ruler schedules its request after 20 ms rather than 80 ms. Playback still
updates the picture and white line together through GTK's frame clock.
Compressed-container timestamp offsets are converted to media time, and a
queued final frame cannot pull the line backward after playback ends.

## Reproducible local measurement

Run `/usr/bin/python3 tools/benchmark_scrub.py --output /tmp/scrub.json` from
the source checkout. It generates a 3-second original 1920×1080 H.264 clip at
30 fps, with a 60-frame keyframe interval, and adds a full-resolution text
overlay. Both paths read the same source and create a 1920×1080 picture.

| Frame request | Previous FFmpeg process per seek | Reused native decoder |
|---|---:|---:|
| First request | 259 ms | 193 ms |
| Repeated random seeks, median | 438 ms | 141 ms |
| Consecutive forward dragging, median | 278 ms | 20 ms |

The native decoder opened once for the whole run. These are worker decoding
and composition times, excluding mouse input scheduling and the next GTK
paint. They are measurements of generated media on this computer, not a
guarantee for every project. Random backwards jumps still decode from a
keyframe; the source format, keyframe interval, CPU load and layer count matter.

At 1080p, the two RGB source frames occupy about 12 MB, alongside the codec's
reference frames and the current canvas. This is a bounded look-ahead buffer,
not a cache of the entire video. The seek decoder closes on project changes,
playback, export and application cleanup. Cancelling a worker also releases
its decoder, including if opening the source raced cancellation.

## Remaining limits

Transitions, active overlay videos and unusual source aspect metadata continue
through the complete composition renderer. Those seeks can take longer.
Native decoding/Cairo and FFmpeg can differ slightly in chroma/scaling rounding;
both use original media and export retains its existing quality settings.
The optional HD/Fast choices change preview resolution only when selected.

Regression checks cover rapid pointer movement, cross-clip seeking, pause/
resume, source trim/speed, picture fades, text/caption placement, transition
fallback, decoder reuse, cancellation and export cleanup. The benchmark is
separate from correctness tests so CPU variability does not make tests flaky.
