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

from .model import adapt_frame_rate, clip_duration, media_items, project_duration, timeline, validate_project
from . import audio_effects


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


def _processed_audio(item, length, audible_length=None, with_effects=True):
    """The same trim/tempo/effect/volume/fade chain for render and audition.

    Echo is bounded by the item's trimmed duration. It does not extend the
    timeline or leak into the following clip. Delay is measured after speed.
    """
    filters=_tempo(item.get('speed',1))
    filters.extend(['apad',f'atrim=duration={_fmt(length)}'])
    if with_effects:filters.extend(audio_effects.filters(item))
    filters.extend([f'atrim=duration={_fmt(length)}',f'volume={_fmt(item.get("volume",1))}'])
    filters.extend(_audio_fades(item,length if audible_length is None else audible_length))
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


def _caption_stream(project, scratch, width, height, total, cancel=None):
    """Make one variable-duration PNG stream for all captions, including gaps.

    Only one decoder is used even for thousands of cues. PNG sprites share the
    caption box and are reused when the visible wording repeats. Overlapping cues
    follow list order, just as they do in the native preview.
    """
    from . import visuals
    import cairo
    cues = [c for c in project.get('captions', []) if c['start'] < total and c['end'] > 0]
    if not cues:
        return None
    item = visuals.caption_item(project, '')
    left, top, sw, sh = visuals.sprite_geometry(project, item, (width, height))
    events = {}
    for index, cue in enumerate(cues):
        for key, entering in (('start', True), ('end', False)):
            boundary = max(0.0, min(total, float(cue[key])))
            events.setdefault(boundary, []).append((index, entering))
    boundaries = sorted({0.0, total} | set(events))
    active = set()
    cached = {}
    sequence = ['ffconcat version 1.0']
    last_path = None
    for begin, end in zip(boundaries, boundaries[1:]):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Export cancelled.')
        if end <= begin:
            continue
        for index, entering in events.get(begin, []):
            if entering:
                active.add(index)
            else:
                active.discard(index)
        text = tuple(cues[index]['text'] for index in sorted(active))
        if text not in cached:
            path = Path(scratch)/f'caption-{len(cached)}.png'
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
            cr = cairo.Context(surface)
            cr.translate(-left, -top)
            for wording in text:
                visuals._paint(cr, project, visuals.caption_item(project, wording), (0, 0, width, height))
            surface.write_to_png(str(path))
            cached[text] = path
        last_path = cached[text]
        # The image demuxer's fine timebase retains sub-frame cue boundaries;
        # the main canvas frame rate decides which frame displays each cue.
        sequence += [f"file '{last_path.as_posix()}'", 'option framerate 1000', f'duration {_fmt(end-begin)}']
    sequence += [f"file '{last_path.as_posix()}'", 'option framerate 1000']
    sequence_path = Path(scratch)/'captions.ffconcat'
    sequence_path.write_text('\n'.join(sequence)+'\n', encoding='utf-8')
    return sequence_path, left, top


