# Pro-NLE Auto-Proxy Workflow Plan

**Goal:** Make 4K, 60 fps, and high-bitrate camera footage edit, scrub, and play smoothly in ClipCanvas on modest laptops (without buffering stalls or A/V drift), while keeping every edit non-destructive and rendering exports from original full-resolution files.

## Why 4K / 60 fps footage buffers today

Measured on the reference machine (**Intel Core i3-10110U @ 2.10 GHz, 4 threads, Intel UHD Graphics**, Ubuntu 24.04, FFmpeg 6.1.1) using a real 4K60 source (`14723620_3840_2160_60fps.mp4`, 3840×2160 @ 59.94 fps, H.264 High, 95.1 Mbps, 243 MB for 21.35 s):

| Pipeline (5.0 s sample) | Elapsed | Throughput |
|---|---:|---:|
| CPU software decode → 1080p60 (2 threads) | 10.67 s | **0.47× realtime** |
| CPU software decode → 1080p60 (all 4 threads) | 9.06 s | **0.55× realtime** |
| CPU software decode → 640p60 (2 threads) | 9.34 s | **0.54× realtime** |
| Intel VA-API GPU decode + copy back to CPU RAM → 1080p60 / 640p60 | 11.15 s / 7.87 s | **0.45× / 0.64× realtime** |
| Intel VA-API GPU decode only (stays on GPU — VLC-style) | 1.84 s | **2.72× realtime** |

Three factors combine to make raw 4K60 footage stall during live editing:

1. **CPU decode ceiling:** Even with all 4 CPU threads, decoding 95 Mbps 4K60 H.264 runs at half speed (~0.55×). Copying decoded 4K60 frames back from GPU to CPU RAM (`3840 × 2160 × 1.5 × 60 ≈ 746 MB/s`) is equally memory-bound, and open-source `intel-media-va-driver` (without `-non-free`) does not expose VA-API GPU scaling (`scale_vaapi`).
2. **Timeline FPS inflation:** Dropping one 59.94 fps clip first automatically sets the entire project canvas to 59.94 fps (`1920 × 1080 × 1.5 × 60 ≈ 186 MB/s` raw I420 through FFmpeg → Python → GStreamer → `GdkPixbuf` → Cairo).
3. **Late proxy preparation:** ClipCanvas already has `clipcanvas/preview_cache.py`, but today:
   - Preview defaults to **Full resolution (`edge=0`)**, which never builds or uses proxies.
   - Even in **Fast (640)** or **HD (1280)** mode, `PreviewWarmup` only starts building proxies *after* the user presses Play, so the first playback still decodes the raw 4K60 original at ~0.5× while competing with the background proxy encode for CPU.

Professional NLEs (DaVinci Resolve, Premiere Pro, Final Cut Pro, Kdenlive) solve this with an **automatic editing proxy workflow**: heavy media is transcoded into lightweight edit-friendly proxies in the background on import, live preview seamlessly plays the proxies (optionally at a capped preview frame rate), and export always reads the untouched originals.

---

## Core Guarantees (Non-Negotiable)

1. **Originals untouched, exports full quality:** Proxies live strictly inside `~/.cache/clipcanvas/` and are never referenced in `.clip` / `.clipcanvas` project files or portable copies. Final export and audio always read original source files.
2. **100% effect & frame parity:** Because proxies feed directly into ClipCanvas's existing FFmpeg/GStreamer composition pipeline (`lazy_preview.py` + `engine.py`), every transition, color grade, crop, animation, caption, and audio mix renders identically—only decode resolution (and optionally preview FPS) is reduced.
3. **Color-range & timestamp fidelity:** Proxies preserve exact source timestamps, orientation, display aspect ratio, and limited/full color range metadata (matching [docs/COLOR-RANGE.md](COLOR-RANGE.md)).
4. **Zero UI blocking & instant cancellation:** Proxy builds run in a bounded background queue, yield CPU during active playback/export, publish atomically (`os.replace`), and cancel cleanly when closing or switching projects.

---

## Phased Implementation Plan

### Stage 1 — Heavy-Media Detection & Import-Time Background Proxy Queue

**Files:** `clipcanvas/preview_cache.py`, `clipcanvas/app.py`, `clipcanvas/preferences.py`

1. **Define heavy-media classification (`preview_cache.needs_proxy(metadata)`):**
   - Trigger automatically when any of the following hold for a video asset:
     - `max(width, height) > 1920` (4K / 2.7K / vertical 2160×3840), OR
     - `width * height * fps > 1920 * 1080 * 35` (e.g., 1080p60 / 1080p120), OR
     - `bit_rate > 35_000_000` (high-bitrate camera / ProRes / drone footage), OR
     - High-cost codec for CPU decode (e.g., HEVC/H.265, AV1, VP9 at `>= 1080p`).
   - Light clips (standard 720p/1080p @ 24–30 fps H.264) skip automatic proxy generation to avoid unnecessary disk/CPU work.
