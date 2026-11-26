"""Portable SDR primary grading, shared by native pictures and FFmpeg LUTs."""
from functools import lru_cache
import hashlib
import math
from pathlib import Path


RANGES = {'exposure': (-3, 3), 'contrast': (.25, 2), 'gamma': (.25, 3),
          'shadows': (-1, 1), 'highlights': (-1, 1),
          'temperature': (-1, 1), 'tint': (-1, 1), 'crayon': (0, 1), 'strength': (0, 1)}
PRIMARY_KEYS = ('exposure', 'contrast', 'gamma', 'shadows', 'highlights', 'temperature', 'tint')


def defaults():
    return {'enabled': True, 'exposure': 0., 'contrast': 1., 'gamma': 1.,
            'shadows': 0., 'highlights': 0., 'temperature': 0., 'tint': 0.,
            'crayon': 0., 'strength': 1.}


PRESETS = {
    'Neutral': {},
    'Warm': {'temperature': .35, 'contrast': 1.08, 'shadows': .08},
    'Cool': {'temperature': -.35, 'contrast': 1.08},
    'Soft': {'contrast': .85, 'shadows': .15, 'highlights': -.15},
    'Punchy': {'contrast': 1.25, 'shadows': -.12, 'highlights': .08},
    'Pixar-inspired animation': {'exposure': .12, 'contrast': 1.18, 'gamma': 1.08,
        'shadows': .06, 'highlights': -.1, 'temperature': .18, 'tint': .04},
    'Crayon drawing': {'exposure': .12, 'contrast': 1.18, 'gamma': 1.08,
        'shadows': .08, 'highlights': -.1, 'temperature': .18, 'tint': .04, 'crayon': 1.},
    'Horror': {'exposure': -.35, 'contrast': 1.3, 'gamma': .88,
        'shadows': -.15, 'highlights': -.12, 'temperature': -.3, 'tint': -.16},
    'Adventure': {'exposure': .1, 'contrast': 1.24, 'gamma': 1.04,
        'shadows': -.08, 'highlights': -.08, 'temperature': .2},
    'Romantic': {'exposure': .12, 'contrast': .88, 'gamma': 1.1,
        'shadows': .12, 'highlights': -.12, 'temperature': .28, 'tint': .12},
    'Mystery': {'exposure': -.2, 'contrast': 1.22, 'gamma': .94,
        'shadows': -.1, 'highlights': -.15, 'temperature': -.24, 'tint': .1},
}

PRESET_SATURATION = {'Pixar-inspired animation': 1.35, 'Crayon drawing': 1.35,
                     'Horror': .65, 'Adventure': 1.18, 'Romantic': .93, 'Mystery': .78}
PRESET_DESCRIPTIONS = {
    'Pixar-inspired animation': 'Bright, rich color with warm midtones and gentle highlights.',
    'Crayon drawing': 'Bright, Pixar-inspired crayon colors, luminous shading, sketchy outlines and textured paper.',
    'Horror': 'Dark, desaturated color with cold, slightly green shadows.',
    'Adventure': 'Crisp contrast, warm sunlight and vivid color.',
    'Romantic': 'Soft contrast, lifted shadows and warm rosy color.',
    'Mystery': 'Cool, muted color with deeper shadows and restrained highlights.',
}


def validate(value):
    if not isinstance(value, dict) or set(value)-set(defaults()):
        raise ValueError('Color grading settings are invalid.')
    if not isinstance(value.get('enabled', True), bool):
        raise ValueError('Color grading must be enabled or bypassed.')
    for key, (low, high) in RANGES.items():
        number = value.get(key, defaults()[key])
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not low <= number <= high):
            raise ValueError(f'Color grading {key} must be between {low:g} and {high:g}.')


def settings(clip):
    return {**defaults(), **clip.get('color_grade', {})}


def saturation(clip):
    grade = settings(clip)
    return 1+(clip.get('saturation', 1)-1)*grade['strength'] if grade['enabled'] else 1


def signature(clip):
    return (*settings(clip).values(), clip.get('saturation', 1))


def crayon_amount(clip):
    grade = settings(clip)
    return grade['crayon'] * grade['strength'] if grade['enabled'] else 0.


def crayon_active(clip):
    return crayon_amount(clip) > 0


def rgb_active(clip):
    value = settings(clip)
    return value['enabled'] and value['strength'] > 0 and any(value[key] != defaults()[key] for key in PRIMARY_KEYS)


def active(clip):
    return rgb_active(clip) or crayon_active(clip) or saturation(clip) != 1


def _channel(value, channel, grade):
    x = min(1, max(0, value * 2**grade['exposure']))
    x = min(1, max(0, (x-.5)*grade['contrast']+.5))**(1/grade['gamma'])
    x += .3*grade['shadows']*(1-x)**2 + .3*grade['highlights']*x*x
    temperature, tint = grade['temperature'], grade['tint']
    x += (.12*temperature+.06*tint, -.12*tint, -.12*temperature+.06*tint)[channel]
    x = value+(min(1, max(0, x))-value)*grade['strength']
    return min(255, max(0, math.floor(x*255+.5)))


@lru_cache(maxsize=64)
def _tables(values):
    grade = dict(zip(defaults(), values))
    return tuple(bytes(_channel(n/255, channel, grade) for n in range(256)) for channel in range(3))


def tables(clip):
    return _tables(tuple(settings(clip).values()))


def _lut_file(clip, scratch):
    lookup = tables(clip)
    path = Path(scratch)/('grade-'+hashlib.sha256(b''.join(lookup)).hexdigest()+'.cube')
    if not path.exists():
        # lut1d truncates float outputs to integer pixels. Stay inside each
        # 8-bit bin so float roundoff cannot turn an exact byte into byte-1.
        rows = [' '.join(f'{min(1, (channel[n]+.25)/255):.12f}' for channel in lookup)
                for n in range(256)]
        path.write_text('LUT_1D_SIZE 256\n'+'\n'.join(rows)+'\n', encoding='ascii')
    return filter_path(path)


