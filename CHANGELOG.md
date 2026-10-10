# Changelog

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
