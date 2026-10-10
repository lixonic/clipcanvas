#!/usr/bin/env python3
"""Explicit one-time, per-user setup for ClipCanvas local speech captions.

No sudo and no system pip: everything is installed into a private virtual
environment. This script emits JSON progress/results for the native UI.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


FASTER_WHISPER_VERSION = "1.2.1"
# Faster-whisper 1.2.1's download helper uses the 0.x Hub API. Keep that API
# stable rather than accepting a future incompatible major release.
HUGGINGFACE_HUB_VERSION = "0.36.0"
# PyAV 19 removed metadata_errors, which faster-whisper 1.2.1 still passes.
PYAV_VERSION = "16.1.0"
MODELS = ("tiny", "tiny.en", "base", "base.en", "small", "small.en")


def emit(type_, **values):
    print(json.dumps({"type": type_, **values}, ensure_ascii=False), flush=True)


def atomic_json(path, data):
    handle, temporary = tempfile.mkstemp(prefix=".setup-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_setup(root, model):
    root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({"HF_HOME": str(root / "cache" / "huggingface"),
                        "PIP_CACHE_DIR": str(root / "cache" / "pip"),
                        "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_XET": "1"})
    # Setup is the sole network-enabled path; ordinary generation sets offline.
    environment.pop("HF_HUB_OFFLINE", None)
    environment.pop("TRANSFORMERS_OFFLINE", None)
    venv = root / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        emit("progress", fraction=.02, message="Creating a private speech runtime…")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, env=environment)
    emit("progress", fraction=.08, message="Installing local speech libraries (first setup only)…")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "--only-binary=:all:", f"faster-whisper=={FASTER_WHISPER_VERSION}",
                    f"huggingface-hub=={HUGGINGFACE_HUB_VERSION}",
                    f"av=={PYAV_VERSION}"], check=True, env=environment)
    subprocess.run([str(python), "-I", "-c", "import faster_whisper; import ctranslate2; import onnxruntime"],
                   check=True, env=environment)
    atomic_json(root / "runtime.json", {"faster_whisper": FASTER_WHISPER_VERSION,
                                       "huggingface_hub": HUGGINGFACE_HUB_VERSION,
                                       "av": PYAV_VERSION,
                                       "device": "cpu", "compute_type": "int8"})
    emit("progress", fraction=.35, message=f"Downloading the {model} speech model; audio stays on this computer…")
    model_path = root / "models" / model
    model_path.mkdir(parents=True, exist_ok=True)
    worker = (
        "import sys; from faster_whisper.utils import download_model; "
        "from faster_whisper import WhisperModel; "
        "download_model(sys.argv[1], output_dir=sys.argv[2]); "
        "WhisperModel(sys.argv[2], device='cpu', compute_type='int8', local_files_only=True, cpu_threads=2)"
    )
    subprocess.run([str(python), "-I", "-u", "-c", worker, model, str(model_path)],
                   check=True, env=environment)
    atomic_json(model_path / "ready.json", {"model": model, "device": "cpu", "compute_type": "int8"})
    emit("result", result={"runtime_dir": str(root), "model": model, "model_path": str(model_path), "ready": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, help="Private runtime directory; defaults to the XDG data directory")
    parser.add_argument("--model", default="small", choices=MODELS)
    args = parser.parse_args()
    root = args.runtime_dir or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "clipcanvas" / "transcription"
    try:
        run_setup(root.expanduser().resolve(), args.model)
        return 0
    except Exception as error:
        emit("error", message="Local speech setup failed. Check the connection, available disk space and python3-venv. " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
