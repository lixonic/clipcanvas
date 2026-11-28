# Long-form editing performance plan

**Decision at 0.22.0 (839a1f4). Keep Python for the editor, project model and playback scheduling.** FFmpeg already decodes and filters the media, and GStreamer already buffers and clocks playback. The first milestone is to make the work before Play proportional to what the viewer is about to see and hear. A C or C++ rewrite would still open the same inputs, prepare the same future effects and move the same frames if it kept today's scheduling.

## What the last ten commits tell us

| Commits, oldest first | Implication for preview |
|---|---|
| f131b93, 8c7d3df | Shared grading LUTs and separate embedded-audio inputs reduced startup. Two-clip picture windows then reduced it further for eligible cuts. On a previously measured 196 s graded project, warm Full first picture fell from 3.383 s to 0.480 s at the start. Keep this path as a control. |
| 8df1ec6, 41e2957 | Export speed settings and probed VAAPI encoding accelerate file output. Live preview transports raw frames. Track export throughput separately from Play latency. |
| 56bbf5b | Native paused preview gained full-range color handling. Include full-range material in seek and frame-equivalence tests when changing decode or display. |
| b30560a, 3cce3bc | Speech-timed word highlights and caption placement added more distinct visible states. Count sprite preparations before first frame, including those for distant speech. |
| 8bf7c7d, 21ef528 | Zoom changed display sizing. Caption-edit crash fixes concern widget lifetime. Time GTK painting independently of decoder arrival and retain the caption-edit lifecycle test when narrowing UI refreshes. |
| 839a1f4 | Batch edits can activate grading, audio effects or sparkle removal across many clips. Measure edit-to-responsive time separately from subsequent playback with those filters enabled. Include older saved projects as well as .clip files. |

The reported screen shows Full resolution, graded clips, join controls and several audio lanes. Join controls alone do not establish which transitions are enabled. The screenshot shows 1:23.3, so use the actual longer project to establish where its stalls occur. Three minutes is a useful reproduction point. The code selects a playback path by project contents and metadata, without a three-minute switch.

### A controlled startup check on this revision

Measured at the native sinks with the repository's `tools/benchmark_long_preview.py` measurement function. One 10 s 640×360 H.264/AAC test source at 24 fps was reused in 21 clips. Play began at zero in Full with metadata warm. The only edit was a 0.5 s legacy dissolve at the last join. Run order was cuts, dissolve, dissolve, cuts.

| Project | First picture, two runs | First audio, two runs |
|---|---:|---:|
| Cuts, two-clip picture windows | 181 / 89 ms | 273 / 165 ms |
| Same opening, dissolve near the end, continuous graph | 355 / 312 ms | 422 / 382 ms |

A distant edit raised startup cost before a single affected frame was visible. This comparison covers route selection and native startup on synthetic media. It does not measure GTK presentation, sustained stutter, or 1080p performance on the reported project. Historical graded-project measurements and their hardware-dependent caveats are in [Preview buffering](PREVIEW-BUFFERING.md).

## Implementation order

1. **Instrument the wait and reproduce the stall.** Record a monotonic timestamp for Play, route choice and reason, project snapshot/validation, proxy lookup, graph and sprite preparation, FFmpeg launch, first decoded picture/PCM, first GTK presentation, clock start and each buffer refill. Log project time at each stall, active effects, queue levels, presented versus produced frames and cancellation time. Compare Full with cold and warm Fast/HD at the same position. A slow first frame, a refill at a join and a late GTK paint need different fixes. Start with the affected saved project read-only when available.
2. **Extend picture windows to composed timelines.** In `lazy_preview.supported`, any transition, image clip, layer or caption sends the whole project to `engine.live_stream`. That path prunes completed main clips but can still build the remaining graph before emitting a frame. Build a time index once per project revision. Plan around the playhead with a bounded time horizon and bounded lookahead. Include both sources for transition overlap, layers already active at the start, caption word state, animation phase and source-time mapping. Slice long individual clips by time too. First support cuts interrupted by an ordinary dissolve, then images, captions and layers, then compound joins such as Match & Move. Keep the continuous graph as a parity reference and a fallback for unsupported spans. Check frame counts and timestamps at fractional seeks and both sides of every window boundary.
3. **Make deep audio seeks and cue preparation bounded.** Even in the windowed route, `lazy_preview.audio` builds a continuous audio graph and applies output-side seek. Time first PCM at the start, midpoint and near the end. Seek directly for proven stateless spans. Preserve echo tails, compressor history, speed, fades and overlapping lanes using validated preroll or cached processed sections for stateful spans. In `sound_mix.mix`, even one audible element cue can write a timeline-length stereo float WAV before playback. At 48 kHz this is 1.38 GB per hour, or 4.15 GB at three hours, before intermediate files. Mix only cues intersecting the requested interval, including tails. Compare joined PCM against the continuous reference across seeks and seams.
4. **Bound preparation and UI work around the edit.** `engine._caption_stream` already reuses identical sprites, but can enumerate and rasterize future word states before playback. Prepare the visible interval and the next window, with a byte-limited cache keyed by text, style, timing state and preview size. In `lazy_preview.video`, every two-clip window currently deep-copies the entire project before slicing. Reuse the immutable playback snapshot and build local views. In `app.py`, timeline painting clips expensive drawing but rebuilds layout and hit geometry across the project. Cache layout by edit revision and query visible items while retaining accurate scroll, drag and selection hits. Measure project-wide refresh, dirty-state serialization and undo snapshots on dense edits.
5. **Profile the remaining frame cost.** Trace Python and native child CPU separately alongside disk I/O, memory and frame cadence. Check proxy warmup competing with playback and cancelled seeks leaving work behind. In `player._receive_sample`, RGB frames cross into Python via `extract_dup`. A 1080p RGB24 copy alone moves about 149 MB/s at 24 fps. Also inspect I420 row repacking on unaligned widths in `live_player.py`. Prototype a native-buffer display path or a small C/C++ routine only if profiles show that operation consumes enough of the missed frame budget to matter. Require an end-to-end gain on the same machine, frame/color/PCM parity, safe cancellation and a working software fallback before adopting it.

