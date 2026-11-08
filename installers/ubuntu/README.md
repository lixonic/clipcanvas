# Ubuntu installers

`clipcanvas_0.21.1_all.deb` is the current native Ubuntu 24.04 prototype. It adds a
saved media library, group selection/clipboard/Delete, Ctrl+wheel timeline zoom,
remembered folders/audio settings, visible project controls and a bigger Play
button. Group volume and Mute selected videos/audio are included in 0.9.1.
Version 0.9.2 adds shared audio lanes, vertical drag/drop, placement menus and
splits that remain on the same lane. Version 0.9.3 releases preview decoders
before export to reduce memory use while keeping export quality unchanged.
See [memory use and validation](../../docs/EXPORT-MEMORY.md).
Version 0.9.4 presents the picture and white playhead in the same GTK paint
cycle and reduces timeline drawing work during playback. See [timing validation](../../docs/LIVE-PREVIEW.md).
The playback line follows displayed frames and silent scrubbing seeks
into the active source. Earlier installers remain for historical reference.

Download the `.deb` and `SHA256SUMS.txt`; on GitHub choose **Download raw file**.

```sh
sudo apt install ./clipcanvas_0.21.1_all.deb
```

Save and reopen an already-running ClipCanvas session after upgrading. Installation
may fetch Ubuntu dependencies. Automatic captions initially download their private
runtime/model, then run offline. See [caption setup](../../docs/CAPTIONS.md).

See [workflow validation](../../docs/EDITOR-WORKFLOW.md). Build with
`/usr/bin/python3 build_deb.py --output-dir dist`; GitHub Actions provides temporary
artifacts. Windows and external provider/API/MCP work remain pending.

Version 0.21.1 makes the caption Highlight tile follow each spoken word and
adds horizontal and vertical alignment choices. Save and reopen ClipCanvas
after upgrading. Choose Captions → Caption style → Highlight, then Apply.

Version 0.21.0 adds speech-timed word highlighting, horizontal/vertical caption
position and size controls, and reusable named presets including placement.
Save and reopen the app, then regenerate old captions for speech word timestamps.

Version 0.20.1 fixes crushed shadows in native paused previews of full-range
video, retaining the reusable decoder and original import color. Save and reopen
the app after upgrading. See [source color](../../docs/COLOR-RANGE.md).

Version 0.20.0 adds Automatic/Software MP4 encoding choices, actual VAAPI capability
probes and software retry on hardware failure. The confirmed encoding preference
is remembered. See [hardware export](../../docs/HARDWARE-EXPORT.md).

Version 0.19.0 adds independent Fast, Balanced and Efficient compression MP4
export speeds, remembers the last confirmed MP4 choice and uses up to two
export filter workers. See [export performance](../../docs/EXPORT-SPEED.md).

