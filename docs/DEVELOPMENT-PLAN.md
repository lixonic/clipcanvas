# ClipCanvas overhaul: implementation plan

Updated 10 October 2026. The user selected all C01–C60 and X01–X10, then deferred
C44–C49 and AI/API/MCP integration. Work proceeds in usable, tested increments.
Selected features are not necessarily implemented. Ubuntu 0.2.0 is the first
foundation increment; Windows remains an intended, unvalidated platform.

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

## Remaining staged work

| Increment | Feature groups | Acceptance checks |
|---|---|---|
| Canvas layers and titles | C01–C07 refinements, C17–C22, X03 | Timed text, photos, logos, overlay video and transforms agree in preview/export at 16:9 and 9:16; original templates animate reproducibly |
| Complete local timeline | C08–C16 refinements, X01–X04 | Trim/split/linking, reverse/freeze, speed ramps, preview caching and keyframes keep timing consistent; preview stays responsive on ordinary social projects |
| Captions | C23–C26 | Correctable timed captions, styles, SRT import/export and optional local recognition/translation; model downloads are separate from offline editing |
| Sound and recording | C27–C38, C43, C54, X05 | WAV and other sources, extraction, voice-over/capture, beat markers/sync, balancing, ducking, meters and EQ tested against exported sound |
| Picture finishing | C39–C42, X06–X07 | Color/filter controls, blur/vignette, chroma/masks, optional tracking/background models, LUT handling and deliberate HDR conversion have reproducible output checks |
| Templates and media | C50–C53 | Editable original templates, local asset collections, reusable brand styles and clearly licensed optional stock catalogs |
| Delivery | C55–C58, X08 | MP4/GIF and separate segments, resolution/codec/bitrate settings, queue/cancellation and optional hardware encoding preserve sources and produce compatible files |
| Review and publishing | C59–C60 | Optional authenticated review and social integrations; external accounts/services are separate from the offline editor and cannot be assumed available |
| Desktop releases | X09 refinements, X10 | Project migration/relink/collection and fresh-machine Ubuntu/Windows install, preview audio/video, Unicode paths, cancellation, uninstall and notices pass |
| Deferred AI creation | C44–C49, API providers and MCP | Outside the current milestone. If resumed, use optional provider adapters, model capability discovery, secure key storage and explicit generation actions |

C20's automatic animation recommendations can initially use local presets. AI
background separation, captions and voice cleanup may need optional local models;
no external model/provider integration is included in the 0.2.0 package.

Next implementation target: **C21 timed text and C05/C18 visual layers**, followed
by captions and the remaining soundtrack controls. Detailed designs will keep
simple defaults and put advanced controls behind the main workflow.

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
