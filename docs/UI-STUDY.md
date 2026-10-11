# ClipCanvas UI study and acceptance criteria

Study date: **10 October 2026**. Baseline inspected: ClipCanvas **0.2.0**.

This document separates four kinds of evidence:

- **Screenshot observation:** visible in the user's supplied images.
- **Official behavior:** confirmed by a linked Canva page, which can change as its editor rolls out.
- **Baseline implementation:** present in the inspected ClipCanvas code, irrespective of how easy it is to find.
- **Proposed design / acceptance criterion:** work for our native desktop application; not a claim that Canva behaves identically or that ClipCanvas has passed the criterion.

The user's priority is a clear Canva-like editing workflow using Ubuntu's normal widgets, icons, and file dialogs. This is a usability overhaul, not a request to embed the Canva website. AI creation, OpenRouter keys, and MCP integration remain deferred.

## 1. What the supplied screenshots establish

### Image 1 — ClipCanvas editor

The media library takes roughly 13% of the window width, the canvas column roughly 41%, and the inspector the remaining 46%. The inspector is wider than the entire preview column. Its transition, trim, volume, speed, and rotation inputs are always visible, regardless of the task being performed. That turns the main workspace into a settings form.

The canvas does not have enough width to be the visual focus, especially for landscape video. The long title entry and many timeline buttons create competing horizontal strips. Text in the inspector is small relative to its available space. The preview status sentence sits beside the main playback controls and distracts from the timing readout.

There is no visible boundary action on the single-clip timeline. That alone cannot prove whether a transition is supported; with one clip there is no adjacent join. The code audit below establishes what is implemented and what is missing.

### Image 2 — Canva editor

The main composition occupies a quiet, uninterrupted canvas area. A navigation rail and media browser live on the left; there is no permanent right inspector. Playback sits directly below the composition. The timeline sits below that, close enough that the eye can follow the playhead into the canvas.

The left browser separates media types with tabs and gives each audio item a recognizable play symbol, name, and duration. It can scroll independently. The timeline uses thumbnail strips for video, separate purple waveform rows for audio, and a separate green caption row. These are observations of this project, not a requirement to show unsupported caption tools in the current app.

### Image 3 — Canva timeline detail

A round action is placed directly at the meeting point between adjacent clips. The tooltip identifies it as “Change transition”. This makes the join itself the place where the user edits a transition, rather than requiring them to discover an outgoing effect in a generic clip form.

The selected audio track shows a triangular taper near its right end. This establishes that the fade is visually represented on the waveform. A still image does not establish how that taper is edited; draggable fade handles are a proposed ClipCanvas interaction unless independently implemented and tested.

### Image 4 — ClipCanvas format menu defect

The format popup has a dark background while several items retain dark text. The highlighted row is more legible than the ordinary rows. The selector value can be difficult to identify. This is a contrast defect, not merely a preference for another color scheme.

The screenshot is consistent with the code's global foreground/background CSS overriding the system widget theme without styling every popup state. A root-cause fix should remove that conflict instead of adding another isolated color patch.

## 2. Official Canva interactions relevant to this overhaul

### Timeline, selection, and preview

Canva documents a bottom timeline with a playhead, ruler, main visual track, overlay tracks, and separate audio tracks. Clips on the main track close gaps. Edge dragging trims; the selected element can be split at the playhead; the canvas previews playback. Its current help includes both rollout language and differences for older projects, so our design should follow the user's screenshots without claiming one universal Canva layout. [Create and edit videos](https://www.canva.com/help/creating-and-editing-videos/).

### Transitions

Canva's documented transition workflow opens a Transitions panel from the timeline and exposes duration and direction. The supplied screenshot provides stronger visual evidence for the specific join-button placement the user wants. Canva describes dissolves, slides, and wipes as distinct transition families; their availability does not imply the current ClipCanvas four-effect subset is complete parity. [Video transitions](https://www.canva.com/features/video-transitions/).

### Extract audio