2. **Replace ad-hoc play-time `PreviewWarmup` with a window-scoped `ProxyManager`:**
   - Starts automatically on **media import** and **project open** for any uncached heavy video assets.
   - **Priority order:** (1) clips on the timeline from the current playhead forward, (2) remaining timeline clips, (3) media-library-only assets.
   - **Resource cooperation:** Automatically pauses/throttles new proxy builds while active export is running, and drops to `nice` / single-worker background priority while live preview is playing so proxy encoding never competes with active playback.
   - **Clean lifecycle:** Cancels and cleans `.building-*` temp directories immediately on project switch, app close, or user pause.
3. **Expand & harden the LRU cache (`preview_cache.py`):**
   - Raise default cache budget from `1 GB` / `64 files` to `4 GB` / `128 files` (configurable in preferences) so multi-clip 4K projects don't thrash evicted proxies.
   - Keep atomic temp-file publish and hard-link leasing (`lease_available`) so in-flight playback never breaks if eviction occurs.

### Stage 2 — Smart Preview Defaults & Optional 30 fps Preview Cap

**Files:** `clipcanvas/app.py`, `clipcanvas/engine.py`, `clipcanvas/lazy_preview.py`, `clipcanvas/live_player.py`, `clipcanvas/preferences.py`

1. **Smart preview resolution mode (`Auto` default):**
   - Add **`Auto (Smart proxy)`** as the default preview quality choice alongside **Full resolution**, **HD preview (1280)**, and **Fast preview (640)**.
   - Behavior in `Auto` mode:
     - Standard 720p/1080p ≤30 fps clips play at full canvas resolution (zero quality loss for typical edits).
     - Heavy 4K / ≥48 fps clips automatically lease cached **HD (1280)** or **Fast (640)** proxies (choosing 640p for 4K60 on ≤4-thread CPUs, 1280p on faster CPUs or 4K24/30) as soon as they are ready, and transparently fall back to the original until the proxy finishes.
   - Users can explicitly override to **Full resolution** at any time (e.g., to inspect fine details or watermark masks).
2. **Optional 30 fps live-preview cap for high-FPS timelines (`preview_fps_cap`):**
   - When a timeline runs at `>= 48 fps` (e.g., 59.94 fps or 60 fps) and live preview is in `Auto` / `HD` / `Fast` mode:
     - Render live playback at **`fps / 2`** (e.g., 29.97 fps for 59.94 fps, 30 fps for 60 fps) — halving FFmpeg decode/filter work, raw-frame pipe bandwidth (`186 MB/s → 93 MB/s`), GStreamer `videoconvert`, and GTK Cairo paints with **exact timeline cadence** (every 2nd frame, zero timing drift).
     - Keep paused frame-stepping, scrubbing, and exports at the project's full native frame rate (`59.94 / 60 fps`).
     - Expose a preference toggle (`Smooth 30 fps preview for 60 fps projects`, default On in Auto/Fast/HD; always Off in Full resolution).
3. **Seamless hot-swap:**
   - When `ProxyManager` finishes building a clip's proxy, notify the editor (without interrupting playback). The next play, seek, scrub, or upcoming `lazy_preview` window automatically leases the newly published proxy.

### Stage 3 — Hardware-Accelerated & Fast Proxy Encoding

**Files:** `clipcanvas/preview_cache.py`, `clipcanvas/engine.py`

