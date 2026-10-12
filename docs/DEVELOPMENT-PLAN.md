# ClipCanvas overhaul: implementation plan

Updated 12 October 2026. The user selected all C01–C60 and X01–X10, then deferred
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
no external provider/API integration is included in the 0.8.0 package. Local
speech recognition is included because the user explicitly requested it.

Next selected work remains animation/effects, richer timeline/audio tools and
caption refinements. Choose the next usable increment after reviewing 0.8.0;
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

## Live playback and source frame rate: 0.7.0

The native canvas consumes raw video and PCM audio directly from the shared
composition graph. No prepared preview movie is required. Queues are bounded;
seek/edit/close cancels the prior producer. CPU-heavy projects may use the
explicit rendered-preview fallback. First-source FPS replaces manual choice,
including fractional rates and legacy-project metadata resolution. See
[live preview and validation](LIVE-PREVIEW.md). Hardware/GPU playback, waveform
cache improvements and Windows runtime validation remain future work.

## Continuous scrubbing and preview view: 0.7.1

Drag the white playhead line anywhere along its height to update the composed
picture while holding it. One cancellable frame worker consumes the latest
pointer time without a growing queue or debounce starvation; paused release
loads no audio mix. Previously playing preview resumes from the released time.
Native Fit/25–400% controls, Ctrl+wheel zoom, and Shift/middle-button pan change
only the view and retain layer placement coordinates. The suite now passes
76 local checks, including 13 native workflows; see [validation](LIVE-PREVIEW.md).
Persistent seek caches/proxies and hardware decoding remain future work.

## Quality, cards and drag feedback: 0.8.0

Full project resolution is the default for native playback/paused pictures,
with explicit HD/Fast choices. Maximum MP4 is the export default; an FFV1/float-PCM
MKV master preserves composed output without lossy encoding. File-free color
cards have editable color/duration and main-sequence editing/transitions. Format
4 migrates formats 1–3 and retains source-only media collection/relinking.
Lifted thumbnails, insertion gaps/markers and viewport-edge scrolling make a
reorder visible before committing one undo step. See [validation](QUALITY-CARDS.md).
High-bit-depth/HDR mastering, persistent preview caches and hardware acceleration
remain separate work.

## Desktop workflow follow-up: 0.9.0

Separate saved library assets from timeline copies, with explicit click placement.
Add group selection, clipboard/Delete controls, pointer-anchored Ctrl+wheel
zoom, persistent accepted folders and applied audio settings, visible project
controls and a larger Play button. Present playback time with the decoded frame;
input-seek silent scrubbing while preserving source/output frame alignment and
fades/joins. Format 5 migrates prior projects and collects unused assets too.
See [workflow validation](EDITOR-WORKFLOW.md). Hardware decoding/caching and
Windows/provider work remain separate increments.

### Shared audio lanes — 0.9.2

Use stable lane IDs independent of clip IDs, with sequential audio clips per
lane, vertical dragging, overlap feedback and sample-accurate snapping. Splits,
clipboard groups and row ordering retain lane membership. Preserve all per-clip
processing and migrate prior projects to format 6; verify lane-only placement
with byte-identical rendered sound. See [validation](AUDIO-LANES.md).

### Export memory follow-up — 0.9.3

Release the live preview decoder/filter graph and cancel queued scrub work before
starting export. Wait outside GTK for their processes to exit; keep the frame,
playhead, project and export quality unchanged, and recreate playback afterwards.
Validate actual export and cancellation/replay, plus combined FFmpeg peak RSS
and identical MP4 output on a generated sample. See [measurements](EXPORT-MEMORY.md).
Bounded segment rendering for larger compositions remains future work.

### Shared display timing — 0.9.4

Promote the newest pending preview picture and timeline timestamp in one GTK
frame-clock update, including prepared previews. Repaint only old/new playhead
strips and skip drawing offscreen thumbnail tiles and waveform samples. Retain
interaction geometry, drag repaint, pause/generation guards and export quality.
Verify matching preview/timeline paint frame counters and pixel/hit equivalence
of timeline damage painting. See [validation](LIVE-PREVIEW.md).


### Effects and reusable seeking — 0.10.0

C15/C16 now include the nine illustrated choices from the supplied screenshots,
direction/colour controls and compact native join buttons. C17 includes six
entry/exit effects plus three text/caption word styles, and C52 includes four
original attached sound cues and local-file selection. Matching editable layers
use explicit keys. Custom motion paths, coordinated/group animation and a stock
music catalogue remain separate work. Format 7 retains effects and own-file
sounds through recovery, relinking and portable collection.

Dragging/paused seeking reuses one native decoder and two source frames for
ordinary scenes, preserving full resolution. Transitions and overlay videos
keep the composed renderer. See [effects](EFFECTS.md) and
[seek measurements](SEEK-PERFORMANCE.md). The prior timeline paint benchmark
only measured drawing; it did not establish interactive seek performance.
