"""Offline FFmpeg media operations. All public functions are worker-safe.

The render writes a temporary video beside its destination and atomically replaces
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

from .model import adapt_frame_rate, clip_duration, media_items, project_duration, timeline, validate_project, is_card, is_image, is_still
from . import audio_effects, transitions


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


def image_picture(path):
    """Decode supported local still formats, respecting camera orientation."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    try:
        return GdkPixbuf.Pixbuf.new_from_file(str(path)).apply_embedded_orientation()
    except Exception as error:
        raise RenderError(f'Cannot read image {Path(path).name}. Choose a supported PNG, JPEG, WebP, BMP, TIFF or SVG image.') from error


def probe_image(path):
    picture = image_picture(path)
    return {'width': picture.get_width(), 'height': picture.get_height(),
            'has_video': False, 'has_audio': False, 'fps': 0}


def _image_source(path, target):
    # Rasterize SVGs and normalize EXIF/alpha once per composition, so every
    # decoder sees the same oriented picture on a black canvas.
    picture = image_picture(path)
    from gi.repository import GdkPixbuf
    if picture.get_has_alpha():
        picture = picture.composite_color_simple(picture.get_width(), picture.get_height(),
            GdkPixbuf.InterpType.NEAREST, 255, max(picture.get_width(), picture.get_height()), 0, 0)
    picture.savev(str(target), 'png', [], [])
    return str(target)


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


def _word_stream(project, item, scratch, width, height, prefix, soften=False, cancel=None):
    """One decoder, one bounded sprite per word state, never a canvas PNG movie."""
    from . import animations, visuals
    import cairo
    length = float(item['end'])-float(item['start'])
    steps = max(1, animations.text_steps(item))
    sequence = ['ffconcat version 1.0']
    plain = dict(item, rotation=0, opacity=1)
    for step in range(steps):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Export cancelled.')
        sprite = dict(plain, _text_effect=animations.normalise(item.get('animation'))['text_effect'], _text_step=step)
        path = Path(scratch)/f'{prefix}-{step}.png'
        visuals.rasterize(project, sprite, path, (width, height))
        if soften:
            visuals.soften(cairo.ImageSurface.create_from_png(str(path))).write_to_png(str(path))
        sequence += [f"file '{path.as_posix()}'", 'option framerate 1000', f'duration {_fmt(length/steps)}']
    sequence += [f"file '{path.as_posix()}'", 'option framerate 1000']
    target = Path(scratch)/f'{prefix}.ffconcat'
    target.write_text('\n'.join(sequence)+'\n', encoding='utf-8')
    return target


def _caption_lanes(project):
    """Keep the decoder count proportional to simultaneous captions, not cues."""
    lanes = []
    for cue in sorted(project.get('captions', []), key=lambda item: (item['start'], item['end'])):
        lane = next((items for items in lanes if items[-1]['end'] <= cue['start']), None)
        if lane is None:
            lane = []
            lanes.append(lane)
        lane.append(cue)
    return lanes


def _cue_expressions(project, variable='t'):
    from . import animations, visuals
    result = {key: base for key, base in (('dx', '0'), ('dy', '0'), ('opacity', '1'), ('scale', '1'), ('wipe', '1'), ('blur', '0'), ('mask', '1'))}
    for cue in reversed(project.get('captions', [])):
        item = visuals.caption_item(project, cue['text'], cue)
        values = animations.expressions(item, variable)
        if values['wipe'] != '1':
            values['mask'] = _wipe_mask(item, values['wipe'])
        for key, expression in values.items():
            if expression != result[key] or expression not in ('0', '1'):
                result[key] = f'if(gte({variable},{_fmt(cue["start"])})*lt({variable},{_fmt(cue["end"])}),{expression},{result[key]})'
    return result


