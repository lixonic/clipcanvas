"""Speech highlight styles and reusable desktop caption presets."""
from copy import deepcopy
import re


def defaults():
    return dict(word_highlight=False, highlight_color='#ffe500ff',
                active_color='#111111ff', highlight_radius=10., highlight_padding=6.)


def validate(style):
    from .model import _number
    if not isinstance(style.get('word_highlight', False), bool):
        raise ValueError('Spoken-word highlighting must be on or off.')
    for key in ('highlight_color', 'active_color'):
        value = style.get(key, defaults()[key])
        if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
            raise ValueError('Highlight colors must be hex RGB or RGBA colors.')
    _number(style.get('highlight_radius', 10), 'Highlight roundness', 0, 256)
    _number(style.get('highlight_padding', 6), 'Highlight padding', 0, 128)


def spoken_word(style):
    from .text_styles import apply_preset
    result = apply_preset(style, 'outline')
    result.update(defaults(), word_highlight=True)
    return result


def preset_style(style):
    """Store only appearance/placement, never cue wording, IDs or timestamps."""
    from . import model
    result = model.caption_style()
    result.update({key: deepcopy(style[key]) for key in result if key in style})
    model._text_style(result, 'Caption preset')
    model._animation(result, 'Caption preset', True)
    validate(result)
    return result


def saved_presets(preferences):
    values = preferences.get('caption_presets', {})
    result = {}
    if isinstance(values, dict):
        for name, style in list(values.items())[:50]:
            try:
                if isinstance(name, str) and name.strip() and len(name) <= 80 and isinstance(style, dict):
                    result[name] = preset_style(style)
            except (ValueError, TypeError):
                continue
    return result


def save_preset(preferences, name, style):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError('Give this preset a name of 1–80 characters.')
    presets = saved_presets(preferences)
    if name not in presets and len(presets) >= 50:
        raise ValueError('You can save up to 50 caption presets.')
    presets[name] = preset_style(style)
    preferences.set('caption_presets', presets)
    return name
