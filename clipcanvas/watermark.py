"""Match the Erasio visible Gemini sparkle templates in source coordinates."""
from functools import lru_cache
from math import sqrt
from pathlib import Path
from statistics import median
import subprocess


# Template, side in pixels, short edge of the source, bottom-right offsets.
TEMPLATES = {
    '720': (48, 720, (144, 120, 128, 72)),
    '2026-48': (48, 720, (144, 120, 128, 72)),
    '1080': (84, 1080, (222, 186)),
    '2026-96': (96, 1080, (222, 186)),
}


@lru_cache(maxsize=4)
def _template(name):
    path = Path(__file__).with_name('sparkle_masks') / (name + '.pgm')
    return path.read_bytes().split(b'\n', 3)[3]


def validate(patch):
    if not isinstance(patch, dict):
        raise ValueError('Invalid Gemini watermark patch.')
    name, offset = patch.get('template'), patch.get('offset')
    if not isinstance(name, str) or name not in TEMPLATES or type(offset) is not int or offset not in TEMPLATES[name][2]:
        raise ValueError('Unknown Gemini watermark template or position.')


def _matches_size(width, height, short):
    return sorted((width, height)) == [short, {720: 1280, 1080: 1920}[short]]


def detect(clip):
    from . import engine
    info = engine.probe(clip['path'])
    templates = [name for name, (_, short, _) in TEMPLATES.items()
                 if _matches_size(info['width'], info['height'], short)]
    if not templates:
        return None
    edge = max(max(TEMPLATES[name][2]) for name in templates)
    binary, env = engine._tool('ffmpeg')
    scores = {}
    for fraction in (.1, .5, .85):
        at = clip['in'] + fraction * max(0, clip['out'] - clip['in'] - 1/max(1, info['fps']))
        result = subprocess.run([binary, '-nostdin', '-v', 'error', '-ss', str(at),
            '-threads', '1', '-i', clip['path'], '-map', f"0:{info['video_index']}",
            '-vf', f'crop={edge}:{edge}:iw-{edge}:ih-{edge}', '-frames:v', '1',
            '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'],
            capture_output=True, env=env, timeout=20, check=True)
        frame = result.stdout
        if len(frame) != edge * edge:
            continue
        for name in templates:
            side, _, offsets = TEMPLATES[name]
            mask = _template(name)
            for offset in offsets:
                start = (edge-offset)*(edge+1)
                patch = b''.join(frame[row:row+side] for row in range(start, start+side*edge, edge))
                # Normalized correlation rejects uniform corners and ignores brightness.
                count = len(mask)
                covariance = count*sum(a*b for a,b in zip(mask,patch))-sum(mask)*sum(patch)
                variance = (count*sum(a*a for a in mask)-sum(mask)**2)*(count*sum(b*b for b in patch)-sum(patch)**2)
                scores.setdefault((name, offset), []).append(covariance/sqrt(variance) if variance else 0)
    matches = [(median(values), name, offset) for (name, offset), values in scores.items()
               if sum(value >= .6 for value in values) >= 2]
    if matches:
        _, name, offset = max(matches)
        return {'template': name, 'offset': offset}
    return None


def removal_filter(patch, metadata, scratch):
    """Inpaint only the template silhouette before applying picture transforms."""
    validate(patch)
    name, offset = patch['template'], patch['offset']
    side, short, _ = TEMPLATES[name]
    width, height = metadata['width'], metadata['height']
    if not _matches_size(width, height, short):
        return []
    path = Path(scratch) / f'sparkle-{width}-{height}-{name}-{offset}.pgm'
    if not path.exists():
        mask = bytearray(width*height)
        start = (height-offset)*width + width-offset
        template = _template(name)
        for i, alpha in enumerate(template):
            if alpha >= 4:
                y, x = divmod(i, side)
                # Include the one-pixel fringe left by video compression.
                for row in (y-1, y, y+1):
                    pos = start + row*width + x-1
                    mask[pos:pos+3] = b'\xff'*3
        path.write_bytes(f'P5\n{width} {height}\n255\n'.encode()+mask)
    from .color_grading import filter_path
    return ['removelogo=filename='+filter_path(path)]
