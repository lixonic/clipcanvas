"""Portable, versioned ClipCanvas project data and timeline calculations.

Media paths are absolute in memory and relative to the project on disk where
possible. Saving never changes the media. Formats 1–3 migrate on load; format 4
adds file-free color cards. Visual layers use canvas-relative centers and sizes, so their placement
survives a canvas resize; text sizes are expressed in canvas pixels.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

from .text_styles import effect_defaults
from . import track_layout, audio_effects


TRANSITIONS = ("none", "fade", "wipeleft", "slideright", "dissolve")
PROJECT_VERSION = 4
AUDIO_FADES = ("fade_in", "fade_out", "transition_fade_in", "transition_fade_out")
MAX_TIMELINE_SECONDS = 86400 * 7


def text_style():
    """Return independent style defaults for a title or caption."""
    return {**effect_defaults(), "x": 0.5, "y": 0.5, "width": 0.7, "height": 0.18,
            "rotation": 0.0, "opacity": 1.0, "font_family": "Sans",
            "font_size": 64.0, "bold": False, "italic": False,
            "color": "#ffffff", "background": "#00000000",
            "outline_color": "#000000", "outline_width": 2.0,
            "align": "center"}


def caption_style():
    """Captions share a style; each cue stores only its text and timing."""
    result = text_style()
    result.update({"font_size": 48.0, "width": 0.9, "height": 0.15, "y": 0.88})
    return result


def new_project(width=1920, height=1080):
    return {"version": PROJECT_VERSION, "name": "Untitled video", "width": int(width),
            "height": int(height), "fps": 30, "clips": [], "audio": [],
            "layers": [], "captions": [], "caption_style": caption_style(), "timeline_order": []}


def new_text(text="Your title", start=0.0, end=3.0):
    result = {"id": uuid.uuid4().hex, "kind": "text", "start": start,
              "end": end, "text": text}
    result.update(text_style())
    return result


def new_visual(path, kind="image", start=0.0, end=3.0, metadata=None):
    """Create a local image or video overlay without altering its source.

    Still images have no source duration and need no media probe. Video
    metadata is read lazily when omitted, as it is for a main video clip.
    """
    if kind not in ("image", "video"):
        raise ValueError("Choose an image or video for a visual layer.")
    result = {"id": uuid.uuid4().hex, "kind": kind,
              "path": str(Path(path).expanduser().resolve()),
              "start": start, "end": end, "x": 0.5, "y": 0.5,
              "width": 0.4, "height": 0.3, "rotation": 0.0,
              "opacity": 1.0, "fit": "fit", "in": 0.0, "speed": 1.0}
    if kind == "video":
        metadata = _metadata(path, metadata)
        if not metadata.get("has_video", True):
            raise ValueError("This file has no video to use as a visual layer.")
        result["duration"] = float(metadata["duration"])
    return result


def new_caption(text="", start=0.0, end=3.0):
    return {"id": uuid.uuid4().hex, "start": start, "end": end, "text": text}


def media_items(project):
    """Return references to every file-backed item, including visual layers.

    Do not deduplicate items here: callers must update each path when saving
    or relinking. Collection deduplicates the source files separately.
    """
    return [item for item in project.get("clips", []) if not is_card(item)] + list(project.get("audio", [])) + [
        item for item in project.get("layers", []) if item.get("kind") in ("image", "video")]


def _metadata(path, metadata):
    if metadata is None:
        from .engine import probe
        metadata = probe(path)
    return metadata


def new_clip(path, metadata=None):
    metadata = _metadata(path, metadata)
    if not metadata.get("has_video", True):
        raise ValueError("This file has no video. Add it as a soundtrack instead.")
    duration = float(metadata["duration"])
    return {"id": uuid.uuid4().hex, "path": str(Path(path).expanduser().resolve()),
            "duration": duration, "source_fps": float(metadata.get('fps', 0)),
            "in": 0.0, "out": duration, "fit": "fill",
            "volume": 1.0, "transition": "none", "transition_duration": 0.5,
            "position_x": 0.5, "position_y": 0.5, "speed": 1.0,
            "rotation": 0, "flip_x": False, "flip_y": False,
            "fade_in": 0.0, "fade_out": 0.0,
            "video_fade_in": 0.0, "video_fade_out": 0.0}


def is_card(item):
    return item.get('kind') == 'card'


def item_name(item):
    return 'Color card' if is_card(item) else Path(item['path']).name


def new_card(color='#ffffff', duration=3.0):
    """A file-free solid-color scene in the main, ripple timeline."""
    return {'id': uuid.uuid4().hex, 'kind': 'card', 'color': color,
            'duration': float(duration), 'in': 0.0, 'out': float(duration),
            'speed': 1.0, 'volume': 0.0, 'fit': 'fill',
            'fade_in': 0.0, 'fade_out': 0.0, 'position_x': .5, 'position_y': .5,
            'rotation': 0, 'flip_x': False, 'flip_y': False,
            'transition': 'none', 'transition_duration': .5,
            'video_fade_in': 0.0, 'video_fade_out': 0.0}


def new_audio(path, metadata=None):
    metadata = _metadata(path, metadata)
    if not metadata.get("has_audio", True):
        raise ValueError("This file has no audio track.")
    duration = float(metadata["duration"])
    return {"id": uuid.uuid4().hex, "path": str(Path(path).expanduser().resolve()),
            "duration": duration, "in": 0.0, "out": duration, "start": 0.0,
            "volume": 1.0, "speed": 1.0, "fade_in": 0.0, "fade_out": 0.0,
            "transition_fade_in": 0.0, "transition_fade_out": 0.0}


def clip_duration(clip):
    return max(0.0, float(clip["out"]) - float(clip.get("in", 0.0))) / float(clip.get("speed", 1))


def adapt_frame_rate(project):
    """Follow the first main clip, preserving fractional rates such as 29.97.

    Old projects keep their stored rate until the first source has been probed.
    A fixed export needs one rate; later mixed-rate sources are resampled to it.
    """
    first = next((item for item in project.get('clips', []) if not is_card(item)), None)
    if first:
        fps = first.get('source_fps', 0)
        if isinstance(fps, (int, float)) and not isinstance(fps, bool) and math.isfinite(fps) and 1 <= fps <= 240:
            project['fps'] = fps
    return project['fps']


def timeline(project):
    """Return each clip's start, duration, and its OUTGOING overlap.

    Each transition may use at most half of either adjacent clip. This keeps
    short clips visible and prevents three clips from overlapping at once.
    """
    clips = project.get("clips", [])
    rows = []
    start = 0.0
    for index, clip in enumerate(clips):
        duration = clip_duration(clip)
        overlap = 0.0
        if index + 1 < len(clips) and clip.get("transition", "none") != "none":
            overlap = max(0.0, min(float(clip.get("transition_duration", 0.5)),
                                   duration / 2, clip_duration(clips[index + 1]) / 2))
            if overlap < 1.0 / float(project.get("fps", 30)):
                overlap = 0.0
        rows.append({"start": start, "duration": duration, "overlap": overlap})
        start += duration - overlap
    return rows


def project_duration(project):
    rows = timeline(project)
    return rows[-1]["start"] + rows[-1]["duration"] if rows else 0.0


def _number(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a number.")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be between {lower:g} and {upper:g}.")
    return float(value)


def _text(value, name, maximum, allow_empty=True):
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be valid text with no more than {maximum} characters.")


def _media_path(item, label, check_files):
    path = item.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError(f"{label} has no valid file path.")
    if not Path(path).is_absolute():
        raise ValueError(f"{label} needs an absolute local file path.")
    if check_files and not Path(path).is_file():
        raise ValueError(f"Missing media: {path}\nRestore the file to its original location, or replace this item.")


def _geometry(item, label):
    for key in ("x", "y"):
        _number(item.get(key), f"{label} {key} position", 0, 1)
    for key in ("width", "height"):
        _number(item.get(key), f"{label} {key}", 0.001, 2)
    _number(item.get("rotation", 0), f"{label} rotation", -360, 360)
    _number(item.get("opacity", 1), f"{label} opacity", 0, 1)


def _text_style(item, label):
    _geometry(item, label)
    _text(item.get("font_family"), f"{label} font family", 128, allow_empty=False)
    _number(item.get("font_size"), f"{label} font size", 1, 2048)
    _number(item.get("outline_width", 0), f"{label} outline width", 0, 64)
    for key in ("bold", "italic"):
        if not isinstance(item.get(key, False), bool):
            raise ValueError(f"{label} {key} must be on or off.")
    for key in ("color", "background", "outline_color"):
        value = item.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
            raise ValueError(f"{label} {key.replace('_', ' ')} must be a hex RGB or RGBA color.")
    shadow = item.get("shadow_color", "#00000000")
    if not isinstance(shadow, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", shadow):
        raise ValueError(f"{label} shadow color must be a hex RGB or RGBA color.")
    for key in ("shadow_x", "shadow_y"):
        _number(item.get(key, 3), f"{label} shadow offset", -256, 256)
    _number(item.get("shadow_blur", 0), f"{label} shadow softness", 0, 64)
    _number(item.get("background_radius", 0), f"{label} background roundness", 0, 256)
    _number(item.get("background_padding", 8), f"{label} background padding", 0, 128)
    if item.get("background_mode", "box") not in ("box", "text"):
        raise ValueError(f"{label} background must cover the layer or follow the text.")
    if item.get("align") not in ("left", "center", "right"):
        raise ValueError(f"{label} alignment must be left, center or right.")


def _timed_item(item, label):
    if not isinstance(item, dict):
        raise ValueError(f"{label} is invalid.")
    _text(item.get("id"), f"{label} identifier", 128, allow_empty=False)
    start = _number(item.get("start"), f"{label} start", 0, MAX_TIMELINE_SECONDS)
    end = _number(item.get("end"), f"{label} end", 0, MAX_TIMELINE_SECONDS)
    if end <= start:
        raise ValueError(f"{label} end must be later than its start.")


def validate_project(project, check_files=True, require_clips=False):
    """Raise ValueError with a readable explanation; return True on success."""
    if not isinstance(project, dict) or project.get("version") != PROJECT_VERSION:
        raise ValueError(f"This is not a supported ClipCanvas project (version {PROJECT_VERSION}).")
    for key in ("width", "height"):
        value = project.get(key)
        _number(value, f"Canvas {key}", 64, 8192)
        if int(value) != value or value % 2:
            raise ValueError("Canvas dimensions must be even whole numbers.")
    _number(project.get("fps", 30), "Frame rate", 1, 240)
    _text(project.get("name", ""), "The project name", 1000)
    track_layout.validate_order(project.get('timeline_order', []))
    for group in ("clips", "audio"):
        items = project.get(group)
        if not isinstance(items, list):
            raise ValueError(f"The project {group} list is missing or invalid.")
        if len(items) > 200:
            raise ValueError("Please use no more than 200 clips or soundtracks per project.")
        for index, item in enumerate(items):
            label = f"{'Clip' if group == 'clips' else 'Soundtrack'} {index + 1}"
            if not isinstance(item, dict):
                raise ValueError(f"{label} is invalid.")
            if group == 'clips' and is_card(item):
                if not isinstance(item.get('color'), str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', item['color']):
                    raise ValueError(f'{label} color must be a hex RGB color.')
                if item.get('speed', 1) != 1 or item.get('volume', 0) != 0:
                    raise ValueError('Color cards have a duration and no source sound or playback speed.')
            else:
                if group == 'clips' and item.get('kind', 'video') != 'video':
                    raise ValueError(f'{label} must be a video or color card.')
                _media_path(item, label, check_files)
            duration = _number(item.get("duration"), f"{label} source duration", 0.001, 86400 * 7)
            begin = _number(item.get("in", 0), f"{label} trim start", 0, duration)
            end = _number(item.get("out"), f"{label} trim end", 0, duration + 0.05)
            if end - begin < 0.04:
                raise ValueError(f"{label} must be at least 0.04 seconds long.")
            _number(item.get("volume", 1), f"{label} volume", 0, 2)
            if 'audio_effects' in item:audio_effects.validate(item['audio_effects'])
            speed = _number(item.get("speed", 1), f"{label} playback speed", 0.25, 4)
            for key in AUDIO_FADES:
                # Fades use seconds on the timeline, after changing speed. An
                # overlong fade is clamped to the audible segment when rendered.
                _number(item.get(key, 0), f"{label} fade", 0, max(duration, duration / speed))
            if group == "clips":
                _number(item.get('source_fps', 0), f'{label} original frame rate', 0, 240)
                for key in ("video_fade_in", "video_fade_out"):
                    _number(item.get(key, 0), f"{label} picture fade", 0, max(duration, duration / speed))
                if isinstance(item.get("rotation", 0), bool) or item.get("rotation", 0) not in (0, 90, 180, 270):
                    raise ValueError(f"{label} rotation must be 0, 90, 180 or 270 degrees.")
                for key in ("flip_x", "flip_y"):
                    if not isinstance(item.get(key, False), bool):
                        raise ValueError(f"{label} flip must be on or off.")
                if item.get("fit", "fill") not in ("fit", "fill"):
                    raise ValueError(f"{label} has an unsupported framing option.")
                if item.get("transition", "none") not in TRANSITIONS:
                    raise ValueError(f"{label} has an unsupported transition.")
                _number(item.get("transition_duration", 0.5), f"{label} transition duration", 0, 10)
                for key in ("position_x", "position_y"):
                    _number(item.get(key, 0.5), f"{label} crop position", 0, 1)
            else:
                _number(item.get("start", 0), f"{label} timeline start", 0, 86400 * 7)
                source_clip_id = item.get("source_clip_id")
                if source_clip_id is not None and (not isinstance(source_clip_id, str) or not source_clip_id):
                    raise ValueError(f"{label} has an invalid source clip reference.")
    layers = project.get("layers")
    if not isinstance(layers, list) or len(layers) > 500:
        raise ValueError("Use a valid list of no more than 500 visual layers.")
    for index, item in enumerate(layers):
        label = f"Layer {index + 1}"
        _timed_item(item, label)
        if item.get("kind") == "text":
            _text(item.get("text"), f"{label} text", 10000)
            _text_style(item, label)
        elif item.get("kind") in ("image", "video"):
            _media_path(item, label, check_files)
            _geometry(item, label)
            if item.get("fit", "fit") not in ("fit", "fill"):
                raise ValueError(f"{label} has an unsupported framing option.")
            _number(item.get("speed", 1), f"{label} playback speed", 0.25, 4)
            if item["kind"] == "video":
                duration = _number(item.get("duration"), f"{label} source duration", 0.001, MAX_TIMELINE_SECONDS)
                begin = _number(item.get("in", 0), f"{label} source start", 0, duration)
                if begin >= duration:
                    raise ValueError(f"{label} source start must be before the video end.")
            else:
                _number(item.get("in", 0), f"{label} source start", 0, MAX_TIMELINE_SECONDS)
        else:
            raise ValueError(f"{label} must be text, an image or a video.")
    captions = project.get("captions")
    if not isinstance(captions, list) or len(captions) > 5000:
        raise ValueError("Use a valid list of no more than 5000 captions.")
    for index, item in enumerate(captions):
        label = f"Caption {index + 1}"
        _timed_item(item, label)
        _text(item.get("text"), f"{label} text", 2000)
    style = project.get("caption_style")
    if not isinstance(style, dict):
        raise ValueError("The caption style is missing or invalid.")
    _text_style(style, "Caption")
    if require_clips and not project["clips"]:
        raise ValueError("Add at least one video or color card before creating a preview or export.")
    return True


def save_project(project, path):
    """Save atomically, including empty projects; leave source files untouched."""
    validate_project(project, check_files=False)
    target = Path(path).expanduser().resolve()
    for item in media_items(project):
        if Path(item["path"]).resolve() == target:
            raise ValueError("Choose a project filename different from your original media.")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = copy.deepcopy(project)
    data.pop("_project_path", None)
    for item in media_items(data):
        try:
            item["path"] = Path(os.path.relpath(item["path"], target.parent)).as_posix()
        except ValueError:  # Different Windows drives cannot use relative paths.
            pass
    handle, temporary = tempfile.mkstemp(prefix=".clipcanvas-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def load_project(path):
    target = Path(path).expanduser().resolve()
    if target.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("This project file is too large to open safely.")
    try:
        with target.open(encoding="utf-8") as stream:
            project = json.load(stream)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("This file is not a readable ClipCanvas project.") from error
    # Optional fields may be absent in hand-written/older projects.
    # Normalize them before presenting the data to the UI, which uses direct
    # indexing for editable fields. Explicit invalid values are still rejected.
    if isinstance(project, dict):
        old_version = project.get("version")
        if old_version not in (1, 2, 3, PROJECT_VERSION):
            raise ValueError("This project was made by an unsupported ClipCanvas version.")
        project["version"] = PROJECT_VERSION
        project.setdefault("name", "Untitled video")
        project.setdefault("fps", 30)
        project.setdefault("audio", [])
        project.setdefault("layers", [])
        project.setdefault("captions", [])
        project.setdefault("timeline_order", [])
        project.setdefault("caption_style", caption_style())
        if isinstance(project.get("caption_style"), dict):
            for key, value in caption_style().items():
                project["caption_style"].setdefault(key, value)
        for group in ("clips", "audio"):
            items = project.get(group)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                media_path = item.get("path")
                if old_version >= 2 and isinstance(media_path, str) and media_path:
                    item["path"] = str((target.parent / media_path).resolve())
                defaults = {"id": uuid.uuid4().hex, "in": 0.0,
                            "out": item.get("duration"), "volume": 1.0,
                            "speed": 1.0, "fade_in": 0.0, "fade_out": 0.0}
                if group == "clips":
                    defaults.update({"fit": "fill", "transition": "none",
                                     "transition_duration": 0.5,
                                     "position_x": 0.5, "position_y": 0.5,
                                     "rotation": 0,
                                     "flip_x": False, "flip_y": False,
                                     "video_fade_in": 0.0, "video_fade_out": 0.0})
                else:
                    defaults.update({"start": 0.0, "transition_fade_in": 0.0,
                                     "transition_fade_out": 0.0})
                for key, value in defaults.items():
                    item.setdefault(key, value)
        layers = project.get("layers")
        if isinstance(layers, list):
            for item in layers:
                if not isinstance(item, dict):
                    continue
                item.setdefault("id", uuid.uuid4().hex)
                if item.get("kind") == "text":
                    for key, value in text_style().items():
                        item.setdefault(key, value)
                elif item.get("kind") in ("image", "video"):
                    media_path = item.get("path")
                    if old_version >= 2 and isinstance(media_path, str) and media_path:
                        item["path"] = str((target.parent / media_path).resolve())
                    for key, value in {"x": 0.5, "y": 0.5, "width": 0.4,
                                       "height": 0.3, "rotation": 0.0,
                                       "opacity": 1.0, "fit": "fit", "in": 0.0,
                                       "speed": 1.0}.items():
                        item.setdefault(key, value)
        captions = project.get("captions")
        if isinstance(captions, list):
            for item in captions:
                if isinstance(item, dict):
                    item.setdefault("id", uuid.uuid4().hex)
    validate_project(project, check_files=False)
    return project


def detach_audio(project, clip_index):
    """Extract a clip's soundtrack without creating or modifying a media file.

    Return an edited copy with the independent track appended and the original
    clip muted. Source trims and pitch-preserving speed are retained. Existing
    transition envelopes are copied as additional fades, so extraction itself
    does not change the mix. The new track then moves independently of the clip.
    Repeated extraction is rejected while any part of its track remains.
    """
    validate_project(project, check_files=True)
    if isinstance(clip_index, bool) or not isinstance(clip_index, int) or not 0 <= clip_index < len(project["clips"]):
        raise ValueError("Select a video clip to extract its audio.")
    clip = project["clips"][clip_index]
    if is_card(clip):
        raise ValueError("Color cards have no audio to extract.")
    source_clip_id = clip.get("id") or uuid.uuid4().hex
    if any(track.get("source_clip_id") == source_clip_id for track in project["audio"]):
        raise ValueError("Audio has already been extracted from this clip. Select its soundtrack in the timeline.")
    # Import lazily, as with new_audio, to keep project calculations independent
    # of the renderer and to avoid an engine/model import cycle.
    from .engine import RenderError, probe
    try:
        metadata = probe(clip["path"])
    except RenderError as error:
        raise ValueError(str(error)) from error
    if not metadata["has_audio"]:
        raise ValueError("This clip has no audio track to extract.")
    rows = timeline(project)
    track = new_audio(clip["path"], metadata)
    for key in ("duration", "in", "out", "volume", "speed", "fade_in", "fade_out"):
        track[key] = clip.get(key, track[key])
    if 'audio_effects' in clip:track['audio_effects']=copy.deepcopy(clip['audio_effects'])
    track.update({"start": rows[clip_index]["start"], "source_clip_id": source_clip_id,
                  "transition_fade_in": rows[clip_index - 1]["overlap"] if clip_index else 0.0,
                  "transition_fade_out": rows[clip_index]["overlap"]})
    result = copy.deepcopy(project)
    result["clips"][clip_index]["id"] = source_clip_id
    result["clips"][clip_index]["volume"] = 0.0
    result["audio"].append(track)
    validate_project(result, check_files=True)
    return result


def split_item(project, kind, index, at):
    """Return an edited copy. `at` is time in the complete video, not the source."""
    if kind not in ("clips", "audio"):
        raise ValueError("Select a video clip or soundtrack to split.")
    result = copy.deepcopy(project)
    item = result[kind][index]
    start = timeline(project)[index]["start"] if kind == "clips" else item["start"]
    offset = _number(at, "Split position", 0, 86400 * 7) - start
    length = clip_duration(item)
    minimum = max(.04 / item.get("speed", 1), 1 / project["fps"])
    if offset < minimum or length - offset < minimum:
        raise ValueError("Place the playhead inside the selected item, away from its edges.")
    if kind == "clips":
        rows = timeline(project)
        incoming = rows[index - 1]["overlap"] if index else 0
        outgoing = rows[index]["overlap"]
        if offset < 2 * incoming or length - offset < 2 * outgoing:
            raise ValueError("Split farther from the transition, or shorten the transition first.")
    audible = min(length, max(0, project_duration(project) - start))
    fade_in = max(item.get("fade_in", 0), item.get("transition_fade_in", 0))
    fade_out = max(item.get("fade_out", 0), item.get("transition_fade_out", 0))
    if offset < fade_in or (fade_out and offset > audible - fade_out):
        raise ValueError("Split outside the audio fade, or shorten the fade first.")
    if kind == "clips":
        if offset < item.get("video_fade_in", 0) or (item.get("video_fade_out", 0) and offset > length - item["video_fade_out"]):
            raise ValueError("Split outside the picture fade, or shorten the fade first.")
    right = copy.deepcopy(item)
    right["id"] = uuid.uuid4().hex
    cut = item["in"] + offset * item.get("speed", 1)
    item["out"] = cut
    right["in"] = cut
    if kind == "clips":
        item["transition"] = "none"  # Keep the original outgoing effect on the right.
    else:
        right["start"] = at
    # Fades belong to the original item's outer edges, not the new middle cut.
    for key in ("fade_out", "transition_fade_out", "video_fade_out"):
        if key in item:
            item[key] = 0.0
    for key in ("fade_in", "transition_fade_in", "video_fade_in"):
        if key in right:
            right[key] = 0.0
    result[kind].insert(index + 1, right)
    validate_project(result, check_files=False)
    return result


def trim_item(project, kind, index, edge, delta):
    """Ripple-trim video; audio left trim also moves its start to keep sync."""
    if kind not in ("clips", "audio") or edge not in ("left", "right"):
        raise ValueError("Choose an item edge to trim.")
    result = copy.deepcopy(project)
    item = result[kind][index]
    speed = item.get("speed", 1)
    minimum = max(.04, speed / project["fps"])
    if edge == "left":
        lower = max(0, item["in"] - item.get("start", 0) * speed) if kind == "audio" else 0
        begin = min(item["out"] - minimum, max(lower, item["in"] + delta * speed))
        if kind == "audio":
            item["start"] += (begin - item["in"]) / speed
        item["in"] = begin
    else:
        limit = MAX_TIMELINE_SECONDS if kind == 'clips' and is_card(item) else item['duration']
        item["out"] = min(limit, max(item["in"] + minimum, item["out"] + delta * speed))
        if kind == 'clips' and is_card(item):item['duration'] = max(item['duration'], item['out'])
    for key in AUDIO_FADES + ("video_fade_in", "video_fade_out"):
        if key in item:
            item[key] = min(item[key], clip_duration(item))
    validate_project(result, check_files=False)
    return result
