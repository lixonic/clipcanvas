# Canva video feature inventory for ClipCanvas

Research checked against official Canva pages on **10 October 2026**. This is a decision list of 60 useful capabilities, not an assertion that all appear in every account. Canva's native editor, premium tools, and marketplace apps have different entitlements. All C01–C60 and X01–X10 were selected for staged implementation. C44–C49 and AI/API/MCP connections were subsequently deferred; they are outside the current milestone.

Access labels: **Basic** = available as an ordinary/free editing capability; **Pro** = Canva explicitly identifies a paid capability (other eligible plans may include it); **Varies** = assets, AI allowance, account, region, or edition affect access, or the source does not establish a precise entitlement. These are Canva access labels, not intended ClipCanvas pricing.


## Scope and implementation status

The full overhaul is selected, with AI creation and provider/MCP integration
(C44–C49) deferred. The ClipCanvas column records implemented behavior in 0.9.4;
**New work** means selected but not implemented. Basic and Partial labels identify
remaining work. Selection is not a promise that these features are all present
in this installer. See [the staged plan](DEVELOPMENT-PLAN.md).

## Canvas and composition

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C01 | Social-video canvas presets | Basic | Start landscape, portrait, or square projects. | Existing presets |
| C02 | Resize an existing project | Pro | Adapt the composition to another size; current timeline has conversion restrictions. | Basic: change preset |
| C03 | Crop and manually reframe | Basic | Choose which area of a clip fills the composition. | Basic: numeric crop position |
| C04 | Rotate and flip footage | Basic | Correct orientation or mirror the picture. | Existing: 90° steps and both flips |
| C05 | Multiple visual layers | Basic | Stack video, photos, graphics, and text for overlays. | Existing: ordered text, image/logo, and silent video overlays; canvas drag and numeric transforms |
| C06 | Background versus overlay placement | Basic | Make footage fill the canvas or position it above other media. | Basic: base fit/fill, file-free color-card scenes, and positioned image/video overlays |

