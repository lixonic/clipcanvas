"""Recovery snapshots and self-contained project copies. No network access."""
from __future__ import annotations

import copy
import hashlib
import os
import re
from pathlib import Path
import shutil
import tempfile
import uuid

from . import model


def recovery_directory():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "clipcanvas/recovery"


class Recovery:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else recovery_directory()
        self.path = self.directory / (uuid.uuid4().hex + ".clipcanvas")

    def write(self, project, project_path=None):
        data = copy.deepcopy(project)
        data["_recovery_project_path"] = str(Path(project_path).resolve()) if project_path else None
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        model.save_project(data, self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)

    def available(self):
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.glob("*.clipcanvas"):
            if path == self.path:
                continue
            try:
                found.append((path.stat().st_mtime, path))
            except FileNotFoundError:  # Another window may have saved/closed.
                continue
        return [path for _, path in sorted(found, reverse=True)]

    def read(self, path):
        project = model.load_project(path)
        original = project.pop("_recovery_project_path", None)
        if original is not None and (not isinstance(original, str) or not Path(original).is_absolute()):
            original = None
        return project, original


def collect_project(project, destination):
    """Create a new folder containing a project and copies of all its originals.

    Stage beside the destination, then rename only after every copy succeeds.
    Existing folders are never replaced; partial collections are removed.
    """
    model.validate_project(project, check_files=True)
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError("Choose a new folder name for the portable project.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".clipcanvas-collect-", dir=destination.parent))
    try:
        media = staging / "Media"
        media.mkdir()
        data = copy.deepcopy(project)
        data.pop("_recovery_project_path", None)
        copied = {}
        for item in model.media_items(data):
            source = Path(item["path"]).resolve()
            if source not in copied:
                # Prefix every filename, so collisions also work on case-insensitive disks.
                basename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', source.stem)[:90].rstrip(' .') or 'media'
                name = hashlib.sha256(str(source).encode()).hexdigest()[:12] + "-" + basename + source.suffix[:12]
                target = media / name
                shutil.copy2(source, target)
                copied[source] = str(target)
            item["path"] = copied[source]
        model.save_project(data, staging / "Project.clipcanvas")
        if destination.exists():
            raise ValueError("The destination folder already exists.")
        staging.rename(destination)
        return str(destination / "Project.clipcanvas")
    finally:
        if staging.exists():
            shutil.rmtree(staging)