def filter_path(path):
    # Escape once for filter options, then again for the filtergraph parser.
    value = ''.join('\\'+c if c in "\\':" else c for c in str(path.resolve()))
    return ''.join('\\'+c if c in "\\'[],;" else c for c in value)


_CRAYON_STROKE_KERNEL = (
    '0 0 0 1 2 '
    '0 0 2 3 1 '
    '0 2 4 2 0 '
    '1 3 2 0 0 '
    '2 1 0 0 0'
)
_CRAYON_STROKE_CONV = (
    f'convolution={_CRAYON_STROKE_KERNEL}:{_CRAYON_STROKE_KERNEL}:'
    f'{_CRAYON_STROKE_KERNEL}:{_CRAYON_STROKE_KERNEL}:1/24:1/24:1/24:1/24'
)


def _crayon_filter(amount, prefix):
    """Stylize a YUV420P stream as bright, Pixar-inspired wax crayon art on paper.

    Runs directly in YUV420P with Y-plane Sobel outlines and Y-plane paper grain
    (preserving vivid color-tinted contours and running ~4-8x faster on single-
    threaded live preview). Paper tooth and diagonal wax strokes are synthesized
    once on frame 0 at bounded resolution (<=480x270) via a thread-safe 2D hash
    and reused across every frame with eof_action=repeat.
    """
    p = prefix
    blended = amount < 1 - 1e-6
    head = f'format=yuv420p,split=3[{p}orig][{p}main][{p}tex];' if blended else f'format=yuv420p,split=2[{p}main][{p}tex];'
    hash1 = 'sin(X*12.9898+Y*78.233)*43758.5453'
    hash2 = 'sin(X*39.346+Y*11.135)*24634.6345'
    body = (
        f'[{p}main]bilateral=sigmaS=5:sigmaR=0.16,split=2[{p}base][{p}edge];'
        f"[{p}base]lutyuv=y='clip(16+0.93*val+8*sin(val*0.0123),16,248)':"
        f"u='clip(128+(val-128)*1.14,16,240)':v='clip(128+(val-128)*1.14,16,240)'[{p}waxy];"
        f"[{p}edge]sobel=planes=1,lutyuv=y='clip(255-max(0,val-22)*1.55,82,255)':u=128:v=128[{p}outline];"
        f'[{p}waxy][{p}outline]blend=c0_mode=multiply:c1_mode=normal:c1_opacity=1:c2_mode=normal:c2_opacity=1[{p}outlined];'
        f'[{p}tex]trim=end_frame=1,split=2[{p}tref][{p}tgen];'
        f"[{p}tgen]scale='min(iw,480)':'min(ih,270)':flags=fast_bilinear,format=gray,"
        f"geq=lum='128+86*(2*({hash1}-floor({hash1}))-1)',{_CRAYON_STROKE_CONV},"
        f"geq=lum='clip(lum(X,Y)+34*(2*({hash2}-floor({hash2}))-1),0,255)',"
        f"lutyuv=y='clip(130+(val-128)*1.95,28,255)'[{p}tlow];"
        f'[{p}tlow][{p}tref]scale2ref=flags=bilinear[{p}tscaled][{p}tdrop];'
        f'[{p}tdrop]nullsink;'
        f'[{p}tscaled]format=yuv420p[{p}paper];'
    )
    if blended:
        tail = (
            f'[{p}outlined][{p}paper]blend=c0_mode=overlay:c0_opacity=0.46:c1_mode=normal:c1_opacity=1:c2_mode=normal:c2_opacity=1:eof_action=repeat[{p}done];'
            f'[{p}done][{p}orig]blend=all_mode=normal:all_opacity={amount:.12g}'
        )
    else:
        tail = f'[{p}outlined][{p}paper]blend=c0_mode=overlay:c0_opacity=0.46:c1_mode=normal:c1_opacity=1:c2_mode=normal:c2_opacity=1:eof_action=repeat'
    return head + body + tail


def filters(clip, scratch=None, prefix='cg_'):
    """Load precomputed byte tables instead of evaluating a large expression
    for every clip at FFmpeg startup. Expression fallback supports graph-only
    callers without a workspace; playback and export always supply one.
    """
    result = []
    if rgb_active(clip) and scratch is not None:
        result += ['format=rgb24', 'lut1d=file='+_lut_file(clip, scratch)+':interp=nearest', 'format=yuv420p']
    elif rgb_active(clip):
        grade = settings(clip)
        x = f"clip(val/255*{2**grade['exposure']:.12g},0,1)"
        x = f"pow(clip(({x}-.5)*{grade['contrast']:.12g}+.5,0,1),{1/grade['gamma']:.12g})"
        tone = f"({x}+.3*{grade['shadows']:.12g}*pow(1-{x},2)+.3*{grade['highlights']:.12g}*pow({x},2))"
        t, i = grade['temperature'], grade['tint']
        offsets = (.12*t+.06*i, -.12*i, -.12*t+.06*i)
        expressions = [f"{channel}='clip(floor(val+(clip(255*({tone}+{offset:.12g}),0,255)-val)*{grade['strength']:.12g}+.5),0,255)'"
                       for channel, offset in zip('rgb', offsets)]
        result += ['format=rgb24', 'lutrgb='+':'.join(expressions), 'format=yuv420p']
    crayon = crayon_amount(clip)
    if crayon > 0:
        result.append(_crayon_filter(crayon, prefix))
    level = saturation(clip)
    if level != 1:
        result.append(f'hue=s={level:.12g}')
    return result
