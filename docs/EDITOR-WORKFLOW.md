# Desktop workflow — ClipCanvas 0.11.5

## Import, then place

**Add videos**, **Add images**, **Add audio**, the end-of-timeline plus, and dropping local files
import into **Your media**. They leave the timeline and current preview alone.
Click a library asset to create an independent timeline copy: videos and images
append to the main sequence, and audio starts at the playhead. Image clips start
at five seconds; **Edit → Timing → Duration (s)** sets each copy's display time,
and **Motion** offers slow zoom in/out. Timeline edge dragging also changes the
duration. Clicking again creates another copy; editing/deleting it does
not edit/delete the source asset. Explicit Layers → Add image/overlay actions
create the requested layer and also retain its source in Your media.

The library deduplicates imported files by path/type and is searchable/filterable.
Unused imported assets are saved, recovered and collected in portable copies too.
A missing unused library file does not prevent exporting available timeline media.
Relink missing media updates its library and timeline references.

Use a media row's remove icon or right-click → **Remove from media library** to
remove an import. **Delete** does the same when its library row has keyboard
focus. This keeps the original file and all existing timeline copies, leaves
playback running, and supports **Ctrl+Z** / **Ctrl+Shift+Z**. Removed imports stay
removed when saving and reopening the project. Import the file again to add it
back to the library.

## Shared audio lanes

Several sequential audio clips can share one Audio row. Drag a waveform into a
gap on another lane; **Snap** aligns it to another clip's end. The lifted waveform
follows the pointer and **Drop here** marks the destination. A red overlap marker
means the drop would cover another clip and will be rejected. Simultaneous music,
speech and effects use separate lanes; the bottom **new lane** area creates one.

Right-click → **Move to audio lane** lists destinations. **Keep time** preserves
the current start; **after last clip (time)** appends when that time is occupied.
Splitting keeps both parts in the same lane. Up/Down moves the whole lane;
deleting its first clip leaves the remaining clips in place. Clicking an audio
asset adds it to the selected audio lane when the playhead has enough room,
otherwise to a separate lane. See [audio lane details](AUDIO-LANES.md).

## Zoomed timeline navigation

Selected scope: C12, placing the playhead at the time clicked inside a video or
audio clip. From 0.11.5, an ordinary body click selects the clip and seeks to the
pointer's horizontal timeline position, including when its start is offscreen.
Repeated clicks within the selected clip seek again. The preview follows that
time; source trim and speed do not change the pointer's timeline coordinates.
Drag the playhead or ruler to scrub, the clip body to move it, and its edges to
trim. Shift/Ctrl selection and right-click menus keep their selection behavior.

Selected scope: C08/C09/C12, keeping the part of the timeline being edited in
view. From 0.11.3, timeline clicks, modifier selection, right-click menus,
transition controls, fade handles and drag starts retain the current horizontal
and vertical scroll position, including when an item's beginning is offscreen.
Dragging at the viewport edges still scrolls to reach more of the timeline.
Selecting an item in the sidebar still reveals it; moving a soundtrack into a
lane can reveal that lane without jumping to the soundtrack's beginning.

The native GTK regression uses a 210-second sequence at 100 pixels/second. It
checks actual pointer events, focus changes, popups, quick click/release before
idle callbacks, overlays/captions and a long soundtrack's move/fade controls.
Existing desktop checks cover held-drag edge scrolling and row navigation.

The 0.11.5 regression clicks generated video and audio at several positions,
checks immediate playhead placement and the decoded paused preview time, and
repeats the clicks with a rendered preview. It also checks source trim/speed,
modifier selection, edge handles, unchanged project data and undo history.
The long-timeline regression checks the requested seek time after scrolling.
The local full-suite run completed 157 checks: 154 passed, two optional speech
checks were skipped, and the caption trash-button keyboard-focus check timed
out. The same focus timeout reproduced on unchanged 0.11.4 in a temporary copy;
all timeline checks passed. Python compilation, desktop launcher validation,
the Debian build and package checksums passed too.
For a manual check, click midway through a video and then an audio waveform:
the vertical line should move under each click and the preview should show that
time. Repeat after zooming and scrolling, then check body dragging, edge trim,
playhead scrubbing and an export of the unchanged project.

