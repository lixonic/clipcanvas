"""Match the Erasio visible Gemini sparkle templates in source coordinates."""
from functools import lru_cache
from math import sqrt
from operator import mul
from pathlib import Path
from statistics import median
import subprocess
import tempfile


# Template, side in pixels, short edge of the source, bottom-right offsets.
TEMPLATES = {
    '720': (48, 720, (144, 120, 128, 72)),
    '2026-48': (48, 720, (144, 120, 128, 72)),
    '1080': (84, 1080, (222, 186)),
    '2026-96': (96, 1080, (222, 186)),
}
CORNER_EDGE = {720: 200, 1080: 300}
SHIFTED_THRESHOLD = 0.72


@lru_cache(maxsize=4)
def _template(name):
    path = Path(__file__).with_name('sparkle_masks') / (name + '.pgm')
    return path.read_bytes().split(b'\n', 3)[3]


def _downsample4(buf, size):
    step = size // 4
    out = [0] * (step * step)
    for cy in range(step):
        r0 = cy * 4 * size
        r1 = r0 + size
        r2 = r1 + size
        r3 = r2 + size
        row_out = cy * step
        for cx in range(step):
            x = cx * 4
            out[row_out + cx] = (
                sum(buf[r0+x:r0+x+4]) + sum(buf[r1+x:r1+x+4]) +
                sum(buf[r2+x:r2+x+4]) + sum(buf[r3+x:r3+x+4])
            ) >> 4
    return out


@lru_cache(maxsize=4)
def _template_stats(name):
    mask = _template(name)
    count = len(mask)
    total = sum(mask)
    var = count * sum(map(mul, mask, mask)) - total * total
    return mask, count, total, var


@lru_cache(maxsize=4)
def _coarse_template_stats(name):
    side = TEMPLATES[name][0]
    mask = _downsample4(_template(name), side)
    count = len(mask)
    total = sum(mask)
    var = count * sum(map(mul, mask, mask)) - total * total
    return mask, side // 4, count, total, var


def patches(value):
    """Return a list of patch dicts from None, one patch dict, or a list."""
    if not value:
        return []
    return [value] if isinstance(value, dict) else list(value)


def _offsets(patch):
    if 'offset' in patch:
        offset = patch['offset']
        return offset, offset
    return patch['offset_x'], patch['offset_y']


def _make_patch(name, offset_x, offset_y):
    if offset_x == offset_y:
        return {'template': name, 'offset': offset_x}
    return {'template': name, 'offset_x': offset_x, 'offset_y': offset_y}


def _validate_single(patch):
    if not isinstance(patch, dict):
        raise ValueError('Invalid Gemini watermark patch.')
    name = patch.get('template')
    if not isinstance(name, str) or name not in TEMPLATES:
        raise ValueError('Unknown Gemini watermark template or position.')
    side, short, _ = TEMPLATES[name]
    limit = CORNER_EDGE[short]
    keys = set(patch)
    if keys == {'template', 'offset'}:
        offset = patch['offset']
        if type(offset) is not int or not (side <= offset <= limit):
            raise ValueError('Unknown Gemini watermark template or position.')
    elif keys == {'template', 'offset_x', 'offset_y'}:
        ox, oy = patch['offset_x'], patch['offset_y']
        if type(ox) is not int or type(oy) is not int or not (side <= ox <= limit and side <= oy <= limit):
            raise ValueError('Unknown Gemini watermark template or position.')
    else:
        raise ValueError('Unknown Gemini watermark template or position.')


def validate(patch):
    if isinstance(patch, dict):
        _validate_single(patch)
    elif isinstance(patch, list) and patch:
        for item in patch:
            _validate_single(item)
    else:
        raise ValueError('Invalid Gemini watermark patch.')


def merge_patches(existing, added):
    """Combine existing and newly detected patches without duplicating positions."""
    items = []
    seen = set()
    for item in patches(existing) + patches(added):
        _validate_single(item)
        key = (item['template'], *_offsets(item))
        if key not in seen:
            seen.add(key)
            items.append(dict(item))
    if not items:
        return None
    return items[0] if len(items) == 1 else items


