# Ubuntu installers

`clipcanvas_0.7.0_all.deb` is the current Ubuntu 24.04 prototype. It adds live
composition playback without a prepared preview file and automatic original
FPS from the first main clip. Right-click voice effects with A/B listening,
text/caption presets, logo opacity, Up/Down timeline rows,
local speech captions and WAV mixing are included. Earlier installers are kept
for historical reference.

Download the current `.deb` and `SHA256SUMS.txt`. On GitHub, open the file and use
**Download raw file**.

```sh
sha256sum --check SHA256SUMS.txt
sudo apt install ./clipcanvas_0.7.0_all.deb
```

Installation may fetch Ubuntu dependencies. The first automatic-caption use
separately downloads a private runtime and Tiny/Small speech model. After setup,
recognition runs offline; audio/video is not uploaded. See [caption setup](../../docs/CAPTIONS.md).
Open **ClipCanvas** from Applications. Save and reopen an already-running session
after upgrading.

73 local checks passed, including streamed video/PCM, seek agreement,
fractional-FPS export, native play/pause/replay/cancellation and real speech
captions; see [0.7.0 validation](../../docs/LIVE-PREVIEW.md).
Build with `/usr/bin/python3 build_deb.py --output-dir dist`. GitHub Actions also
provides temporary build artifacts. Windows and provider/API/MCP work remain pending.

Version 0.7.0 reads projects from 0.1.x–0.6.x and saves project format 3.
Use 0.6.x or newer to retain voice effects; older apps may ignore them.