Version 0.18.4 loads cut-only timelines in two-clip picture windows while keeping
a continuous soundtrack and bounded playback queues. Transitions and visual
layers retain the established composition path. See [windowed preview](../../docs/PREVIEW-BUFFERING.md#windowed-picture-decoding--0184).
Version 0.18.3 reduces Full/HD/Fast startup waits on long graded timelines with
precomputed color lookup tables and independent embedded-audio reads. See
[graded preview validation](../../docs/PREVIEW-BUFFERING.md#graded-timeline-startup--0183).

Version 0.18.2 removes the up-front Fast/HD conversion of every remaining source.
Playback uses available media immediately while one low-priority worker prepares
smaller cached videos for later plays/seeks. Stopping or seeking discards the
previous preview promptly. See [startup validation](../../docs/PREVIEW-BUFFERING.md#long-timeline-startup--0182).

Version 0.10.0 adds illustrated transitions, layer/caption animations and attached
sound effects. Repeated ordinary seeks reuse a full-resolution native decoder.
See [effects and validation](../../docs/EFFECTS.md).

Version 0.10.1 reduces the Play delay after seeking by decoding video close to
the playhead while keeping voice-effect history. Preview and export quality
settings are unchanged. See [startup measurements](../../docs/PLAYBACK-STARTUP.md).

Version 0.10.0 reads project formats 1–7 and saves format 7. Newly saved projects
need 0.10.0 or newer; media, sound cues and animation settings remain editable.

Version 0.10.2 accelerates Circle Wipe, Colour Wipe, Line Wipe and Flow with native
compositing and reusable lossless geometry masks. Preview resolution and export
quality are retained. See [transition measurements](../../docs/TRANSITION-PERFORMANCE.md).

Version 0.10.3 fixes audio starvation during animated text/caption playback using
native opacity, wipe and blur operations. Audio settings and video quality remain
unchanged. See [animation/audio validation](../../docs/ANIMATION-AUDIO.md).

Version 0.10.4 adds vertical wheel scrolling and two-axis trackpad movement in
enlarged previews. Shift+scroll moves horizontally, Ctrl+scroll zooms, and Fit
recenters. Project data, playback time and exports are unchanged.

Version 0.10.5 adds a native remove icon and right-click removal for sidebar
imports, plus Delete when a media row has focus. Undo restores the entry.
Original files, timeline copies and active playback remain intact.

Version 0.10.6 starts captions directly using Small and automatic language
detection by default. The adjacent gear opens optional caption settings; Apply
remembers source, model and language across launches. Progress, cancellation
and editable caption results remain available.

Version 0.10.7 adds a trash button on hovered sidebar captions, also visible
with keyboard focus. It deletes only that cue, keeps other timeline items and
preview time, and supports Undo/Redo and project save/reopen.

Version 0.11.0 imports images as main timeline clips with an independent duration
and a visible time badge. Edit → Timing sets each photo's display time; Motion
offers slow zoom in/out with an adjustable amount. Image scenes support
transitions, soundtracks, undo/redo and save/reopen. This release saves project
format 8 and reads formats 1–7; newly saved projects need 0.11.0 or newer.
See [image clips and validation](../../docs/IMAGE-CLIPS.md).

Version 0.11.1 keeps preview audio and video buffered together at slow splits and
transitions, with visible buffering feedback and preserved manual pause. Fast/HD
prepare and reuse smaller cached video sources, showing progress on first use.
Sound, Full resolution and export still use originals. The project format stays
at 8. See [preview buffering and validation](../../docs/PREVIEW-BUFFERING.md).

Version 0.11.2 explicitly depends on the SVG and WebP image loaders, including
when Ubuntu recommended packages are disabled. CI installs the same loaders.

Version 0.11.3 keeps the zoomed timeline at the current scroll position when
clicking, dragging, right-clicking or opening a transition far along the sequence.
Drag-edge autoscroll and revealing items selected in the sidebar remain available.

Version 0.11.4 fixes short text-layer edge dragging at minimum timeline zoom,
shows the changing bar length while stretching, and offers Fit to full video for
text watermarks in Layers, Timing and the right-click menu. Text also shares the
sidebar opacity slider. Timing edits support Undo/Redo and save/reopen.

Version 0.11.5 makes video and audio clip body clicks move the playhead and
preview to the clicked timeline time, including when zoomed and scrolled.
Trim/fade handles, modifier selection and clip dragging remain available.

Version 0.11.6 replaces decorative RMS waveforms with detailed source-sample
peaks, preserving silence, stereo pulses and trim/speed alignment across zoom.
Adjoining, unchanged audio splits play and export continuously through the same
tempo/effect processor. Save and reopen the running app after installation.

Version 0.11.7 keeps moved/trimmed WAV pieces audible even with incorrect source
byte-rate headers. Paused seeks skip distant future scenes; short forward jumps
reuse the decoder, and continuous dragging keeps showing completed frames.

Version 0.12.0 adds a visible Malayalam caption language choice and bundles three
Google Fonts: Noto Sans Malayalam, Manjari and Anek Malayalam. Selecting Malayalam
uses multilingual Turbo (about 1.6 GB on first use). Fonts work offline
in captions, titles, preview and export, with their SIL Open Font Licenses.

Version 0.13.0 adds Picture → Saturation (0–100%) and right-click Desaturate/Restore
original color for one or more timeline clips. Video, photo and color-card
thumbnails, scrubbing, playback and export reflect the effect; originals and
overlay colors are preserved. See [desaturation and validation](../../docs/DESATURATION.md).

Version 0.13.1 fixes Malayalam caption generation: saved Malayalam settings
use a dedicated Malayalam speech model (about 1.5 GB first download), with short
recognition windows and checks against wrong-language or wrong-script output.
Save your project and restart after upgrading, then Generate captions again.

Version 0.14.0 adds **Color** grading with Pixar-inspired animation, Horror,
Adventure, Romantic and Mystery presets, adjustable strength and full primary
color/light sliders. Compare original color, bypass/reset, and apply to selected
clips with Undo/Redo. Video, photo and card previews/exports share the grade.
Save and restart after upgrading. See [color grading](../../docs/COLOR-GRADING.md).

Version 0.15.0 adds a separate **Apply transition to all clips** button beneath
Apply changes on the Transition page. It copies effect, duration, direction and
color to every join in one Undo step. None removes all transitions. Save and
reopen the running app after upgrading. See [transitions](../../docs/EFFECTS.md).

Version 0.16.0 adds saved per-clip **Picture → Crop and zoom** at 100–400%, with
horizontal/vertical crop position shared by preview and export. Older projects
default to 100%. The preview also clips video and layers to a fixed landscape,
portrait or square frame and ignores clicks on hidden layer regions. Toolbar
magnification and panning remain separate view settings. Save and reopen the
running app after upgrading. See [crop and preview framing](../../docs/LIVE-PREVIEW.md#saved-crop-and-fixed-preview-frame--0160).

Version 0.17.0 adds direct mouse cropping. Left-click the preview video or image
to show an outline, drag the picture to position it, or drag a corner for
100–400% zoom. Done or Enter finishes; Escape cancels the current drag. Each
released change supports Undo and is saved for preview/export. Movement works
at 100% zoom too, with black exposed areas. Existing numeric controls and overlay
selection remain available. Save and restart after upgrading. See
[mouse crop controls](../../docs/LIVE-PREVIEW.md#mouse-crop-controls--0170).

Version 0.17.1 reduces cropped playback RAM use by trimming invisible source
regions before enlargement and skipping an extra composed still when starting
playback. Interactive photo crop sources are bounded before grading/rotation.
Saved crops, selected preview resolution and export dimensions are preserved.
Save your project and restart after upgrading. See
[crop memory and validation](../../docs/EXPORT-MEMORY.md#crop-playback-memory--0171).

Version 0.18.0 replaces stacked media-library rows with thumbnail tiles that
wrap to the sidebar width. Video/image previews preserve their aspect ratio;
audio and loading previews use media-type icons. The three import buttons share
a line when space permits and wrap at narrower widths. Search, filters,
click-to-add, removal and keyboard controls remain available. Save and restart
after upgrading.
See [media workflow](../../docs/EDITOR-WORKFLOW.md#import-then-place).

Version 0.18.1 groups timeline right-click commands into Edit, Picture, Audio,
Motion, Arrange and Insert submenus. Only relevant groups appear; Select all and
Delete remain in the main menu. Keyboard shortcuts work in nested menus.
Save and restart after upgrading. See
[grouped timeline menus](../../docs/EDITOR-WORKFLOW.md#grouped-timeline-menus).
