#!/usr/bin/env python3
"""Build ClipCanvas's Debian package and source archive without root privileges."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

PROJECT = Path(__file__).resolve().parent
VERSION = "0.1.1"
NAME = "clipcanvas"
DEPENDENCIES = [
    "python3 (>= 3.10)", "python3-gi", "python3-cairo", "python3-gi-cairo",
    "gir1.2-gtk-3.0", "gir1.2-gstreamer-1.0", "gstreamer1.0-plugins-base",
    "gstreamer1.0-plugins-good", "gstreamer1.0-libav", "ffmpeg",
]


def copy_file(source: Path, destination: Path, executable: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755 if executable else 0o644)


def build_package(output: Path) -> Path:
    if not shutil.which("dpkg-deb"):
        raise SystemExit("dpkg-deb is required. Build this package on Ubuntu or Debian.")
    if not (PROJECT / "clipcanvas" / "app.py").is_file():
        raise SystemExit("The app source is missing: clipcanvas/app.py")

    output.mkdir(parents=True, exist_ok=True)
    package_path = output / f"{NAME}_{VERSION}_all.deb"
    with tempfile.TemporaryDirectory(prefix="clipcanvas-deb-", dir=PROJECT.parent) as temporary:
        staging = Path(temporary)
        for source in sorted((PROJECT / "clipcanvas").rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            relative = source.relative_to(PROJECT)
            copy_file(source, staging / "usr/share/clipcanvas" / relative)
        copy_file(PROJECT / "packaging/clipcanvas", staging / "usr/bin/clipcanvas", executable=True)
        copy_file(PROJECT / "packaging/clipcanvas.desktop", staging / "usr/share/applications/clipcanvas.desktop")
        copy_file(PROJECT / "packaging/clipcanvas.svg", staging / "usr/share/icons/hicolor/scalable/apps/clipcanvas.svg")
        copy_file(PROJECT / "packaging/copyright", staging / "usr/share/doc/clipcanvas/copyright")
        copy_file(PROJECT / "README.md", staging / "usr/share/doc/clipcanvas/README.md")
        files = sorted(path for path in staging.rglob("*") if path.is_file())
        size_kib = sum((path.stat().st_size + 1023) // 1024 for path in files)
        control = staging / "DEBIAN"
        control.mkdir(mode=0o755)
        (control / "control").write_text(
            f"Package: {NAME}\nVersion: {VERSION}\nArchitecture: all\n"
            "Section: video\nPriority: optional\n"
            "Maintainer: ClipCanvas contributors <clipcanvas@localhost>\n"
            f"Installed-Size: {size_kib}\nDepends: {', '.join(DEPENDENCIES)}\n"
            "Description: Simple offline video creator for Ubuntu\n"
            " Create landscape, portrait and square videos from local clips.\n"
            " Arrange and trim clips, add transitions, layer soundtracks,\n"
            " and export an MP4 from a native GTK desktop application.\n",
            encoding="utf-8",
        )
        (control / "md5sums").write_text(
            "".join(f"{hashlib.md5(path.read_bytes()).hexdigest()}  {path.relative_to(staging)}\n" for path in files),
            encoding="utf-8",
        )
        for path in control.iterdir():
            path.chmod(0o644)
        for path in staging.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        staging.chmod(0o755)
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(staging), str(package_path)],
            check=True,
        )
    return package_path


def build_source(output: Path) -> Path:
    archive_path = output / f"{NAME}-{VERSION}-source.tar.gz"
    excluded = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    source_names = ["clipcanvas", "tests", "packaging", "docs", "README.md",
                    "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "build_deb.py"]
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in source_names:
            source = PROJECT / name
            if not source.exists():
                continue
            paths = [source] if source.is_file() else sorted(source.rglob("*"))
            for path in paths:
                if not path.is_file() or path.is_symlink() or excluded.intersection(path.parts) or path.suffix == ".pyc":
                    continue
                archive.add(path, arcname=f"{NAME}-{VERSION}/{path.relative_to(PROJECT)}", recursive=False)
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "dist")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    package = build_package(output)
    archive = build_source(output)
    guide = output / f"ClipCanvas-{VERSION}-Quick-Start.md"
    copy_file(PROJECT / "README.md", guide)
    checksum_path = output / f"{NAME}-{VERSION}-SHA256SUMS.txt"
    checksum_path.write_text(
        "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in (package, archive, guide)),
        encoding="utf-8",
    )
    print(f"Debian installer: {package}")
    print(f"Source archive:   {archive}")
    print(f"User guide:       {guide}")
    print(f"Checksums:        {checksum_path}")


if __name__ == "__main__":
    main()