To check manually, zoom a sequence longer than three minutes and scroll to
around 175 seconds. Click and drag the visible tail of a long clip or waveform,
right-click an unselected clip, and open a transition at a visible join. The
visible time range should stay in place through popup dismissal and returning
focus. Drag near a viewport edge to confirm intentional scrolling, then select
a sidebar layer/caption to confirm navigation can still reveal its position.

## Text watermarks and layer duration

Selected scope: C18/C21, stretching a text watermark across the video. Select
the text layer, then drag either timeline edge to change when it starts or ends.
The handles remain available on short layers at the smallest zoom; the cursor
changes over an edge and the bar stretches while dragging. Drag its middle to
move the whole interval. With Snap enabled, an edge can align exactly to the
video's end. Edge scrolling remains available when the end is outside the view.

For a watermark covering the complete video, select it and click **Fit to full
video** in Layers. The same command is in **Edit → Timing** and the layer's
right-click menu. It sets the interval from zero to the current video's end;
repeat it after changing the video's length. The opacity slider now also works
for text. Words, styling, placement and effects remain editable. These edits
support Undo/Redo and save/reopen without changing the project format.

The 0.11.4 native check stretches 0.2-, 3- and 4.5-second text layers at six
pixels/second across a 295.7-second sequence, checks both edges and body movement,
then checks fitting through all three controls. It verifies undo, saved timing,
preview time, unchanged style and watermark pixels near both ends of a real
short preview/export. To check manually, add a short text layer above a long
sequence, zoom out, stretch its right edge and watch the bar grow. Undo, use
Fit to full video, then preview and export the beginning and last second to
confirm the watermark stays visible throughout.

## Selection and shortcuts

| Control | Result |
|---|---|
| Click a timeline item | Select that item |
| Shift+click | Add the range from the previous selected item, within that kind |
| Ctrl+click | Toggle an individual item, including across video/audio/layers/captions |
| Shift+right-click video | Select all main-sequence videos/cards and open their menu |
| Shift+right-click audio | Select all audio tracks and open their menu |
| Ctrl+A | Select the complete timeline, including layers and captions |
| Delete with timeline focus | Remove selected timeline items together; the library remains |
| Delete with a media row focused | Remove that library entry; original file and timeline copies remain |
| Ctrl+C / Ctrl+X | Copy / cut selected timeline items |
| Ctrl+V | Paste copies at the playhead |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| Ctrl+wheel over timeline | Zoom time scale around the pointer |
| Ctrl+wheel over canvas | Zoom the preview independently |
| Space | Play / pause |
| Ctrl+S / Ctrl+Shift+S | Save project / Save as |
| Ctrl+O | Open project |

Copy/paste preserves media, trim, effects, opacity and text. Pasted items receive
new IDs and independent settings. Audio/layers/captions retain their relative
start offsets from the earliest copied item. Main-sequence clips insert at the
next scene boundary at/after the playhead; the main sequence closes gaps.
Groups delete/cut/paste in one undo step. Group volume is available too:
**Shift+right-click → Mute selected videos** sets all selected video volumes to 0.
Choose **Volume for N selected items**, enter a percentage, and use **Apply volume
to N items** to set any shared level. In a group Sound panel, only volume changes;
source trims, timing, speed, fades, picture properties and voice effects stay as
saved for each item. Cards, text and image/video overlays have no sound control.
Video-only selection leaves independent soundtracks untouched. Ctrl-selection
can include both video and audio tracks, and Shift+right-click audio selects only
soundtracks. A group change has one undo step and persists in a saved project.
**Unmute selected** restores prior positive levels remembered during the current
app session, or normal volume when there is no remembered level.
Dragging, Duplicate and other property commands still operate on the primary item.
Entries, text editors and numeric fields keep their own copy/paste/select/delete
shortcuts. Context menus also handle Delete and clipboard shortcuts.
Media-row context menus offer Add to timeline and Remove from media library;
Shift+F10 opens that menu from the focused media button.

## Remembered settings

**Captions → Generate captions** starts immediately using Small and automatic
language detection by default. The gear beside it opens **Caption settings**.
Apply saves source, model and language for later generations and app launches;
Cancel leaves them unchanged. Choose **Full mix** there for a separate voice
soundtrack. Caption settings do not edit the project or start a generation.

Hover a sidebar caption to reveal its trash button, or Tab to that button.
It removes only that cue and supports Undo/Redo; other selected items stay.

