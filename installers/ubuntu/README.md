# Ubuntu installers

`clipcanvas_0.2.0_all.deb` is the current Ubuntu 24.04 prototype. It adds split,
drag-edge trim, snapping, source-frame scrubbing, constant speed, rotation/flips,
recovery snapshots, media relinking and portable project collection. WAV support
continues from 0.1.1, which is retained here as a historical baseline.

Download the current `.deb` and `SHA256SUMS.txt` from this directory. On GitHub,
open the file and use **Download raw file**.

```sh
sha256sum --check SHA256SUMS.txt
sudo apt install ./clipcanvas_0.2.0_all.deb
```

The installer uses Ubuntu packages for GTK, GStreamer and FFmpeg. Installation
may download dependencies; editing and rendering operate on local files.
Open **ClipCanvas** from the applications menu after installation.

18 generated-media, project and native desktop checks passed locally; see
[0.2.0 validation](../../docs/OVERHAUL-VALIDATION.md). The 0.1.1 results remain in
[baseline validation](../../docs/BASELINE-VALIDATION.md).

Build with `/usr/bin/python3 build_deb.py --output-dir dist` from the repository
root. Successful GitHub Actions builds also provide temporary artifacts.
Windows runtime/installer development and AI/MCP integration are not included
in this package. See the [development plan](../../docs/DEVELOPMENT-PLAN.md).

Version 0.2.0 reads older projects. Projects saved in 0.2.0 cannot be opened in
0.1.x; this matters if you choose to reinstall the historical package.