def _element_motion(filters, source, output, item, width, height, values=None, blurred=None, caption=False,
                    fps=30, length=None, scratch=None, cues=None, project=None, cancel=None):
    """Animate a sprite with global-time expressions; preserve source resolution."""
    from . import animations
    values = values or animations.expressions(item)
    placement = animations.placement_expressions(item)
    from . import element_render
    current = element_render.append(filters,source,output+'native',item,width,height,fps,length,scratch,
                                    blurred,cues,project,cancel)
    parts = [f'[{current}]format=rgba']
    motion_scale = values['scale']
    ratio_w, ratio_h = '1', '1'
    if item.get('_match_to'):
        ratio_w = f'({placement["width"]})/{_fmt(item["width"])}'
        ratio_h = f'({placement["height"]})/{_fmt(item["height"])}'
    if motion_scale != '1' or ratio_w != '1' or ratio_h != '1':
        parts.append(f"scale=w='max(2,round(iw*({motion_scale})*({ratio_w})))':h='max(2,round(ih*({motion_scale})*({ratio_h})))':eval=frame")
    if not caption and (float(item.get('rotation', 0)) or item.get('_match_to') and float(item['_match_to'].get('rotation', 0)) != float(item.get('rotation', 0))):
        angle = f'({placement["rotation"]})*PI/180'
        parts.append(f"rotate=a='{angle}':ow='ceil(hypot(iw,ih))':oh='ceil(hypot(iw,ih))':c=0x00000000")
    filters.append(','.join(parts)+f'[{output}]')
    x = f'{_fmt(width)}*(({placement["x"]})+({values["dx"]}))-overlay_w/2'
    y = f'{_fmt(height)}*(({placement["y"]})+({values["dy"]}))-overlay_h/2'
    return f"x='{x}':y='{y}'"


def _expression_time(expression, variable):
    import re
    return re.sub(r'\bt\b', variable, expression)


def _wipe_mask(item, progress):
    from . import animations
    direction = animations.normalise(item.get('animation'))['direction']
    axis, extent = ('X', 'W') if direction in ('left', 'right') else ('Y', 'H')
    return (f'gte({axis},{extent}*(1-({progress})))' if direction in ('left', 'up')
            else f'lt({axis},{extent}*({progress}))')


def _animated_visual(project, item, number, args, filters, width, height, fps, length, scratch, cancel=None):
    from . import animations, visuals
    import cairo
    start = float(item['start'])
    animation = animations.normalise(item.get('animation'))
    blur = animation['enter'] == 'blur' or animation['exit'] == 'blur'
    source = f'motionbase{number}'
    plain = dict(item, rotation=0, opacity=1)
    inputs = []
    if item['kind'] in ('text', 'image'):
        for soft in ((False, True) if blur else (False,)):
            index = args.count('-i')
            prefix = f'layer-{number}'+('-soft' if soft else '')
            if item['kind'] == 'text' and animation['text_effect'] != 'none':
                path = _word_stream(project, plain, scratch, width, height, prefix, soft, cancel)
                args += ['-threads', '1', '-f', 'concat', '-safe', '0', '-i', str(path)]
                preparation = f'fps={_fmt(fps)},trim=duration={_fmt(length)}'
            else:
                path = Path(scratch)/f'{prefix}.png'
                visuals.rasterize(project, plain, path, (width, height))
                if soft:
                    visuals.soften(cairo.ImageSurface.create_from_png(str(path))).write_to_png(str(path))
                args += ['-threads', '1', '-loop', '1', '-framerate', _fmt(fps), '-t', _fmt(length), '-i', str(path)]
                preparation = 'null'
            label = source+('soft' if soft else '')
            filters.append(f'[{index}:v]{preparation},format=rgba,settb=AVTB,setpts=PTS-STARTPTS+{_fmt(start)}/TB[{label}]')
            inputs.append(label)
    else:
        index = args.count('-i')
        metadata = probe(item['path'])
        if not metadata['has_video']:
            raise RenderError(f"{Path(item['path']).name} has no video to use as an overlay.")
        available = min(metadata['duration']-item.get('in', 0), length*item.get('speed', 1))
        args += ['-threads', '1', '-ss', _fmt(item.get('in', 0)), '-t', _fmt(available), '-i', item['path']]
        bw, bh = max(1, round(width*item['width'])), max(1, round(height*item['height']))
        fit = 'decrease' if item.get('fit', 'fit') == 'fit' else 'increase'
        framing = f'scale=trunc(iw*sar/2)*2:ih,setsar=1,format=rgba,scale={bw}:{bh}:force_original_aspect_ratio={fit}'
        framing += (f',pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color=0x00000000' if fit == 'decrease' else f',crop={bw}:{bh}:(iw-ow)/2:(ih-oh)/2')
        filters.append(f'[{index}:{metadata["video_index"]}]{framing},setpts=(PTS-STARTPTS)/{_fmt(item.get("speed",1))},fps={_fmt(fps)},tpad=stop_mode=clone:stop_duration={_fmt(length)},trim=duration={_fmt(length)},settb=AVTB,setpts=PTS-STARTPTS+{_fmt(start)}/TB[{source}]')
        if blur:
            sharp = source+'sharp'
            filters.append(f'[{source}]split=2[{sharp}][{source}blurinput]')
            filters.append(f'[{source}blurinput]gblur=sigma=6:steps=2[{source}soft]')
            inputs = [sharp, source+'soft']
        else:
            inputs = [source]
    placement = _element_motion(filters, inputs[0], f'visual{number}', item, width, height,
                                blurred=inputs[1] if blur else None,fps=fps,length=length,scratch=scratch,
                                project=project,cancel=cancel)
    return placement


