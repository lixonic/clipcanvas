# ClipCanvas overhaul: implementation plan

Updated 10 October 2026. The user selected all C01–C60 and X01–X10, then deferred
C44–C49 and AI/API/MCP integration. Work proceeds in usable, tested increments.
Selected features are not necessarily implemented. Ubuntu 0.2.0 established the
first foundation increment; 0.3.0 adds native UI and audio workflows; 0.4.0 adds
text/visual layers and local editable captions; 0.5.0 adds shared text presets,
quick logo opacity and user-arranged timeline rows; 0.6.0 adds voice effects. Windows
remains an intended, unvalidated platform.

## Product direction

A native desktop video creator with Canva-style simplicity and CapCut-style
assembly and mixing. Core editing stays local, without accounts or uploads.
Previews may use smaller frames; final export must use originals. Comparable
controls and original templates are in scope; copying Canva code or proprietary
assets is not.

## First increment: 0.2.0

Implemented C04 rotation/flips, C10 video split, C13 constant speed and C31 audio
split. Expanded C09/X01 with drag-edge trim, frame-aligned edits, snapping and
ripple video trims. X02 now has source-frame scrubbing without rendering the
complete composition. X09 adds recovery snapshots, missing-media relinking and
portable copies with collected originals. Format 2 reads format 1 projects and
stores relative media paths where possible.

The source-frame scrub does not simulate overlapping transitions or play mixed
audio. Those still require a prepared preview. Video splits are guarded near
transitions, and audio splits are guarded within fades, so the existing effects
retain their timing. Waveform tails beyond the video end are visible but do not
extend export duration. Recovery runs every 30 seconds while edits are unsaved;
clean save/close clears the current recovery copy.

The FFmpeg progress reader now works without Unix pipe selectors. This removes
one Windows blocker; it does not establish Windows runtime or installer support.

## Native editor and audio increment: 0.3.0

The user's review made workspace clarity the immediate priority. The permanent
right inspector is replaced with compact contextual Timing, Picture, Sound,
and Transition popovers. Native GTK chrome, symbolic icons, system fonts, and
file choosers follow Ubuntu's light/dark theme. The media sidebar can be
searched, filtered, or collapsed; a divider adjusts canvas/timeline height.

Every adjacent clip join has a clickable add/change transition control. Its
popover shows the effective overlap after neighboring-clip limits. The final
plus action imports video. Right-click menus expose the editing actions where
users need them. C15/C16 are still a limited transition subset, rather than full
Canva effects parity.

C34 extraction creates an independent soundtrack without rewriting originals.
It retains the clip's trim, timeline start, speed, volume, audio fades, and
existing transition audio envelopes, then mutes the clip. Extraction from clips without an audio stream or already extracted clips
is unavailable. Later clip edits do not move or retime that independent
track; linked editing remains separate work.

C33 now covers attached clip audio as well as soundtracks. Soundtrack waveform
tapers and drag handles expose fade durations directly. Picture fades from/to
black are separate controls, not sound fades or join dissolves. WAV remains a
local input format. See the [UI study and acceptance criteria](UI-STUDY.md) for
the evidence behind these changes. Release validation records completed checks
separately from the study's design targets.

## Layers and captions increment: 0.4.0

C05/C18/C21 now cover ordered text, still-image/logo and silent video layers,
with independent timing, position, size, rotation, opacity and fit/fill. Canvas
dragging places layers; timeline bodies/edges move and trim visibility. Pango/Cairo
layout is shared by native still preview and export. Prepared preview includes
moving overlay video; source scrubbing also refreshes active overlay frames.

C23/C24 now generate short captions locally from original clip speech or the full
mix, with Tiny/Small model choice, language detection, editable wording/timing,
shared appearance, manual entry and SRT import/export. Setup downloads a private
runtime and model once; recognition is then offline. MP4 captions are burned in.
Format 3 preserves these edits; migrations, recovery, collection and relinking
include visual sources. See [caption setup](CAPTIONS.md) and
[release validation](LAYERS-CAPTIONS-VALIDATION.md).

## Text styles and timeline arrangement: 0.5.0

C22/C25 now include four original static presets shared by every title and
manual/imported/generated caption: Clean, Outline, Shadow and Highlight. Words,
font family/size, placement, timing and opacity stay editable. Shadows, outlines
and rounded line-following backgrounds use the same renderer for native preview
and MP4 export. Caption appearance applies across cues; words/timing are per cue.

