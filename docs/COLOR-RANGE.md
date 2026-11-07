# Original import color — 0.20.1

Selected work: **C40**, color adjustments, with **X02**, native preview agreement.
The intended outcome is that importing a video preserves its original color.
Color grading is applied only when the user changes a clip's color settings.

## Cause and correction

New clips already use neutral color settings and 100% saturation. The reported
darkening came from native paused/scrub decoding: a full-range H.264 MP4 could
negotiate limited-range YUV caps in GStreamer. The subsequent RGB conversion
crushed shadows and clipped highlights, making the picture appear graded.
The FFmpeg composition/export path correctly read the source's full-range flag.

The media probe now retains `color_range`. For explicitly full-range decoder
inputs, a small native filter corrects the range field before RGB conversion.
It retains the negotiated matrix, transfer function and primaries, without
editing pixels or adding a creative color adjustment. This uses GStreamer's
[capssetter metadata merge](https://gstreamer.freedesktop.org/documentation/debug/capssetter.html)
and its [colorimetry representation](https://gstreamer.freedesktop.org/documentation/video/video-color.html).
The filter comes from the already-required GStreamer Good Plugins package.

Repeated seeks still reuse the same native decoder. HD/Fast proxies use their
own range metadata; a limited-range proxy of a full-range original must not be
corrected twice. Unknown/limited-range sources keep normal native negotiation.
If the correction element or usable native color information is unavailable,
the existing composition-renderer fallback supplies the frame. Decoder cleanup
also removes the range callback.

This is an SDR range fix, not HDR tone mapping or display-profile management.
Existing saved grades, saturation, crops, project format and source files remain
unchanged. There is no need to reset intentional color edits or reimport media.

## Validation

`tests/test_color_range.py` generates dark gray steps and broad color patches in
full-range sRGB-transfer, full-range BT.709-transfer and limited-range MP4s.
It checks neutral imports, original-source agreement, forward/backward native
seeks and decoder reuse, composition frames, native decoding of live playback,
lossless/MP4 export, proxy range handling, fallback, unchanged projects and
original-file hashes. No user footage is included in tests or this release.

On Ubuntu's GStreamer 1.24 runtime, disabling the correction reproduces both
full-range failures at about 10 RGB byte levels of average error. With the fix,
native frames remain within the test's 2-level tolerance against direct source
decoding, and encoded exports/playback within 3 levels. These tolerances allow
normal decoder rounding, chroma conversion and MP4 compression differences.

Release checks on 2026-11-07: the full 252-test run had 249 passes, two optional
speech-runtime skips and one playback-seeking UI timeout. That UI test passed
in the subsequent isolated run alongside all color-range, grading and grading
UI tests (12 passes). The extracted installer also passed the two generated
color-range tests, and its checksums and Python compilation were verified.

Repeatable visual check:

1. Import a full-range SDR clip with dark detail into a matching canvas. Leave
   Color neutral and saturation at 100%.
2. Scrub forward and backward in Full resolution, then Play/Pause at the same
   frame. Compare shadows and highlights with direct playback of the original.
3. Repeat with HD/Fast after the proxy is ready, then with a limited-range clip.
4. Export a lossless master and MP4. Confirm equivalent tones, then deliberately
   apply a grade and verify that resetting it restores the original appearance.

Save and reopen ClipCanvas after updating so the running preview decoder uses
the corrected code.
