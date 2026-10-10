"""Original, editable text presets shared by titles and every caption workflow."""
from copy import deepcopy

PRESETS = (
    ('clean', 'Clean', 'Simple white text without effects.'),
    ('outline', 'Outline', 'Bold white text with a dark outline.'),
    ('shadow', 'Shadow', 'White text with a soft dark drop shadow.'),
    ('highlight', 'Highlight', 'Dark bold text on rounded yellow highlights.'),
)


def effect_defaults():
    # Optional additions to format 3. Older projects keep their original look.
    return {'shadow_color': '#00000000', 'shadow_x': 3.0, 'shadow_y': 3.0,
            'shadow_blur': 0.0, 'background_mode': 'box',
            'background_radius': 0.0, 'background_padding': 8.0}


def apply_preset(style, preset):
    """Return an independent style; preserve words, timing, font, size and geometry."""
    if preset not in {key for key, _, _ in PRESETS}:
        raise ValueError('Choose Clean, Outline, Shadow or Highlight.')
    result = deepcopy(style)
    result.update(effect_defaults())
    result.update(color='#ffffffff', background='#00000000',
                  outline_color='#000000ff', outline_width=0.0,
                  bold=False, italic=False)
    scale = float(result.get('font_size', 48)) / 48
    if preset == 'outline':
        result.update(bold=True, outline_width=min(64, 3*scale))
    elif preset == 'shadow':
        result.update(shadow_color='#000000d9', shadow_x=min(256, 3*scale),
                      shadow_y=min(256, 3*scale), shadow_blur=min(64, 2.5*scale))
    elif preset == 'highlight':
        result.update(bold=True, color='#111827ff', background='#ffd54fff',
                      background_mode='text', background_radius=min(256, 10*scale),
                      background_padding=min(128, 8*scale))
    return result