def _caption_stream(project, scratch, width, height, total, cancel=None, prefix="captions", animated=False, soft=False):
    """Make one variable-duration PNG stream for all captions, including gaps.

    Only one decoder is used even for thousands of cues. PNG sprites share the
    caption box and are reused when the visible wording repeats. Overlapping cues
    follow list order, just as they do in the native preview.
    """
    from . import visuals, animations
    import cairo
    cues = [c for c in project.get('captions', []) if c['start'] < total and c['end'] > 0]
    if not cues:
        return None
    item = visuals.caption_item(project, '')
    if animated: item.update(rotation=0, opacity=1)
    left, top, sw, sh = visuals.sprite_geometry(project, item, (width, height))
    events = {}
    for index, cue in enumerate(cues):
        for key, entering in (('start', True), ('end', False)):
            boundary = max(0.0, min(total, float(cue[key])))
            events.setdefault(boundary, []).append((index, entering))
        if animations.normalise(cue.get('animation', project.get('caption_style', {}).get('animation')))['text_effect'] != 'none':
            steps = max(1, animations.text_steps(cue))
            for step in range(1, steps):
                boundary = cue['start']+(cue['end']-cue['start'])*step/steps
                if 0 < boundary < total: events.setdefault(boundary, [])
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
        text = tuple((cues[index]['text'], animations.text_step(cues[index], begin+1e-8),
                      str(cues[index].get('animation', project.get('caption_style', {}).get('animation')))) for index in sorted(active))
        if text not in cached:
            path = Path(scratch)/f'{prefix}-{len(cached)}.png'
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
            cr = cairo.Context(surface)
            cr.translate(-left, -top)
            for index in sorted(active):
                cue = cues[index]
                sprite = visuals.caption_item(project, cue['text'], cue)
                sprite['_text_effect'] = animations.normalise(sprite.get('animation'))['text_effect']
                sprite['_text_step'] = animations.text_step(sprite, begin+1e-8)
                if animated: sprite.update(rotation=0, opacity=1)
                visuals._paint(cr, project, sprite, (0, 0, width, height))
            if soft: surface = visuals.soften(surface)
            surface.write_to_png(str(path))
            cached[text] = path
        last_path = cached[text]
        # The image demuxer's fine timebase retains sub-frame cue boundaries;
        # the main canvas frame rate decides which frame displays each cue.
        sequence += [f"file '{last_path.as_posix()}'", 'option framerate 1000', f'duration {_fmt(end-begin)}']
    sequence += [f"file '{last_path.as_posix()}'", 'option framerate 1000']
    sequence_path = Path(scratch)/f'{prefix}.ffconcat'
    sequence_path.write_text('\n'.join(sequence)+'\n', encoding='utf-8')
    return sequence_path, left, top


