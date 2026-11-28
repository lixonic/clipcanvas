"""Match the Erasio visible Gemini sparkle templates in source coordinates."""
from functools import lru_cache
from math import isfinite, sqrt
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
TRANSIENT_SEED_THRESHOLD = 0.76
TRANSIENT_MIN_CONTRAST = 12.0
TRACK_LOCK_THRESHOLD = 0.68
TRACK_LOCK_CONTRAST = 10.0
TRACK_FADE_THRESHOLD = 0.35
TRACK_FADE_CONTRAST = 6.0


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
def _template_regions(name):
    mask = _template(name)
    hi = max(mask) // 2
    core = tuple(i for i, v in enumerate(mask) if v >= hi)
    ring = tuple(i for i, v in enumerate(mask) if v == 0)
    return core, ring


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


def _is_time(value):
    return type(value) in (int, float) and isfinite(value)


def _validate_single(patch):
    if not isinstance(patch, dict):
        raise ValueError('Invalid Gemini watermark patch.')
    name = patch.get('template')
    if not isinstance(name, str) or name not in TEMPLATES:
        raise ValueError('Unknown Gemini watermark template or position.')
    side, short, _ = TEMPLATES[name]
    limit = CORNER_EDGE[short]
    keys = set(patch)
    if keys == {'template', 'segments'}:
        segments = patch['segments']
        if not isinstance(segments, list) or not segments:
            raise ValueError('Unknown Gemini watermark template or position.')
        prev_start = -1.0
        for seg in segments:
            if not isinstance(seg, (list, tuple)) or len(seg) != 4:
                raise ValueError('Unknown Gemini watermark template or position.')
            start, end, ox, oy = seg
            if (not _is_time(start) or not _is_time(end) or start < 0 or end <= start or
                    start < prev_start - 1e-6 or
                    type(ox) is not int or type(oy) is not int or
                    not (side <= ox <= limit and side <= oy <= limit)):
                raise ValueError('Unknown Gemini watermark template or position.')
            prev_start = float(start)
        return
    timed = 'start' in keys or 'end' in keys
    if timed:
        if 'start' not in keys or 'end' not in keys:
            raise ValueError('Unknown Gemini watermark template or position.')
        start, end = patch['start'], patch['end']
        if not _is_time(start) or not _is_time(end) or start < 0 or end <= start:
            raise ValueError('Unknown Gemini watermark template or position.')
        pos_keys = keys - {'start', 'end'}
    else:
        pos_keys = keys
    if pos_keys == {'template', 'offset'}:
        offset = patch['offset']
        if type(offset) is not int or not (side <= offset <= limit):
            raise ValueError('Unknown Gemini watermark template or position.')
    elif pos_keys == {'template', 'offset_x', 'offset_y'}:
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


def _patch_key(item):
    if 'segments' in item:
        return (item['template'], tuple(
            (round(float(s), 3), round(float(e), 3), ox, oy)
            for s, e, ox, oy in item['segments']
        ))
    if 'start' in item:
        return (item['template'], *_offsets(item),
                round(float(item['start']), 3), round(float(item['end']), 3))
    return (item['template'], *_offsets(item))


def _copy_patch(item):
    copied = dict(item)
    if 'segments' in copied:
        copied['segments'] = [list(seg) for seg in copied['segments']]
    return copied


def merge_patches(existing, added):
    """Combine existing and newly detected patches without duplicating positions."""
    items = []
    seen = set()
    for item in patches(existing) + patches(added):
        _validate_single(item)
        key = _patch_key(item)
        if key not in seen:
            seen.add(key)
            items.append(_copy_patch(item))
    if not items:
        return None
    return items[0] if len(items) == 1 else items


def _existing_positions_at(existing, at):
    positions = []
    for item in existing:
        if 'segments' in item:
            for start, end, ox, oy in item['segments']:
                if start - 1e-3 <= at <= end + 1e-3:
                    positions.append((ox, oy))
        elif 'start' in item:
            if item['start'] - 1e-3 <= at <= item['end'] + 1e-3:
                positions.append(_offsets(item))
        else:
            positions.append(_offsets(item))
    return positions


def _static_existing_positions(existing):
    return [_offsets(item) for item in existing if 'segments' not in item and 'start' not in item]


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


