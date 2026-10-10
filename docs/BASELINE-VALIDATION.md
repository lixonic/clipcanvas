# Baseline validation

Version: 0.1.1. Checked on Ubuntu 24.04 on 10 October 2026.

The complete generated-media suite passed: **8 tests**, including the native
desktop integration test. It was run with Ubuntu's system Python and FFmpeg.

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

Coverage includes:

- Landscape and portrait output, mixed source sizes/frame rates, all four
  transition effects and hard cuts.
- Delayed/overlapping soundtracks, with rendered audio levels measured.
- Import, waveform creation, trimming, offsets and fades for 16-bit PCM,
  24-bit PCM and 32-bit floating-point WAV.
- Project saving, defaults, validation and exact round-trip reopening.
- Source-file overwrite protection, cancelled rendering, preserving an existing
  destination when cancelled, and temporary-file cleanup.
- Native window import, inspector edits, undo/redo, clip order, preview playback,
  seeking and project reopening.

The Ubuntu package is assembled without administrator privileges. Its contents,
metadata and SHA-256 checksums are inspected as part of the build workflow.
This is development-machine verification. Clean-machine installation, upgrade
and removal remain release checklist items. No Windows runtime or installer has
been tested or published.

The GitHub workflow runs the same suite with a virtual desktop and silent audio
sink, then builds a `.deb` and source archive. A committed workflow is not itself
evidence of a successful hosted run; inspect the corresponding Actions result.
