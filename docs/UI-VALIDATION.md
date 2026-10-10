# ClipCanvas 0.3.0 UI and audio validation

Checked on Ubuntu 24.04 on 10 October 2026 with system Python, GTK 3,
GStreamer, Yaru/Yaru-dark, Yaru icons and FFmpeg. Generated media was used;
no private footage or external editing services were involved.

## Functional checks

`/usr/bin/python3 -m unittest discover -s tests -v`

All **27 tests passed**, including the native desktop workflows (none skipped).
The suite covers the previous transition, WAV, speed, transform, project,
recovery and source-protection checks plus:

- Clicking a clip join opens Transition while preserving the playhead.
- The contextual properties panel starts hidden and does not reserve canvas
  width; the preview occupies more than 65% of the main workspace.
- Import/open/save use `Gtk.FileChooserNative` with the desktop theme.
- Right-click Extract audio retains source trim, actual timeline start, speed,
  volume, individual fades and incoming/outgoing transition audio envelopes.
  It mutes the original; Undo restores the previous project.
- Detached audio and WAV at 0.25× and 4× retain measured pitch and timeline
  offsets. Resetting timestamps after delay avoids FFmpeg dropping that offset
  after tempo conversion.
- Clips without an audio stream and repeat extraction are rejected without
  edits. A present but silent audio stream remains silent when extracted.
- Attached video sound and independent soundtracks both have audio fades.
  Dragging a selected audio fade redraws its taper and is undoable.
- Picture fades render black beginning/end pixels with the middle unaffected.
- Older format 2 projects gain defaults; saves retain the new fade/speed data.

## Visual checks

The native editor, contextual transition controls and format dropdown were
captured and inspected in both Yaru-dark and Yaru. Normal menu rows are readable
in both themes. The system supplies icons, font, focus, hover, window controls
and popup styling. The Picture action uses an icon present in both Yaru and
Adwaita.

The fixed right inspector is removed. The library remains on the left; media
search/type filtering, a collapse action, and a resizable vertical divider
provide workspace control. Joins remain visible at cuts and active transitions;
the last clip has a separate Add videos target. Soundtrack fades have visible
waveform tapers and selected drag handles.

The layout fits a 1280×720 client area without forcing a larger minimum.
A requested 1920×1080 window was limited by the available desktop height to
1920×993; the canvas occupied 1639 px of width, increasing to 1920 px with the
sidebar hidden. This verifies the specified desktop sizes, not every display
scale, font size, accessibility setting, theme or Linux distribution.

## Remaining limits

Full mixed preview still requires a local render. Source-frame scrubbing does
not simulate fades/transitions or play the mix. An extracted audio track is
independent: later clip edits do not move or retime it, and its copied transition
envelopes are snapshots. MP4 exports use H.264/AAC compression, not lossless
encoding. Real-time compositing, text/overlay/caption work, Windows installers,
and deferred AI/API/MCP integration are not delivered by this UI release.

The [Canva UI study](UI-STUDY.md) distinguishes observations, official sources,
the original baseline, implemented changes and broader design targets.
