# Live preview and automatic FPS — ClipCanvas 0.7.0

Press **Play**, **Space** or **Preview** to play the composition. It begins once
its first frames are available; it no longer waits for a complete preview MP4.
Pause/resume, ruler seeking and replay at the end use the native GTK canvas.
After an edit, press play again. No cloud service or upload is involved.

The preview includes clip cuts/transitions, picture/audio fades, trim and speed,
text/caption styles, image/video overlays and opacity, independent soundtrack
placement, voice effects and the final audio limiter. Video overlays remain
silent, as in export. Live frames are at most 640 pixels on their longest edge.
Export still reads the originals at the chosen canvas resolution and quality.

## Automatic frame rate

The FPS dropdown is removed. The first main video clip supplies its original
average frame rate, including 30000/1001 (29.97) and 60000/1001 (59.94). Mixed-rate
projects need one output rate: later clips follow the first. Reordering/removing
that clip changes the project rate. Speed changes do not change output FPS.
Original FPS is retained with each clip; older projects obtain it from source
metadata when opened, and the render worker probes it again before export.
When metadata is unavailable/unknown, the saved project rate (30 for a new
project) remains the fallback. Variable-rate sources are converted to their
average rate; this is not variable-rate mastering. Rates up to 240 are supported.

## Playback implementation and limits

FFmpeg executes the same composition graph used by export, producing raw I420
video and 48 kHz stereo PCM in a Matroska pipe. GStreamer demuxes and clocks the
audio sink and RGB appsink together. There is no video/audio encoding step and
no prepared preview media file. Text/logo sprites and caption PNG sequences are
still temporary files; those are removed with the producer's workspace.

Appsrc and downstream queues apply backpressure, bounding frame/audio buffering.
Seek, edit, project switch and close cancel the previous generation. Stale frames
and error callbacks cannot replace the new generation. The worker reaps FFmpeg
and removes its temporary workspace. Seeking skips completed main clips while
retaining global layer/caption timing and soundtrack processing history.

Playback is CPU-based. Complex projects can stutter; long active clips and
soundtrack effect history can take catch-up decoding after a seek. A large set
of caption sprites also adds startup work. GPU acceleration, adaptive proxies,
a media cache and an interactive timeline engine remain separate improvements.
**Project → Render smooth preview…** retains the prepared-file fallback for
projects that cannot keep up. Export still renders the final video.

On-canvas layer dragging briefly uses a source editing frame so the selected
layer can move directly with the pointer. Ordinary ruler seeking uses the
composed frame, including transitions and fades.

## Validation

The full local suite passes **73 tests**, including **11 native GTK workflows**
and actual local Tiny speech recognition. Generated test media exercises:

- Raw-video/PCM stream codecs, source/project preservation and cancellation.
- Frame and sample equality after seeking into a transition and a later clip,
  retaining captions, title placement, music offset, fades and echo history.
- Video-overlay speed, opacity, rotation and frozen final frame after seeking.
- Original fractional FPS in both the live stream and final MP4, mixed-rate
  first-clip policy, reorder, undo, older-project probing and save/reopen.
- Native play/pause/resume, end/replay, clip selection, delayed asset completion,
  edit cancellation and producer cleanup, with the MP4 renderer forbidden.
- Existing voice effects, WAV mixing, text styles, overlays, captions, extraction,
  transitions, recovery, native file chooser and project portability.

A separate native fixture check measured a first live frame in **0.26 seconds**
on this machine with titles, logo, captions, a soundtrack and the video's own sound.
This is one generated-fixture measurement, not a general performance guarantee.
Rapid seeks retire every producer. Light/dark native captures were inspected;
the FPS selector is absent and the preview is unobstructed. A stray timeline
join outline was also fixed by starting its circle on an independent Cairo path.

![Native live composition preview](images/live-preview-0.7.0.png)

Dependencies remain Ubuntu GTK3, GStreamer base/good/libav and FFmpeg. This
release provides an Ubuntu .deb; Windows runtime/installer validation is pending.