def _visual_graph(project, args, filters, video, width, height, fps, total, scratch, cancel=None):
    """Append ordered transparent layers without interpreting user text as code."""
    layers = [item for item in project.get('layers', []) if item['start'] < total and item['end'] > 0]
    if not layers and not project.get('captions'):
        return video
    if scratch is None:
        raise RenderError('Visual rendering needs a temporary working folder.')
    from . import visuals, animations
    next_input = args.count('-i')
    for number, item in enumerate(layers):
        if cancel is not None and cancel.is_set():
            raise RenderCancelled('Export cancelled.')
        start, end = max(0.0, float(item['start'])), min(total, float(item['end']))
        length = end-start
        if animations.animated(item) or item.get('_match_from'):
            try:
                placement = _animated_visual(project, item, number, args, filters, width, height, fps, length, scratch, cancel)
            except RenderCancelled: raise
            except Exception as error:
                raise RenderError(f'Could not animate visual layer {number+1}: {error}') from error
        elif item['kind'] in ('text', 'image'):
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
        start = max(start, float(item.get('_suppress_until', 0)))
        label = f'composite{number}'
        filters.append(f"[{video}][visual{number}]overlay={placement}:eof_action=pass:repeatlast=0:format=auto:enable='gte(t,{_fmt(start)})*lt(t,{_fmt(end)})'[{label}]")
        video = label
        next_input = args.count('-i')
    animated_captions = any(animations.animated(visuals.caption_item(project, cue['text'], cue)) for cue in project.get('captions', []))
    if animated_captions:
        for lane_number, cues in enumerate(_caption_lanes(project)):
            caption_project = dict(project, captions=cues)
            values = _cue_expressions(caption_project)
            inputs = []
            blur = values['blur'] != '0'
            for soft in ((False, True) if blur else (False,)):
                prefix = f'captions-{lane_number}'+('-soft' if soft else '')
                sequence, _, _ = _caption_stream(caption_project, scratch, width, height, total, cancel, prefix, True, soft)
                index = args.count('-i')
                args += ['-threads', '1', '-f', 'concat', '-safe', '0', '-i', str(sequence)]
                source = f'captionbase{lane_number}'+('soft' if soft else '')
                filters.append(f'[{index}:v]fps={_fmt(fps)},format=rgba,settb=AVTB,setpts=PTS-STARTPTS[{source}]')
                inputs.append(source)
            item = visuals.caption_item(project, '')
            placement = _element_motion(filters, inputs[0], f'captionmotion{lane_number}', item, width, height, values, inputs[1] if blur else None,
                                        fps=fps,length=total,scratch=scratch,cues=cues,project=project,cancel=cancel)
            label = f'captioned{lane_number}'
            filters.append(f'[{video}][captionmotion{lane_number}]overlay={placement}:eof_action=pass:repeatlast=0:format=auto[{label}]')
            video = label
        return video
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


def preview_dimensions(project, max_edge=640):
    """0 means the full project canvas; never enlarge beyond that canvas."""
    if isinstance(max_edge, bool) or not isinstance(max_edge, (int, float)) or not math.isfinite(max_edge) or max_edge < 0:
        raise RenderError('Choose a valid preview resolution.')
    width, height = int(project['width']), int(project['height'])
    scale = min(1, max_edge/max(width, height)) if max_edge else 1
    return max(2, round(width*scale/2)*2), max(2, round(height*scale/2)*2)


