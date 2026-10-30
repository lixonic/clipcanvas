# Timeline desaturation — ClipCanvas 0.13.0

Selected feature: **C40**, basic color adjustments. The requested outcome is to
reduce or remove color from clips already placed on the timeline.

Select a video, image or color card and open **Picture → Saturation (%)**.
Drag the slider or enter a percentage: **100%** keeps the original color,
**0%** makes the clip black and white, and intermediate values reduce color.
Press **Apply changes** to commit the adjustment.

For a quick black-and-white edit, right-click a clip and choose **Desaturate**.
Ctrl-click or Shift-click several clips first to apply **Desaturate selected
clips** in one undoable edit. Shift+right-click selects the main sequence.
**Restore original color** sets the selected clips back to 100%; **Undo** restores
their previous individual levels. Audio and overlay selections are ignored by
these clip actions.

The timeline thumbnails, paused scrubbing, live playback, rendered preview and
exports show the effect. Each clip is adjusted before transitions and overlays:
titles, captions and logos keep their own colors. Imported assets and original
files retain their color. Settings survive splitting, copying/pasting, duplicate,
save/reopen and recovery. Projects lacking this optional setting default to 100%.
The project format remains version 8. Older app versions do not render the new
saturation adjustment; use 0.13.0 or newer to preview/export it.

## Repeatable visual check

1. Add two colorful videos and a photo to the timeline. Keep an extra copy of the
   photo or video at its original color for comparison.
2. Select a clip, open Picture, enter 50% saturation, and Apply changes. Scrub
   inside that clip and press Play; color should be reduced in both views.
3. Select multiple clips and use right-click → Desaturate selected clips. Check
   the grayscale thumbnails and preview. Undo and Redo the single group edit.
4. Restore original color on one clip; the other clips stay desaturated. Open
   Picture and check that its saturation reads 100%.
5. Add a colored title/logo and a transition. Scrub and play across the join;
   clip colors follow their own settings and overlays retain their color.
6. Split or duplicate a desaturated clip. Save and reopen the project and confirm
   each new piece keeps the adjustment. Check an older project stays colorful.
7. Export MP4 and a lossless master, and compare with the preview. Confirm sound,
   duration and originals are preserved. Also repeat on a color card.

## Automated checks

`tests/test_desaturation.py` generates its own footage and photo. It checks
0%, 50% and 100% saturation, native scrub decoder reuse, grayscale/color pixel
measurements, live sound, lossless export agreement, image/card exports,
transitions, colored overlays, original-file hashes, mixed selections, split,
clipboard, save/reopen, missing-field defaults and rejected invalid values.
`tests/test_ui.py` exercises actual GTK menu actions, the Picture control,
multi-selection, card properties, one-step undo/redo, no-op handling,
save/reopen and unchanged shared library thumbnails.

```sh
/usr/bin/python3 -m unittest discover -s tests -p test_desaturation.py -v
/usr/bin/python3 -m unittest discover -s tests -p test_ui.py -k desaturate -v
```

The GTK check requires a working display; use Xvfb in headless environments.

Validation on 30 October 2026: all four desaturation/rendering and GTK checks
pass, and the panel was visually inspected on the native display. The full suite
ran 174 tests: 171 passed, two speech-runtime checks were skipped, and one
pre-existing bundled-font license checksum check failed. The same mismatch for
`notosansmalayalam/OFL.txt` reproduces on unmodified commit `7dbb76b`.
