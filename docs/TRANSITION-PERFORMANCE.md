# Full-resolution transition performance — 0.10.2

Circle Wipe, Colour Wipe, Line Wipe and Flow previously used interpreted FFmpeg
expressions for every pixel in every transition frame. At 1080p those expressions
could take many seconds to calculate a half-second effect, exhausting the live
preview's short buffer and stopping the picture at the join.

Version 0.10.2 uses native compositing instead. Circle and directional wipes
combine the original decoded pictures with lossless geometry masks. Flow moves
both pictures with padded, edge-preserving crops and a native crossfade. The
selected resolution, direction, colour, duration and audio processing remain
intact. The same implementation serves playback, composed scrub frames and final
exports. No GPU, model, new package dependency or lossy video proxy is required.

## Resource handling

The reusable geometry cache contains compressed grayscale PNG masks, never source
video. Its retained entries are limited to 64 MiB and 32 entries. Each render
leases immutable files through private hard links; eviction removes cache links
without invalidating an active decoder. A copy fallback supports different file
systems. Cancellation stops generation and removes partial entries. Temporary
render directories and the process cache are cleaned up when released.

Mask generation uses scanline spans and byte repetitions, avoiding Python arrays
for every full-resolution frame. A 1920×1080 half-second Circle Wipe generated
16 masks occupying 253 KiB on disk, with about 305 KiB peak Python allocations
in the local check. This is geometry memory, not total decoder/compositor memory.

An outgoing prefix routes into the final composition rather than splitting and
queuing all preceding video frames. A local FFmpeg memory check with 1080p colour
cards and a half-second Circle Wipe measured 199.8 MiB peak RSS for a 1.5-second
outgoing card and 200.0 MiB for a 30-second card. Buffering does not grow with that
prefix's duration. More simultaneous sources, overlays, longer transitions and
4K frames can still require substantially more RAM.

## Measurements

Run the isolated effect benchmark with:

```sh
/usr/bin/python3 tools/benchmark_transitions.py --output /tmp/transitions.json
```

Both implementations use generated colour sources, 1920×1080, 30 fps, fifteen
frames over 0.5 seconds, and one FFmpeg filter thread. Computation times include
FFmpeg launch and pixel conversion. Initial local measurements on 2026-10-12:

| Effect | Previous computation | Native computation | Initial geometry preparation |
|---|---:|---:|---:|
| Circle Wipe | 14,860 ms | 261 ms | 129 ms |
| Colour Wipe | 14,014 ms | 633 ms | 190 ms |
| Line Wipe | 14,357 ms | 447 ms | 182 ms |
| Flow | 32,288 ms | 393 ms | Less than 1 ms |

Geometry is reused by subsequent requests with identical parameters. These
isolated timings explain the bottleneck; they are not application frame rates.
Colour and Line Wipe received additional frame-clock alignment after this run.

The end-to-end sample delivery check uses original H.264/AAC test footage, full
resolution, the app's live Matroska transport, synchronized RGB picture sink and
a silent synchronized audio sink:

```sh
/usr/bin/python3 tools/benchmark_transition_playback.py --output /tmp/delivery.json
```

With other render tests stopped, all four effects delivered all 75 pictures of
a 2.5-second composition. Maximum frame-delivery intervals around the join were
34.05 ms for Circle Wipe, 34.07 ms for Colour Wipe, 34.13 ms for Line Wipe and
34.14 ms for Flow; expected spacing at 30 fps is 33.33 ms. Initial picture arrival
was 313–552 ms, including graph preparation. This measures native sample delivery,
not GTK painting or a real audio device.

A run during other render tests showed a 271 ms Flow gap, and another run showed
653 ms. This implementation removes the expensive interpreted effects; it does
not guarantee uninterrupted playback under CPU contention. Complex layer effects,
many decoders, slow storage and high resolutions can still need **Render smooth
preview**. Preview buffering remains short to preserve responsive playback and
avoid retaining a whole video in RAM.

## Correctness checks

Regression tests compare decoded pictures with the previous effect renderer at
29.97 fps, including fractional timing, source trim, playback speed, all four
directions, user colour and text overlays. They check frame counts, per-frame
appearance and byte-identical PCM audio with independent echo effects, without
mutating the project. Additional checks cover mixed joins after frame-aligned
seeks, preview/export agreement, cache reuse, eviction, active leases and
cancellation.

Opaque video backgrounds remain opaque beneath editable overlays. The historical
custom Flow sampler could accidentally use chroma as alpha when the filter graph
negotiated an alpha format; the native implementation fixes that artifact.
Existing project files and transition identifiers need no migration.
