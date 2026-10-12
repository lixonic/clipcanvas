# Changelog

## 0.10.2 — 2026-10-12

- Replace interpreted per-pixel transition expressions in Circle Wipe,
  Colour Wipe, Line Wipe and Flow with native compositing. Wipes use reusable,
  lossless geometry masks; Flow uses edge-preserving movement and native
  blending. Retain the selected preview resolution, export quality, timing,
  directions, colours and original audio processing.
- Bound the reusable mask cache to 64 MiB and 32 entries on disk. Generate
  masks by scanline with little resident memory; private hard links protect
  active renders during eviction. Route outgoing prefixes rather than queuing
  their preceding full-resolution frames. No GPU or new dependency is needed.
- Align fractional-rate mask timestamps and stop movement at the actual
  outgoing frame boundary. Keep opaque video backgrounds under overlays;
  the former custom Flow sampler could mistakenly use chroma as alpha.
- Add original-renderer frame/audio comparisons, cache/cancellation tests,
  isolated 1080p effect benchmarks and native preview frame-delivery
  measurements. See [transition performance](docs/TRANSITION-PERFORMANCE.md).

## 0.10.1 — 2026-10-12

- Seek the active video input close to the playhead when starting live playback.
  Previously Play after seeking decoded every earlier picture in that clip.
  Retain complete trimmed audio history for echo, compression, tempo and fades;
  an audible active clip uses a separate audio input. Keep future transitions,
  video fades, overlays and captions on the original composition frame grid.
- Show Loading preview until GTK commits the first picture for the current
  playback generation. Pause responds during startup; stale frames/errors cannot
  clear a newer loading state. Release startup resources on decoder failure.
- Add full-resolution startup benchmarks and regression checks comparing deep
  seeks with complete-stream pictures and PCM audio, including echo/compression,
  speed, source trim, fades and transitions. Preview resolution and export
  quality settings are unchanged. See [measurements and limits](docs/PLAYBACK-STARTUP.md).

## 0.10.0 — 2026-10-12

- Add illustrated transition tiles for None, Dissolve, Slide, Circle Wipe,
  Colour Wipe, Line Wipe, Match & Move, Flow and Stack. Direction and colour
  controls appear where applicable, alongside a shared duration slider/spin.
  Compact white join buttons use the bowtie symbol; inactive joins reveal a
  plus on hover. Existing project transition keys retain their old rendering.
- Add Rise, Pan, Fade, Pop, Wipe and Blur on entry and exit for text, images and
  overlay video. Editable titles/captions also support Reveal, Highlight and
  Snake word animations. Caption animation can be applied to every cue.
  Match & Move tweens explicitly paired identical editable elements across
  neighboring scenes; flattened video objects cannot be matched separately.
- Attach original procedural Pop, Crisp pop, Click and Whoosh sound cues or
  local WAV/MP3 audio to a layer's appearance/disappearance. Compose many cues
  into one float PCM input using bounded blocks, retaining headroom and
  avoiding a decoder/delay buffer per caption. Relinking and portable projects
  include custom sound files; original-video speech excludes element sounds.
- Reuse one native decoder for ordinary paused seeks and continuous dragging,
  with two buffered source frames for nearby forward movement. Timed text and
  images draw through the shared native renderer; transitions and overlay
  videos use the composed renderer. Suppress obsolete seek results, shorten
  click scheduling to 20 ms and release the seek decoder before export.
- Save format 7 with animation, transition and attached sound settings; migrate
  formats 1–6. Add real frame/audio, native editing, portability, resource and
  seek regression checks. See [effects](docs/EFFECTS.md) and
  [seek measurements](docs/SEEK-PERFORMANCE.md).

## 0.9.4 — 2026-10-12

- Present the newest decoded preview picture and its timeline position together
  in GTK’s frame-clock update phase, before widgets paint. Both live and prepared
  previews use picture timestamps; the file-preview query timer no longer moves
  the line independently. Pending frames remain bounded to one.
- Redraw only the old and new playhead strips during playback. Cull thumbnail
  tiles, waveform bars, clips, captions and joins outside the dirty area while
  preserving all interaction targets. Item drags keep their complete repaint.
- Avoid redundant canvas redraws and unchanged clock-label updates. Native tests
  compare preview/timeline paint frame counters, newest-frame coalescing, pause
  and generation guards. Project data and export quality stay unchanged.

## 0.9.3 — 2026-10-11

- Stop and release the live preview before export or prepared-preview rendering.
  Previously, a paused FFmpeg preview could retain its decoder/filter graph
  and buffered frames throughout export, adding to the renderer’s memory use.
- Cancel queued scrub requests and wait for their worker to exit before starting
  the renderer. Keep the displayed picture, playhead and project unchanged;
  Play recreates the preview after completion or cancellation.
- Retain all export resolution, frame rate, encoding and quality settings. A
  local 720p sample used 26% less combined FFmpeg memory and produced an
  identical MP4. See [measurement and limits](docs/EXPORT-MEMORY.md).

