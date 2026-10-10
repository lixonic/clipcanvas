"""Portable, versioned ClipCanvas project data and timeline calculations.

Media paths are absolute in memory and relative to the project on disk where
possible. Saving never changes the media. Version 1 projects migrate on load.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import tempfile
import uuid


TRANSITIONS = ("none", "fade", "wipeleft", "slideright", "dissolve")
PROJECT_VERSION = 2


def new_project(width=1920, height=1080):
    return {"version": PROJECT_VERSION, "name": "Untitled video", "width": int(width),
            "height": int(height), "fps": 30, "clips": [], "audio": []}


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
            "duration": duration, "in": 0.0, "out": duration, "fit": "fill",
            "volume": 1.0, "transition": "none", "transition_duration": 0.5,
            "position_x": 0.5, "position_y": 0.5, "speed": 1.0,
            "rotation": 0, "flip_x": False, "flip_y": False}


def new_audio(path, metadata=None):
    metadata = _metadata(path, metadata)
    if not metadata.get("has_audio", True):
        raise ValueError("This file has no audio track.")
    duration = float(metadata["duration"])
    return {"id": uuid.uuid4().hex, "path": str(Path(path).expanduser().resolve()),
            "duration": duration, "in": 0.0, "out": duration, "start": 0.0,
            "volume": 1.0, "fade_in": 0.0, "fade_out": 0.0}


def clip_duration(clip):
    return max(0.0, float(clip["out"]) - float(clip.get("in", 0.0))) / float(clip.get("speed", 1))


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


def validate_project(project, check_files=True, require_clips=False):
    """Raise ValueError with a readable explanation; return True on success."""
    if not isinstance(project, dict) or project.get("version") != PROJECT_VERSION:
        raise ValueError("This is not a supported ClipCanvas project (version 2).")
    for key in ("width", "height"):
        value = project.get(key)
        _number(value, f"Canvas {key}", 64, 8192)
        if int(value) != value or value % 2:
            raise ValueError("Canvas dimensions must be even whole numbers.")
    _number(project.get("fps", 30), "Frame rate", 1, 120)
    if not isinstance(project.get("name", ""), str):
        raise ValueError("The project name must be text.")
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
            path = item.get("path")
            if not isinstance(path, str) or not path or "\x00" in path:
                raise ValueError(f"{label} has no valid file path.")
            if not Path(path).is_absolute():
                raise ValueError(f"{label} needs an absolute local file path.")
            if check_files and not Path(path).is_file():
                raise ValueError(f"Missing media: {path}\nRestore the file to its original location, or replace this item.")
            duration = _number(item.get("duration"), f"{label} source duration", 0.001, 86400 * 7)
            begin = _number(item.get("in", 0), f"{label} trim start", 0, duration)
            end = _number(item.get("out"), f"{label} trim end", 0, duration + 0.05)
            if end - begin < 0.04:
                raise ValueError(f"{label} must be at least 0.04 seconds long.")
            _number(item.get("volume", 1), f"{label} volume", 0, 2)
            if group == "clips":
                _number(item.get("speed", 1), f"{label} playback speed", 0.25, 4)
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
                for key in ("fade_in", "fade_out"):
                    _number(item.get(key, 0), f"{label} fade", 0, duration)
    if require_clips and not project["clips"]:
        raise ValueError("Add at least one video clip before creating a preview or export.")
    return True


def save_project(project, path):
    """Save atomically, including empty projects; leave source files untouched."""
    validate_project(project, check_files=False)
    target = Path(path).expanduser().resolve()
    for item in project["clips"] + project["audio"]:
        if Path(item["path"]).resolve() == target:
            raise ValueError("Choose a project filename different from your original media.")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = copy.deepcopy(project)
    data.pop("_project_path", None)
    for item in data["clips"] + data["audio"]:
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
    # Version 1's optional fields may be absent in hand-written/older projects.
    # Normalize them before presenting the data to the UI, which uses direct
    # indexing for editable fields. Explicit invalid values are still rejected.
    if isinstance(project, dict):
        old_version = project.get("version")
        if old_version not in (1, PROJECT_VERSION):
            raise ValueError("This project was made by an unsupported ClipCanvas version.")
        project["version"] = PROJECT_VERSION
        project.setdefault("name", "Untitled video")
        project.setdefault("fps", 30)
        project.setdefault("audio", [])
        for group in ("clips", "audio"):
            items = project.get(group)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                media_path = item.get("path")
                if old_version == 2 and isinstance(media_path, str) and media_path:
                    item["path"] = str((target.parent / media_path).resolve())
                defaults = {"id": uuid.uuid4().hex, "in": 0.0,
                            "out": item.get("duration"), "volume": 1.0}
                if group == "clips":
                    defaults.update({"fit": "fill", "transition": "none",
                                     "transition_duration": 0.5,
                                     "position_x": 0.5, "position_y": 0.5,
                                     "speed": 1.0, "rotation": 0,
                                     "flip_x": False, "flip_y": False})
                else:
                    defaults.update({"start": 0.0, "fade_in": 0.0, "fade_out": 0.0})
                for key, value in defaults.items():
                    item.setdefault(key, value)
    validate_project(project, check_files=False)
    return project


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
    else:
        audible = min(length, max(0, project_duration(project) - start))
        if offset < item["fade_in"] or (item["fade_out"] and offset > audible - item["fade_out"]):
            raise ValueError("Split outside the audio fade, or shorten the fade first.")
    right = copy.deepcopy(item)
    right["id"] = uuid.uuid4().hex
    cut = item["in"] + offset * item.get("speed", 1)
    item["out"] = cut
    right["in"] = cut
    if kind == "clips":
        item["transition"] = "none"  # Keep the original outgoing effect on the right.
    else:
        right["start"] = at
        item["fade_out"] = 0.0
        right["fade_in"] = 0.0
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
        item["out"] = min(item["duration"], max(item["in"] + minimum, item["out"] + delta * speed))
    if kind == "audio":
        for key in ("fade_in", "fade_out"):
            item[key] = min(item[key], clip_duration(item))
    validate_project(result, check_files=False)
    return result