def _visual_graph(project, args, filters, video, width, height, fps, total, scratch, cancel=None):
    """Append ordered transparent layers without interpreting user text as code."""
    layers = [item for item in project.get('layers', []) if item['start'] < total and item['end'] > 0]
    if not layers and not project.get('captions'):
        return video
    if scratch is None:
        raise RenderError('Visual rendering needs a temporary working folder.')
    from . import visuals
    next_input = len(project['clips']) + len(project.get('audio', []))
    for number, item in enumerate(layers):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Export cancelled.')
        start, end = max(0.0, float(item['start'])), min(total, float(item['end']))
        length = end-start
        if item['kind'] in ('text', 'image'):
            path = Path(scratch)/f'layer-{number}.png'
            try:
                x, y = visuals.rasterize(project, item, path, (width, height))
            except Exception as error:
                raise RenderError(f"Could not read or draw visual layer {number+1}: {error}") from error
            args += ['-threads', '1', '-loop', '1', '-framerate', _fmt(fps), '-t', _fmt(length), '-i', str(path)]
            filters.append(f'[{next_input}:v]format=rgba,settb=AVTB,setpts=PTS-STARTPTS+{_fmt(start)}/TB[visual{number}]')
            placement = f'x={x}:y={y}'
        else:
            metadata = probe(item['path'])
            if not metadata['has_video']:
                raise RenderError(f"{Path(item['path']).name} has no video to use as an overlay.")
            available = min(metadata['duration']-item.get('in', 0), length*item.get('speed', 1))
            args += ['-threads', '1', '-ss', _fmt(item.get('in', 0)), '-t', _fmt(available), '-i', item['path']]
            bw = max(1, round(width*item['width']));bh = max(1, round(height*item['height']))
            fit = 'decrease' if item.get('fit', 'fit') == 'fit' else 'increase'
            framing = f'scale=trunc(iw*sar/2)*2:ih,setsar=1,format=rgba,scale={bw}:{bh}:force_original_aspect_ratio={fit}'
            if fit == 'decrease':
                framing += f',pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=0x00000000'
            else:
                framing += f',crop={bw}:{bh}:(iw-ow)/2:(ih-oh)/2'
            parts = [f'[{next_input}:{metadata["video_index"]}]{framing}',
                     f'setpts=(PTS-STARTPTS)/{_fmt(item.get("speed", 1))}', f'fps={_fmt(fps)}',
                     f'tpad=stop_mode=clone:stop_duration={_fmt(length)}', f'trim=duration={_fmt(length)}']
            angle = math.radians(item.get('rotation', 0))
            if angle:
                a = _fmt(angle)
                parts.append(f'rotate={a}:ow=rotw({a}):oh=roth({a}):c=0x00000000')
            parts += [f'colorchannelmixer=aa={_fmt(item.get("opacity", 1))}', 'settb=AVTB',
                      f'setpts=PTS-STARTPTS+{_fmt(start)}/TB']
            filters.append(','.join(parts)+f'[visual{number}]')
            placement = f'x={_fmt(width*item["x"])}-overlay_w/2:y={_fmt(height*item["y"])}-overlay_h/2'
        label = f'composite{number}'
        filters.append(f"[{video}][visual{number}]overlay={placement}:eof_action=pass:repeatlast=0:format=auto:enable='gte(t,{_fmt(start)})*lt(t,{_fmt(end)})'[{label}]")
        video = label
        next_input += 1
    try:
        captions = _caption_stream(project, scratch, width, height, total, cancel)
    except RenderCancelled:
        raise
    except Exception as error:
        raise RenderError(f'Could not draw captions: {error}') from error
    if captions:
        sequence, x, y = captions
        args += ['-threads', '1', '-f', 'concat', '-safe', '0', '-i', str(sequence)]
        filters.append(f'[{next_input}:v]format=rgba,settb=AVTB,setpts=PTS-STARTPTS[captionpicture]')
        filters.append(f'[{video}][captionpicture]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0:format=auto[captioned]')
        video = 'captioned'
    return video


