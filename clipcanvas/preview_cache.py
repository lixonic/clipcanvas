"""Disposable, bounded video proxies for HD/Fast preview; originals supply sound."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading


MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 64
_lock = threading.Lock()


def directory():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'clipcanvas/preview-video-v1'


def _target(path, edge):
    source = Path(path).resolve();stat = source.stat()
    fingerprint = [str(source), stat.st_size, stat.st_mtime_ns, int(edge), 'h264-crf18-gop1-v1']
    key = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
    return directory()/(key+'.mp4')


def available(path, edge):
    """Never start background work from a scrub request."""
    if not edge:return None
    try:
        target = _target(path, edge)
        return str(target) if target.is_file() else None
    except OSError:
        return None


def lease_available(path, edge, scratch):
    target = available(path, edge)
    if target is None:return None
    lease = Path(scratch)/('proxy-'+Path(target).name)
    try:
        if not lease.exists():
            try:os.link(target, lease)
            except OSError:shutil.copyfile(target, lease)
        return str(lease)
    except OSError:
        return None


def _check(cancel):
    if cancel is not None and cancel.is_set():
        from .engine import RenderCancelled
        raise RenderCancelled('Preview cancelled.')


def _evict(root, keep):
    files = sorted(root.glob('*.mp4'), key=lambda p:p.stat().st_mtime_ns, reverse=True)
    size = 0
    for index, path in enumerate(files):
        size += path.stat().st_size
        if path != keep and (index >= MAX_FILES or size > MAX_BYTES):path.unlink(missing_ok=True)


def prepare(path, edge, scratch, cancel=None, status=None):
    """Build once, then lease an immutable file into the active stream workspace.

    A lease survives eviction or source changes during another preview. A cancelled
    or failed build never becomes a cache hit. Video-only proxies cannot alter the
    soundtrack, project paths, saved edits, or exported media.
    """
    from . import engine
    if not edge:return None
    metadata = engine.probe(path)
    if max(metadata['width']*metadata.get('sample_aspect_ratio',1), metadata['height']) <= edge:return None
    while not _lock.acquire(timeout=.05):_check(cancel)
    try:
        _check(cancel)
        target = _target(path, edge)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            title = 'Preparing '+('Fast' if edge<=640 else 'HD')+' preview · '+Path(path).name
            if status:status(title+'…')
            last_percent = -1
            def progress(fraction, _):
                nonlocal last_percent
                percent = int(fraction*100)
                if status and percent != last_percent:status(f'{title} · {percent}%')
                last_percent = percent
            gop = max(1, math.ceil(metadata['fps']))
            # Normalize camera rotation/SAR as the existing renderer does. Keep
            # the source frame timestamps and cadence; only spatial detail is
            # reduced. A keyframe every second makes repeat seeks inexpensive.
            filters = ('scale=trunc(iw*sar/2)*2:ih,setsar=1,'
                       f'scale={int(edge)}:{int(edge)}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1')
            with tempfile.TemporaryDirectory(prefix='.building-', dir=target.parent) as folder:
                output = Path(folder)/'video.mp4'
                arguments = ['-nostdin','-hide_banner','-loglevel','error','-y','-filter_threads','1',
                    '-threads','2','-i',str(Path(path).resolve()),'-map',f"0:{metadata['video_index']}",
                    '-an','-sn','-dn','-vf',filters,'-fps_mode','passthrough',
                    '-c:v','libx264','-preset','ultrafast','-crf','18','-pix_fmt','yuv420p',
                    '-g',str(gop),'-keyint_min',str(gop),'-sc_threshold','0','-bf','0',
                    '-threads','2','-movflags','+faststart','-progress','pipe:1',str(output)]
                engine._execute(arguments, metadata['duration'], progress, cancel, Path(folder)/'ffmpeg.log')
                _check(cancel)
                if not output.is_file() or not output.stat().st_size:
                    raise engine.RenderError('Could not prepare the lower-resolution video preview.')
                if output.stat().st_size > MAX_BYTES:
                    lease = Path(scratch)/('proxy-'+target.name)
                    shutil.move(str(output), lease)
                    return str(lease)
                os.replace(output, target)
        os.utime(target, None)
        lease = Path(scratch)/('proxy-'+target.name)
        if not lease.exists():
            try:os.link(target, lease)
            except OSError:shutil.copyfile(target, lease)
        _evict(target.parent, target)
        return str(lease)
    finally:
        _lock.release()