def _contrast(patch, core, ring):
    return sum(patch[i] for i in core) / len(core) - sum(patch[i] for i in ring) / len(ring)


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
                matches.append((median(values), name, ox, oy, values))
    if matches:
        best_score, name, ox, oy, values = max(matches)
        return best_score, name, ox, oy, values
    return None


def _find_in_frame(frame, edge, templates, existing_positions):
    coarse = _downsample4(frame, edge)
    c_edge = edge // 4
    matches = []
    for name in templates:
        side = TEMPLATES[name][0]
        c_mask, c_side, c_count, c_sum, c_var = _coarse_template_stats(name)
        candidates = []
        for cy in range(c_edge - c_side + 1):
            for cx in range(c_edge - c_side + 1):
                score = _ncc(c_mask, c_count, c_sum, c_var,
                             _crop_patch_list(coarse, c_edge, c_side, cx, cy))
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
        core, ring = _template_regions(name)
        max_xy = edge - side
        for _, cx, cy in peaks:
            fx, fy = cx * 4, cy * 4
            best = (-1.0, fx, fy)
            for y0 in range(max(0, fy - 4), min(max_xy, fy + 4) + 1, 2):
                for x0 in range(max(0, fx - 4), min(max_xy, fx + 4) + 1, 2):
                    score = _ncc(mask, count, mask_sum, mask_var, _crop_patch(frame, edge, side, x0, y0))
                    if score > best[0]:
                        best = (score, x0, y0)
            _, bx, by = best
            for y0 in range(max(0, by - 2), min(max_xy, by + 2) + 1):
                for x0 in range(max(0, bx - 2), min(max_xy, bx + 2) + 1):
                    score = _ncc(mask, count, mask_sum, mask_var, _crop_patch(frame, edge, side, x0, y0))
                    if score > best[0]:
                        best = (score, x0, y0)
            score, x0, y0 = best
            ox, oy = edge - x0, edge - y0
            if any(abs(ox - px) <= 6 and abs(oy - py) <= 6 for px, py in existing_positions):
                continue
            if score >= TRANSIENT_SEED_THRESHOLD:
                patch = _crop_patch(frame, edge, side, x0, y0)
                if _contrast(patch, core, ring) >= TRANSIENT_MIN_CONTRAST:
                    matches.append((score, name, ox, oy))
    return max(matches) if matches else None


def _track_frame(frame, edge, side, stats, regions, cx, cy, locked):
    mask, count, mask_sum, mask_var = stats
    core, ring = regions
    max_xy = edge - side

    def evaluate(x0, y0):
        patch = _crop_patch(frame, edge, side, x0, y0)
        return _ncc(mask, count, mask_sum, mask_var, patch), patch

    if locked:
        best_score, best_patch = evaluate(cx, cy)
        bx, by = cx, cy
        # Fast hill-climb within +/-2px; most camera pans move <= 1px/frame.
        for _ in range(2):
            improved = False
            for dy in (-1, 0, 1):
                ny = by + dy
                if not (0 <= ny <= max_xy) or abs(ny - cy) > 2:
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = bx + dx
                    if not (0 <= nx <= max_xy) or abs(nx - cx) > 2:
                        continue
                    score, patch = evaluate(nx, ny)
                    if score > best_score:
                        best_score, best_patch = score, patch
                        bx, by = nx, ny
                        improved = True
            if not improved:
                break
        if best_score < 0.72:
            for dy in range(-4, 5, 2):
                ny = cy + dy
                if not (0 <= ny <= max_xy):
                    continue
                for dx in range(-4, 5, 2):
                    nx = cx + dx
                    if not (0 <= nx <= max_xy):
                        continue
                    score, patch = evaluate(nx, ny)
                    if score > best_score:
                        best_score, best_patch = score, patch
                        bx, by = nx, ny
            rx, ry = bx, by
            for dy in (-1, 0, 1):
                ny = ry + dy
                if not (0 <= ny <= max_xy):
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = rx + dx
                    if not (0 <= nx <= max_xy):
                        continue
                    score, patch = evaluate(nx, ny)
                    if score > best_score:
                        best_score, best_patch = score, patch
                        bx, by = nx, ny
        if best_score >= TRACK_LOCK_THRESHOLD and _contrast(best_patch, core, ring) >= TRACK_LOCK_CONTRAST:
            return 'lock', bx, by
        # During fade-out, stay anchored at the locked position to avoid drifting onto texture.
        fade_score, fade_patch = evaluate(cx, cy)
        for dy in (-1, 0, 1):
            ny = cy + dy
            if not (0 <= ny <= max_xy):
                continue
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx = cx + dx
                if not (0 <= nx <= max_xy):
                    continue
                score, patch = evaluate(nx, ny)
                if score > fade_score:
                    fade_score, fade_patch = score, patch
        if fade_score >= TRACK_FADE_THRESHOLD and _contrast(fade_patch, core, ring) >= TRACK_FADE_CONTRAST:
            return 'fade', cx, cy
        return 'miss', cx, cy

    best_score, best_patch = evaluate(cx, cy)
    bx, by = cx, cy
    for dy in (-1, 0, 1):
        ny = cy + dy
        if not (0 <= ny <= max_xy):
            continue
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = cx + dx
            if not (0 <= nx <= max_xy):
                continue
            score, patch = evaluate(nx, ny)
            if score > best_score:
                best_score, best_patch = score, patch
                bx, by = nx, ny
    contrast = _contrast(best_patch, core, ring)
    if best_score >= TRACK_LOCK_THRESHOLD and contrast >= TRACK_LOCK_CONTRAST:
        return 'lock', bx, by
    if best_score >= TRACK_FADE_THRESHOLD and contrast >= TRACK_FADE_CONTRAST:
        return 'fade', cx, cy
    return 'miss', cx, cy


