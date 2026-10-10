# Ubuntu installers

`clipcanvas_0.4.0_all.deb` is the current Ubuntu 24.04 prototype. It adds text,
image/logo and silent video layers, plus speech-generated, editable captions,
shared caption styling and SRT import/export. Earlier installers are retained
for historical reference.

Download the current `.deb` and `SHA256SUMS.txt`. On GitHub, open the file and use
**Download raw file**.

```sh
sha256sum --check SHA256SUMS.txt
sudo apt install ./clipcanvas_0.4.0_all.deb
```

Installation may fetch Ubuntu dependencies. The first automatic-caption use
separately downloads a private runtime and Tiny/Small speech model. After setup,
recognition runs offline; audio/video is not uploaded. See [caption setup](../../docs/CAPTIONS.md).
Open **ClipCanvas** from Applications. Save and reopen an already-running session
after upgrading.

48 local checks passed, including real speech generation/correction/export and
native desktop workflows; see [0.4.0 validation](../../docs/LAYERS-CAPTIONS-VALIDATION.md).
Build with `/usr/bin/python3 build_deb.py --output-dir dist`. GitHub Actions also
provides temporary build artifacts. Windows and provider/API/MCP work remain pending.

Version 0.4.0 reads projects from 0.1.x–0.3.x and saves project format 3.
Use 0.4.x or newer to reopen projects containing layers and captions.