def _matches_size(width, height, short):
    return sorted((width, height)) == [short, {720: 1280, 1080: 1920}[short]]


def _ncc(mask, count, mask_sum, mask_var, patch):
    # Normalized correlation rejects uniform corners and ignores brightness.
    patch_sum = sum(patch)
    patch_var = count * sum(map(mul, patch, patch)) - patch_sum * patch_sum
    if patch_var <= 0 or mask_var <= 0:
        return 0
    covariance = count * sum(map(mul, mask, patch)) - mask_sum * patch_sum
    return covariance / sqrt(mask_var * patch_var)


def _crop_patch(frame, edge, side, x0, y0):
    return b''.join(frame[(y0 + r) * edge + x0:(y0 + r) * edge + x0 + side] for r in range(side))


def _crop_patch_list(frame, edge, side, x0, y0):
    patch = []
    for r in range(side):
        start = (y0 + r) * edge + x0
        patch.extend(frame[start:start + side])
    return patch


def _detect_shifted(frames, edge, templates, existing_positions):
    coarse_frames = [_downsample4(frame, edge) for frame in frames]
    c_edge = edge // 4
    if len(coarse_frames) == 3:
        c0, c1, c2 = coarse_frames
        coarse_med = [sorted((a, b, c))[1] for a, b, c in zip(c0, c1, c2)]
    else:
        coarse_med = [sum(col) // len(col) for col in zip(*coarse_frames)]
    # Average full-res frames for fast local refinement; final decision checks each frame.
    count_frames = len(frames)
    mean_frame = bytes(sum(col) // count_frames for col in zip(*frames))
    matches = []
    for name in templates:
        side = TEMPLATES[name][0]
        c_mask, c_side, c_count, c_sum, c_var = _coarse_template_stats(name)
        candidates = []
        for cy in range(c_edge - c_side + 1):
            for cx in range(c_edge - c_side + 1):
                patch = _crop_patch_list(coarse_med, c_edge, c_side, cx, cy)
                score = _ncc(c_mask, c_count, c_sum, c_var, patch)
                if score >= 0.45:
                    candidates.append((score, cx, cy))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        peaks = []
        min_gap = max(2, c_side // 2)
        for score, cx, cy in candidates:
            if all(abs(cx - px) > min_gap or abs(cy - py) > min_gap for _, px, py in peaks):
                peaks.append((score, cx, cy))
                if len(peaks) == 3:
                    break
        mask, count, mask_sum, mask_var = _template_stats(name)
        max_xy = edge - side
        for _, cx, cy in peaks:
            fx, fy = cx * 4, cy * 4
            best = (-1.0, fx, fy)
            for y0 in range(max(0, fy - 4), min(max_xy, fy + 4) + 1, 2):
                for x0 in range(max(0, fx - 4), min(max_xy, fx + 4) + 1, 2):
                    score = _ncc(mask, count, mask_sum, mask_var, _crop_patch(mean_frame, edge, side, x0, y0))
                    if score > best[0]:
                        best = (score, x0, y0)
            _, bx, by = best
            for y0 in range(max(0, by - 2), min(max_xy, by + 2) + 1):
                for x0 in range(max(0, bx - 2), min(max_xy, bx + 2) + 1):
                    score = _ncc(mask, count, mask_sum, mask_var, _crop_patch(mean_frame, edge, side, x0, y0))
                    if score > best[0]:
                        best = (score, x0, y0)
            _, x0, y0 = best
            ox, oy = edge - x0, edge - y0
            if any(abs(ox - px) <= 6 and abs(oy - py) <= 6 for px, py in existing_positions):
                continue
            values = [_ncc(mask, count, mask_sum, mask_var, _crop_patch(frame, edge, side, x0, y0))
                      for frame in frames]
            if sum(value >= SHIFTED_THRESHOLD for value in values) >= 2:
                matches.append((median(values), name, ox, oy))
    if matches:
        best_score, name, ox, oy = max(matches)
        return best_score, _make_patch(name, ox, oy)
    return None


def detect(clip):
    from . import engine
    info = engine.probe(clip['path'])
    templates = [name for name, (_, short, _) in TEMPLATES.items()
                 if _matches_size(info['width'], info['height'], short)]
    if not templates:
        return None
    short = TEMPLATES[templates[0]][1]
    edge = CORNER_EDGE[short]
    binary, env = engine._tool('ffmpeg')
    existing = patches(clip.get('sparkle_patch'))
    existing_positions = [_offsets(item) for item in existing]
    frames = []
    with tempfile.TemporaryDirectory(prefix='clipcanvas-sparkle-') as scratch:
        repair = removal_filter(existing, info, scratch) if existing else []
        vf = ','.join(repair + [f'crop={edge}:{edge}:iw-{edge}:ih-{edge}'])
        for fraction in (.1, .5, .85):
            at = clip['in'] + fraction * max(0, clip['out'] - clip['in'] - 1 / max(1, info['fps']))
            result = subprocess.run([binary, '-nostdin', '-v', 'error', '-ss', str(at),
                '-threads', '1', '-i', clip['path'], '-map', f"0:{info['video_index']}",
                '-vf', vf, '-frames:v', '1',
                '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'],
                capture_output=True, env=env, timeout=20, check=True)
            frame = result.stdout
            if len(frame) == edge * edge:
                frames.append(frame)
    if len(frames) < 2:
        return None
    scores = {}
    for name in templates:
        side, _, offsets = TEMPLATES[name]
        mask, count, mask_sum, mask_var = _template_stats(name)
        for offset in offsets:
            if any(abs(offset - px) <= 6 and abs(offset - py) <= 6 for px, py in existing_positions):
                continue
            x0 = edge - offset
            values = [_ncc(mask, count, mask_sum, mask_var, _crop_patch(frame, edge, side, x0, x0))
                      for frame in frames]
            scores[(name, offset)] = values
    matches = [(median(values), name, offset) for (name, offset), values in scores.items()
               if sum(value >= .6 for value in values) >= 2]
    if matches:
        exact_score, name, offset = max(matches)
        exact_patch = _make_patch(name, offset, offset)
        if exact_score >= 0.78:
            return exact_patch
        shifted = _detect_shifted(frames, edge, templates, existing_positions)
        if shifted and shifted[0] > exact_score + 0.08:
            return shifted[1]
        return exact_patch
    shifted = _detect_shifted(frames, edge, templates, existing_positions)
    return shifted[1] if shifted else None


def removal_filter(patch, metadata, scratch):
    """Inpaint only the template silhouette(s) before applying picture transforms."""
    validate(patch)
    width, height = metadata['width'], metadata['height']
    applicable = []
    for item in patches(patch):
        name = item['template']
        side, short, _ = TEMPLATES[name]
        if _matches_size(width, height, short):
            ox, oy = _offsets(item)
            applicable.append((name, side, ox, oy))
    if not applicable:
        return []
    key = '_'.join(sorted(f'{name}-{ox}' if ox == oy else f'{name}-{ox}x{oy}'
                          for name, _, ox, oy in applicable))
    path = Path(scratch) / f'sparkle-{width}-{height}-{key}.pgm'
    if not path.exists():
        mask = bytearray(width * height)
        for name, side, ox, oy in applicable:
            x0, y0 = width - ox, height - oy
            template = _template(name)
            for i, alpha in enumerate(template):
                if alpha >= 4:
                    y, x = divmod(i, side)
                    # Include the one-pixel fringe left by video compression.
                    for dy in (-1, 0, 1):
                        yy = y0 + y + dy
                        if 0 <= yy < height:
                            row_start = yy * width
                            for dx in (-1, 0, 1):
                                xx = x0 + x + dx
                                if 0 <= xx < width:
                                    mask[row_start + xx] = 255
        path.write_bytes(f'P5\n{width} {height}\n255\n'.encode() + mask)
    from .color_grading import filter_path
    return ['removelogo=filename=' + filter_path(path)]

