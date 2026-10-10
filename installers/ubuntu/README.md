# Ubuntu baseline installer

`clipcanvas_0.1.1_all.deb` is the current Ubuntu 24.04 baseline. It includes
explicit WAV soundtrack selection. It is a prototype release, before the
planned feature overhaul.

Download the `.deb` and `SHA256SUMS.txt` from this directory. On GitHub, open the
file and use **Download raw file**.

```sh
sha256sum --check SHA256SUMS.txt
sudo apt install ./clipcanvas_0.1.1_all.deb
```

The installer uses Ubuntu packages for GTK, GStreamer and FFmpeg. Installation
may download dependencies; editing and rendering operate on local files.
Open **ClipCanvas** from the applications menu after installation.

The eight generated-media and desktop tests passed on the development Ubuntu
machine. See [validation details](../../docs/BASELINE-VALIDATION.md).

Subsequent builds can be reproduced with
`/usr/bin/python3 build_deb.py --output-dir dist` from the repository root.
Successful GitHub Actions builds also provide temporary artifacts. There is no
Windows installer in this baseline; its planned work is recorded in the
[development plan](../../docs/DEVELOPMENT-PLAN.md).