def _graph(project, preview=False, scratch=None, audio_only=False, cancel=None, origin=0, video_only=False, preview_edge=640, picture_start=None):
    if any(clip.get('transition')=='match_move' for clip in project['clips']):
        from . import animations
        project=copy.deepcopy(project)
        project['layers']=animations.prepare_layers(project)
    # Attached element sounds follow their saved entry/exit timing. Treat them
    # as ephemeral mix inputs, keeping the user's timeline and media untouched.
    if not video_only and not project.get('_original_speech_only'):
        sounds=[item for item in project.get('layers',[])+project.get('captions',[])
                if item.get('sound_effect',{}).get('preset','none')!='none']
        if sounds:
            from . import sound_mix
            from .model import new_audio
            project=copy.deepcopy(project)
            if scratch is None:raise RenderError('Layer sounds need a temporary working folder.')
            path=sound_mix.mix(project,scratch,cancel)
            if path is not None:project['audio'].append(new_audio(path))
    # For live seeking, omit completed main clips. Keep the original global
    # timestamps for music, captions and overlays, including effect history.
    if origin:
        project = copy.deepcopy(project)
        project['clips'] = [clip for clip, row in zip(project['clips'], timeline(project))
                            if row['start'] >= origin-0.0000001]
    width, height = int(project["width"]), int(project["height"])
    if preview:
        width, height = preview_dimensions(project, preview_edge)
    fps = float(project.get("fps", 30))
    args = []
    filters = []
    rows = timeline(project)
    total = project_duration(project) + origin
    clips = project['clips']
    skip=0
    if not audio_only and picture_start is not None:
        limit=max(0,rows[0]['duration']-rows[0]['overlap']-2/fps)
        from fractions import Fraction
        source_fps=fps if is_still(clips[0]) else probe(clips[0]['path'])['fps']
        ratio=Fraction(source_fps/fps*clips[0].get('speed',1)).limit_denominator(10000)
        frames=ratio.denominator
        skip=math.floor(max(0,min(limit,picture_start-origin-2/fps))*fps/frames)*frames/fps
    for index, clip in enumerate(clips):
        duration = clip_duration(clip)
        speed = clip.get("speed", 1)
        seek=skip if index==0 else 0
        remaining=duration-seek
        video_input=args.count('-i')
        if is_card(clip):
            metadata = {'has_video': True, 'has_audio': False, 'video_index': 0}
            args += ['-f', 'lavfi', '-i', f"color=c=0x{clip['color'][1:]}:s={width}x{height}:r={_fmt(fps)}:d={_fmt(remaining)}"]
        elif is_image(clip):
            metadata = {'has_video': True, 'has_audio': False, 'video_index': 0}
            if not audio_only:
                if scratch is None:raise RenderError('Images need a temporary working folder.')
                source = _image_source(clip['path'], Path(scratch)/f'scene-{index}.png')
                args += ['-threads', '1', '-i', source]
        else:
            metadata = probe(clip["path"])
            if not metadata["has_video"]:
                raise RenderError(f"{Path(clip['path']).name} has no video stream.")
            args += ["-threads", "1", "-ss", _fmt(clip.get("in", 0)+seek*speed), "-t", _fmt(clip["out"] - clip["in"]-seek*speed), "-i", clip["path"]]
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
        moving = is_image(clip) and clip.get('motion', 'none') != 'none' and clip.get('zoom_amount', .15) > 0
        # Supersample the still before zoompan to avoid whole-pixel stepping
        # during a slow move. Only one source frame is held at this size.
        frame_width, frame_height = (width*2, height*2) if moving else (width, height)
        if clip.get("fit", "fill") == "fit":
            framing += f"scale={frame_width}:{frame_height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={frame_width}:{frame_height}:(ow-iw)/2:(oh-ih)/2:black"
        else:
            x, y = _fmt(clip.get("position_x", 0.5)), _fmt(clip.get("position_y", 0.5))
            framing += f"scale={frame_width}:{frame_height}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={frame_width}:{frame_height}:(iw-ow)*{x}:(ih-oh)*{y}"
        if moving:
            progress = f"min(1,max(0,({_fmt(clip.get('in', 0)+seek)}+on/{_fmt(fps)})/{_fmt(max(1/fps, clip['duration']-1/fps))}))"
            if clip['motion'] == 'zoom_out':progress = f'(1-({progress}))'
            framing += (f",zoompan=z='1+{_fmt(clip.get('zoom_amount', .15))}*({progress})':"
                        f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
                        f"d={math.ceil(remaining*fps)}:s={width}x{height}:fps={_fmt(fps)}")
        picture = [f"[{video_input}:{metadata['video_index']}]{framing}", "setsar=1", "format=yuv420p",
                   f"setpts=(PTS-STARTPTS)/{_fmt(speed)}", f"tpad=stop_mode=clone:stop_duration={_fmt(remaining)}",
                   f"trim=duration={_fmt(remaining)}", f"fps={_fmt(fps)}", "settb=AVTB"]
        if seek:picture.append(f"setpts=PTS+{_fmt(seek)}/TB")
        picture_fade_in = min(float(clip.get("video_fade_in", 0)), duration)
        picture_fade_out = min(float(clip.get("video_fade_out", 0)), duration)
        if picture_fade_in > 0:
            picture.append(f"fade=t=in:st=0:d={_fmt(picture_fade_in)}")
        if picture_fade_out > 0:
            picture.append(f"fade=t=out:st={_fmt(duration-picture_fade_out)}:d={_fmt(picture_fade_out)}")
        if seek:picture.append(f"setpts=PTS-{_fmt(seek)}/TB")
        if not audio_only:
            filters.append(",".join(picture) + f"[v{index}]")
        if video_only:
            continue
        if metadata["has_audio"] and clip.get("volume", 1) > 0:
            audio_input=video_input
            if seek:
                # Video can input-seek near the playhead. Audio keeps the
                # complete trimmed history for echo/compression and envelopes.
                # This second input only decodes audio, not the old pictures.
                audio_input=args.count('-i')
                args += ['-threads','1','-ss',_fmt(clip.get('in',0)),
                         '-t',_fmt(clip['out']-clip.get('in',0)),'-i',clip['path']]
            parts = [f"[{audio_input}:{metadata['audio_index']}]aresample=48000",
                     "aformat=sample_fmts=fltp:channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
            parts.extend(_processed_audio(clip,duration))
            filters.append(",".join(parts) + f"[a{index}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={_fmt(duration)},asetpts=PTS-STARTPTS[a{index}]")
    video, audio = "v0", "a0"
    running_duration = rows[0]["duration"]-skip
    running_frames = round(running_duration*fps)
    for index in range(1, len(clips)):
        overlap = rows[index - 1]["overlap"]
        if overlap:
            if not audio_only:
                from . import transition_render
                transition_render.append(args,filters,video,f'v{index}',clips[index-1],
                                         overlap,running_duration-overlap,width,height,fps,
                                         rows[index]['duration'],scratch,index,cancel,
                                         with_alpha=bool(project.get('layers') or project.get('captions')),
                                         left_frames=running_frames)
                running_frames = transition_render._first_frame(running_duration-overlap,fps)+round(rows[index]['duration']*fps)
            if not video_only:
                filters.append(f"[{audio}][a{index}]acrossfade=d={_fmt(overlap)}:c1=tri:c2=tri[joina{index}]")
        else:
            if not audio_only:
                filters.append(f"[{video}][v{index}]concat=n=2:v=1:a=0,fps={_fmt(fps)},settb=AVTB[joinv{index}]")
                running_frames += round(rows[index]['duration']*fps)
            if not video_only:
                filters.append(f"[{audio}][a{index}]concat=n=2:v=0:a=1[joina{index}]")
        video, audio = f"joinv{index}", f"joina{index}"
        running_duration += rows[index]["duration"] - overlap
    if origin or skip:
        if not audio_only:
            # Quantize the shifted picture back onto the composition frame
            # grid. Adding rounded microseconds can turn .1 into .099999 and
            # make an output seek skip the requested animation frame.
            filters.append(f'[{video}]settb=expr=1/{_fmt(fps)},setpts=PTS+round({_fmt(origin+skip)}*{_fmt(fps)}),settb=AVTB[globalvideo]')
            video = 'globalvideo'
        if not video_only:
            filters.append(f'[{audio}]adelay={round(origin*1000)}:all=1,asetpts=N/SR/TB[globalaudio]')
            audio = 'globalaudio'
    mix_labels = [audio]
    for offset, track in enumerate([] if video_only else project.get("audio", [])):
        index = args.count('-i')
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
    if not video_only:
        filters.append("".join(f"[{label}]" for label in mix_labels) + f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=0:latency=1,apad,atrim=duration={_fmt(total)}[outaudio]")
    if not audio_only:
        video = _visual_graph(project, args, filters, video, width, height, fps, total, scratch, cancel)
        filters.append(f"[{video}]format=yuv420p[outvideo]")
    return args, ";\n".join(filters), total


def preview_frame(project, seconds, cancel=None, preview_edge=640):
    """Read one composed RGB frame for interactive, silent playhead dragging.

    Seek into the active source at aligned source/composition frame boundaries,
    skipping completed clips and inactive layers/captions. Keep future clip joins
    so color-format negotiation matches playback, with no soundtrack decoding
    or video file.
    """
    project = copy.deepcopy(project)
    _source_rate(project)
    validate_project(project, check_files=True, require_clips=True)
    if any(clip.get('transition')=='match_move' for clip in project['clips']):
        from . import animations
        project['layers']=animations.prepare_layers(project)
    seconds = max(0, min(float(seconds), project_duration(project)-1/project['fps']))
    # FFmpeg's output seek chooses the next frame on the composition grid.
    # Include cues/overlays entering on that frame, even just after the pointer.
    seconds = math.ceil(seconds*project['fps']-1e-7)/project['fps']
    active = [(i, row) for i, row in enumerate(timeline(project))
              if row['start'] <= seconds < row['start']+row['duration']]
    origin = active[0][1]['start']
    project['audio'] = []
    for group in ('layers', 'captions'):
        project[group] = [item for item in project[group] if item['start'] <= seconds < item['end']]
    width, height = preview_dimensions(project, preview_edge)
    with tempfile.TemporaryDirectory(prefix='clipcanvas-frame-') as scratch:
        args, graph, _ = _graph(project, True, scratch, cancel=cancel, origin=origin, video_only=True, preview_edge=preview_edge, picture_start=seconds)
        graph_path = Path(scratch)/'filters.txt'
        graph_path.write_text(graph, encoding='utf-8')
        picture = Path(scratch)/'picture.rgb'
        arguments = ['-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                     '-filter_complex_threads', '1'] + args + [
                     '-filter_complex_script', str(graph_path), '-ss', _fmt(seconds),
                     '-map', '[outvideo]', '-an', '-frames:v', '1', '-threads', '1',
                     '-c:v', 'rawvideo', '-pix_fmt', 'rgb24', '-f', 'rawvideo',
                     '-progress', 'pipe:1', str(picture)]
        _execute(arguments, 1/project['fps'], None, cancel, Path(scratch)/'ffmpeg.log')
        pixels = picture.read_bytes()
        if len(pixels) != width*height*3:
            raise RenderError('Could not read a frame at this timeline position.')
        return {'pixels': pixels, 'width': width, 'height': height, 'position': seconds}


def live_stream(project, start=0, cancel=None, preview_edge=640):
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
        args, graph, total = _graph(project, True, scratch, cancel=cancel, origin=origin,
                                    preview_edge=preview_edge, picture_start=start)
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


def render(project, output, preview=False, progress=None, cancel=None, quality="high", preview_edge=640):
    """Render synchronously; progress receives (fraction_0_to_1, message).

    Call from a background worker. Prepared preview uses preview_edge (640 by
    default) and a fast encoder. Choices: high (CRF 18), smaller (23), master (14).
    Lossless uses FFV1/float PCM in MKV, preserving the composed frames/audio.
    Source decoding, scaling, compositing and 8-bit YUV conversion still apply.
    """
    project = copy.deepcopy(project)
    _source_rate(project)
    try:
        validate_project(project, check_files=True, require_clips=True)
    except ValueError as error:
        raise RenderError(str(error)) from error
    if quality not in ("high", "smaller", "master", "lossless"):
        raise RenderError("Choose high, smaller, master or lossless export quality.")
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
    lossless = quality == 'lossless' and not preview
    if lossless and output.suffix.lower() != '.mkv':
        raise RenderError('Choose an .mkv filename for the lossless master.')
    handle, temporary = tempfile.mkstemp(prefix=".clipcanvas-", suffix=".mkv" if lossless else ".mp4", dir=str(output.parent))
    os.close(handle)
    try:
        with tempfile.TemporaryDirectory(prefix="clipcanvas-render-") as scratch:
            input_args, graph, seconds = _graph(project, preview, scratch=scratch, cancel=cancel, preview_edge=preview_edge)
            graph_path = Path(scratch) / "filters.txt"
            graph_path.write_text(graph, encoding="utf-8")
            if lossless:
                encoding = ['-c:v', 'ffv1', '-level', '3', '-g', '1', '-slicecrc', '1',
                            '-pix_fmt', 'yuv420p', '-c:a', 'pcm_f32le']
            else:
                crf = "18" if preview else {"high": "18", "smaller": "23", "master": "14"}[quality]
                encoding = ['-c:v', 'libx264', '-preset', 'ultrafast' if preview else 'medium',
                            '-crf', crf, '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                            '-b:a', '192k' if preview else '320k', '-movflags', '+faststart']
            arguments = ["-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
                         "-filter_complex_threads", "1"] + input_args + [
                         "-filter_complex_script", str(graph_path), "-map", "[outvideo]", "-map", "[outaudio]"] + encoding + [
                         "-threads", str(min(4, os.cpu_count() or 2)), "-ar", "48000", "-ac", "2", "-t", _fmt(seconds),
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
    first = next((clip for clip in project.get('clips', []) if not is_still(clip)), None)
    if first and first.get('path'):
        first['source_fps'] = probe(first['path'])['fps']
    adapt_frame_rate(project)


def render_audio(project, output, source='mix', progress=None, cancel=None):
    """Write composed speech-recognition audio as a local 16 kHz mono WAV.

    ``video`` includes the trimmed/speed-adjusted main clips with original audio
    restored even when a clip was muted after extraction. ``mix`` follows the
    audible project including independent soundtracks and attached layer sounds.
    Original-video speech excludes element sounds. This operation never
    rasterizes or encodes a picture.
    """
    if source not in ('mix', 'video'):
        raise RenderError('Choose original video speech or the full audio mix.')
    project = copy.deepcopy(project)
    protected_media = media_items(project)
    if source == 'video':
        project['_original_speech_only']=True
        project['audio'] = []
        for item in project.get('layers',[])+project.get('captions',[]):
            item.pop('sound_effect',None)
        for clip in project.get('clips', []):
            clip['volume'] = 0.0 if is_still(clip) else 1.0
            clip.pop('audio_effects',None)
    try:
        validate_project(project, check_files=True, require_clips=True)
    except ValueError as error:
        raise RenderError(str(error)) from error
    output = Path(output).expanduser().resolve()
    for item in protected_media:
        source_path = Path(item['path']).resolve()
        if source_path == output or (output.exists() and source_path.exists() and os.path.samefile(source_path, output)):
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
            inputs, graph, seconds = _graph(project, audio_only=True, scratch=scratch, cancel=cancel)
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
