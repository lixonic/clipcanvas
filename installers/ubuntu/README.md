# Ubuntu installers

`clipcanvas_0.10.3_all.deb` is the current native Ubuntu 24.04 prototype. It adds a
saved media library, group selection/clipboard/Delete, Ctrl+wheel timeline zoom,
remembered folders/audio settings, visible project controls and a bigger Play
button. Group volume and Mute selected videos/audio are included in 0.9.1.
Version 0.9.2 adds shared audio lanes, vertical drag/drop, placement menus and
splits that remain on the same lane. Version 0.9.3 releases preview decoders
before export to reduce memory use while keeping export quality unchanged.
See [memory use and validation](../../docs/EXPORT-MEMORY.md).
Version 0.9.4 presents the picture and white playhead in the same GTK paint
cycle and reduces timeline drawing work during playback. See [timing validation](../../docs/LIVE-PREVIEW.md).
The playback line follows displayed frames and silent scrubbing seeks
into the active source. Earlier installers remain for historical reference.

Download the `.deb` and `SHA256SUMS.txt`; on GitHub choose **Download raw file**.

```sh
sudo apt install ./clipcanvas_0.10.3_all.deb
```

Save and reopen an already-running ClipCanvas session after upgrading. Installation
may fetch Ubuntu dependencies. Automatic captions initially download their private
runtime/model, then run offline. See [caption setup](../../docs/CAPTIONS.md).

See [workflow validation](../../docs/EDITOR-WORKFLOW.md). Build with
`/usr/bin/python3 build_deb.py --output-dir dist`; GitHub Actions provides temporary
artifacts. Windows and external provider/API/MCP work remain pending.

Version 0.10.0 adds illustrated transitions, layer/caption animations and attached
sound effects. Repeated ordinary seeks reuse a full-resolution native decoder.
See [effects and validation](../../docs/EFFECTS.md).

Version 0.10.1 reduces the Play delay after seeking by decoding video close to
the playhead while keeping voice-effect history. Preview and export quality
settings are unchanged. See [startup measurements](../../docs/PLAYBACK-STARTUP.md).

Version 0.10.0 reads project formats 1–7 and saves format 7. Newly saved projects
need 0.10.0 or newer; media, sound cues and animation settings remain editable.

Version 0.10.2 accelerates Circle Wipe, Colour Wipe, Line Wipe and Flow with native
compositing and reusable lossless geometry masks. Preview resolution and export
quality are retained. See [transition measurements](../../docs/TRANSITION-PERFORMANCE.md).

Version 0.10.3 fixes audio starvation during animated text/caption playback using
native opacity, wipe and blur operations. Audio settings and video quality remain
unchanged. See [animation/audio validation](../../docs/ANIMATION-AUDIO.md).
