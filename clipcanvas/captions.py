"""SRT files and optional, entirely local speech recognition.

Recognition runs in a private virtual environment, never in GTK's Python.
Only explicit setup downloads packages/models; transcription is forced offline.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading


FASTER_WHISPER_VERSION = "1.2.1"
MODEL_DOWNLOAD_MB = {"tiny": 75, "tiny.en": 75, "base": 145, "base.en": 145,
                     "small": 490, "small.en": 490}
MAX_SRT_BYTES = 10 * 1024 * 1024
MAX_CAPTIONS = 5000
MAX_SECONDS = 86400 * 7
_STAMP = r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})"
_TIMING = re.compile(r"^" + _STAMP + r"\s+-->\s+" + _STAMP + r"$")


class CaptionError(RuntimeError):
    pass


class CaptionCancelled(CaptionError):
    pass


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Each caption needs non-empty text without null characters.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > 2000 or re.search(r"\n[ \t]*\n", value):
        raise ValueError("Caption text must be at most 2000 characters and cannot contain a blank line.")
    return value


def _entries(entries):
    if not isinstance(entries, list) or len(entries) > MAX_CAPTIONS:
        raise ValueError(f"Use a list of no more than {MAX_CAPTIONS} captions.")
    result = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("Each caption must contain text, start and end.")
        start, end = item.get("start"), item.get("end")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or not 0 <= value <= MAX_SECONDS
               for value in (start, end)) or end <= start:
            raise ValueError("Caption end must be later than start, within seven days.")
        # SRT has millisecond precision. Refuse a cue which rounds to no duration.
        if round(end * 1000) <= round(start * 1000):
            raise ValueError("Each SRT caption must last at least one millisecond.")
        result.append({"start": float(start), "end": float(end), "text": _text(item.get("text"))})
    return sorted(result, key=lambda item: (item["start"], item["end"]))


def parse_srt(text):
    """Parse UTF-8 SubRip text into independent project caption records.

    BOM, CRLF, Unicode and multiline cues are supported. Invalid blocks fail
    together, so a caller can retain existing captions instead of importing a
    partially parsed file. Overlapping cues are allowed and retained.
    """
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SRT_BYTES:
        raise ValueError("Use an SRT text file smaller than 10 MB.")
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    entries = []
    seen = set()
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.split("\n")
        if len(lines) < 3 or not re.fullmatch(r"[1-9]\d*", lines[0].strip()):
            raise ValueError("An SRT cue needs its number, timestamps and caption text.")
        number = int(lines[0].strip())
        if number in seen:
            raise ValueError("SRT cue numbers must be unique.")
        seen.add(number)
        match = _TIMING.fullmatch(lines[1].strip())
        if not match:
            raise ValueError(f"SRT cue {number} needs timestamps such as 00:00:01,000 --> 00:00:03,000.")
        parts = tuple(int(value) for value in match.groups())
        def seconds(at):
            hours, minutes, seconds_, milliseconds = parts[at:at + 4]
            return hours * 3600 + minutes * 60 + seconds_ + milliseconds / 1000
        entries.append({"start": seconds(0), "end": seconds(4), "text": "\n".join(lines[2:])})
    from .model import new_caption
    return [new_caption(item["text"], item["start"], item["end"]) for item in _entries(entries)]


def _timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds_, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_:02d},{milliseconds:03d}"


def to_srt(entries):
    """Return chronological SubRip text, retaining overlaps and multiline text."""
    blocks = [f"{index}\n{_timestamp(item['start'])} --> {_timestamp(item['end'])}\n{item['text']}"
              for index, item in enumerate(_entries(entries), 1)]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def load_srt(path):
    source = Path(path).expanduser().resolve()
    if source.stat().st_size > MAX_SRT_BYTES:
        raise ValueError("Use an SRT text file smaller than 10 MB.")
    try:
        return parse_srt(source.read_text(encoding="utf-8-sig"))
    except UnicodeError as error:
        raise ValueError("This SRT file is not readable UTF-8 text.") from error


def save_srt(project, path):
    """Save atomically; never overwrite a project or any original media file."""
    if not isinstance(project, dict):
        raise ValueError("Choose a valid project to export captions.")
    content = to_srt(project.get("captions", []))
    from .model import media_items
    target = Path(path).expanduser().resolve()
    protected = [item["path"] for item in media_items(project)]
    if project.get("_project_path"):
        protected.append(project["_project_path"])
    for path_ in protected:
        source = Path(path_).expanduser().resolve()
        if source == target or (source.exists() and target.exists() and os.path.samefile(source, target)):
            raise ValueError("Choose an SRT filename different from the project and original media.")
    if target.is_dir() or not target.parent.is_dir():
        raise ValueError("Choose an SRT filename inside an existing folder.")
    handle, temporary = tempfile.mkstemp(prefix=".clipcanvas-captions-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return str(target)


def transcription_dir(runtime_dir=None):
    if runtime_dir is not None:
        return Path(runtime_dir).expanduser().resolve()
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (root / "clipcanvas" / "transcription").expanduser().resolve()


def _model_name(model):
    if model not in MODEL_DOWNLOAD_MB:
        raise ValueError("Choose tiny, base or small (or an English-only .en variant) for local captions.")
    return model


def runtime_status(runtime_dir=None, model="small"):
    model = _model_name(model)
    root = transcription_dir(runtime_dir)
    python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    model_path = root / "models" / model
    try:
        versions = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        versions = {}
    installed = python.is_file() and versions.get("faster_whisper") == "1.2.1" and versions.get("av") == "16.1.0"
    ready = installed and (model_path / "ready.json").is_file() and all(
        (model_path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))
    return {"runtime_dir": str(root), "python": str(python), "installed": installed,
            "ready": ready, "model": model, "model_path": str(model_path),
            "download_mb": MODEL_DOWNLOAD_MB[model]}


def _stop(process):
    if process.poll() is None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()


def _run_worker(command, progress=None, cancel=None, env=None):
    if cancel is not None and cancel.is_set():
        raise CaptionCancelled("Caption generation cancelled.")
    with tempfile.TemporaryFile(mode="w+b") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                   stdin=subprocess.DEVNULL, env=env, start_new_session=os.name != "nt")
        messages = queue.SimpleQueue()
        def read_lines():
            try:
                for line in iter(process.stdout.readline, b""):
                    messages.put(line)
            finally:
                messages.put(None)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        result = None
        failure = None
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise CaptionCancelled("Caption generation cancelled.")
                try:
                    line = messages.get(timeout=.15)
                except queue.Empty:
                    continue
                if line is None:
                    break
                # pip/Hugging Face may write ordinary console output during
                # explicit setup. Our machine-readable messages are JSON lines.
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "progress" and progress:
                    progress(max(0.0, min(.995, float(message["fraction"]))), str(message["message"]))
                elif message.get("type") == "result":
                    result = message.get("result")
                elif message.get("type") == "error":
                    failure = str(message.get("message", "Local captions failed."))
            process.wait()
            if cancel is not None and cancel.is_set():
                raise CaptionCancelled("Caption generation cancelled.")
            if process.returncode or failure or result is None:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 2000))
                detail = log.read().decode("utf-8", "replace").strip()
                raise CaptionError(failure or "The local caption worker failed. " + detail)
            if progress:
                progress(1.0, "Captions ready." if isinstance(result, dict) and "captions" in result else "Speech model ready.")
            return result
        finally:
            _stop(process)
            reader.join(timeout=3)
            process.stdout.close()


def setup_transcription(runtime_dir=None, model="small", progress=None, cancel=None):
    """Explicitly download/install the isolated CPU runtime and selected model."""
    model = _model_name(model)
    if cancel is not None and cancel.is_set():
        raise CaptionCancelled("Caption generation cancelled.")
    status = runtime_status(runtime_dir, model)
    if status['ready']:
        if progress:
            progress(1.0, "Using the installed local speech model.")
        return status
    helper = Path(__file__).resolve().parent.parent / "tools" / "setup_transcription.py"
    if not helper.is_file():
        raise CaptionError("The speech setup helper is missing. Reinstall ClipCanvas.")
    if progress:
        progress(0.0, "Setting up local speech recognition…")
    return _run_worker([sys.executable, "-u", str(helper), "--runtime-dir",
                        str(transcription_dir(runtime_dir)), "--model", model], progress, cancel)


def generate_from_audio(path, runtime_dir=None, model="small", language=None, progress=None, cancel=None):
    """Transcribe a local file offline. Return captions and detected language.

    This function never edits a project. Callers apply its complete successful
    result through normal undoable project edits; failures/cancellation leave
    existing captions intact. The source should already reflect timeline trims,
    speed and the selected video-sound/full-mix option.
    """
    if cancel is not None and cancel.is_set():
        raise CaptionCancelled("Caption generation cancelled.")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise CaptionError("Choose a readable local audio or video file for captions.")
    if language is not None and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}", language)):
        raise ValueError("Use a language code such as en, hi or ml, or automatic detection.")
    status = runtime_status(runtime_dir, model)
    if not status["ready"]:
        raise CaptionError("Download the local speech model in Captions first. After setup, recognition works offline.")
    environment = os.environ.copy()
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                        "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HOME": str(transcription_dir(runtime_dir) / "cache")})
    if progress:
        progress(0.0, "Loading the local speech model…")
    command = [status["python"], "-I", "-u", str(Path(__file__).resolve()), "--worker",
               str(source), status["model_path"], language or ""]
    result = _run_worker(command, progress, cancel, environment)
    if not isinstance(result, dict) or not isinstance(result.get("language"), str):
        raise CaptionError("The caption worker returned an invalid result.")
    from .model import new_caption
    try:
        entries = _entries(result.get("captions"))
    except ValueError as error:
        raise CaptionError("The caption worker returned invalid timing or text.") from error
    return {"captions": [new_caption(item["text"], item["start"], item["end"]) for item in entries],
            "language": result["language"]}


def _emit(message):
    print(json.dumps(message, ensure_ascii=False, allow_nan=False), flush=True)


def _worker():
    # Running this file directly with the private Python avoids importing GTK,
    # the app package or any system-Python transcription dependencies.
    try:
        from faster_whisper import WhisperModel
        source, model_path, language = sys.argv[2:5]
        _emit({"type": "progress", "fraction": .02, "message": "Loading the local speech model…"})
        model = WhisperModel(model_path, device="cpu", compute_type="int8",
                             cpu_threads=min(4, os.cpu_count() or 2), num_workers=1,
                             local_files_only=True)
        segments, info = model.transcribe(source, language=language or None,
                                         beam_size=5, vad_filter=True,
                                         word_timestamps=True, condition_on_previous_text=False)
        captions = []
        # Word timestamps make shorter readable cues instead of one long 30s
        # sentence. Split at punctuation, ~7 words, 52 chars or 4 seconds.
        for segment in segments:
            words = list(segment.words or [])
            if words:
                group = []
                def flush():
                    if group:
                        text = "".join(word.word for word in group).strip()
                        start, end = float(group[0].start), float(group[-1].end)
                        if text and end > start:
                            captions.append({"start": start, "end": end, "text": text})
                        group.clear()
                for word in words:
                    if group and (len(group) >= 7 or len("".join(w.word for w in group) + word.word) > 52
                                  or word.end - group[0].start > 4):
                        flush()
                    group.append(word)
                    if word.word.strip().endswith((".", "?", "!", "。", "？", "！")):
                        flush()
                flush()
            elif segment.text.strip() and segment.end > segment.start:
                captions.append({"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip()})
            _emit({"type": "progress", "fraction": min(.98, .05 + .93 * segment.end / max(info.duration, .001)),
                   "message": "Recognizing speech on this computer…"})
            if len(captions) > MAX_CAPTIONS:
                raise ValueError("This recording produces more than 5000 captions. Use a shorter video.")
        _emit({"type": "result", "result": {"captions": captions, "language": info.language}})
        return 0
    except Exception as error:
        _emit({"type": "error", "message": "Local speech recognition failed: " + str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(_worker() if len(sys.argv) >= 2 and sys.argv[1] == "--worker" else 2)