Canva documents selecting a video and extracting through Audio Tools, and also a right-click route. The extracted sound becomes a separate timeline track for independent editing. This directly supports adding **Extract audio** to our clip context menu. [Extract audio from video](https://www.canva.com/features/extract-audio/).

### Audio levels, trimming, and fades

Canva documents contextual volume controls, fade sliders or text values, and timeline trim handles. It also documents fade in/out directly on video clips with sound, without requiring extraction first. ClipCanvas should make both the detached-track and attached-video workflows accessible. [Edit and trim audio tracks](https://www.canva.com/en_au/help/trim-audio-and-adjust-volume/).

## 3. Honest audit of the 0.2.0 baseline

The inventory in `CANVA-FEATURES.md` is a scoped catalogue, not a completed-feature checklist. A capability needs to be discoverable and visually verified as well as present in the render engine before it satisfies the user's workflow.

| Area | Code inspected in 0.2.0 | What the user is missing |
|---|---|---|
| Right panel | `make_editor()` creates a permanent nested horizontal `Gtk.Paned` inspector. `show_inspector()` builds a long form. | A clean canvas; tools only when needed. |
| File browsing | Import already uses `Gtk.FileChooserDialog`; project open/save also use GTK dialogs. | Consistent native presentation; no imitation file explorer. |
| Icons | Most actions use text or Unicode glyphs. | Normal theme-provided Ubuntu/GNOME icons and clear labels. |
| Widget theme | Global CSS restyles buttons, labels, entries, combo boxes, lists, and scales. | Reliable system-theme colors, menu contrast, focus, and disabled states. |
| Join effects | `TRANSITIONS` contains Cut, Cross dissolve, Wipe left, Slide right, Dissolve pixels. The engine joins with `xfade` and `acrossfade`. | Clickable join controls and obvious transition choices. |
| Join display | The timeline draws an indicator only when a transition is already set. It has no separate transition hit target. | An add/change action at every eligible join. |
| Soundtrack fades | `fade_in` / `fade_out` exist for audio items, numeric inspector inputs, and `afade` rendering. | Contextual Fade controls and a visible taper on the waveform. |
| Attached clip fades | Clip audio is normalized and given volume/speed, but has no individual fade controls in the baseline. | Fade video sound without detaching. |
| Video end fades | A cross-dissolve exists between clips; that is not fade-from/to-black on the project ends. | Explicitly named visual fades if included in this iteration. |
| Extract audio | No clip context menu or detach action exists. C34 is marked “New work”. | Right-click → Extract audio with independent, correctly timed sound. |
| Timeline editing | Trim, split, reorder, audio offsets, snapping, and zoom exist. | A calmer toolbar and task-specific menus without losing those functions. |
| Preview | 0.7.0 streams the composed video/audio; optional rendering is a fallback. | Direct play/pause/seek, with CPU and seek latency described. |

The top-right section currently means **properties of the selected clip or audio item**. Those properties remain useful. Their permanent placement and width are the defect; removing the panel must not remove access to editing.

## 4. Proposed native workspace

The following is our design specification. Dimensions are starting points for visual QA, not Canva measurements.

### Application chrome

- Use the installed GTK theme for window decoration, menus, entries, combo popups, dialogs, focus rings, and disabled states.
- Use system symbolic icons for open, save, undo, redo, play, pause, add, remove, and more actions. Pair unfamiliar actions with text or a tooltip.
- Keep project name, File/Project actions, Preview, and Export in a compact header. The export action is visually primary.
- Avoid hard-coded white text leaking into popups and labels. Brand color belongs in selected states, small accents, and the timeline, not every native widget.
- Use a normal native file chooser for import/open/save/collect. This integrates the desktop's places, mounted drives, and search; it does not mean embedding the full Files/Nautilus application.

### Left navigation and tool area

- Keep a small navigation rail or tab bar for **Media**, **Audio**, and **Edit**. Do not add empty AI, template, or caption tabs as decoration.
- Reserve approximately 280–320 px for the active browser/tool area on ordinary desktop widths. It should not expand to consume surplus space.
- Media and Audio show local assets with thumbnail/icon, readable filename, and duration. Long names ellipsize with full-path tooltips.
- Clicking **Edit**, a transition join, or a Fade command changes the active tool area or opens a small anchored popover. Only one detailed tool section needs to be open at a time.
- Provide a clear way back to media and a way to collapse the sidebar. Editing controls must not remain as an empty right-hand column.

### Canvas and playback

- Remove the persistent right inspector. Give the remaining horizontal space to the composition.
- Center the composition within a quiet background, preserving the selected 16:9, 9:16, or 1:1 shape.
- Put play/pause and current/total time together directly beneath the canvas.
- Put format and frame rate in a clearly labeled Project/Canvas control or a compact toolbar; menu values remain readable in every state.
- Move technical progress and render status into the status area or a progress dialog. Only show it beside playback when it directly explains a wait.
- When no clip is selected, leave the composition visible. Do not replace it with a form or clear it accidentally on asset refresh.

### Timeline

- Keep a dominant thumbnail strip for main clips with waveforms directly below. Keep track labels narrow and aligned.
- Make the timeline height user-adjustable, with a useful minimum that leaves a substantial canvas at 1280×720.
- Retain edge handles, zoom, snapping, and a strong playhead. Selection must remain visible against either light or dark system chrome.
- Put a compact add-media action after the last clip. It is separate from transition controls.
- Reduce always-visible text buttons: undo/redo, split, and zoom can be compact; duplicate/delete/move belong in the clip context menu as well as keyboard shortcuts.

## 5. Interaction contracts

### Right-click a video clip

1. Select the item under the pointer before opening its menu.
2. Offer **Split at playhead**, **Duplicate**, **Extract audio**, **Mute / Unmute**, **Fade…**, and **Delete** when applicable.
3. Disable Extract audio if the source has no audio; explain that condition without a modal error for a predictable unavailable action.
4. Extract into an independent audio track at that clip's actual timeline start. Preserve the source interval, volume, and playback speed. Mute the original clip so the result does not double the sound.
5. The operation must be undoable as one action, and the separate track must survive project save/load.
6. For a speed-adjusted source, preserving timing is mandatory. The baseline soundtrack model does not implement speed; an implementation must add a correct audio speed path or otherwise preserve the audible result, never silently detach at normal speed.

Extraction should retain a reference to local source media where possible. It should not need an upload, destructive rewrite, or an unnecessary intermediate compressed file.

### Click a join between two clips

1. Show a transition action even for a plain cut. A cut is an editable boundary, not an absence of UI.
2. Open choices such as **None**, **Cross dissolve**, **Wipe**, **Slide**, and **Dissolve pixels**. Label only implemented effects.
3. Show duration in seconds. Cap it to valid neighboring clip lengths and display the effective duration.
4. Keep the button on the visible join after zoom, trim, split, reorder, or duration change. Its hit area takes precedence over clip-body dragging while leaving trim handles reachable.
5. Do not offer an outgoing transition after the final clip as though another clip exists. An ending visual fade is a different command.
6. Undo restores both effect and duration. Playback/export must render the selected effect, not merely draw an icon.

### Adjust a fade

Keep three meanings distinct:

| Label | Changes | Where it belongs |
|---|---|---|
| Cross dissolve | Overlaps the pictures of adjacent clips. | Join control. |
| Video fade in / out | Changes picture opacity against black at a clip boundary. | Selected video effect controls, if implemented. |
| Audio fade in / out | Gradually changes sound level; it does not fade the picture. | Selected audio/video Audio or Fade controls. |

For audio, show a taper over the waveform and provide duration controls with explicit units. If direct drag handles are included, they must be distinguishable from trim handles and must not move the source cut by accident. Fade-out should end at the actual audible end of the track in the composition, including tracks that extend beyond the video. Values must remain valid after trim, split, and speed changes.

## 6. Acceptance checklist for the UI iteration

These criteria are **targets**. Mark them passed only against the finished build and recorded visual/functional results.

### Native presentation and layout

- [ ] No permanent inspector is visible to the right of the canvas.
- [ ] Clip and audio properties are reachable from contextual controls.
- [ ] The preview gets the largest contiguous editor area after the sidebar.
- [ ] 16:9 and 9:16 projects remain centered and uncropped by the UI.
- [ ] At 1280×720, primary actions and playback remain visible without clipping.
- [ ] Resizing the window does not let the sidebar consume the canvas.
- [ ] Import/open/save use native desktop file choosers with appropriate filters.
- [ ] WAV, WAVE, and mixed-case WAV files remain selectable.
- [ ] Common actions use theme icons, with fallback text or a tooltip.
- [ ] Format, project menu, and property popups are readable under Ubuntu light and dark themes.
- [ ] Hover, selected, disabled, focused, and popup states have readable text.
- [ ] Keyboard focus remains visible; icon actions expose a meaningful accessible label.

### Timeline discovery and behavior

- [ ] Right-click on a video targets that clip and opens an actual context menu.
- [ ] Right-click on audio opens appropriate audio actions.
- [ ] Every adjacent main-track boundary has an add/change transition control.
- [ ] The transition control remains usable at different zoom levels.
- [ ] Clicking it does not start a trim or reorder drag.
- [ ] A cut and an applied transition are visually distinguishable.
- [ ] The selected join offers effect and duration; actual overlap is explained where constrained.
- [ ] A final-clip add-media action cannot be mistaken for a transition.
- [ ] Existing trim, reorder, split, snapping, undo/redo, and keyboard actions still work.

### Extract audio and fades

- [ ] Extract audio works on a trimmed clip at its correct timeline offset.
- [ ] Extract audio preserves speed-adjusted timing and pitch behavior.
- [ ] Original clip sound is muted on extraction and restored by undo.
- [ ] The extracted audio is independently movable, trimmable, splittable, and audible.
- [ ] Sources without sound cannot trigger misleading extraction.
- [ ] Audio fade in/out controls are easy to find for a selected soundtrack.
- [ ] A waveform visibly reflects nonzero fades.
- [ ] If attached-video fades are delivered, they work without extracting first.
- [ ] If picture fades are delivered, they are labeled separately from audio fades and join dissolves.
- [ ] A track extending beyond the composition fades at the audible composition end.
- [ ] Fade changes survive save/open and match the rendered output.
- [ ] Undo/redo restores extraction, transitions, and fade changes in coherent steps.

### Review evidence

- [ ] Capture a clean landscape editor with the contextual tools closed.
- [ ] Capture a portrait editor with tools open.
- [ ] Capture an ordinary cut, an applied transition, and its chooser.
- [ ] Capture a clip context menu showing Extract audio.
- [ ] Capture audio fade controls and the resulting waveform taper.
- [ ] Capture readable format popups in light and dark themes.
- [ ] Functionally verify a short export with transition, extracted audio, and fades.
- [ ] Record release limitations clearly; do not mark the entire C01–C60 inventory complete.

## 7. Implemented native editor changes in 0.3.0

This section records the implementation delivered from the study. It does not
mark the acceptance checklist above passed or imply full Canva feature parity;
release QA records the completed visual and functional checks separately.

- **Desktop chrome:** native GTK header bar, system fonts and symbolic icons,
  theme-derived colors, and `Gtk.FileChooserNative` import/open/save dialogs.
  Removing global foreground/background overrides addresses the dark dropdown
  defect while retaining native hover, focus, disabled, and popup states.
- **Clean workspace:** no persistent right inspector. The selected item's
  Timing, Picture, Sound, or Transition controls open in a compact contextual
  popover. The local library has filename search/type filtering and a collapse
  action. A vertical divider adjusts the canvas/timeline allocation.
- **Visible timeline actions:** round controls open transitions at every
  adjacent join, including cuts. Effect/duration controls show the effective
  overlap, constrained to half of either neighboring clip. A separate plus
  action after the last clip imports videos.
- **Context menus:** video/audio right-click actions target the selected item
  for edit, volume/fades, split, duplicate, and delete. Videos additionally
  expose movement, mute/unmute, extraction, and an eligible outgoing transition.
- **Audio extraction:** an independent source-referencing soundtrack retains
  source trim, actual timeline start, pitch-preserving speed, volume, fades, and
  existing transition envelopes. Extraction mutes the video and is undoable.
  Clips without an audio stream and repeated extraction while that clip's extracted track
  remains are unavailable. Subsequent clip edits do not move, retime, or update
  the independent audio's copied transition envelopes.
- **Three distinct fade controls:** joins retain cross dissolves; Picture
  controls add fades from/to black; Sound controls add audio fades for both
  video sound and soundtracks. Selected soundtracks show taper/drag handles at
  the audible composition boundary. WAV import and original-source export are
  retained.

Prepared preview still renders the composition before mixed playback. Source
scrubbing does not reproduce fades or transitions. Text/caption tracks and
unimplemented design tools are not added as empty decorative sections.

## 8. Scope after this iteration

Timed text, image/video overlays, caption styling, templates, recording, richer effects, real-time mixed preview, and Windows packaging remain separate work unless expressly implemented and validated in this release. Empty UI sections should not suggest they are available. The existing feature catalogue should continue distinguishing **Existing**, **Basic/Partial**, **New work**, and **Deferred**.

The useful Canva pattern is direct manipulation with contextual controls: media on the left, composition in the center, timing below, and tools attached to the selected item or join. Using native Ubuntu controls is compatible with that pattern. Copying its web colors while retaining a large form does not reproduce the workflow.

0.7.0 removes the FPS selector and follows the first main source automatically.
See [live preview](LIVE-PREVIEW.md) for the current playback behavior.
