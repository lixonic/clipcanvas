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

## Crop playback memory — 0.17.1

Selected scope: **C03** and **X02**. The intended outcome is to preview and export
saved mouse crops without allocating the complete enlarged picture outside the
canvas. The 0.17.0 crop path scaled the whole source before clipping. At 400%
zoom, that intermediate has sixteen times the pixels of the unzoomed picture;
multiple clip filters and color-format conversions can retain several buffers.
This caused a large RAM spike on cropped timelines, including before playback's
first frame.

The renderer now discards invisible source margins before enlargement and places
the visible picture inside the output canvas. The output still uses the selected
Full/HD/Fast preview dimensions and the chosen export dimensions. It retains
saved zoom, position, crop alignment, rotation, fit/fill and exposed black areas.
This fix does not silently select a smaller preview or change the originals.

Starting playback directly from the crop controls also skips the extra composed
still request that would immediately be superseded. Interactive photo sources
are limited to 1920 pixels before grading and rotation, matching the video crop
controls; export continues to read the original image.

Memory checks use short synthetic media and stop their own FFmpeg process at a
defined memory ceiling, so an unfixed crop path fails the check without exhausting
the machine. Compare uncropped and 400%-zoomed timelines, including free offsets
and transitions. Framing checks compare recognizable source regions and black
borders across scrubbing, live playback and export. Decoder, transition, audio,
overlay and ordinary canvas buffers still contribute to memory; this is a crop
allocation fix, not a fixed total-RAM guarantee for arbitrary projects.

On 2026-11-01, a generated timeline of ten half-second clips at 960×540,
400% zoom and a horizontal offset of 30% produced these FFmpeg peak-RSS results:

| Join | 0.17.0 | 0.17.1 |
|---|---:|---:|
| Cuts | Over 300 MiB; stopped before the first frame | 135.5 MiB |
| Fades | Over 300 MiB; stopped before the first frame | 197.0 MiB |

The old runs were deliberately stopped at a 300 MiB safety ceiling, so their
full peak was not measured. The fixed runs completed every frame; 100% and
400% zoom used similar memory. A separate four-clip 1920×1080 fixture at 400%
used 124.6 MiB for cuts and 180.6 MiB for fades. These measurements cover FFmpeg,
not GTK/GStreamer or the whole desktop, and do not predict arbitrary projects.
Six animated photo scenes at 400% also complete below the 300 MiB test ceiling.
Their working images stay at twice the canvas dimensions, independent of saved
zoom; source windows retain the same motion phase after splitting and seeking.

Run the guarded benchmark with
`/usr/bin/python3 tools/benchmark_crop_memory.py --clips 1 10 --zooms 1 4`.
It uses its own short generated source, reports JSON, and kills its child
renderer if the memory ceiling or timeout is exceeded.

Repeatable manual check:

1. Save the project before upgrading, restart ClipCanvas, and use a short copy of
   the timeline first. Keep the preview quality at its current selection.
2. Left-click a clip, drag it horizontally and vertically, and zoom to 400% using
   a corner. Start Play directly from the crop controls. Check that the first
   frame appears and RAM settles while playing, pausing and seeking repeatedly.
3. Repeat with several cropped clips joined by cuts and transitions. Observe
   FFmpeg and ClipCanvas separately in the system monitor. Closing preview should
   retire its renderer; no old producer should keep consuming memory.
4. Export a short section at the same canvas size. Compare the visible source
   region, black borders, overlay placement and audio with the preview. Check
   portrait, square and landscape canvases, both Fit/Fill, rotated clips, and an
   image scene with slow zoom. Save/reopen and verify that framing is retained.
