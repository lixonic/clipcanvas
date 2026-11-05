# Preview audio buffering and Fast/HD cache — 0.11.1

Scope: X02 responsive preview, C12 seeking and C14 playback. Video splits and
transitions must not drop narration or music from independent timeline lanes.
Fast preview must reduce source-decoding work as well as the displayed size.

## Behavior

Playback buffers picture and sound together before starting. If source decoding
or a transition falls behind, it shows **Buffering preview…**, pauses their
shared clock, fills the buffer and resumes. Narration resumes at the next
sample. A user-initiated pause stays paused after buffering finishes. Seek,
edit, export and close cancel the previous generation and any cache preparation.

**Full resolution** reads the originals. **HD preview** (1280-pixel longest edge)
and **Fast preview** (640) use available smaller main-sequence video files.
Since 0.18.2, missing files build in the background while playback reads the
originals at the selected preview canvas size. Later playback and seeks reuse
completed entries, including after restarting the app. Paused native/composed
seeks use an available cached source without starting a new build.

Fast/HD video caches add preview-only H.264 compression. Sound always comes from
the original files and retains trims, volume, fades and voice effects. Full
resolution and export retain original-source picture processing and quality.
Text, captions, transitions and image motion still render at the chosen preview
canvas size. Project files keep original paths and project format 8.

The cache lives in `$XDG_CACHE_HOME/clipcanvas/preview-video-v1`, normally
`~/.cache/clipcanvas/preview-video-v1`. It is disposable. Changed source size or
modification time invalidates its entry. Older entries are removed at 1 GB or
64 files; active previews retain private file leases through eviction. An entry
larger than the cache budget is temporary for that playback only. Cancelled or
failed encodes never publish a partial cache entry. An unwritable cache falls
back to reading the source at the selected output size.

## Cause and implementation

The previous 300 ms queues allowed the playback clock to keep advancing while
the FFmpeg producer decoded the next clip. Reopening a split of a long-GOP
source required decoding from its preceding keyframe again. Shrinking the final
picture did not reduce that decoding work, explaining similar Fast/Full waits.

In a read-only copy of the reported project, a split introduced roughly 2.3
seconds of audio/video arrival delay. The exported mix retained its samples;
the user confirmed that the fault occurred in preview. Some transition audio
also arrived as half-second PCM blocks.

The native transport now uses a coordinated GStreamer multiqueue, bounded by
64 MB per stream and 1.5 seconds. Buffering messages pause/resume the pipeline
according to the user's requested play state. Live PCM is packetized into at
most 1024 samples per channel, without changing its samples. Smaller proxies
retain source frame cadence, normalize source orientation/pixel aspect ratio,
and use a keyframe at least every second. Original audio uses a separate input
when a clip's picture comes from a proxy.

