# Image clips and slow zoom

Scope: C07 photo import and a C17 scene animation subset. Each imported image
can become a main timeline clip with its own display time and optional slow
zoom. Available in ClipCanvas 0.11.0 and newer.

## Use

1. Choose **Add images** in Your media. PNG, JPEG, WebP, BMP, TIFF and SVG are
   supported. The main video chooser and file drops also accept these images.
   Import reads files in a worker and leaves the timeline alone.
2. Click the imported image to append a five-second scene. Clicking again creates
   an independent copy. Images can be the only scenes or appear between videos
   and color cards. Use Layers → Image / logo for a positioned overlay.
3. Select a photo and open **Edit → Timing**. Set **Duration (s)** and apply.
   Each photo has its own duration; timeline edges also shorten or extend it.
   A duration badge appears on each photo clip when there is room in the timeline.
4. Open **Edit → Motion** (or right-click → Image motion). Choose **Still image**,
   **Slow zoom in**, or **Slow zoom out**. Zoom amount is the scale change,
   initially 15%. The motion spans the scene; a longer scene moves more slowly.
5. Use Picture for fit/fill, crop position, rotation, flips and picture fades.
   Join controls add transitions. Their overlaps shorten the total project time,
   just as they do for videos. Image clips are silent; add separate soundtracks.

The first video supplies the frame rate even when photos precede it. New
image-only projects start at 30 fps. Sources retain their original files and
resolution; JPEG camera orientation is respected, SVGs are rasterized for the
canvas, and transparent areas show the black scene background. Animated GIF
playback is outside this feature.

## Timing and saving

Still scenes use virtual source time with in/out points. Splitting or trimming
keeps the original motion phase, so an unedited split has no zoom jump. Extending
a scene adjusts its motion range; an explicit duration edit resets its end to
the entered display length. Text, captions and soundtrack timing remain absolute
when scenes change length, as with existing video edits.

Image scenes save in project format 8. Formats 1–7 migrate on load; earlier
ClipCanvas releases cannot open newly saved format-8 projects. Save/reopen,
recovery, portable collection, clipboard and undo/redo include photo settings.
Relinking an image requires a readable replacement picture rather than a
video duration. Source files are never rewritten.

## Verification

Run the generated-media suite with system Python:

```sh
/usr/bin/python3 -m unittest discover -s tests -p test_images.py -v
/usr/bin/python3 -m unittest discover -s tests -p test_ui.py -k image -v
```

The GTK checks need a desktop display. Rendering tests compare exported frames,
live playback, resumed playback and seeks at the beginning/middle/end. They
verify opposite zoom directions, no motion jump after a split, exact frame
counts, mixed photo/video transitions, first-video FPS, soundtrack presence,
silent original-video audio, supported formats, alpha, rotation and fades.
Project checks cover duration extension/trimming, invalid settings, save paths,
collection, recovery, migration and original file hashes. GTK checks cover
library placement, independent durations, zoom controls, edge dragging,
undo/redo, playback and reopening, plus the existing image-overlay workflow.

Manual preview/export check:

1. Import a landscape photo and a portrait photo. Place both and set durations
   of 4 and 8 seconds. Apply zoom in to the first and zoom out to the second.
2. Review the start/middle/end, then play through the join. Try a fade transition,
   fit/fill, picture fades, a soundtrack and text. Confirm the longer move is slow.
3. Split during a move and verify it continues smoothly. Drag an image edge,
   undo/redo, save/reopen, and confirm the duration and motion controls return.
4. Export MP4 and a lossless master at the canvas size. Compare timing, framing
   and movement with the preview; check the input files remain unchanged.
