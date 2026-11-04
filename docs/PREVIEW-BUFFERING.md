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
