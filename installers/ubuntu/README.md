# Ubuntu installers

`clipcanvas_0.9.0_all.deb` is the current native Ubuntu 24.04 prototype. It adds a
saved media library, group selection/clipboard/Delete, Ctrl+wheel timeline zoom,
remembered folders/audio settings, visible project controls and a bigger Play
button. The playback line follows displayed frames and silent scrubbing seeks
into the active source. Earlier installers remain for historical reference.

Download the `.deb` and `SHA256SUMS.txt`; on GitHub choose **Download raw file**.

```sh
sudo apt install ./clipcanvas_0.9.0_all.deb
```

Save and reopen an already-running ClipCanvas session after upgrading. Installation
may fetch Ubuntu dependencies. Automatic captions initially download their private
runtime/model, then run offline. See [caption setup](../../docs/CAPTIONS.md).

See [workflow validation](../../docs/EDITOR-WORKFLOW.md). Build with
`/usr/bin/python3 build_deb.py --output-dir dist`; GitHub Actions provides temporary
artifacts. Windows and external provider/API/MCP work remain pending.

Version 0.9.0 reads project formats 1–4 and saves format 5, including unused local
assets. Newly saved projects need 0.9 or newer.