The Layers sidebar exposes image/video opacity, Full video visibility and
Size / position controls. Up/Down arrows arrange the selected Video, overlay,
soundtrack or shared Captions row, independently of clip sequence and visual
stacking. Layout is saved in optional format 3 fields and supports undo/redo.
Prepared previews remain usable after a display-only row move. See
[release validation](TEXT-STYLES-VALIDATION.md).

Animation presets, motion paths, animated caption packs, translation and
image-only slideshow assembly remain pending.

## Voice effects: 0.6.0

An Audacity-inspired addition covers part of X05: per-track echo, amplification,
bass/treble, high-pass/low-pass and compression. Right-click Voice effects opens
native tabs with presets, bypass and isolated 12-second A/B audition. Parameters
are validated and stored as optional format 3 fields; preview/export use one
processing chain. Effects follow trims and speed, then track volume/fades;
echo stays inside the item boundary. Extraction retains effects; split parts
are independent. Original-video caption recognition bypasses voice effects.
See [voice effects and validation](VOICE-EFFECTS.md).

## Remaining staged work

| Increment | Feature groups | Acceptance checks |
|---|---|---|
| Canvas layers and titles | C01–C07 refinements, C17/C19/C20/C22, X03 | Image-only assembly, effects, original animation presets, motion paths and keyframes; retain preview/export agreement at both aspect ratios |
| Complete local timeline | C08–C16 refinements, X01–X04 | Trim/split/linking, reverse/freeze, speed ramps, preview caching and keyframes keep timing consistent; preview stays responsive on ordinary social projects |
| Captions | C23–C26 refinements | Animated style packs, translation, richer language/accuracy validation, speaker and word-level workflows |
| Sound and recording | C27–C38, C43, C54, X05 | WAV and other sources, extraction, voice-over/capture, beat markers/sync, balancing, ducking, meters and EQ tested against exported sound |
| Picture finishing | C39–C42, X06–X07 | Color/filter controls, blur/vignette, chroma/masks, optional tracking/background models, LUT handling and deliberate HDR conversion have reproducible output checks |
| Templates and media | C50–C53 | Editable original templates, local asset collections, reusable brand styles and clearly licensed optional stock catalogs |
| Delivery | C55–C58, X08 | MP4/GIF and separate segments, resolution/codec/bitrate settings, queue/cancellation and optional hardware encoding preserve sources and produce compatible files |
| Review and publishing | C59–C60 | Optional authenticated review and social integrations; external accounts/services are separate from the offline editor and cannot be assumed available |
| Desktop releases | X09 refinements, X10 | Project migration/relink/collection and fresh-machine Ubuntu/Windows install, preview audio/video, Unicode paths, cancellation, uninstall and notices pass |
| Deferred AI creation | C44–C49, API providers and MCP | Outside the current milestone. If resumed, use optional provider adapters, model capability discovery, secure key storage and explicit generation actions |

C20's automatic animation recommendations can initially use local presets. AI
background separation, captions and voice cleanup may need optional local models;
no external provider/API integration is included in the 0.6.0 package. Local
speech recognition is included because the user explicitly requested it.

Next selected work remains animation/effects, richer timeline/audio tools and
caption refinements. Choose the next usable increment after reviewing 0.6.0;
keep simple defaults and place advanced controls behind the main workflow.

## Windows engineering

GTK/GStreamer currently comes from Ubuntu packages. Validate a Windows runtime
bundle before committing to an interface rewrite; keep the FFmpeg composition
engine. Remaining work includes Windows file URIs, executable discovery,
GTK/GStreamer deployment, packaging, process/cancellation tests, preview and
export codecs, and third-party redistribution notices. Collected projects now
avoid absolute Ubuntu paths, but native Windows project movement still needs
validation. No Windows installer is provided or claimed supported yet.

## Quality and release discipline

For each increment, update feature statuses, test generated media and the native
workflow, and provide a reproducible installer. Preserve original-file
protection, atomic output replacement, project compatibility and undo/redo.

High-quality H.264 exports still re-encode pixels/audio. Source resolution and
preview resolution are separate; enlarging a source does not restore detail.
Lossless/mezzanine export and HDR mastering are distinct delivery features.
External generation, asset catalogs, collaboration and publishing will never be
required to assemble and export local clips and soundtracks.
