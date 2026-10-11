# Changelog

## 0.7.0 — 2026-10-11

- Play the composition directly through a bounded raw video/PCM stream. No
  preview MP4, full-timeline render or encoding is required before playback.
- Live preview and scrubbing use the export composition graph: clip joins,
  picture/audio fades, speed, text/captions, image/video overlays and voice DSP.
  Pause, resume, end/replay and seek remain in the native canvas.
- Removed the FPS dropdown. The first main clip supplies the original frame
  rate, including fractional rates; mixed sources follow it. Reorder/remove,
  legacy metadata probing, save/reopen and final export retain this policy.
- Keep Project → Render smooth preview as an optional fallback for CPU-heavy
  projects. Live frames are at most 640 pixels; final exports use originals.
- Live playback is CPU-based and can stutter on complex projects. Seeking can
  require decoding part of the active clip and soundtrack effect history.
  Windows packaging and provider/API/MCP support remain pending.

## 0.6.0 — 2026-10-11

- Added right-click Voice effects for WAV/MP3 and other soundtracks, plus attached
  video sound: gain in dB, bass/treble, rumble/low-pass filters, echo and compressor.
- Added Neutral, Narration, Telephone and Echo starting presets, editable native
  tabs, bypass, and cancellable first-12-second listening with/without effects.
- Shared the effect chain across isolated audition, prepared preview and export.
  Echo repeats/decay use timeline milliseconds after speed conversion and stop
  at the trimmed item/video boundary. Original source files remain untouched.
- Saved validated optional voice settings in format 3 projects, with undo/redo,
  independent split/duplicate settings, effect retention on extraction and FX
  markers. Original-video caption recognition bypasses the applied voice effects.
- This is track-wide processing; waveform-selection edits, automatic loudness
  normalization, noise removal, pitch changing and external effect plugins remain
  separate work. Windows packaging and provider/API/MCP work remain pending.

## 0.5.0 — 2026-10-10

- Added four original shared text/caption presets: Clean, Outline, Shadow and
  Highlight. Words, font family/size, geometry, timing and opacity stay editable.
  Titles and generated/imported/manual captions use the same native controls.
- Added editable drop shadows and rounded backgrounds following each text line,
  with colors, padding, roundness, offsets and softness. Native preview and MP4
  export share the renderer. Existing format 3 projects keep their prior look.
- Added visible logo/image/video opacity controls in the Layers sidebar, a
  Full video action and direct Size / position access. One slider drag is one
  undoable edit; PNG alpha is multiplied by overall layer opacity.
- Added Up/Down arrows to arrange selected timeline rows, including Video,
  individual overlays/soundtracks and the shared Captions row. Save/reopen and
  undo/redo retain the order; row placement does not change timing, visual
  stacking or invalidate a prepared preview.
- Text and caption editing opens directly on words and style choices.
- Styles are static; animated/karaoke captions, Windows packaging and deferred
  provider/API/MCP integration remain pending.

## 0.4.0 — 2026-10-10

- Added timed text layers with Unicode shaping, installed fonts, bold/italic,
  alignment, text/background/outline colors, and shared native/export rendering.
- Added transparent images/logos and silent video overlays with position, size,
  rotation, opacity, fit/fill, source start, playback speed, and layer ordering.
  Drag on the canvas to position; drag timeline bodies/edges for timing.
- Added editable captions generated locally from original clip speech or the
  full mix. First setup downloads a private CPU runtime and a Tiny/Small model;
  later recognition is offline. Language detection, explicit language, progress,
  cancellation, and undoable replacement are supported.
- Added manual caption entry, word/timing correction, shared caption styling,
  burned-in MP4 captions, and Unicode SRT import/export.
- Added format 3 projects, migration from formats 1/2, and layer-aware media
  collection, recovery, relinking, and original-file protection.
- Pinned faster-whisper 1.2.1, Hugging Face Hub 0.36.0, and PyAV 16.1.0 in the
  isolated runtime after actual recognition exposed a PyAV 19 incompatibility.
- Animated titles/caption packs, translation, image-only slideshows, Windows
  packaging, and the previously deferred provider/API/MCP scope remain pending.

## 0.3.0 — 2026-10-10

- Rebuilt the editor around Ubuntu's GTK theme, system fonts, symbolic icons,
  native header bar, and native file choosers. Removed global widget color
  overrides that made dropdown text unreadable under Yaru Dark.
- Removed the permanent right inspector. Timing, Picture, Sound, and Transition
  controls now open in compact contextual popovers beside the selected item.
- Added media search/type filtering, sidebar collapse, and an adjustable divider
  between the canvas and timeline.
- Added clickable round transition controls at every adjacent clip join, with
  effective overlap feedback; the trailing plus action adds local videos.
- Added clip/audio context menus for editing, fades, split, duplicate, delete,
  clip movement, mute/unmute, and audio extraction.
- Extract audio into an independent local track while retaining source trim,
  timeline start, pitch-preserving speed, volume, fades, and existing transition
  audio envelopes. Mute the original clip; undo restores it. Reject clips without audio
  and repeated extraction while that clip's extracted track remains.
- Added audio fades to attached video sound as well as soundtracks. Selected
  soundtracks show draggable fade handles and waveform tapers at their audible
  composition boundaries.
- Added separately labeled picture fade in/out from/to black. These are distinct
  from audio fades and cross-dissolve joins.
- Added a detailed Canva UI study separating screenshot observations, verified
  official interactions, baseline gaps, implemented changes, and QA targets.
- Retained local WAV support and original-media rendering. Real-time mixed
  preview, titles/overlays, Windows packaging, and deferred AI/MCP work remain
  outside this release.

## 0.2.0 — 2026-10-10

- Split video and WAV/audio tracks at the playhead, with undo/redo. Splits near
  transitions or within fades are guarded to preserve their timing.
- Drag clip/audio edges to trim. Video edits ripple; audio left trims keep
  source sound aligned. Snap to clip edges, audio edges and the playhead.
- Scrub source frames without first rendering a complete composition. This
  frame preview does not simulate transitions or play mixed audio.
- Set constant video speed from 0.25× to 4×, preserving original audio pitch.
- Rotate by 90° increments and flip footage horizontally or vertically.
- Save recovery copies every 30 seconds; recover through the Project menu.
- Relink missing media and collect a portable project with copies of originals.
- Project format 2 uses relative media paths where possible and loads format 1.
  New projects cannot be opened in ClipCanvas 0.1.x.
- Replaced the Unix-only FFmpeg progress reader with a portable worker thread.
  Windows runtime and installer work remain pending.
- Recorded the full overhaul scope; C44–C49 and AI/API/MCP connections are
  deferred following the latest scope adjustment.

## 0.1.1 — 2026-10-10

- Made WAV files easier to select in the soundtrack importer, including uppercase
  `.WAV` filenames.
- Added regression checks for 16-bit PCM, 24-bit PCM, and 32-bit floating-point
  WAV files: import, waveforms, saved projects, trims, timing, layered volume,
  fades, and exported sound.
- Added an Ubuntu 24.04 workflow to test the native application and build Debian
  packages, with downloadable build artifacts. Releases are not published
  automatically.
- Added issue forms and repository baseline files. Windows support remains
  planned; this version does not include a Windows application or installer.

## 0.1.0 — 2026-09-24

- Initial native Ubuntu video creator with landscape, portrait, and square
  canvases.
- Local clip import, ordering, trimming, fit/fill, crop position, and transitions.
- Layered soundtracks with start times, source trims, volume, and fades.
- Local rendered preview and MP4 export from the original media.
- Project saving and reopening, undo/redo, and a Debian installer.
