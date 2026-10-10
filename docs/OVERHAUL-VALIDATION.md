# ClipCanvas 0.2.0 validation

Checked locally on Ubuntu 24.04, 10 October 2026, using system Python, GTK,
GStreamer and FFmpeg. The test suite uses generated media, never private footage
or remote services.

## Automated checks

`/usr/bin/python3 -m unittest discover -s tests -v`

18 checks passed with the native desktop display enabled. These include:

- Existing transitions, aspect ratios, mixed frame rates, original audio and
  soundtrack mixing, fades, cancellation and original-file protection.
- 16-bit PCM mono, 24-bit PCM stereo and 32-bit float stereo WAV import/export.
- Speed at 0.25×, 2× and 4×: exported duration and a measured 440 Hz source tone
  confirm pitch-preserving audio.
- 90° rotation plus horizontal flip: exported RGB pixels compared with a direct
  reference transform.
- Audio split: sound remains audible on both sides of the cut.
- Speed-aware video splits, independent audio placement, outer fades, guarded
  transitions/fades, trim limits and source alignment.
- Format 1 migration; format 2 round trips; invalid speed rejection.
- Portable collection with duplicate references and colliding basenames; moving
  the folder and deleting the originals still leaves every media path readable.
- Recovery through a fresh session without modifying the saved project.
- Native import, edit, split, source-frame scrub, snapping, drag trim, undo/redo,
  save/reopen and rendered playback.

## Limits

This is an Ubuntu prototype. No Windows runtime/installer has been tested.
Source-frame scrubbing omits transitions and mixed sound. Full preview effects
still require a local render. Recovery is periodic, not a backup of originals.
Generative AI, API key settings and MCP integration are deferred and absent.
