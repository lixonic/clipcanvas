# Export memory — ClipCanvas 0.9.3

Export now releases the preview's FFmpeg process before starting the renderer.
Pausing alone kept its decoder/filter graph and queued frames alive during
export. A busy preview could therefore hold substantial memory alongside the
export process. Pending scrub timers and frame requests are cancelled too.

The worker waits for preview shutdown without blocking GTK. A shutdown timeout
reports a retry message instead of starting a second large render. The displayed
picture, timeline playhead and project stay unchanged. Play starts a fresh live
preview after a successful or cancelled export, including after a prepared preview.

## Quality and practical settings

This change does not alter the render graph, source files, canvas size, frame
rate, MP4 CRF/preset, audio bitrate or lossless-master settings. Continue using
High or Maximum quality MP4 for your normal export.

Full / HD / Fast above the canvas affects editing preview resolution only.
HD or Fast can reduce memory and decoding work during editing; export still
uses the original files at the selected canvas size. Preview zoom only changes
the view. Save your project and reopen the app after installing an update.

There is no fixed RAM cap. The export still renders the whole composition in
one FFmpeg graph, so many source clips, transitions, overlays and larger canvas
sizes can require substantial memory on an 8 GB computer. Releasing the preview
removes the duplicate workload; bounded segment rendering is future work.
FFmpeg's [pipeline documentation](https://ffmpeg.org/ffmpeg.html#Detailed-description)
describes its decoders, filter graphs and encoder stages.

## Local measurement

Measured on 2026-10-11 with Ubuntu's FFmpeg 6.1.1, native GTK playback and
generated media. The same 30.4-second, 1280×720 composition had eight main clips,
alternating cuts and 0.4-second fades, eight text layers and three soundtracks
with echo. A full-resolution live preview was played, paused, then exported
with High quality MP4. The before run retained the paused player; the after run
used the updated export workflow.

| Measurement | Before | 0.9.3 |
|---|---:|---:|
| Peak combined FFmpeg resident memory | 665.5 MiB | 495.1 MiB |
| Maximum simultaneous FFmpeg processes | 2 | 1 |

The sample used 170.4 MiB (25.6%) less combined FFmpeg memory. Both exported MP4
files had SHA-256 `1a41e8e186e95642232482a1722a24bfe31bba2603ba2208067c2bb9dadcc900`.
Memory was sampled from the benchmark's own processes every 20 ms. These are
single-run measurements, excluding GTK and other applications. They do not
predict a four-minute 1080p project's peak or establish a general RAM limit.

## Regression coverage

The native desktop regression verifies that a real paused live preview exits
before a real MP4 export starts, quality remains High, and the frame, playhead
and project are preserved. It then checks that a pending scrub cannot restart
the preview during export, cancellation preserves the project, and Play works
again after either live or prepared-preview export. The full suite has 98 checks,
including 21 native GTK workflows and real local speech recognition.