## 0.9.2 — 2026-10-11

- Place multiple sequential audio clips in one lane. Drag horizontally for
  timing and vertically between lanes, with a lifted waveform, destination
  marker, overlap feedback and viewport edge scrolling. Drop into the new-lane
  area to separate simultaneous sounds. Each move has one undo step.
- Right-click → Move to audio lane offers the current time when it fits, or
  appends after the target lane's last clip with the resulting time in its label.
- Audio splits retain their lane. Up/Down moves the complete lane. Deleting its
  first clip keeps its row identity/order for the remaining clips. New copies
  join the selected audio lane when the playhead has room; overlapping duplicates
  and pasted groups use separate lanes while preserving their timing.
- Format 6 saves lane membership and migrates formats 1–5 without changing
  existing audio timing, effects or row order. Lane placement alone leaves the
  rendered sound byte-identical.

## 0.9.1 — 2026-10-11

- Apply volume to every selected video/audio item from the Sound panel, with
  the target count shown in the control and Apply button. Group volume preserves
  each item's trim, timing, speed, fades, voice effects and picture properties.
- Add Mute/Unmute selected videos/audio context actions. Cards and visual/text
  layers are excluded; video-only selection leaves soundtracks alone. Changes
  have one undo step and persist in saved projects. Unmute restores remembered
  levels within the current app session, otherwise normal volume.
- Rebuild the inspector for the right-clicked item so a group panel opens with
  that item's current volume, including when it is not the first selected clip.

## 0.9.0 — 2026-10-11

- Imported video/audio/image assets live in a separate media library. Clicking
  creates an independent timeline copy; importing leaves the composition alone.
  Format 5 saves unused assets too and migrates formats 1–4.
- Shift+right-click selects all videos or audio; Shift+click selects a range,
  Ctrl+click toggles items and Ctrl+A selects the complete timeline. Delete and
  Ctrl+C/X/V operate on groups with one undo step and fresh pasted IDs. Text
  fields keep their native editing shortcuts.
- Ctrl+wheel zooms the timeline around the pointer independently of canvas zoom.
- Native file pickers remember accepted folders across restarts. Applied voice
  effects become initial settings for unconfigured tracks; configured tracks
  retain their own values. Volume/fades have a Use last settings button.
- Labelled Open/Save project controls, Save as/Ctrl+Shift+S, and a larger primary
  Play/Pause button make project and playback actions visible.
- Live playhead updates accompany presented video frames rather than a 100 ms
  query timer. Pause discards queued frames. Silent scrubbing input-seeks into
  the current clip with aligned source/composition frames and preserved fades
  and transitions; sound processing retains its full history.

## 0.8.0 — 2026-10-11

- Default native playback and paused pictures to the full project resolution.
  Add Full / HD (1280) / Fast (640) preview choices, independent of zoom/export.
  Prepared previews follow the chosen size and use CRF 18 instead of 28.
  On-canvas editing frames now use PNG rather than JPEG.
- Default the export dialog to Maximum quality MP4. Add a lossless FFV1 video /
  floating-point PCM audio master in MKV, preserving the composed output without
  lossy encoding. Scaling, effects and 8-bit YUV 4:2:0 conversion still apply.
- Add native color cards with per-card duration, toolbar and before/after context
  insertion, color/duration editing, trimming, split, duplicate, reorder and join
  transitions. Cards can form a video without source clips and carry no sound.
- Save format 4 projects for file-free scenes; migrate formats 1–3. Collection,
  recovery, relinking, source protection and automatic FPS skip card file paths.
- Lift a clip thumbnail under the pointer while dragging, make room at its target,
  show a drop marker, and scroll near viewport edges. Commit one undoable reorder
  on release. Audio and visual layer bodies also move visibly during dragging.
- Cancel obsolete paused-frame callbacks on a newer seek so they cannot stop a
  new playback generation. Keep source, timing and undo data unchanged by quality
  or view controls. Add rendering/native regression coverage for these workflows.

## 0.7.1 — 2026-10-11

- Drag the white playhead anywhere along its line, as well as the ruler, to
  update the composed preview while moving. Coalesce pointer positions without
  postponing every frame until dragging stops; ignore stale playback positions.
- Pause sound while scrubbing and resume at the chosen time if previously
  playing. Paused scrubbing reads one composed RGB frame without soundtrack
  processing or a prepared video file. Completed clips and inactive cues are
  skipped; deep seeks and complex pictures still depend on CPU decoding.
- Add native preview minus/plus and Fit/25–400% controls, Ctrl+wheel zoom, and
  Shift+drag or middle-button pan. Zoom follows the complete composition and
  layer editing coordinates. Fit resets the view; exports and project geometry
  are unchanged. New/open/recovered projects start at Fit.
- Validate continuous dragging, cross-clip frames, captions/transitions/overlays,
  paused release, playback resume, zoom/pan and layer placement.

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