Native file pickers remember folders only after **Accept**, not Cancel. Media,
project and export folders are remembered separately, with the most recently
accepted folder as fallback. A deleted folder falls back to a valid recent folder
or Home. Settings survive app restarts in the desktop config directory
(`$XDG_CONFIG_HOME/clipcanvas/preferences.json`, normally `~/.config/clipcanvas`).

Applying Voice effects remembers echo, gain, tone and compressor values. A track
without configured effects opens with these values next time; it requires
**Apply effects** to add them. Existing configured tracks show their own settings.
Cancel and audition do not change remembered values. Neutral resets the editor
controls; Apply keeps that choice as the new default.

Applying volume/fades remembers those values too. **Audio → Use last volume and
fade settings** loads them into the controls of another item; **Apply changes**
commits them. New tracks start at normal volume; a remembered setting never
silently changes their sound.

## Save and resume

Labelled **Open project** and **Save project** are in the top bar. The project menu
also has **Save project as**, portable collection, relinking and crash recovery.
`.clipcanvas` files save the library, sequence, audio mix/effects, text, overlays,
captions/styles, canvas and timeline row order. Originals remain separate local
files; collect a portable copy before moving between computers.

Project format 7 migrates formats 1–6 and retains transition options, layer/caption animations and attached sounds. Projects newly saved by 0.10.0 need 0.10.0 or newer.
If upgrading an open app, save your work, close it and reopen ClipCanvas to load
the new installed code.

## Preview timing and validation

The 0.10.5 sidebar-removal regression covers video, audio and image imports,
Undo/Redo, filtered context menus, focused Delete, save/reopen, unchanged source
hashes and timeline settings, and continued playback on the same decoder.

Play/Pause now has a large labelled primary button below the preview. Live
playhead notifications accompany displayed decoded frames in the same GTK
frame-clock update phase. Only old/new timeline line strips redraw during playback;
thumbnail tiles and waveform bars outside that damage are skipped. Pause discards
queued frames. Silent dragging seeks into the current clip at aligned source and
composition frame boundaries, preserving picture fades and transition overlap.
Full uses original footage. HD/Fast prepare and reuse smaller main-sequence video
files for faster decoding and seeking; first-use progress appears by Play. Sound
and export read the originals. A slow preview join pauses the shared picture/audio
clock while buffering, then resumes without discarding soundtrack samples. A
manual pause stays paused after the buffer is ready. See
[preview buffering and cache](PREVIEW-BUFFERING.md).
Export stops the preview decoder and cancels pending scrub frames before the
renderer starts, avoiding a second FFmpeg graph holding memory throughout the
render. The displayed frame and playhead stay in place, and Play starts a new
preview afterwards. See [export memory measurements](EXPORT-MEMORY.md).

99 local automated checks pass, including 22 native GTK workflows and real offline
speech recognition. Added checks cover library-only imports followed by actual
asset clicks, group selection/Delete/undo, clipboard identity/timing/effects,
text-field shortcut isolation, separate wheel zoom, accepted-folder restoration,
cancel/apply persistence, project/library collection/migration, presented-buffer
playhead time, and deep scrubbing through speed, fades, overlays and transitions. The group-volume
regression selects a non-first video, applies 0% via the real popup, checks all
selected video levels and untouched soundtrack/other properties, then exercises
undo/redo, menu mute/unmute, save/reopen and mixed video/audio selection.
The export regression runs a real high-quality MP4 render after pausing the live
preview, verifies the old preview worker has exited first, then checks replay,
pending scrub cancellation and prepared-preview replay without project changes.
The paint-cycle regression compares actual preview/timeline draw frame counters
for both live and prepared frames, checks newest-frame coalescing, and verifies
that query clocks, paused frames and stale generations cannot advance the line.

A local 640×360 moving source scrubbed at 6.5 seconds averaged 219 ms before and
175 ms after aligned seeking (three reads each on this machine). This measures
one frame computation, not guaranteed pointer-to-screen latency. FFmpeg startup,
keyframe spacing, project resolution, layers and CPU affect responsiveness;
hardware decoding and a persistent seek cache are future work.

Manual native QA also confirmed Full 1920×1080 frames, actual library-button
placement, group highlighting, save/reopen and zero difference between the
reported live frame time and the playhead during a one-second Fast playback sample.

![Native project and playback controls](images/editor-workflow-0.9.0.png)

![Selected main-sequence group](images/group-selection-0.9.0.png)

![Volume for a video selection](images/group-volume-0.9.1.png)
