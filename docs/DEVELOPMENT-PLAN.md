# ClipCanvas overhaul: staged development plan

Updated 10 October 2026. This is a proposal for selection, not a list of completed
features. The current native Ubuntu baseline is version 0.1.1.

## Product direction

Build a local desktop video creator for people comfortable with Canva's visual
canvas and clip strip. Support the practical clip assembly and audio mixing
expected from CapCut-style workflows. Core editing must work without an account,
uploads, or a browser. Export must read original media, even when preview uses
proxies. Ubuntu and Windows are the intended platforms.

## Current baseline

The current Python/GTK app has landscape, portrait, square, and 4K canvas presets;
one video sequence; numerical trim controls; fit/fill with crop positioning;
clip reordering, duplication and removal; four transitions; independent audio
layers with start time, trim, volume and fades; audio waveforms; locally rendered
previews; high-quality H.264/AAC MP4 exports; save/open; and undo/redo.

WAV import is verified with 16-bit PCM mono, 24-bit PCM stereo, and 32-bit float
stereo sources. Projects reference local source files rather than containing
media. The renderer and playback widget use FFmpeg and GStreamer respectively.

Ubuntu is the implemented platform. There is no Windows installer yet. The
prototype renders the whole preview after an edit and has no video overlays,
text, captions, full timeline trimming, or real-time effects.

## Recommended sequence for selection

| Stage | Deliverable | Acceptance check |
|---|---|---|
| 0. Preserve a working baseline | Source control, repeatable tests, Ubuntu installer, source archive, feature decisions | Clean install, project reopen, WAV mixing, original-file protection and MP4 export pass |
| 1. Desktop and timeline foundation | Confirm toolkit; plan Windows runtime; direct trim/split, snapping, scrubbing, proxy/cache strategy, recoverable projects | A mixed-format project can be assembled and resumed without repeated full preview renders for every simple edit |
| 2. Clip and audio workflow | Improved transitions, speed, image clips, audio split/loop/crossfade, voice-over recording and ducking if selected | Timing and level changes agree between preview and final export |
| 3. Canvas layers | Text, images/logos, overlay video, picture-in-picture, transform handles and layer timing | Preview/export agree for timed and overlapping elements in both aspect ratios |
| 4. Captions and reusable style | Manual/SRT captions, optional local speech recognition, reusable styles/templates and brand assets | Captions can be corrected, saved, reopened and rendered reproducibly |
| 5. Optional advanced tools | Beat detection, keyframes, chroma key, local noise cleanup/background separation or selected AI | Each tool has a documented quality/performance test and an explicit resource requirement |
| 6. Releases for both desktops | Ubuntu .deb and Windows installer with full bundled/runtime dependency strategy | Fresh-machine install, preview audio/video, Unicode paths, cancellation, uninstall and source-protection checks pass on each OS |

Windows planning starts in stage 1; a release becomes supported only after its
native tests pass. Stages can be narrowed to the selected features. Do not begin
the full feature overhaul before the first batch has been chosen.

## Windows engineering work

The current GTK/GStreamer Python stack is installed from Ubuntu packages. A
Windows release needs either a tested GTK/GStreamer distribution or a migration
of the interface/player to another native desktop toolkit. Qt/PySide is a
candidate for a short feasibility comparison, not an approved rewrite.

The existing FFmpeg composition engine can remain the rendering core. Before a
Windows port, replace its Unix pipe-selector progress reader, handle Windows
file URIs and paths, locate packaged FFmpeg/ffprobe, and test process cancellation
and atomic output replacement. Project portability needs media relinking or a
collect-media command: absolute Ubuntu paths will not open unchanged on Windows.

Packaging must include the relevant third-party notices. Test both preview
decoding and export encoding, not just whether the window opens. Record signing
and distribution decisions before calling a Windows installer production-ready.

## Feature selection process

1. Use the feature IDs in [the Canva inventory](CANVA-FEATURES.md).
2. Assign **Now**, **Later**, or **Skip**. Existing basic features can receive an
   **Improve** decision.
3. Keep the first implementation batch small: one user workflow, with explicit
   acceptance examples and dependencies.
4. Implement one feature or tightly related group in a branch, then test real
   media, update the catalogue status and create a reviewable installer.
5. Tag a release only after its platform checks pass. Keep unreleased work and
   supported installers clearly labelled.

Recommended first batch for discussion: timeline split/trim and snapping,
responsive scrubbing, better audio controls, and project autosave/media relinking.
Timed text and overlays are the next useful visual additions. These are
recommendations awaiting selection, not commitments to implement every item.

## Quality and local operation

Editing should preserve original files and source resolution. A small preview
is not an export source. A 1080p landscape clip cannot fill a 1080p portrait frame
without cropping and scaling; the interface must make that choice clear.

High-quality delivery codecs still re-encode pixels and audio. Lossless or
mezzanine export is a separate possible feature, with much larger files. Some
effects and AI models may need substantial RAM/GPU resources; optional online
services must be clearly identified before media is transmitted.

Canva's premium media catalogue, music licensing, branding and cloud services
are separate products. ClipCanvas can implement comparable editing controls and
original templates; importing Canva's proprietary assets or copying its code is
not part of this plan.
