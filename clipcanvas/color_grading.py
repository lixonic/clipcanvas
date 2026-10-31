"""Portable SDR primary grading, shared by native pictures and FFmpeg LUTs."""
from functools import lru_cache
import math


RANGES = {'exposure': (-3, 3), 'contrast': (.25, 2), 'gamma': (.25, 3),
          'shadows': (-1, 1), 'highlights': (-1, 1),
          'temperature': (-1, 1), 'tint': (-1, 1), 'strength': (0, 1)}


def defaults():
    return {'enabled': True, 'exposure': 0., 'contrast': 1., 'gamma': 1.,
            'shadows': 0., 'highlights': 0., 'temperature': 0., 'tint': 0., 'strength': 1.}


PRESETS = {
    'Neutral': {},
    'Warm': {'temperature': .35, 'contrast': 1.08, 'shadows': .08},
    'Cool': {'temperature': -.35, 'contrast': 1.08},
    'Soft': {'contrast': .85, 'shadows': .15, 'highlights': -.15},
    'Punchy': {'contrast': 1.25, 'shadows': -.12, 'highlights': .08},
    'Pixar-inspired animation': {'exposure': .12, 'contrast': 1.18, 'gamma': 1.08,
        'shadows': .06, 'highlights': -.1, 'temperature': .18, 'tint': .04},
    'Horror': {'exposure': -.35, 'contrast': 1.3, 'gamma': .88,
        'shadows': -.15, 'highlights': -.12, 'temperature': -.3, 'tint': -.16},
    'Adventure': {'exposure': .1, 'contrast': 1.24, 'gamma': 1.04,
        'shadows': -.08, 'highlights': -.08, 'temperature': .2},
    'Romantic': {'exposure': .12, 'contrast': .88, 'gamma': 1.1,
        'shadows': .12, 'highlights': -.12, 'temperature': .28, 'tint': .12},
    'Mystery': {'exposure': -.2, 'contrast': 1.22, 'gamma': .94,
        'shadows': -.1, 'highlights': -.15, 'temperature': -.24, 'tint': .1},
}

PRESET_SATURATION = {'Pixar-inspired animation': 1.35, 'Horror': .65,
                     'Adventure': 1.18, 'Romantic': .93, 'Mystery': .78}
PRESET_DESCRIPTIONS = {
    'Pixar-inspired animation': 'Bright, rich color with warm midtones and gentle highlights.',
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


def rgb_active(clip):
    value = settings(clip)
    return value['enabled'] and value['strength'] > 0 and any(value[key] != defaults()[key] for key in RANGES if key != 'strength')


def active(clip):
    return rgb_active(clip) or saturation(clip) != 1


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


def filters(clip):
    """Use FFmpeg's compiled lookup tables, with no per-frame expression work."""
    result = []
    if rgb_active(clip):
        grade = settings(clip)
        x = f"clip(val/255*{2**grade['exposure']:.12g},0,1)"
        x = f"pow(clip(({x}-.5)*{grade['contrast']:.12g}+.5,0,1),{1/grade['gamma']:.12g})"
        tone = f"({x}+.3*{grade['shadows']:.12g}*pow(1-{x},2)+.3*{grade['highlights']:.12g}*pow({x},2))"
        t, i = grade['temperature'], grade['tint']
        offsets = (.12*t+.06*i, -.12*i, -.12*t+.06*i)
        expressions = [f"{channel}='clip(floor(val+(clip(255*({tone}+{offset:.12g}),0,255)-val)*{grade['strength']:.12g}+.5),0,255)'"
                       for channel, offset in zip('rgb', offsets)]
        result += ['format=rgb24', 'lutrgb='+':'.join(expressions), 'format=yuv420p']
    level = saturation(clip)
    if level != 1:
        result.append(f'hue=s={level:.12g}')
    return result
