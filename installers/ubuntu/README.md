# Ubuntu installers

`clipcanvas_0.8.0_all.deb` is the current Ubuntu 24.04 prototype. It adds Full/HD/Fast
preview quality with Full as default, lossless MKV masters, native color cards
and lifted timeline dragging with insertion gaps and edge scrolling. Existing
live playback, WAV mixing, voice effects, layers and editable speech captions
remain included. Earlier installers are kept for historical reference.

Download the current `.deb` and `SHA256SUMS.txt`. On GitHub, open the file and use
**Download raw file**. Check the current installer's line from the checksum list.

```sh
sudo apt install ./clipcanvas_0.8.0_all.deb
```

Installation may fetch Ubuntu dependencies. Automatic captions initially download
a private runtime and Tiny/Small model; recognition then works offline. See
[caption setup](../../docs/CAPTIONS.md). Save and reopen an already-running
ClipCanvas session after upgrading.

82 local checks cover rendering, WAV, quality/export, cards, 15 native desktop
workflows and real speech recognition. See [validation](../../docs/QUALITY-CARDS.md).
Build with `/usr/bin/python3 build_deb.py --output-dir dist`. GitHub Actions also
provides temporary build artifacts. Windows and provider/API/MCP work remain pending.

Version 0.8.0 reads projects from 0.1.x–0.7.x and saves format 4. Projects saved by
0.8 require 0.8 or newer; earlier versions cannot read file-free card scenes.