def _quantize_track(points, clip_in, clip_out, frame_dur):
    """Group frame-level (t, ox, oy) points into segments with <= 1px position error."""
    segments = []
    bucket = [points[0]]
    min_ox = max_ox = points[0][1]
    min_oy = max_oy = points[0][2]
    for point in points[1:]:
        _, ox, oy = point
        new_min_ox, new_max_ox = min(min_ox, ox), max(max_ox, ox)
        new_min_oy, new_max_oy = min(min_oy, oy), max(max_oy, oy)
        if new_max_ox - new_min_ox > 2 or new_max_oy - new_min_oy > 2:
            segments.append((bucket[0][0], bucket[-1][0],
                             (min_ox + max_ox) // 2, (min_oy + max_oy) // 2))
            bucket = [point]
            min_ox = max_ox = ox
            min_oy = max_oy = oy
        else:
            bucket.append(point)
            min_ox, max_ox = new_min_ox, new_max_ox
            min_oy, max_oy = new_min_oy, new_max_oy
    segments.append((bucket[0][0], bucket[-1][0],
                     (min_ox + max_ox) // 2, (min_oy + max_oy) // 2))

    # Convert sample times to contiguous half-frame intervals so PTS rounding never misses.
    result = []
    for index, (start_t, end_t, ox, oy) in enumerate(segments):
        if index == 0:
            s0 = clip_in if start_t <= clip_in + 0.5 * frame_dur else max(clip_in, start_t - 0.5 * frame_dur)
        else:
            s0 = (segments[index - 1][1] + start_t) / 2.0
        if index == len(segments) - 1:
            s1 = clip_out if end_t >= clip_out - 1.5 * frame_dur else min(clip_out, end_t + 0.5 * frame_dur)
        else:
            s1 = (end_t + segments[index + 1][0]) / 2.0
        if s1 > s0:
            result.append((s0, s1, ox, oy))
    return result


def _make_tracked_patch(name, segments, clip_in, clip_out):
    if len(segments) == 1:
        s0, s1, ox, oy = segments[0]
        patch = _make_patch(name, ox, oy)
        if s0 > clip_in + 0.15 or s1 < clip_out - 0.15:
            patch['start'] = round(s0, 4)
            patch['end'] = round(s1, 4)
        return patch
    return {
        'template': name,
        'segments': [[round(s0, 4), round(s1, 4), ox, oy] for s0, s1, ox, oy in segments],
    }


def _decode_corner_frame(binary, env, clip, info, existing, scratch, edge, at):
    fps = max(1.0, float(info['fps']))
    repair = removal_filter(existing, info, scratch, source_start=at, source_end=at + 1.0 / fps) if existing else []
    vf = ','.join(repair + [f'crop={edge}:{edge}:iw-{edge}:ih-{edge}'])
    result = subprocess.run([
        binary, '-nostdin', '-v', 'error', '-ss', str(at),
        '-threads', '1', '-i', clip['path'], '-map', f"0:{info['video_index']}",
        '-vf', vf, '-frames:v', '1',
        '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'
    ], capture_output=True, env=env, timeout=20, check=True)
    return result.stdout if len(result.stdout) == edge * edge else None


def _track_sparkle(binary, env, clip, info, existing, scratch, edge, seed_t, name, seed_ox, seed_oy):
    fps = max(1.0, float(info['fps']))
    frame_dur = 1.0 / fps
    clip_in = float(clip.get('in', 0.0))
    clip_out = float(clip['out'])
    side = TEMPLATES[name][0]
    stats = _template_stats(name)
    regions = _template_regions(name)
    frame_size = edge * edge
    max_gap = max(6, round(0.25 * fps))

    # Forward streaming pass from seed_t to clip_out (O(1) memory, stops early on disappearance).
    forward = []
    forward_fade_tail = 0
    span = max(frame_dur, clip_out - seed_t)
    repair = removal_filter(existing, info, scratch, source_start=seed_t, source_end=clip_out) if existing else []
    vf = ','.join(repair + [f'crop={edge}:{edge}:iw-{edge}:ih-{edge}'])
    proc = subprocess.Popen([
        binary, '-nostdin', '-v', 'error', '-ss', str(seed_t), '-t', str(span),
        '-threads', '1', '-i', clip['path'], '-map', f"0:{info['video_index']}",
        '-vf', vf, '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        cx, cy = edge - seed_ox, edge - seed_oy
        locked = True
        misses = 0
        pending = []
        frame_index = 0
        while True:
            buf = proc.stdout.read(frame_size)
            if len(buf) < frame_size:
                break
            at = seed_t + frame_index * frame_dur
            frame_index += 1
            state, nx, ny = _track_frame(buf, edge, side, stats, regions, cx, cy, locked)
            if state == 'lock':
                cx, cy = nx, ny
                locked = True
                if pending:
                    forward.extend(pending)
                    pending.clear()
                forward.append((at, edge - cx, edge - cy))
                misses = 0
                forward_fade_tail = 0
            elif state == 'fade':
                locked = False
                if pending:
                    forward.extend(pending)
                    pending.clear()
                forward.append((at, edge - cx, edge - cy))
                misses = 0
                forward_fade_tail += 1
            else:
                if not forward:
                    # Seed frame itself must always be covered.
                    forward.append((at, edge - cx, edge - cy))
                    misses = 1
                else:
                    misses += 1
                    pending.append((at, edge - cx, edge - cy))
                if misses >= max_gap:
                    break
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    if not forward:
        forward.append((seed_t, seed_ox, seed_oy))
    if forward_fade_tail >= 2:
        # Gradual AI dissolve leaves a faint remnant for a few frames past the fade threshold.
        pad = min(round(0.25 * fps), forward_fade_tail + 2)
        last_t, last_ox, last_oy = forward[-1]
        for step in range(1, pad + 1):
            t_pad = last_t + step * frame_dur
            if t_pad <= clip_out:
                forward.append((t_pad, last_ox, last_oy))

    # Backward chunked pass only when the seed wasn't already at clip_in.
    backward = []
    if seed_t > clip_in + 0.5 * frame_dur:
        cx, cy = edge - seed_ox, edge - seed_oy
        locked = True
        misses = 0
        pending = []
        backward_fade_tail = 0
        chunk_end = seed_t
        while chunk_end > clip_in + 1e-4 and misses < max_gap:
            chunk_start = max(clip_in, chunk_end - 5.0)
            chunk_repair = (removal_filter(existing, info, scratch, source_start=chunk_start, source_end=chunk_end)
                            if existing else [])
            chunk_vf = ','.join(chunk_repair + [f'crop={edge}:{edge}:iw-{edge}:ih-{edge}'])
            result = subprocess.run([
                binary, '-nostdin', '-v', 'error', '-ss', str(chunk_start), '-t', str(chunk_end - chunk_start),
                '-threads', '1', '-i', clip['path'], '-map', f"0:{info['video_index']}",
                '-vf', chunk_vf, '-threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'
            ], capture_output=True, env=env, timeout=30, check=True)
            data = result.stdout
            count_frames = len(data) // frame_size
            if count_frames == 0:
                break
            for idx in range(count_frames - 1, -1, -1):
                buf = data[idx * frame_size:(idx + 1) * frame_size]
                at = chunk_start + idx * frame_dur
                if at >= seed_t - 0.5 * frame_dur:
                    continue
                state, nx, ny = _track_frame(buf, edge, side, stats, regions, cx, cy, locked)
                if state == 'lock':
                    cx, cy = nx, ny
                    locked = True
                    if pending:
                        backward.extend(pending)
                        pending.clear()
                    backward.append((at, edge - cx, edge - cy))
                    misses = 0
                    backward_fade_tail = 0
                elif state == 'fade':
                    locked = False
                    if pending:
                        backward.extend(pending)
                        pending.clear()
                    backward.append((at, edge - cx, edge - cy))
                    misses = 0
                    backward_fade_tail += 1
                else:
                    misses += 1
                    pending.append((at, edge - cx, edge - cy))
                    if misses >= max_gap:
                        break
            chunk_end = chunk_start
        if backward and backward_fade_tail >= 2:
            pad = min(round(0.25 * fps), backward_fade_tail + 2)
            first_t, first_ox, first_oy = backward[-1]
            for step in range(1, pad + 1):
                t_pad = first_t - step * frame_dur
                if t_pad >= clip_in:
                    backward.append((t_pad, first_ox, first_oy))
        backward.reverse()

    points = backward + forward
    segments = _quantize_track(points, clip_in, clip_out, frame_dur)
    return _make_tracked_patch(name, segments, clip_in, clip_out)


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
    static_existing = _static_existing_positions(existing)
    clip_in = float(clip.get('in', 0.0))
    clip_out = float(clip['out'])
    fps = max(1.0, float(info['fps']))
    sample_times = [
        clip_in + fraction * max(0.0, clip_out - clip_in - 1.0 / fps)
        for fraction in (.1, .5, .85)
    ]
    samples = []
    with tempfile.TemporaryDirectory(prefix='clipcanvas-sparkle-') as scratch:
        for at in sample_times:
            frame = _decode_corner_frame(binary, env, clip, info, existing, scratch, edge, at)
            if frame is not None:
                samples.append((at, frame))
        if not samples:
            return None
        frames = [frame for _, frame in samples]
        if len(frames) >= 2:
            scores = {}
            for name in templates:
                side, _, offsets = TEMPLATES[name]
                mask, count, mask_sum, mask_var = _template_stats(name)
                for offset in offsets:
                    if any(abs(offset - px) <= 6 and abs(offset - py) <= 6 for px, py in static_existing):
                        continue
                    x0 = edge - offset
                    values = [_ncc(mask, count, mask_sum, mask_var, _crop_patch(frame, edge, side, x0, x0))
                              for frame in frames]
                    scores[(name, offset)] = values
            matches = [(median(values), name, offset, values)
                       for (name, offset), values in scores.items()
                       if sum(value >= .6 for value in values) >= 2]
            static_match = None
            if matches:
                exact_score, name, offset, values = max(matches)
                static_match = (exact_score, name, offset, offset, values)
                if exact_score < 0.78:
                    shifted = _detect_shifted(frames, edge, templates, static_existing)
                    if shifted and shifted[0] > exact_score + 0.08:
                        static_match = shifted
            else:
                shifted = _detect_shifted(frames, edge, templates, static_existing)
                if shifted:
                    static_match = shifted
            if static_match:
                _, name, ox, oy, values = static_match
                if min(values) >= 0.45:
                    return _make_patch(name, ox, oy)
                # One sample missed the sparkle: track it to see whether it vanished partway through.
                best_idx = max(range(len(values)), key=values.__getitem__)
                return _track_sparkle(binary, env, clip, info, existing, scratch, edge,
                                      samples[best_idx][0], name, ox, oy)

        # Fallback: detect transient (disappearing) or drifting sparkles starting from frame 0 or sampled frames.
        probe_list = []
        if clip_in < samples[0][0] - 0.5 / fps:
            first_frame = _decode_corner_frame(binary, env, clip, info, existing, scratch, edge, clip_in)
            if first_frame is not None:
                probe_list.append((clip_in, first_frame))
        probe_list.extend(samples)
        for at, frame in probe_list:
            seed = _find_in_frame(frame, edge, templates, _existing_positions_at(existing, at))
            if seed:
                _, name, ox, oy = seed
                return _track_sparkle(binary, env, clip, info, existing, scratch, edge,
                                      at, name, ox, oy)
        return None


def _write_mask(path, width, height, entries, dilation):
    if path.exists():
        return
    mask = bytearray(width * height)
    deltas = range(-dilation, dilation + 1)
    for name, side, ox, oy in entries:
        x0, y0 = width - ox, height - oy
        template = _template(name)
        for i, alpha in enumerate(template):
            if alpha >= 4:
                y, x = divmod(i, side)
                for dy in deltas:
                    yy = y0 + y + dy
                    if 0 <= yy < height:
                        row_start = yy * width
                        for dx in deltas:
                            xx = x0 + x + dx
                            if 0 <= xx < width:
                                mask[row_start + xx] = 255
    path.write_bytes(f'P5\n{width} {height}\n255\n'.encode() + mask)


def removal_filter(patch, metadata, scratch, source_start=0.0, source_end=None):
    """Inpaint only the template silhouette(s) before applying picture transforms."""
    validate(patch)
    width, height = metadata['width'], metadata['height']
    static_entries = []
    timed_segments = []
    for item in patches(patch):
        name = item['template']
        side, short, _ = TEMPLATES[name]
        if not _matches_size(width, height, short):
            continue
        if 'segments' in item:
            for s0, s1, ox, oy in item['segments']:
                timed_segments.append((name, side, ox, oy, float(s0), float(s1), 2))
        elif 'start' in item:
            ox, oy = _offsets(item)
            timed_segments.append((name, side, ox, oy, float(item['start']), float(item['end']), 1))
        else:
            ox, oy = _offsets(item)
            static_entries.append((name, side, ox, oy))

    from .color_grading import filter_path
    filters = []
    if static_entries:
        key = '_'.join(sorted(f'{name}-{ox}' if ox == oy else f'{name}-{ox}x{oy}'
                              for name, _, ox, oy in static_entries))
        path = Path(scratch) / f'sparkle-{width}-{height}-{key}.pgm'
        _write_mask(path, width, height, static_entries, dilation=1)
        filters.append('removelogo=filename=' + filter_path(path))

    for name, side, ox, oy, s0, s1, dilation in timed_segments:
        if source_end is not None:
            if s1 <= source_start + 1e-4 or s0 >= source_end - 1e-4:
                continue
        elif s1 <= source_start + 1e-4:
            continue
        rel_start = s0 - source_start
        rel_end = s1 - source_start
        covers_start = rel_start <= 1e-4
        covers_end = source_end is not None and s1 >= source_end - 1e-4
        pos_key = f'{name}-{ox}' if ox == oy else f'{name}-{ox}x{oy}'
        path = Path(scratch) / f'sparkle-{width}-{height}-{pos_key}-d{dilation}.pgm'
        _write_mask(path, width, height, [(name, side, ox, oy)], dilation=dilation)
        spec = 'removelogo=filename=' + filter_path(path)
        if covers_start and covers_end:
            filters.append(spec)
        elif covers_start:
            filters.append(f"{spec}:enable='lt(t,{rel_end:.4f})'")
        elif covers_end:
            filters.append(f"{spec}:enable='gte(t,{rel_start:.4f})'")
        else:
            filters.append(f"{spec}:enable='gte(t,{rel_start:.4f})*lt(t,{rel_end:.4f})'")
    return filters


def validate_region(region):
    """Validate normalized left, top, right and bottom source coordinates."""
    if (not isinstance(region, (list, tuple)) or len(region) != 4 or
            any(type(v) not in (int, float) or not 0 <= v <= 1 for v in region) or
            region[0] >= region[2] or region[1] >= region[3]):
        raise ValueError('Select a rectangular watermark region inside the source frame.')


def region_filters(region, metadata):
    """Interpolate from the edges of the marked source rectangle."""
    from math import floor, ceil
    validate_region(region)
    width, height = metadata['width'], metadata['height']
    left, top, right, bottom = region
    x, y = floor(left * width), floor(top * height)
    w, h = ceil(right * width) - x, ceil(bottom * height) - y
    # delogo needs a sampling border. Extend edge pixels for marks that
    # touch the frame, then restore the exact input dimensions.
    border = x < 2 or y < 2 or x+w > width-2 or y+h > height-2
    offset = 2 if border else 0
    repair = f'delogo=x={x+offset}:y={y+offset}:w={w}:h={h}'
    if border:
        return ['pad=iw+4:ih+4:2:2', 'fillborders=2:2:2:2:smear', repair,
                f'crop={width}:{height}:2:2:exact=1']
    return [repair]


def active(clip):
    return bool(clip.get('sparkle_patch') or clip.get('watermark_region'))
