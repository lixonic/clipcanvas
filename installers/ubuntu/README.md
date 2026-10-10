# Ubuntu installers

`clipcanvas_0.5.0_all.deb` is the current Ubuntu 24.04 prototype. It adds four shared editable text/caption
presets, quick logo opacity and Up/Down timeline row arrangement. Text/image/video
layers, local speech captions, WAV mixing and SRT exchange are included. Earlier installers are retained
for historical reference.

Download the current `.deb` and `SHA256SUMS.txt`. On GitHub, open the file and use
**Download raw file**.

```sh
sha256sum --check SHA256SUMS.txt
sudo apt install ./clipcanvas_0.5.0_all.deb
```

Installation may fetch Ubuntu dependencies. The first automatic-caption use
separately downloads a private runtime and Tiny/Small speech model. After setup,
recognition runs offline; audio/video is not uploaded. See [caption setup](../../docs/CAPTIONS.md).
Open **ClipCanvas** from Applications. Save and reopen an already-running session
after upgrading.

57 local checks passed, including real speech generation/correction/export, style/render agreement,
logo alpha and native row arrangement; see [0.5.0 validation](../../docs/TEXT-STYLES-VALIDATION.md).
Build with `/usr/bin/python3 build_deb.py --output-dir dist`. GitHub Actions also
provides temporary build artifacts. Windows and provider/API/MCP work remain pending.

Version 0.5.0 reads projects from 0.1.x–0.4.x and saves project format 3.
Use 0.5.x or newer to retain new effects and custom row order.