1. **Session-cached GPU transcode probe:**
   - Probe once per session whether full VA-API GPU decode + scale + encode (`-hwaccel vaapi -hwaccel_output_format vaapi` → `scale_vaapi` → `h264_vaapi`) succeeds on `/dev/dri/renderD128`.
   - **When supported** (e.g., Intel with `intel-media-va-driver-non-free`, AMD Mesa VA-API): transcode 4K60 → 1280p/640p proxy fully on GPU at ~2–3× realtime with minimal CPU usage.
   - **When GPU VPP scaling is unavailable** (like Ubuntu's default free `intel-media-va-driver`): fall back cleanly to CPU decode + scale + `libx264 -preset ultrafast -crf 20 -bf 0` (testing whether `h264_vaapi` encode after CPU scale beats `libx264 ultrafast` on the reference machine before enabling).
2. **Edit-friendly proxy format (`h264-proxy-v2`):**
   - Target short GOP (`-g ceil(fps)`, 1-second keyframes, `-bf 0` zero B-frames) for instant random seeks and low decoder memory.
   - When a source is `>= 48 fps` and the user uses 30 fps capped previews, verify whether keeping source FPS in the proxy (for clean frame-accurate stepping) vs half-rate proxy is faster; prefer preserving source FPS at 640p/960p if decode is already `>= 4× realtime` at 60 fps!
   - Benchmark 640p60 and 960p60 proxy decode speed on the reference i3-10110U before freezing the preset.
3. **Watermark-patched heavy clips:**
   - Today `engine._graph` disables proxies whenever `watermark.active(clip)` is True because `removelogo`/`delogo` runs in source pixel coordinates.
   - For heavy 4K clips with a static `watermark_region` (`delogo`) or `sparkle_patch`, scale the normalized rectangle / template coordinates into proxy space (or mark clearly that watermarked 4K clips use original resolution until proxy coordinate mapping is verified with parity tests).

### Stage 4 — Pro-NLE UI Visibility & Controls

**Files:** `clipcanvas/app.py`, `clipcanvas/ui_theme.py`

1. **Non-intrusive proxy progress indicator (toolbar / status bar):**
   - While proxies are building, show a compact clickable pill in the header/status bar: e.g., `Optimizing 4K media · 1/3 (42%)` with a popover/menu to **Pause**, **Resume**, or **Prioritize timeline**.
   - Never block editing, trimming, arranging, or playing while proxies build.
2. **Media library badges:**
   - Small subtle corner pill on heavy video thumbnails in **Your media**:
     - Building: progress `%`
     - Ready: `Proxy` (tooltip: `Fast editing proxy ready — exports use the original 4K file`)
3. **Preview clarity:**
   - Preview selector tooltip & label clearly state when a proxy is active (e.g., `Auto · HD proxy` vs `Full resolution`) so users never mistake proxy softness for export quality.
4. **Preferences & cache management (Project / App menu):**
   - Toggle: **Auto-optimize 4K & 60 fps clips on import** (On by default).
   - Toggle: **Cap live preview at 30 fps for 60 fps timelines** (On by default).
   - Action: **Manage proxy cache…** (shows current cache size on disk + **Clear proxy cache** button).

### Stage 5 — Future Follow-Up (Optional): Zero-Copy GPU Direct Playback

Auto-proxies solve 4K60 editing across *all* features (transitions, color grades, crops, captions, overlays) with zero new dependencies and 100% export parity. As a separate future milestone after Stages 1–4 land, ClipCanvas could optionally add a **zero-copy GPU direct playback** path (e.g., GStreamer `vaapih264dec` + OpenGL `GtkGLArea` or `libmpv`) for untouched single-clip spans in Full resolution mode, falling back to the FFmpeg compositor across transitions and effects.

---

## Validation Plan & Acceptance Criteria

### Automated Tests (`tests/test_auto_proxy.py` + existing suites)
- `needs_proxy()` classification across 720p24, 1080p24, 1080p60, 4K25, 4K60, vertical 2160×3840@60, and high-bitrate sources.
- `ProxyManager` import/open queuing, playhead-forward prioritization, pause/resume during export, clean cancellation with zero leftover `.building-*` temp dirs or surviving child processes.
- Cache LRU eviction under size/file limits without breaking an active playback lease.
- Hot-swap: once a proxy finishes building, the next `lazy_preview` window or scrub immediately leases it.
- Full-range color fidelity and frame-timestamp parity between original and proxy clips (extending `tests/test_color_range.py` and `tests/test_preview_playback.py`).
- 30 fps live-preview cap on 59.94/60 fps projects: verifies exact duration, A/V sync (`< 30 ms` drift), and that final export still renders at full 59.94/60 fps from original media.
- Export safety: verifies `.clip` saved files contain zero proxy paths and `engine.render()` reads originals even when proxies are active.

### Reference Machine Benchmarks (`hero-video.clip`: 4K60 + 4K25 + 1080p25 graded)

| Exercise | Baseline (0.25.1 Full, no proxy) | Target (Auto-Proxy Workflow) |
|---|---:|---:|
| Play `hero-video.clip` 0 s → 16.7 s EOS (warm proxy) | Stalls (~0.5× realtime on 4K60) | **0 refills, 0 gaps > 100 ms, ≥ 1.5× decode margin, < 30 ms A/V drift** |
| First picture latency (warm proxy) | ~1.5 s+ | **< 400 ms** |
| Scrubbing across 4K60 clip (warm proxy) | ~500–900 ms | **< 150 ms** |
| Time to auto-proxy a 20 s 4K60 clip in background | Manual / only after Play | **Starts immediately on import; non-blocking UI (< 16 ms GTK frame time)** |
| Final MP4 export | Full 4K/1080p60 original | **Identical bit-for-bit source read from original files** |