def _graph(project, preview=False, scratch=None, audio_only=False, cancel=None, origin=0):
    # For live seeking, omit completed main clips. Keep the original global
    # timestamps for music, captions and overlays, including effect history.
    if origin:
        project = copy.deepcopy(project)
        project['clips'] = [clip for clip, row in zip(project['clips'], timeline(project))
                            if row['start'] >= origin-0.0000001]
    width, height = int(project["width"]), int(project["height"])
    if preview:
        scale = min(1.0, 640 / max(width, height))
        width, height = max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)
    fps = float(project.get("fps", 30))
    args = []
    filters = []
    rows = timeline(project)
    total = project_duration(project) + origin
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
        if not audio_only:
            filters.append(",".join(picture) + f"[v{index}]")
        if metadata["has_audio"] and clip.get("volume", 1) > 0:
            parts = [f"[{index}:{metadata['audio_index']}]aresample=48000",
                     "aformat=sample_fmts=fltp:channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
            parts.extend(_processed_audio(clip,duration))
            filters.append(",".join(parts) + f"[a{index}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={_fmt(duration)},asetpts=PTS-STARTPTS[a{index}]")
    video, audio = "v0", "a0"
    running_duration = rows[0]["duration"]
    for index in range(1, len(clips)):
        overlap = rows[index - 1]["overlap"]
        if overlap:
            transition = clips[index - 1].get("transition", "fade")
            if not audio_only:
                filters.append(f"[{video}][v{index}]xfade=transition={transition}:duration={_fmt(overlap)}:offset={_fmt(running_duration - overlap)},fps={_fmt(fps)},settb=AVTB[joinv{index}]")
            filters.append(f"[{audio}][a{index}]acrossfade=d={_fmt(overlap)}:c1=tri:c2=tri[joina{index}]")
        else:
            if not audio_only:
                filters.append(f"[{video}][v{index}]concat=n=2:v=1:a=0,fps={_fmt(fps)},settb=AVTB[joinv{index}]")
            filters.append(f"[{audio}][a{index}]concat=n=2:v=0:a=1[joina{index}]")
        video, audio = f"joinv{index}", f"joina{index}"
        running_duration += rows[index]["duration"] - overlap
    if origin:
        if not audio_only:
            filters.append(f'[{video}]setpts=PTS+{_fmt(origin)}/TB[globalvideo]')
            video = 'globalvideo'
        filters.append(f'[{audio}]adelay={round(origin*1000)}:all=1,asetpts=N/SR/TB[globalaudio]')
        audio = 'globalaudio'
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
        audible_length = max(0.0, min(length, total - start))
        parts.extend(_processed_audio(track,length,audible_length))
        # Give the delayed sample sequence an explicit zero origin. With tempo
        # conversion/padding, inherited timestamps can otherwise cause amix to
        # discard the leading silence and start this independent track early.
        parts.extend([f"adelay={round(start*1000)}:all=1", "asetpts=N/SR/TB",
                      f"apad=whole_dur={_fmt(total)}", f"atrim=duration={_fmt(total)}"])
        filters.append(",".join(parts) + f"[music{offset}]")
        mix_labels.append(f"music{offset}")
    filters.append("".join(f"[{label}]" for label in mix_labels) + f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=0:latency=1,apad,atrim=duration={_fmt(total)}[outaudio]")
    if not audio_only:
        video = _visual_graph(project, args, filters, video, width, height, fps, total, scratch, cancel)
        filters.append(f"[{video}]format=yuv420p[outvideo]")
    return args, ";\n".join(filters), total


def live_stream(project, start=0, cancel=None):
    """Yield a bounded, unencoded Matroska stream for immediate native playback.

    Raw I420 video and stereo PCM retain the export graph's effects/mix. The
    consumer supplies backpressure, so we never generate a whole preview file.
    Closing the generator or setting cancel stops FFmpeg and removes sprites.
    """
    project = copy.deepcopy(project)
    _source_rate(project)
    try:
        validate_project(project, check_files=True, require_clips=True)
    except ValueError as error:
        raise RenderError(str(error)) from error
    seconds = project_duration(project)
    start = max(0, min(float(start), max(0, seconds-1/project['fps'])))
    active = [row for row in timeline(project) if row['start'] <= start < row['start']+row['duration']]
    origin = active[0]['start'] if active else 0
    binary, env = _tool('ffmpeg')
    with tempfile.TemporaryDirectory(prefix='clipcanvas-live-') as scratch:
        args, graph, total = _graph(project, True, scratch, cancel=cancel, origin=origin)
        if cancel is not None and cancel.is_set():
            return
        graph_path = Path(scratch)/'filters.txt'
        graph_path.write_text(graph, encoding='utf-8')
        arguments = [binary, '-nostdin', '-hide_banner', '-loglevel', 'error',
                     '-filter_complex_threads', '1'] + args + [
                     '-filter_complex_script', str(graph_path), '-ss', _fmt(start),
                     '-map', '[outvideo]', '-map', '[outaudio]',
                     '-c:v', 'rawvideo', '-pix_fmt', 'yuv420p', '-threads', '1',
                     '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2',
                     '-t', _fmt(seconds-start), '-flush_packets', '1', '-f', 'matroska', 'pipe:1']
        with open(Path(scratch)/'ffmpeg.log', 'wb') as log:
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=log,
                                       stdin=subprocess.DEVNULL, env=env)
            # Reading stdout can block before the first frame or on damaged
            # media. A watcher also cancels those cases without waiting for data.
            finished = threading.Event()
            def watch():
                while not finished.wait(.05):
                    if cancel is not None and cancel.is_set():
                        _stop(process)
                        return
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            try:
                while cancel is None or not cancel.is_set():
                    data = process.stdout.read(65536)
                    if not data:
                        break
                    yield data
                if cancel is not None and cancel.is_set():
                    return
                if process.wait():
                    log.flush()
                    detail = (Path(scratch)/'ffmpeg.log').read_text(errors='replace')[-1600:]
                    raise RenderError('Live preview could not play this project.\n' + detail)
            finally:
                finished.set()
                _stop(process)
                process.stdout.close()
                watcher.join(timeout=4)


def render(project, output, preview=False, progress=None, cancel=None, quality="high"):
    """Render synchronously; progress receives (fraction_0_to_1, message).

    Call from a background worker. Preview uses a 640-pixel canvas and a fast
    encoder. Quality choices: high (CRF 18), smaller (23), master (14).
    Every mode re-encodes video; none promises bit-for-bit preservation.
    """
    project = copy.deepcopy(project)
    _source_rate(project)
    try:
        validate_project(project, check_files=True, require_clips=True)
    except ValueError as error:
        raise RenderError(str(error)) from error
    if quality not in ("high", "smaller", "master"):
        raise RenderError("Choose high, smaller, or master export quality.")
    output = Path(output).expanduser().resolve()
    for item in media_items(project):
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
    handle, temporary = tempfile.mkstemp(prefix=".clipcanvas-", suffix=".mp4", dir=str(output.parent))
    os.close(handle)
    try:
        with tempfile.TemporaryDirectory(prefix="clipcanvas-render-") as scratch:
            input_args, graph, seconds = _graph(project, preview, scratch=scratch, cancel=cancel)
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


def _source_rate(project):
    """Resolve older projects too, without blocking the desktop thread."""
    clips = project.get('clips', [])
    if clips and isinstance(clips[0], dict) and clips[0].get('path'):
        clips[0]['source_fps'] = probe(clips[0]['path'])['fps']
    adapt_frame_rate(project)


def render_audio(project, output, source='mix', progress=None, cancel=None):
    """Write composed speech-recognition audio as a local 16 kHz mono WAV.

    ``video`` includes the trimmed/speed-adjusted main clips with original audio
    restored even when a clip was muted after extraction. ``mix`` follows the
    audible project including its independent soundtracks. Visual layers carry
    no audio, and this operation never rasterizes or encodes a picture.
    """
    if source not in ('mix', 'video'):
        raise RenderError('Choose original video speech or the full audio mix.')
    project = copy.deepcopy(project)
    protected_media = media_items(project)
    if source == 'video':
        project['audio'] = []
        for clip in project.get('clips', []):
            clip['volume'] = 1.0
            clip.pop('audio_effects',None)
    try:
        validate_project(project, check_files=True, require_clips=True)
    except ValueError as error:
        raise RenderError(str(error)) from error
    output = Path(output).expanduser().resolve()
    for item in protected_media:
        source_path = Path(item['path']).resolve()
        if source_path == output or (output.exists() and os.path.samefile(source_path, output)):
            raise RenderError('Choose a different audio filename. Your original media will never be overwritten.')
    if project.get('_project_path') and output == Path(project['_project_path']).resolve():
        raise RenderError('Choose an audio filename different from the project file.')
    if not output.parent.is_dir() or output.is_dir():
        raise RenderError('Choose an audio filename in an existing folder.')
    if cancel is not None and cancel.is_set():
        raise RenderCancelled('Caption generation cancelled.')
    if progress:
        progress(0.0, 'Preparing speech audio…')
    handle, temporary = tempfile.mkstemp(prefix='.clipcanvas-', suffix='.wav', dir=str(output.parent))
    os.close(handle)
    try:
        with tempfile.TemporaryDirectory(prefix='clipcanvas-speech-') as scratch:
            inputs, graph, seconds = _graph(project, audio_only=True)
            graph_path = Path(scratch)/'audio-filters.txt'
            graph_path.write_text(graph, encoding='utf-8')
            arguments = ['-nostdin', '-hide_banner', '-loglevel', 'warning', '-y', '-filter_complex_threads', '1'] + inputs + [
                '-filter_complex_script', str(graph_path), '-map', '[outaudio]', '-vn', '-c:a', 'pcm_s16le',
                '-ar', '16000', '-ac', '1', '-t', _fmt(seconds), '-progress', 'pipe:1', '-stats_period', '0.2', temporary]
            _execute(arguments, seconds, progress, cancel, Path(scratch)/'ffmpeg.log')
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Caption generation cancelled.')
        os.replace(temporary, output)
        if progress:
            progress(1.0, 'Speech audio ready.')
        return str(output)
    except OSError as error:
        raise RenderError(f'Could not prepare speech audio: {error}') from error
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def render_track_sample(item, output, with_effects=True, audible_length=None, seconds=12, cancel=None):
    """Audition the first seconds of one edited audio item without its mix.

    Writes a protected, atomic 48 kHz stereo WAV. The final mix limiter is
    reproduced here for an isolated track; other tracks can affect it in a mix.
    """
    from . import model
    item=copy.deepcopy(item)
    project=model.new_project();project['audio']=[item]
    validate_project(project,check_files=True)
    metadata=probe(item['path'])
    if not metadata['has_audio']:raise RenderError('This item has no audio to audition.')
    output=Path(output).expanduser().resolve();source=Path(item['path']).resolve()
    if source==output or (output.exists() and os.path.samefile(source,output)):
        raise RenderError('An audition cannot replace its original audio.')
    if not output.parent.is_dir() or output.is_dir():raise RenderError('Choose an audio filename in an existing folder.')
    length=clip_duration(item)
    audible_length=length if audible_length is None else max(0,min(length,audible_length))
    seconds=min(float(seconds),audible_length)
    if seconds<=0 or not math.isfinite(seconds):raise RenderError('This track has no audible section in the video.')
    handle,temporary=tempfile.mkstemp(prefix='.clipcanvas-',suffix='.wav',dir=output.parent);os.close(handle)
    try:
        with tempfile.TemporaryDirectory(prefix='clipcanvas-audition-') as scratch:
            filters=['aresample=48000','aformat=sample_fmts=fltp:channel_layouts=stereo','asetpts=PTS-STARTPTS']
            filters.extend(_processed_audio(item,length,audible_length,with_effects))
            filters.extend(['alimiter=limit=0.95:level=0:latency=1','apad',f'atrim=duration={_fmt(seconds)}'])
            args=['-nostdin','-hide_banner','-loglevel','warning','-y','-threads','1',
                  '-ss',_fmt(item.get('in',0)),'-t',_fmt(item['out']-item.get('in',0)),'-i',item['path'],
                  '-map',f'0:{metadata["audio_index"]}','-vn','-filter_threads','1','-af',','.join(filters),
                  '-c:a','pcm_s16le','-ar','48000','-ac','2','-t',_fmt(seconds),
                  '-progress','pipe:1',temporary]
            _execute(args,seconds,None,cancel,Path(scratch)/'audition.log')
        if cancel is not None and cancel.is_set():raise RenderCancelled('Audition cancelled.')
        os.replace(temporary,output);return str(output)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass


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