Sources: [Canvas sizes and premium resizing](https://www.canva.com/features/video-resize/), [free cropping](https://www.canva.com/features/crop-videos/), [rotation and other operations](https://www.canva.com/features/speed-up-video/), [mirroring](https://www.canva.com/features/mirror-video/), [main and upper timeline tracks](https://www.canva.com/help/creating-and-editing-videos/).

## Clip assembly and timeline

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C07 | Import clips, photos, and GIFs | Basic | Bring personal media into a video. | Videos, still-image scenes with independent duration/slow zoom, and image overlays; animated GIF support pending |
| C08 | Arrange clips in sequence | Basic | Drag clips into story order; the main track closes gaps. | Existing: lifted drag thumbnail, insertion gap/marker and edge scrolling |
| C09 | Trim the beginning and end | Basic | Keep the required portion of a clip. | Existing: drag edges and numeric trim |
| C10 | Split and remove sections | Basic | Cut a clip at a chosen moment and edit the pieces. | Existing: split selected clip at playhead |
| C11 | Duplicate and delete timeline items | Basic | Reuse a segment or remove unwanted material. | Existing: group selection, Ctrl+C/X/V clipboard and Delete, one-step undo |
| C12 | Scrubbing and timeline zoom | Basic | Navigate and adjust timing at a finer scale. | Pointer-time seeking from video/audio clip body clicks (0.11.5), forward decoder reuse and bounded paused scene preparation (0.11.7), aligned source seeking, reusable Fast/HD source proxies, per-frame playback line, Ctrl+wheel timeline zoom and independent preview zoom/pan; optional prepared seek |
| C13 | Constant playback speed | Basic | Slow or speed a clip; documented native range is 0.25×–2×. | Existing: 0.25×–4×, pitch-preserving audio |
| C14 | In-editor and full-screen preview | Basic | Review the assembled video during editing. | Live composition with coordinated audio/video buffering, Full/HD/Fast resolution and Fit/25–400% zoom/pan; optional prepared fallback; full-screen pending |

Sources: [Import requirements](https://www.canva.com/help/upload-formats-requirements-variantb/), [free trimming and joining](https://www.canva.com/features/video-trimmer/), [free splitting](https://www.canva.com/features/video-splitter/), [timeline operations](https://www.canva.com/help/creating-and-editing-videos/), [playback controls](https://www.canva.com/help/adjust-video-playback/).

## Transitions and motion

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C15 | Clip transitions | Basic / premium options vary | Apply dissolves, slides, wipes, and other scene changes. | Implemented: nine illustrated choices from the supplied screenshots; compact join controls; Match & Move pairs editable layers |
| C16 | Transition duration and direction | Basic | Adjust how the scene change moves and how long it lasts. | Implemented: duration slider/spin, actual overlap feedback, direction and colour where applicable |
| C17 | Text, element, and scene animation presets | Basic / premium options vary | Add ready-made motion to titles and visuals. | Implemented subset: Rise/Pan/Fade/Pop/Wipe/Blur on entry/exit; Reveal/Highlight/Snake for text/captions; slow zoom in/out across photo scenes |
| C18 | Timed overlay appearance and disappearance | Basic | Set an item's start and end independently of the scene. | Independent start/end; narrow-layer edge stretching with drag preview; Fit to full video for text/image/video watermarks (0.11.4) |
| C19 | Custom motion paths | Basic | Record a visual element's route by dragging it. | New work |
| C20 | Magic Animate | Pro | Automatically recommend/apply coordinated motion across a design. | New work |

Sources: [Transitions](https://www.canva.com/features/video-transitions/), [element timing](https://www.canva.com/en_au/help/element-timing-video/), [motion effects and paths](https://www.canva.com/features/video-effects/), [Magic Animate](https://www.canva.com/pro/animator/). Not all animation presets expose the same speed or duration controls.

## Titles and captions

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C21 | Text titles and labels | Basic / premium fonts vary | Add and format positioned text over video. | Existing: editable Unicode titles, installed fonts, size, bold/italic, alignment, color and four shared presets |
| C22 | Text effects | Basic / effect or asset varies | Add outlines, shadows, backgrounds, and other styles. | Partial: Clean/Outline/Shadow/Highlight presets, editable outlines, shadows and rounded line backgrounds; other effects pending |
| C23 | Automatic speech captions | Varies | Generate captions from spoken video or audio. | Local Tiny/Small/Turbo speech recognition and a dedicated Malayalam model; saved Malayalam settings upgrade automatically with language/script validation (0.13.1); visible Malayalam/English/Hindi/Tamil selection and automatic detection (0.12.0); first-use download |
| C24 | Edit caption wording and timing | Varies | Correct recognition and move caption segments. | Existing: editable words and cue timing; manual cues, SRT import/export, undo/redo |
| C25 | Styled and animated caption packs | Varies | Apply a consistent subtitle treatment, including motion. | Four shared editable static presets; bundled Noto Sans Malayalam, Manjari and Anek Malayalam with offline shaping (0.12.0); animated packs pending |
| C26 | Caption translation | Varies | Generate captions in another supported language. | New work |

Sources: [Text and overlays](https://www.canva.com/video-editor/ai/), [text effects](https://www.canva.com/features/free-text-effects/), [caption generation, editing, styles, and translation](https://www.canva.com/help/generate-edit-captions-on-videos/). Caption entitlement was not unambiguously recoverable from the current Free/Pro comparison; do not casually call all captions Pro-only. The help page documents more than 20 style packs, with locale restrictions, and a dedicated timeline layer. Burned-in/editable captions are different from Canva's accessibility/view-only captions. Captions are included in MP4 exports; no verified claim here of SRT export.

## Audio editing and mixing

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C27 | Import personal soundtracks, including WAV | Basic | Use WAV, MP3, M4A, OGG, or WebM audio. | Existing; WAV verified |
| C28 | Layer music, speech, and sound effects | Basic | Position multiple audio clips in separate tracks. | Existing: sequential clips can share a lane; simultaneous sounds use separate lanes |
| C29 | Visible audio waveforms | Basic | See timing and sound activity while aligning clips. | Source-sample peaks, silence and stereo transients; full-source timing with trim/speed/zoom alignment (0.11.6) |
| C30 | Trim audio and choose the source section | Basic | Select the part of a recording or song to play. | Existing |
| C31 | Split and rearrange audio | Basic | Separate a recording into independently editable parts. | Split at playhead in the same lane; adjoining unchanged pieces keep continuous sound processing (0.11.6); independent placement and vertical drag/drop; sample-based WAV trimming tolerates incorrect byte-rate headers (0.11.7) |
| C32 | Clip/track volume and mute | Basic | Balance individual clips and remove original sound when needed. | Existing: contextual volume; clip mute/unmute |
| C33 | Audio fade in/out | Basic | Smooth the beginnings and endings of audio, including video sound. | Existing: video sound and soundtrack fades; waveform tapers and soundtrack drag handles |
| C34 | Extract audio from video | Basic | Detach sound into a separate timeline track. | Existing: right-click extraction; independent timed/speed-adjusted track; original muted |
| C35 | Record voice-over | Basic / availability varies | Narrate directly in the editor. | New work |
| C36 | Beat markers with manual alignment | Basic | Display detected beats and place cuts to match them. | New work |
| C37 | Automatic Beat Sync | Pro | Retiming clips to the music with one command. | New work |
| C38 | Automatic volume balancing | Pro | Balance varying audio/video levels across the design. | New work |

Sources: [Audio formats](https://www.canva.com/help/upload-formats-requirements-variantb/), [layered audio and waveforms](https://www.canva.com/help/creating-and-editing-videos/), [volume, fades, source selection, Balance all](https://www.canva.com/en_au/help/trim-audio-and-adjust-volume/), [extraction and splitting](https://www.canva.com/features/extract-audio/), [voice-over recording](https://www.canva.com/help/record-voiceover/), [manual versus automatic Beat Sync](https://www.canva.com/features/beat-sync/), [Pro label for automatic balancing](https://www.canva.com/video-editor/). Balance all is verified; arbitrary audio automation curves, EQ, compressor controls, and automatic dialogue ducking were not verified as standard native Canva features.

## Picture and sound enhancement

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C39 | Video filter presets | Basic | Apply a look and adjust its strength. | Implemented locally: five cinematic looks plus neutral/warm/cool/soft/punchy, 0–100% strength, editable controls, reset/bypass and A/B preview (0.14.0) |
| C40 | Basic color and light adjustments | Basic | Tune brightness, contrast, saturation, temperature, tint, highlights, and shadows. | Implemented locally: exposure, contrast, gamma, shadows/highlights, temperature/tint, 0–200% saturation, per-clip/group apply, preview/export (0.14.0) |
| C41 | Blur and vignette effects | Basic / effect varies | Soften the picture or darken its edges. | New work |
| C42 | AI video background removal | Pro | Isolate a subject and replace its background. | New work |
| C43 | Enhance Voice | Pro | Reduce background noise around speech; usage allowance applies. | New work |

Sources: [Video filters](https://www.canva.com/features/video-filters/), [adjustment controls](https://www.canva.com/features/video-enhancer/), [video background removal](https://www.canva.com/features/video-background-remover/), [Enhance Voice eligibility](https://www.canva.com/help/enhance-voice/). Enhance Voice is speech-focused; this is not a claim of general multitrack restoration.

## AI creation and assisted editing

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C44 | Magic Video | Varies; limited free AI / expanded paid access | Assemble supplied clips/photos from a prompt with styling and music. | Deferred: outside current milestone |
| C45 | Highlights | Varies; marketed with Pro | Find promising excerpts in longer footage. | Deferred: outside current milestone |
| C46 | Text-to-video generation | Varies; AI tool and allowance | Generate new clips from a written description. | Deferred: outside current milestone |
| C47 | Image-to-video and AI scene effects | Varies; paid access/credits | Add generated motion or place images into predesigned scenes. | Deferred: outside current milestone |
| C48 | AI text-to-speech voice-over | Varies; AI tool and allowance | Produce narration in selected voices/languages. | Deferred: outside current milestone |
| C49 | AI music and sound-effect generation | Varies; plan, rollout, allowance | Create soundtrack material from a description. | Deferred: outside current milestone |

Sources: [Magic Video, Highlights, and generated video](https://www.canva.com/video-editor/ai/), [current video feature overview](https://www.canva.com/video-editor/), [AI audio generation](https://www.canva.com/help/generate-ai-music-sound-effects-and-voiceovers/), [voice generator](https://www.canva.com/features/ai-voice-generator/). The AI audio help page calls this premium but also says it is available to all with varying limits; label it Varies, not unlimited/free. Marketplace AI apps can have independent fees. Native generation and app-based alternatives should not be treated as one entitlement.

## Templates, assets, branding, and capture

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C50 | Ready-made video templates | Basic + Pro libraries | Start from layouts with established timing and transitions. | New work |
| C51 | Stock footage, photos, graphics, stickers | Basic + Pro assets | Fill visual gaps with a searchable media collection. | New work |
| C52 | Music and sound-effect libraries | Basic + Pro assets; license limits | Find audio without importing your own. | Implemented subset: original local Pop/Crisp pop/Click/Whoosh cues attached to elements; own WAV/MP3; music/stock catalogue pending |
| C53 | Brand Kit and reusable brand styles | Pro for expanded kit | Keep logos, fonts, colors, and templates consistent. | New work |
| C54 | Webcam and screen capture | Basic; screen capture desktop-only | Record source material directly into the Canva workflow. | New work |

Sources: [Customizable video templates](https://www.canva.com/video-editor/templates/slideshow/), [premium assets and tools](https://www.canva.com/pro/), [sound effects](https://www.canva.com/features/sound-effects/), [Brand Kit](https://www.canva.com/pro/brand-kit/), [limited free color palette](https://www.canva.com/help/color-palettes/), [webcam capture](https://www.canva.com/features/online-video-recorder/), [desktop screen recorder](https://www.canva.com/help/screen-recorder/). The latter needs the Mac/Windows desktop app and internet; current maximum is 25 minutes/1 GB. A ClipCanvas asset library needs independently licensed material, not copied Canva assets.

## Export, publishing, and collaboration

| ID | Feature | Canva access | What it does | ClipCanvas 0.10.0 |
|---|---|---|---|---|
| C55 | MP4 download | Basic; content-license conditions | Export a finished video; watermark-free with eligible own/free content. | Existing |
| C56 | Animated GIF download | Basic; duration limits vary | Export a short silent animation. | New work |
| C57 | Alternate export resolutions, including 4K | Pro / eligible plans | Choose sizes beyond Free's default 1080p; see quality caveat below. | Basic: 1080p and 4K presets |
| C58 | Download pages as separate videos | Pro / eligible plans and design type | Export separate files from suitable multipage designs. | New work |
| C59 | Shared editing, comments, and review links | Basic / team controls vary | Collaborate and collect feedback online. | New work |
| C60 | Direct social publishing and scheduled posts | Varies; scheduling is paid/eligible plans | Deliver from Canva to connected social accounts. | New work |

Sources: [Video export types and watermark rules](https://www.canva.com/video-editor/), [resolution, duration, and separate-file limits](https://www.canva.com/help/download-video-as-separate-files/), [collaboration](https://www.canva.com/features/video-effects/), [Content Planner](https://www.canva.com/help/content-planner/). Publishing support is account/platform-dependent; do not promise every video format can be directly scheduled to every network.

## Qualifications that matter to this project

1. **Source quality:** Canva's upload-help page explicitly states that uploaded 4K footage is stored at 1080p, even if later exported at 4K. This supports keeping original media for final ClipCanvas renders; it does not establish that all possible Canva workflows always behave identically. [Exact official source, “Upload video files”](https://www.canva.com/help/upload-formats-requirements-variantb/).
2. **Changing editor:** Canva's help page says the updated timeline is standard for new video projects, while retaining some beta/gradual-rollout wording. It reports Auto-trim temporarily unavailable, limitations converting video and static design types, and preserved older project structures. Therefore this inventory records capabilities, not guaranteed identical UI for every account. [Official editor guidance](https://www.canva.com/help/creating-and-editing-videos/).
3. **Not verified as native Canva features:** general-purpose keyframe curves, speed ramps, optical-flow slow motion, motion tracking, professional masks, LUT workflows, HDR/10-bit mastering controls, arbitrary audio automation, multicamera editing, and lossless MP4 compositing. These can be separate CapCut-inspired ClipCanvas candidates, not falsely attributed Canva parity requirements.
4. **Apps versus built-ins:** reverse playback, voice changing, dubbing, vocal removal, and upscaling appear in Canva's wider ecosystem, but may be marketplace integrations with distinct limits. For example, [Video Reverser](https://www.canva.com/apps/AAGZP_QLmew/video-reverser) identifies Funkersoft as the app provider. [Voice Changer](https://www.canva.com/features/voice-changer/) describes app credits. They are intentionally outside this first 60-item core decision list.
5. **Export quality target:** proposed ClipCanvas policy (our recommendation, not a Canva fact): preserve originals, use temporary lower-resolution previews only for responsiveness, render from originals, expose resolution/quality and follow source frame rate, and clearly distinguish high-quality compression from true lossless output.

## Implementation order

The first increment (0.2.0) covers C04, C09, C10, C13, C31 and practical
parts of X01/X02/X09. Version 0.3.0 makes native desktop controls and a clear
canvas the priority, expands C15/C16/C32/C33 and implements C34 audio extraction.
It also adds separate picture fades from/to black; those are distinct from join
transitions and audio fades. See the [UI study](UI-STUDY.md).

Version 0.4.0 adds timed text, images/logos, silent video overlays, local
speech-generated captions, editable cues and SRT. Version 0.5.0 adds four
shared static text/caption presets, editable shadows/rounded highlights, quick
logo opacity and user-arranged timeline rows with Up/Down arrows. Row order is
independent of timing and compositing. Version 0.6.0 adds voice echo, gain, tone
filters, compression and A/B audition. These are Audacity-inspired additions,
not asserted Canva parity. See [voice effects and validation](VOICE-EFFECTS.md).
Version 0.7.0 adds live composition playback and scrubbing without a preview
video file, plus automatic first-source FPS. Version 0.7.1 makes the white
playhead directly draggable with updates during movement and adds preview
Fit/zoom/pan. See [live preview](LIVE-PREVIEW.md).
Version 0.8.0 adds full-resolution preview by default, explicit quality choices,
lossless masters, file-free color cards and visible drag/drop placement. See
[quality and cards](QUALITY-CARDS.md).
Version 0.10.0 adds illustrated transitions, layer entry/exit motion, word animations, attached sound cues and a reusable native seek decoder. See [effects and validation](EFFECTS.md). More audio tools, custom motion paths, coordinated scene animation and Windows packaging remain pending.
Generative AI and MCP support are deferred. Extracted audio is independent;
linked clip/audio editing and automatic retiming remain separate work.

## Additional desktop and CapCut-inspired candidates

These are proposed ClipCanvas capabilities, not assertions that they are native
Canva features. They are selected; detailed designs and platform validation remain necessary.

| ID | Candidate | Why it matters | Current status |
|---|---|---|---|
| X01 | Direct timeline handles, snapping, ripple edits | Faster precise clip assembly | Basic: edge trim, audio fade handles, snapping, ripple trims and lifted drag/drop feedback; linked edits pending |
| X02 | Responsive preview, proxies, background caching | Avoid a full preview render after each change | Live effects/mix playback with coordinated buffering; persistent HD/Fast video proxies prepared in a worker; cached seeks; latest-position frame worker; optional rendered fallback |
| X03 | Keyframes and easing | Animate position, scale, opacity and audio levels | New work |
| X04 | Reverse, freeze frames and speed ramps | Control pacing within a clip | New work |
| X05 | Dialogue ducking, audio envelopes, meters and EQ | Mix music, voice and effects predictably | Partial: echo, gain, bass/treble, tone filters, compressor and A/B audition; ducking, meters and arbitrary envelopes pending |
| X06 | Chroma key, masks and optional motion tracking | Composite and follow subjects | New work |
| X07 | LUT/color tools and explicit HDR handling | Make picture treatment and color conversion deliberate | New work |
| X08 | Export codec/bitrate choices, queue and optional hardware encoding | Control quality, size and processing time | H.264 quality presets plus lossless FFV1/float-PCM MKV master; queue/hardware encoding pending |
| X09 | Autosave recovery, missing-media relink and collect-media projects | Resume work safely and move it between computers | Existing: 30-second recovery, relink and portable collection |
| X10 | Native Windows installer and Ubuntu release checks | Deliver the same core workflow on both platforms | Ubuntu .deb tested; portable progress reader; Windows runtime/installer pending |

The first foundation increment was implemented in 0.2.0 and native UI/audio tools
expanded in 0.3.0; 0.4/0.5 add layers, captions and editable static presets.
Version 0.7 adds live mixed preview and automatic source FPS. Advanced
snapping/linking and the Windows installer remain pending. HD/Fast source caching
and coordinated audio/video buffering are included in 0.11.1. See the
[development plan](DEVELOPMENT-PLAN.md).

The 0.9 media library is separate from the timeline: import first, click to place.
Unused local assets are saved/collected too. Native folders and applied voice
settings persist across launches; see [workflow validation](EDITOR-WORKFLOW.md).

Version 0.9.1 adds selection volume/mute for video/audio, with target counts and
one-step undo while preserving other item settings. See [workflow](EDITOR-WORKFLOW.md).

Version 0.9.2 adds shared audio lanes, visible vertical dragging, precise audio
edge snapping and placement menus; see [audio lane validation](AUDIO-LANES.md).