## Benchmarks and proposed acceptance criteria

Use 3 min, 60 min and 180 min fixtures. Vary duration and edit density independently. Extend an identical opening with distant material to isolate startup scaling. Compare one long source with hundreds of short edits at the same duration. Include long-GOP footage, a graded sequence with dissolves, word-highlight captions, sparse layer sounds and music/narration spanning joins. Reuse generated sources to keep test media small. Include at least one real long source for deep-seek tests.

| Exercise | Proposed working target on a recorded reference machine |
|---|---|
| Play the identical opening, middle and final minute, with metadata warm | At 1080p project size and warm Fast preview, p95 first presented picture and first audio each within 1 s. Extending the distant timeline from 3 to 180 min adds at most 250 ms to identical-opening startup. Record Full and cold results separately. |
| Play across a dense join for 60 s after startup | Zero post-start refills or missing PCM samples on the warm Fast reference fixture. At 24 fps, allow 41.7 ms per frame. Report missed presentations and A/V drift, including the worst join. |
| Scrub repeatedly, then pause, edit or close | Target p95 warm paused-frame latency under 200 ms and obsolete producer cleanup under 250 ms. Only the newest request may paint. Verify no surviving child processes or accumulating scratch files. |
| Scroll, zoom and edit near the end of the 180 min dense project | Target p95 GTK input-to-paint under 50 ms. Track playback working memory above the loaded model, open decoders, cache bytes and scratch high-water mark. These should plateau for fixed active content and lookahead as distant content grows. |

These are proposed budgets. Establish the reference CPU/GPU, storage, OS, FFmpeg/GStreamer versions, media format, canvas fps, resolution and cache state before using them as release gates. For latency collect at least 20 repetitions per reference case and report median, p95 and failures. Use short generated cases in change checks, and run the scaling matrix and a three-hour playback soak before a long-form release. Sample process-tree memory, disk use and A/V drift throughout the soak.

Start the baseline with the existing harnesses from the repository root, substituting the saved project path and a valid deep start time:

```sh
/usr/bin/python3 tools/benchmark_long_preview.py --project /path/to/edit.clip --edge 0 --start 0
/usr/bin/python3 tools/benchmark_long_preview.py --project /path/to/edit.clip --edge 640 --start 180
/usr/bin/python3 tools/benchmark_preview_buffering.py --help
/usr/bin/python3 tools/benchmark_transition_playback.py --help
/usr/bin/python3 tools/benchmark_scrub.py --help
```

The startup harness stops after both native sinks receive data. Use the buffering and transition harnesses for sustained playback, and add GTK presentation timing for the user-visible budget. Reuse the frame/PCM comparisons in `tests/test_lazy_preview.py` and cancellation checks in `tests/test_preview_playback.py`. Keep original-resolution export as the reference for effects and timing.

## Questions for each new feature

- Does placing one instance in the final minute change which playback path handles the first minute? Compare the same opening before and after.
- What gets opened, decoded, rasterized, copied or written before the first picture and first audio? Count active inputs, bytes and work queued for distant items.
- Does one edit invalidate unrelated sprites, proxies, timeline geometry or processed audio? Exercise batch apply, undo and rapid seeks with cold and warm caches.
- What history or lookahead does the effect need at a seek or window boundary? Test a seek into the effect, a seam inside it, overlapping effects and the final frame/sample.
- Does the feature add per-frame GTK work, cross-process copies or background CPU/disk contention? Record Full and Fast playback, worst-frame time, peak resources and cancellation alongside the feature's correctness tests.
