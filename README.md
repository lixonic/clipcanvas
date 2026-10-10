# ClipCanvas 0.3.0

ClipCanvas is a small native Ubuntu app for making social videos from clips and
soundtracks already on your computer. Choose a canvas, arrange clips, add
transitions, layer audio, and export. Your media stays on your computer; there is
no account, upload, subscription, or watermark.

**Project status:** Ubuntu prototype, version 0.3.0. Windows is an intended
platform; a Windows app and installer have not been implemented yet.

- [Canva video feature inventory and implementation status](docs/CANVA-FEATURES.md)
- [Canva UI study and native editor design](docs/UI-STUDY.md)
- [Overhaul and platform development plan](docs/DEVELOPMENT-PLAN.md)
- [Contributing and build instructions](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

The full feature inventory is in scope for staged development. C44–C49 and
AI/API/MCP connections are deferred. Version 0.3.0 adds a native desktop UI
overhaul, contextual timeline tools, audio extraction, and separate picture/audio
fades. The inventory records what is still pending.

This version focuses on local assembly and mixing. It does not include Canva's stock
library, design templates, collaboration, or publishing services.

## Install and open

Built for Ubuntu 24.04 and compatible Ubuntu systems with GTK 3 and FFmpeg.

[Download the current Ubuntu installer](installers/ubuntu/clipcanvas_0.3.0_all.deb)
and its [SHA-256 checksum](installers/ubuntu/SHA256SUMS.txt). On GitHub, use the
file's **Download raw file** button. Future Ubuntu builds are also available in
the [Actions artifacts](https://github.com/lixonic/clipcanvas/actions) after a
successful workflow run. Those artifacts expire after 14 days.

1. Open the supplied `clipcanvas_0.3.0_all.deb` using your software installer, if
   it supports local Debian packages.
2. Alternatively, open a terminal in the folder containing the installer and run:

   ```sh
   sudo apt install ./clipcanvas_0.3.0_all.deb
   ```

3. Open **ClipCanvas** from the applications menu.

The first installation may need an internet connection to fetch Ubuntu's
supporting packages. Video creation itself works offline. The installer adds a
separate application and does not change VLC.

## Make your first video

1. **Choose a canvas.** Landscape is 16:9 for YouTube, portrait is 9:16 for Reels
   and Shorts, and square is 1:1. The labeled **Canvas** and **Frame rate** menus
   remain available above the preview.
2. **Add local videos.** Use **Add videos** in the media sidebar or the plus
   action after the final timeline clip. Drag clips to reorder them. Search the
   library by filename or filter it to videos/audio. Collapse the sidebar for
   more canvas space; drag the divider above the timeline to adjust its height.
3. **Edit a selected clip.** Click **Edit** for source trim and playback speed,
   or **Picture** for fit/fill, crop position, rotation, flips, and picture fades.
   These controls open in a compact popover, leaving the canvas clear when
   closed. Click **Apply changes** to keep the entered settings. Video and audio
   speed ranges from 0.25× to 4× and preserves audio pitch.
4. **Choose a transition at the join.** Click the round join button between two
   clips, choose an effect and duration, then **Apply changes**. A cut also has a
   join button. Transitions overlap the neighboring clips and shorten the total
   running time. The popover displays the actual overlap, limited to half of
   either adjacent clip. The final clip's plus button adds videos; it is not an
   outgoing transition.
5. **Add soundtracks.** Use **Add audio** or drag local audio files into the app.
   WAV is supported, including 16-bit PCM, 24-bit PCM, and 32-bit floating-point
   audio. Add separate layers for music, voice-over, and effects. Select a layer
   and open **Edit** for its start time, source trim, and speed. Drag its body to
   change when it starts. Soundtracks stop at the video end and do not extend
   the composition.
6. **Mix and fade sound.** Right-click a clip or soundtrack and choose **Volume
   and fades…**, or use the selected item's **Audio** button. Enter volume and
   audio fade durations, then **Apply changes**. Selected soundtrack fade
   handles can also be dragged on the waveform; the taper shows the fade. Fade
   out ends at the audible video boundary when a soundtrack extends beyond it.
   Right-click a video for **Mute / Unmute**. **Picture** fades are separate:
   they fade the image from/to black; audio fades change sound level.
7. **Extract a clip's audio.** Right-click the video and choose **Extract audio**.
   The new track starts at the clip's timeline position and retains source trim,
   speed, volume, audio fades, and existing transition audio envelopes. The
   original clip is muted to avoid doubled sound. The track then edits and moves
   independently; subsequent video edits do not move or retime it. Extraction
   is disabled for clips without an audio stream and while an extracted track for that clip
   remains. Undo restores the original sound and removes the new track.
8. **Trim and split.** Drag either selected item's edge to trim; zoom in for
   short clips. **Snap** aligns edits with clip/audio edges and the playhead.
   Drag the ruler to choose a time, then **Split** (or press **S**) to cut the
   selected video or audio item. Video trims close gaps. Audio beyond the video
   end appears faded and is excluded from export. Splits within audio fades or
   too near transitions require shortening that effect first. Right-click also
   exposes split, duplicate, delete, and clip move actions.
9. **Click Preview.** Preview preparation runs locally and may take time,
   especially for long videos. After an edit, prepare a new preview to hear and
   see the updated composition. Ruler scrubbing can show source frames before
   preparation; it does not simulate transitions or play mixed audio.
10. **Export an MP4.** Choose where to save it with the native file chooser.
    Choose **High quality** for most projects or **Maximum quality** when a
    larger file is acceptable. Keep the app open until export finishes.

Use **Ctrl+Z** to undo an edit and **Ctrl+Shift+Z** to redo it. **Ctrl+S** saves the
project, and **Ctrl+O** opens a saved project. Undo/redo controls also appear above
the story strip. Project actions live in the header's menu.

ClipCanvas uses Ubuntu's GTK theme, system fonts, symbolic icons, and native
file choosers. Light/dark presentation follows the desktop theme; it does not
embed a browser or the full Files application.

## Save projects and keep your originals

Save a project so you can return to the composition later. Projects store edit
decisions and paths to your originals, with relative paths where possible. If
you move a project without its media, use **Project → Relink missing media**.
Replacement files must contain the same media type and be long enough for the
saved trim points.

Use **Project → Save portable copy** to create a new folder containing the
project and copies of every referenced source file. Move that entire folder to
another computer. This can take disk space comparable to your originals; no
media is re-encoded during collection.

Recovery copies are saved every 30 seconds while you have unsaved changes.
After a crash, use **Project → Recover unsaved project**. A normal save or clean
close removes the current recovery copy. Recovery is separate from your saved
project and references originals; it is not a media backup.

This version reads projects from 0.1.x and 0.2.x and fills in the new fade/audio
fields on load. It continues using project format 2. Projects saved by 0.2.x or
0.3.x cannot be opened in 0.1.x; use 0.3.x to retain the new audio and fade behavior.

The exported MP4 is a standalone video that you can share or upload.

## Video quality

Exports are made from the original media, not from the smaller rendered preview.
Use a canvas and frame rate appropriate for your source. Increasing resolution
does not restore detail that is absent in the original, and cropping landscape
video into portrait necessarily removes part of the image.

The MP4 export is re-encoded because the composition contains
scaling, transitions, or mixed audio. It is intended to preserve good visual
quality, but even **Maximum quality** is not mathematically lossless. Social
platforms may compress the finished MP4 again after upload.

## Practical limits of this build

- One sequence of video clips, with additional audio layers.
- Clip transitions and audio mixing require rendering; this is not a real-time
  effects editor.
- Preview and export need temporary disk space. Long or high-resolution projects
  take more time and storage.
- This build does not include text overlays, stickers, automatic captions, or
  animated design templates.
- Keep backups of valuable source media and project files.

## Remove the app

Use Ubuntu's installed-app controls, or run `sudo apt remove clipcanvas`.
Your original media, saved projects, and exported videos are separate files.

## Run or rebuild the source

The source archive is included for inspection and changes. It uses Python's
standard library plus Ubuntu's GTK/GStreamer bindings and FFmpeg.

Install the same dependencies listed by the Debian package, then from the source
folder run:

```sh
/usr/bin/python3 -m clipcanvas
```

To create a new installer and source archive without administrator privileges:

```sh
/usr/bin/python3 build_deb.py --output-dir ./dist
```

Package-building requires `dpkg-deb`. No network access is used during building.
The software is released under the MIT license in `packaging/copyright`.

## Installer targets

| Platform | Status | Format |
|---|---|---|
| Ubuntu 24.04 | Current baseline; native GTK app and build recipe | `.deb` |
| Windows | Planned portability and packaging work; no download yet | Installer format to be selected after runtime validation |

The rendering engine will be retained as we evaluate Windows deployment and
the selected overhaul features. See the development plan for the concrete
Windows prerequisites and release checks.
