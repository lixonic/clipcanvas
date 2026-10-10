# Changelog

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
