"""Offline FFmpeg media operations. All public functions are worker-safe.

The render writes a temporary MP4 beside its destination and atomically replaces
the destination only after success. It never rewrites source media.
"""

from __future__ import annotations

import array
import copy
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading

from .model import clip_duration, project_duration, timeline, validate_project


class RenderError(RuntimeError):
    pass


class RenderCancelled(RenderError):
    pass


@lru_cache(maxsize=2)
def _tool(name):
    """Resolve a working binary without changing the application's environment."""
    candidates = []
    override = os.environ.get("CLIPCANVAS_" + name.upper())
    if override:
        candidates.append((override, None))
    native = Path("/usr/bin") / name
    if native.is_file():
        candidates.append((str(native), None))
    found = shutil.which(name)
    if found:
        candidates.append((found, None))
    if name == "ffprobe":
        found = shutil.which("ffmpeg.ffprobe")
        if found:
            candidates.append((found, None))
    # The installed FFmpeg content snap also works without launching snap-confine.
    snap_root = Path("/snap/ffmpeg-2404/current")
    snap_binary = snap_root / "usr/bin" / name
    if snap_binary.is_file():
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(snap_root / "usr/lib/x86_64-linux-gnu")
        candidates.append((str(snap_binary), env))
    for binary, env in candidates:
        try:
            check = subprocess.run([binary, "-version"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env, timeout=5)
            if check.returncode == 0:
                return binary, env
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise RenderError("FFmpeg is not available. Install the Ubuntu ‘ffmpeg’ package, then reopen ClipCanvas.")


def _fraction(value):
    try:
        top, bottom = str(value).split("/")
        return float(top) / float(bottom)
    except (ValueError, ZeroDivisionError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


@lru_cache(maxsize=256)
def _probe_cached(path, size, mtime):
    binary, env = _tool("ffprobe")
    try:
        result = subprocess.run([binary, "-v", "error", "-show_format", "-show_streams",
                                 "-of", "json", path], capture_output=True, text=True,
                                env=env, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise RenderError(f"Reading this file took too long: {Path(path).name}") from error
    if result.returncode:
        raise RenderError(f"Cannot read {Path(path).name}. It may be damaged or unsupported.\n{result.stderr[-600:].strip()}")
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        videos = [s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic", 0)]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        durations = []
        for value in [data.get("format", {}).get("duration")] + [s.get("duration") for s in streams]:
            try:
                candidate = float(value)
                if math.isfinite(candidate) and candidate > 0:
                    durations.append(candidate)
            except (ValueError, TypeError):
                pass
        if not durations or not (videos or audios):
            raise ValueError("No playable media duration")
        video = videos[0] if videos else {}
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        rotation = video.get("tags", {}).get("rotate", 0)
        for side in video.get("side_data_list", []):
            if "rotation" in side:
                rotation = side["rotation"]
        if abs(float(rotation)) % 180 > 45:
            width, height = height, width
        sar = _fraction(str(video.get("sample_aspect_ratio", "1:1")).replace(":", "/")) or 1
        return {"duration": max(durations), "width": width, "height": height,
                "has_audio": bool(audios), "has_video": bool(videos),
                "fps": _fraction(video.get("avg_frame_rate", "0/1")) or _fraction(video.get("r_frame_rate", "0/1")),
                "video_index": video.get("index", 0),
                "audio_index": audios[0].get("index", 0) if audios else None,
                "sample_aspect_ratio": sar}
    except (ValueError, TypeError, KeyError) as error:
        raise RenderError(f"Cannot read the duration of {Path(path).name}. Use a video or audio file with a known duration.") from error


def probe(path):
    path = Path(path).expanduser().resolve()
    try:
        stat = path.stat()
    except OSError as error:
        raise RenderError(f"Cannot open media: {path}") from error
    return dict(_probe_cached(str(path), stat.st_size, stat.st_mtime_ns))


def _fmt(number):
    return format(float(number), ".8f").rstrip("0").rstrip(".") or "0"


def _tempo(speed):
    """Pitch-preserving tempo changes, including FFmpeg versions limited to 2x."""
    remaining = float(speed)
    filters = []
    while remaining < .5:
        filters.append("atempo=0.5")
        remaining /= .5
    while remaining > 2:
        filters.append("atempo=2")
        remaining /= 2
    filters.append(f"atempo={_fmt(remaining)}")
    return filters


def _audio_fades(item, audible_length):
    """User fades and the independent transition envelope copied on extraction."""
    filters = []
    for prefix in ("", "transition_"):
        fade_in = min(float(item.get(prefix + "fade_in", 0)), audible_length)
        fade_out = min(float(item.get(prefix + "fade_out", 0)), audible_length)
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={_fmt(fade_in)}")
        if fade_out > 0:
            filters.append(f"afade=t=out:st={_fmt(audible_length-fade_out)}:d={_fmt(fade_out)}")
    return filters


def _stop(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _execute(arguments, seconds, progress, cancel, log_path):
    binary, env = _tool("ffmpeg")
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("Export cancelled.")
    with open(log_path, "wb") as log:
        process = subprocess.Popen([binary] + arguments, stdout=subprocess.PIPE,
                                   stderr=log, stdin=subprocess.DEVNULL, env=env)
        # Windows selectors cannot watch subprocess pipes. A reader thread also
        # leaves the main worker free to cancel when FFmpeg emits no progress.
        messages = queue.SimpleQueue()
        def read_progress():
            try:
                for line in iter(process.stdout.readline, b""):
                    messages.put(line.decode("utf-8", "replace").strip())
            finally:
                messages.put(None)
        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    _stop(process)
                    raise RenderCancelled("Export cancelled.")
                try:
                    line = messages.get(timeout=.15)
                except queue.Empty:
                    continue
                if line is None:
                    break
                if line.startswith(("out_time_us=", "out_time_ms=")):
                    try:
                        elapsed = float(line.split("=", 1)[1]) / 1000000
                    except ValueError:
                        continue
                    if progress:
                        progress(min(0.995, max(0.0, elapsed / max(seconds, 0.001))), "Rendering your video…")
            process.wait()
            if process.returncode:
                log.flush()
                detail = Path(log_path).read_text(errors="replace")[-2500:].strip()
                raise RenderError("The video could not be rendered. Check that the media files are readable and the destination has free space.\n\n" + detail)
        except BaseException:
            _stop(process)
            raise
        finally:
            _stop(process)
            reader.join(timeout=3)
            process.stdout.close()


def _graph(project, preview=False):
    width, height = int(project["width"]), int(project["height"])
    if preview:
        scale = min(1.0, 640 / max(width, height))
        width, height = max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)
    fps = float(project.get("fps", 30))
    args = []
    filters = []
    rows = timeline(project)
    total = project_duration(project)
    clips = project["clips"]
    for index, clip in enumerate(clips):
        duration = clip_duration(clip)
        speed = clip.get("speed", 1)
        metadata = probe(clip["path"])
        if not metadata["has_video"]:
            raise RenderError(f"{Path(clip['path']).name} has no video stream.")
        args += ["-threads", "1", "-ss", _fmt(clip.get("in", 0)), "-t", _fmt(clip["out"] - clip["in"]), "-i", clip["path"]]
        # Correct non-square source pixels before placing them in a square-pixel canvas.
        framing = "scale=trunc(iw*sar/2)*2:ih,setsar=1,"
        rotation = clip.get("rotation", 0)
        if rotation == 90:
            framing += "transpose=clock,"
        elif rotation == 180:
            framing += "hflip,vflip,"
        elif rotation == 270:
            framing += "transpose=cclock,"
        if clip.get("flip_x"):
            framing += "hflip,"
        if clip.get("flip_y"):
            framing += "vflip,"
        if clip.get("fit", "fill") == "fit":
            framing += f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        else:
            x, y = _fmt(clip.get("position_x", 0.5)), _fmt(clip.get("position_y", 0.5))
            framing += f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={width}:{height}:(iw-ow)*{x}:(ih-oh)*{y}"
        picture = [f"[{index}:{metadata['video_index']}]{framing}", "setsar=1", "format=yuv420p",
                   f"setpts=(PTS-STARTPTS)/{_fmt(speed)}", f"tpad=stop_mode=clone:stop_duration={_fmt(duration)}",
                   f"trim=duration={_fmt(duration)}", f"fps={_fmt(fps)}", "settb=AVTB"]
        picture_fade_in = min(float(clip.get("video_fade_in", 0)), duration)
        picture_fade_out = min(float(clip.get("video_fade_out", 0)), duration)
        if picture_fade_in > 0:
            picture.append(f"fade=t=in:st=0:d={_fmt(picture_fade_in)}")
        if picture_fade_out > 0:
            picture.append(f"fade=t=out:st={_fmt(duration-picture_fade_out)}:d={_fmt(picture_fade_out)}")
        filters.append(",".join(picture) + f"[v{index}]")
        if metadata["has_audio"] and clip.get("volume", 1) > 0:
            parts = [f"[{index}:{metadata['audio_index']}]aresample=48000",
                     "aformat=sample_fmts=fltp:channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
            parts.extend(_tempo(speed))
            parts.extend(["apad", f"atrim=duration={_fmt(duration)}", f"volume={_fmt(clip.get('volume', 1))}"])
            parts.extend(_audio_fades(clip, duration))
            filters.append(",".join(parts) + f"[a{index}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={_fmt(duration)},asetpts=PTS-STARTPTS[a{index}]")
    video, audio = "v0", "a0"
    running_duration = rows[0]["duration"]
    for index in range(1, len(clips)):
        overlap = rows[index - 1]["overlap"]
        if overlap:
            transition = clips[index - 1].get("transition", "fade")
            filters.append(f"[{video}][v{index}]xfade=transition={transition}:duration={_fmt(overlap)}:offset={_fmt(running_duration - overlap)},fps={_fmt(fps)},settb=AVTB[joinv{index}]")
            filters.append(f"[{audio}][a{index}]acrossfade=d={_fmt(overlap)}:c1=tri:c2=tri[joina{index}]")
        else:
            filters.append(f"[{video}][v{index}]concat=n=2:v=1:a=0,fps={_fmt(fps)},settb=AVTB[joinv{index}]")
            filters.append(f"[{audio}][a{index}]concat=n=2:v=0:a=1[joina{index}]")
        video, audio = f"joinv{index}", f"joina{index}"
        running_duration += rows[index]["duration"] - overlap
    mix_labels = [audio]
    for offset, track in enumerate(project.get("audio", [])):
        index = len(clips) + offset
        length = clip_duration(track)
        start = float(track.get("start", 0))
        metadata = probe(track["path"])
        if not metadata["has_audio"]:
            raise RenderError(f"{Path(track['path']).name} has no audio stream.")
        args += ["-threads", "1", "-ss", _fmt(track.get("in", 0)), "-t", _fmt(track["out"] - track.get("in", 0)), "-i", track["path"]]
        parts = [f"[{index}:{metadata['audio_index']}]aresample=48000",
                 "aformat=sample_fmts=fltp:channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
        parts.extend(_tempo(track.get("speed", 1)))
        parts.extend(["apad", f"atrim=duration={_fmt(length)}", f"volume={_fmt(track.get('volume', 1))}"])
        audible_length = max(0.0, min(length, total - start))
        parts.extend(_audio_fades(track, audible_length))
        # Give the delayed sample sequence an explicit zero origin. With tempo
        # conversion/padding, inherited timestamps can otherwise cause amix to
        # discard the leading silence and start this independent track early.
        parts.extend([f"adelay={round(start*1000)}:all=1", "asetpts=N/SR/TB",
                      f"apad=whole_dur={_fmt(total)}", f"atrim=duration={_fmt(total)}"])
        filters.append(",".join(parts) + f"[music{offset}]")
        mix_labels.append(f"music{offset}")
    filters.append("".join(f"[{label}]" for label in mix_labels) + f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=0:latency=1,apad,atrim=duration={_fmt(total)}[outaudio]")
    filters.append(f"[{video}]format=yuv420p[outvideo]")
    return args, ";\n".join(filters), total


def render(project, output, preview=False, progress=None, cancel=None, quality="high"):
    """Render synchronously; progress receives (fraction_0_to_1, message).

    Call from a background worker. Preview uses a 640-pixel canvas and a fast
    encoder. Quality choices: high (CRF 18), smaller (23), master (14).
    Every mode re-encodes video; none promises bit-for-bit preservation.
    """
    project = copy.deepcopy(project)
    try:
        validate_project(project, check_files=True, require_clips=True)
    except ValueError as error:
        raise RenderError(str(error)) from error
    if quality not in ("high", "smaller", "master"):
        raise RenderError("Choose high, smaller, or master export quality.")
    output = Path(output).expanduser().resolve()
    for item in project["clips"] + project["audio"]:
        source = Path(item["path"]).resolve()
        if source == output or (output.exists() and os.path.samefile(source, output)):
            raise RenderError("Choose a different export filename. Your original media will never be overwritten.")
    if project.get("_project_path") and output == Path(project["_project_path"]).resolve():
        raise RenderError("Choose an export filename different from the project file.")
    if output.is_dir():
        raise RenderError("Choose a video filename, not a folder.")
    if not output.parent.is_dir():
        raise RenderError("The export folder does not exist. Choose an existing folder.")
    if cancel is not None and cancel.is_set():
        raise RenderCancelled("Export cancelled.")
    if progress:
        progress(0.0, "Preparing your video…")
    input_args, graph, seconds = _graph(project, preview)
    handle, temporary = tempfile.mkstemp(prefix=".clipcanvas-", suffix=".mp4", dir=str(output.parent))
    os.close(handle)
    try:
        with tempfile.TemporaryDirectory(prefix="clipcanvas-render-") as scratch:
            graph_path = Path(scratch) / "filters.txt"
            graph_path.write_text(graph, encoding="utf-8")
            crf = "28" if preview else {"high": "18", "smaller": "23", "master": "14"}[quality]
            preset = "ultrafast" if preview else "medium"
            arguments = ["-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
                         "-filter_complex_threads", "1"] + input_args + [
                         "-filter_complex_script", str(graph_path), "-map", "[outvideo]", "-map", "[outaudio]",
                         "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
                         "-threads", str(min(4, os.cpu_count() or 2)), "-c:a", "aac", "-b:a", "192k" if preview else "320k",
                         "-ar", "48000", "-ac", "2", "-t", _fmt(seconds), "-movflags", "+faststart",
                         "-max_muxing_queue_size", "2048", "-progress", "pipe:1", "-stats_period", "0.2", temporary]
            _execute(arguments, seconds, progress, cancel, Path(scratch) / "ffmpeg.log")
        if cancel is not None and cancel.is_set():
            raise RenderCancelled("Export cancelled.")
        if not Path(temporary).stat().st_size:
            raise RenderError("FFmpeg produced an empty export.")
        os.replace(temporary, output)
        if progress:
            progress(1.0, "Preview ready." if preview else "Export complete.")
        return str(output)
    except OSError as error:
        raise RenderError(f"Could not save the video: {error}") from error
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def thumbnail(path, output, at=0, width=320, height=180):
    """Write a thumbnail inside the requested bounds, without padding.

    Its real aspect ratio lets the UI apply the same fit/crop as the renderer.
    """
    binary, env = _tool("ffmpeg")
    meta = probe(path)
    if not meta["has_video"]:
        raise RenderError("This file has no video thumbnail.")
    at = max(0, min(float(at), max(0, meta["duration"] - 0.08)))
    output = Path(output).expanduser().resolve()
    source = Path(path).expanduser().resolve()
    if output == source or (output.exists() and os.path.samefile(source, output)):
        raise RenderError("A thumbnail cannot replace its source video.")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1",
                                 "-ss", _fmt(at), "-i", str(Path(path).resolve()), "-map", f"0:{meta['video_index']}",
                                 "-frames:v", "1", "-vf", f"scale=trunc(iw*sar/2)*2:ih,setsar=1,scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,setsar=1",
                                 "-threads", "1", "-update", "1", str(output)],
                                capture_output=True, text=True, env=env, timeout=30)
        if result.returncode or not output.is_file():
            raise RenderError(f"Could not create the thumbnail: {result.stderr[-600:]}")
        return str(output)
    except subprocess.TimeoutExpired as error:
        raise RenderError("Creating the thumbnail took too long.") from error


def waveform(path, max_points=120):
    """Return normalized RMS bins for a small decorative waveform."""
    meta = probe(path)
    if not meta["has_audio"]:
        return [0.0] * int(max_points)
    max_points = max(1, min(2000, int(max_points)))
    binary, env = _tool("ffmpeg")
    # At 2000 Hz this stays small for ordinary social videos. Bound long files.
    sample_seconds = min(meta["duration"], 1800)
    try:
        result = subprocess.run([binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1", "-i", str(Path(path).resolve()),
                                 "-map", f"0:{meta['audio_index']}", "-t", _fmt(sample_seconds), "-ac", "1", "-ar", "2000", "-f", "f32le", "pipe:1"],
                                capture_output=True, env=env, timeout=45)
    except subprocess.TimeoutExpired as error:
        raise RenderError("Reading this waveform took too long.") from error
    if result.returncode:
        raise RenderError("Could not read this soundtrack's waveform.")
    samples = array.array("f")
    samples.frombytes(result.stdout[:len(result.stdout) // 4 * 4])
    if not samples:
        return [0.0] * max_points
    result = []
    for index in range(max_points):
        start = int(index * len(samples) / max_points)
        end = max(start + 1, int((index + 1) * len(samples) / max_points))
        chunk = samples[start:end]
        result.append(math.sqrt(sum(x*x for x in chunk) / max(1, len(chunk))))
    peak = max(result) or 1.0
    return [value / peak for value in result]