The buffering state handling follows GStreamer's
[buffering guidance](https://gstreamer.freedesktop.org/documentation/application-development/advanced/buffering.html)
and [multiqueue behavior](https://gstreamer.freedesktop.org/documentation/coreelements/multiqueue.html).

## Repeatable checks

```sh
/usr/bin/python3 -m unittest discover -s tests -p test_preview_playback.py -v
PYTHONPATH=tests /usr/bin/python3 -m unittest test_play_start_ui -v
/usr/bin/python3 tools/benchmark_preview_buffering.py --output /tmp/preview-buffering.json
```

The generated-media checks verify cold/warm cache reuse, stream-index changes,
unchanged original hashes and projects, source-change invalidation, eviction
leases, cancelled encodes, reduced preview dimensions, original-sized exports,
fractional-rate seek frames and byte-identical audio between Fast/Full/export.
An injected producer stall verifies that buffering pauses both streams, keeps
manual pause effective, and delivers every PCM byte after resuming. Very short
streams must finish even when shorter than the normal buffer target. Native GTK
checks cover preparation status and stale-generation callbacks.

The benchmark consumes actual native RGB/PCM sinks silently. Its first run may
include cache preparation; repeated 640 runs measure a warm cache. It records
startup, audio timestamp gaps and rebuffering events. `--project`, `--start`,
`--end`, `--split` and `--transition` can exercise a saved project read-only.
Avoid unrelated CPU-heavy jobs when comparing timings. Full resolution can
still need a visible buffering pause on demanding sources.

Read-only measurements of the reported 1080p/24 fps project on 2026-10-25,
playing five seconds around a split or dissolve:

| Join | Preview | First audio | Rebuffer pauses during playback |
|---|---|---:|---:|
| Split | Warm Fast, two runs | 3.21 s / 1.63 s | 0 / 0 |
| Split | Full resolution | 3.60 s | 1 |
| Dissolve | Warm Fast | 3.43 s | 0 |
| Dissolve | Full resolution | 4.97 s | 2 |

Every run finished with continuous audio packet timestamps (less than 1 ms of
Matroska timestamp rounding). Cache preparation is excluded from these warm
runs; source probing, audio history and initial picture/audio buffering are
included. System and filesystem warm-up affect startup. Full-resolution pauses
are now coordinated buffer refills, with intact samples, rather than a running
audio clock consuming missing packets.

Manual check: add a continuous narration/music track below a long video, split
the video, then add a dissolve at that join. Play across it in Full and Fast,
pause while preparing/buffering, resume and seek repeatedly. Check that words
resume intact, a warm Fast seek starts sooner, and exported sound and original
files retain their content.

## Long-timeline startup — 0.18.2

Selected scope: X02 responsive preview, C12 seeking and C14 playback. The user's
goal is quick preview startup on timelines of four minutes and longer.

Previously Fast/HD playback encoded every remaining main video in sequence,
including unused portions of each original, before yielding any picture or
audio. Output backpressure could not help because that work happened before
the playback stream existed.

Playback now takes an immutable snapshot of available proxy leases and starts
decoding immediately. Missing entries fall back to originals. One background
worker visits the remaining distinct sources in timeline order; its encoder
and decoder each use one thread, with lower CPU priority where `nice` is
available. Completed proxies become available for the next Play or seek. The
current graph keeps its original input choices so picture and audio timing
cannot change midway through playback.

The worker owns separate temporary files and observes both playback cancellation
and generator closure. Stop, seek, edit, export and close reap it and discard
unfinished cache files. It releases its unused leases after each build so the
existing cache budget remains effective. Cache/encode failures leave playback
on originals. A discarded live FFmpeg pipe is killed and reaped directly:
graceful termination could otherwise spend three seconds trying to flush into
a consumer that had stopped reading. File renders keep their existing shutdown
and atomic-publication behavior.

The design follows the applicable playback principles in VideoLAN's current
[decoder documentation](https://videolan.videolan.me/vlc/master/decoder_8c.html):
threaded decoding, limited queues with backpressure, and prompt discarding of
obsolete buffers. ClipCanvas retains its shared GStreamer picture/audio clock
and bounded multiqueue. It still composes edits, color, transitions and layers,
so a complex timeline has more work than playing one finished movie in VLC.

Repeatable generated-media check:

```sh
/usr/bin/python3 tools/benchmark_long_preview.py --output /tmp/long-preview.json
/usr/bin/python3 -m unittest discover -s tests -p test_preview_playback.py -v
```

The benchmark uses 21 distinct 15-second 1920×1080 H.264/AAC source paths: a
315-second timeline, Fast preview at 640 pixels, an isolated cold cache and an
explicitly prepared warm cache. It measures actual native first-picture and
first-audio arrival using silent audio, then cancels and verifies worker cleanup.
Generated media, filesystem warm-up and CPU load affect these measurements;
they do not guarantee the same timings for every project.

Local comparison on 2026-11-04, with no competing test workload:

| Run | First picture | First audio | Stop and worker cleanup |
|---|---:|---:|---:|
| 0.18.1, cold cache | 29.158 s | 29.208 s | 3.032 s |
| 0.18.2, cold cache | 0.765 s | 0.978 s | 0.066 s |
| 0.18.1, warm cache | 0.413 s | 0.463 s | 3.032 s |
| 0.18.2, warm cache | 0.394 s | 0.446 s | 0.016 s |

The cold first-picture wait fell by about 38× in this generated case. The
reference used the committed 0.18.1 engine and cache code with the same benchmark
and media settings. Each version uses an independent temporary cache; warm
measurements explicitly populate that cache before timing playback.

Regression checks cover a five-minute timeline starting while distant proxy
preparation is blocked, prompt stop and workspace cleanup, completed-cache
reuse, failed and cancelled builds, and byte-identical cold/warm/Full/export
soundtracks. Existing checks cover deep/fractional seeks, transitions, overlays,
buffer starvation and manual pause.

Manual check: open a long timeline with multiple uncached high-resolution
videos, select Fast or HD, and press Play near the beginning and later in the
timeline. Playback should begin while cache work continues. Pause, seek several
times, edit and export; verify responsive controls, continuous narration,
correct effects and an unchanged original-resolution export. Cold decoding of
demanding sources can still need coordinated buffering. Very many inputs and
complex future transitions/layers also retain graph-setup costs.

## Graded-timeline startup — 0.18.3

Selected scope: X02 responsive preview, C12 seeking and C14 playback, retaining
C39/C40 color grading. The user's four-minute timeline still waited at **Loading
preview…** after 0.18.2, including with Full resolution selected. The earlier
generated benchmark did not include its combination of grading and embedded audio.

Two additional startup costs affected the reported project. Each clip compiled
the same large RGB grading expressions into FFmpeg lookup tables. Also, sharing
picture and embedded audio inputs allowed audio preparation to pull future video
frames through the shared demuxer before the first picture could play.

Rendering now writes 256-entry `.cube` lookup files from the existing native
grading tables, reusing identical tables within the render workspace. FFmpeg
loads them with `lut1d` and nearest interpolation at RGB24 precision. Explicit
quantization retains the previous byte values. Live playback gives embedded
audio separate inputs with video disabled, preserving complete trimmed audio
history for effects. Preview/export grading, soundtrack samples and project
format remain unchanged; workspace cleanup removes the lookup files.

Read-only comparison of the reported 298.368-second project: 21 graded H.264/AAC
clips, a 1080×1080 canvas at 24 fps, embedded audio, and clip speed changes. Both
versions use Full resolution and cached source metadata, as after media import.
The reference uses the committed 0.18.2 engine and expression-based grading.

| Start position | Version | First picture | First audio | Stop and cleanup |
|---|---|---:|---:|---:|
| 0:00.0 | 0.18.2 | 17.721 s | 18.097 s | 0.138 s |
| 0:00.0 | 0.18.3 | 2.319 s | 2.946 s | 0.049 s |
| 0:22.6 | 0.18.2 | 16.996 s | 17.467 s | 0.126 s |
| 0:22.6 | 0.18.3 | 2.407 s | 2.986 s | 0.045 s |

These measure native sink arrival with silent audio, not GTK painting or a
guaranteed startup time on other machines. First-picture startup improved about
7.6× at the beginning. Project and all original media hashes were unchanged.
Full-resolution decoding, composition and buffering still take time.

A separate sustained run played the first 30 seconds across two clip boundaries
with no rebuffering after startup. Audio timestamp gaps stayed below 1 ms of
container rounding. That run took 34.242 seconds including its initial buffer.

Repeat the saved-project or generated graded benchmark without competing
CPU-heavy work:

```sh
/usr/bin/python3 tools/benchmark_long_preview.py --project /path/to/project.clipcanvas --edge 0 --start 0
/usr/bin/python3 tools/benchmark_long_preview.py --grade 'Pixar-inspired animation' --edge 0
PYTHONPATH=tests /usr/bin/python3 -m unittest test_color_grading test_live_preview test_preview_playback -v
```

Regression coverage compares every byte level in all three channels against the
previous grading expressions, across presets, extreme controls and partial
strength. It also checks paths containing quotes, colons, brackets and commas,
and first-picture delivery from 21 graded clips with embedded audio. Existing
checks cover frame/PCM agreement, seeks, export, cancellation and cleanup.
The full suite passed 167 tests, with 60 desktop/optional-runtime checks skipped.

Manual check: reopen the graded project in the updated app, select Full resolution,
play from the beginning and seek to later clips. Check that picture and sound
start together after the short initial buffer, grades retain their appearance,
and pausing/seeking stops obsolete playback. Repeat in Fast/HD and compare an
export with the preview.


## Windowed picture decoding — 0.18.4

Scope: X02 responsive preview, C12 seeking and C14 playback. The requested
outcome is faster first-picture and synchronized playback startup on long
projects, without making their duration determine picture decoder setup cost.

Cut-only video/card timelines with more than two clips and no visual layers/captions now
use a separate raw picture producer and a continuous 48 kHz stereo soundtrack
producer. Pictures load two clips at a time; the producer starts the next window
while buffered pictures still play. Original grading, framing, fades and speeds
use the shared composition filters. Stable window boundaries and integer frame
counts preserve the picture sequence across seeks. The soundtrack keeps all
processing history, including echo and compression, throughout playback.

Both raw appsrc inputs feed the existing bounded GStreamer multiqueue and shared
clock. Each carries explicit frame/sample timestamps. There is no preview-file
render or unbounded picture queue. Stop/seek cancels both producers and removes
their temporary files. Fast/HD reuse available proxies and retain background
cache preparation. Image clips, transitions, layers and captions use the established full
composition path; exports remain on that path as well.

The saved project changed during development to 17 graded clips lasting
196.292 seconds, at 1080×1080/24 fps. In a read-only Full-resolution run, the
first picture arrived at 0.739 s and audio began at 0.965 s. The first 30 seconds
played in 30.968 s, with no rebuffering and no audio timestamp gaps. These are
native sink measurements on this machine, not a guarantee for every source.

Final same-project comparison with warmed metadata:

| Position | Path | First picture | First audio |
|---|---|---:|---:|
| 0:00 | 0.18.3 continuous graph | 3.383 s | 4.245 s |
| 0:00 | 0.18.4 windows | 0.480 s | 0.658 s |
| 0:22.6 | 0.18.3 continuous graph | 3.322 s | 4.564 s |
| 0:22.6 | 0.18.4 windows | 0.623 s | 1.693 s |

Project and source hashes were unchanged. Seeking omits completed clips while
retaining the same subsequent window boundaries; it avoids decoding the earlier
clip in a window before reaching the requested picture. Audio still processes
its history, so deeper seeks can take longer to start sound.

`tests/test_lazy_preview.py` compares all picture bytes and PCM samples against
the continuous reference, including non-frame-aligned seeks, speed changes,
grading, fades, an independent soundtrack and echo. Playback tests exercise
native first-frame delivery, cancellation and worker cleanup.

```sh
PYTHONPATH=tests /usr/bin/python3 -m unittest test_lazy_preview test_preview_playback -v
/usr/bin/python3 tools/benchmark_long_preview.py --project /path/to/project.clipcanvas --edge 0
```

Manual check: play a long graded cut-only timeline from the beginning, pause,
resume, and seek across several clip boundaries. Check the last clip and audio
ending. Repeat in Fast/HD, then add a transition or title and confirm the
established composition path still presents that effect correctly.
