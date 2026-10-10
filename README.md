# ClipCanvas 0.1.1

ClipCanvas is a small native Ubuntu app for making social videos from clips and
soundtracks already on your computer. Choose a canvas, arrange clips, add
transitions, layer audio, and export. Your media stays on your computer; there is
no account, upload, subscription, or watermark.

**Project status:** Ubuntu prototype, version 0.1.1. Windows is an intended
platform; a Windows app and installer have not been implemented yet.

- [Canva video feature inventory and selection checklist](docs/CANVA-FEATURES.md)
- [Proposed overhaul and platform development plan](docs/DEVELOPMENT-PLAN.md)
- [Contributing and build instructions](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

The feature inventory is a selection menu, not a claim that ClipCanvas already
offers every listed feature. The first overhaul batch will be chosen before
implementation begins.

This first version focuses on that workflow. It does not include Canva's stock
library, design templates, collaboration, or publishing services.

## Install and open

Built for Ubuntu 24.04 and compatible Ubuntu systems with GTK 3 and FFmpeg.

[Download the current Ubuntu installer](installers/ubuntu/clipcanvas_0.1.1_all.deb)
and its [SHA-256 checksum](installers/ubuntu/SHA256SUMS.txt). On GitHub, use the
file's **Download raw file** button. Future Ubuntu builds are also available in
the [Actions artifacts](https://github.com/lixonic/clipcanvas/actions) after a
successful workflow run. Those artifacts expire after 14 days.

1. Open the supplied `clipcanvas_0.1.1_all.deb` using your software installer, if
   it supports local Debian packages.
2. Alternatively, open a terminal in the folder containing the installer and run:

   ```sh
   sudo apt install ./clipcanvas_0.1.1_all.deb
   ```

3. Open **ClipCanvas** from the applications menu.

The first installation may need an internet connection to fetch Ubuntu's
supporting packages. Video creation itself works offline. The installer adds a
separate application and does not change VLC.

## Make your first video

1. **Choose a canvas.** Landscape is 16:9 for YouTube, portrait is 9:16 for Reels
   and Shorts, and square is 1:1.
2. **Import local clips.** Drag clips in the story strip to put them in the order
   you want them to play, or select a clip and use **Move left** / **Move right**.
3. **Select a clip.** Adjust its trim points. Choose **Fit** to show the entire
   image with borders, or **Fill** to fill the canvas and crop the edges. Adjust
   the crop position when your subject is away from the center. Click **Apply
   changes** to keep the settings entered in the panel on the right.
4. **Choose a transition** for the join after a clip. A transition overlaps two
   clips, so it shortens the total running time compared with a straight cut.
   Click **Apply changes** after choosing the transition and its duration.
5. **Add soundtracks.** WAV files are supported, including 16-bit PCM, 24-bit PCM,
   and 32-bit floating-point audio. Use **Add soundtracks** and choose your WAV
   files; they can also be dragged into the app. Each soundtrack can have its own start time in the video,
   source trim, volume, and fade in/out. Add more than one for music, voice-over,
   or sound effects. Drag an audio layer along the timeline to change when it
   starts, or enter an exact start time and click **Apply changes**. Lower or mute
   the clips' original sound as needed. Soundtracks stop at the end of the video;
   they do not extend the video's duration.
6. **Click Preview.** Preview preparation runs locally and may take time,
   especially for long videos. After an edit, create a new preview to hear and
   see the updated composition.
7. **Export an MP4** and choose where to save it. Choose **High quality** for most
   projects or **Maximum quality** when a larger file is acceptable. Keep the
   app open until the export finishes.

Use **Ctrl+Z** to undo an edit and **Ctrl+Shift+Z** to redo it. **Ctrl+S** saves the
project, and **Ctrl+O** opens a saved project. The undo and redo buttons are also
available above the story strip.

## Save projects and keep your originals

Save a project so you can return to the composition later. A project stores edit
decisions and references to your original files; it does not copy those files
inside the project. Keep your original clips and audio in their existing
locations. If you move a project to another computer, copy its media as well.

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

## Practical limits of this first build

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
