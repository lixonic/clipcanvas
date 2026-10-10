# Ubuntu installers

`clipcanvas_0.3.0_all.deb` is the current Ubuntu 24.04 prototype. It adds the
native editor UI, contextual clip controls, clickable transition joins,
right-click audio extraction, and separate picture/audio fades. Versions 0.1.1
and 0.2.0 are retained as historical installers.

Download the current `.deb` and `SHA256SUMS.txt` from this directory. On GitHub,
open the file and use **Download raw file**.

```sh
sha256sum --check SHA256SUMS.txt
sudo apt install ./clipcanvas_0.3.0_all.deb
```

The installer uses Ubuntu packages for GTK, GStreamer and FFmpeg. Installation
may download dependencies; editing and rendering operate on local files.
Open **ClipCanvas** from the applications menu after installation. Save your
project and reopen the app if upgrading an already-running session.

27 generated-media, project and native desktop checks passed locally; see
[0.3.0 UI validation](../../docs/UI-VALIDATION.md). Older results remain in
[0.2.0 validation](../../docs/OVERHAUL-VALIDATION.md) and
[baseline validation](../../docs/BASELINE-VALIDATION.md).

Build with `/usr/bin/python3 build_deb.py --output-dir dist` from the repository
root. Successful GitHub Actions builds also provide temporary artifacts.
Windows runtime/installer development and AI/MCP integration are not included
in this package. See the [development plan](../../docs/DEVELOPMENT-PLAN.md).

Version 0.3.0 reads 0.1.x and 0.2.x projects using project format 2. Projects
saved by 0.2.x/0.3.x cannot be opened in 0.1.x. Use 0.3.x to retain the new audio
speed and fade behavior.
