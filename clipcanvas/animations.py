"""Saved element animation timing shared by the native canvas and FFmpeg.

Times are global project seconds, including after a live seek. Text effects use
whole Unicode words, not bytes or interpolated user text in filter expressions.
Word timing is evenly spread over a cue/title when no word timestamps exist.
"""

import copy
import math
import re

GENERAL = ('none', 'rise', 'pan', 'fade', 'pop', 'wipe', 'blur')
TEXT = ('none', 'reveal', 'highlight', 'snake')
DIRECTIONS = ('left', 'right', 'up', 'down')
MAX_TEXT_STEPS = 128


def default():
    return {'enter': 'none', 'exit': 'none', 'duration': .5,
            'direction': 'left', 'text_effect': 'none', 'colour': '#8b3dff'}


DEFAULT = default()


def normalise(value=None):
    result = default()
    if isinstance(value, dict):
        result.update(value)
    return result


def words(text):
    return tuple((match.start(), match.end()) for match in re.finditer(r'\S+', text or ''))


def text_steps(item):
    return min(MAX_TEXT_STEPS, len(words(item.get('text', ''))))


def text_step(item, seconds):
    count = text_steps(item)
    if not count:
        return -1
    length = max(1e-9, float(item.get('end', 0))-float(item.get('start', 0)))
    progress = max(0, min(1, (seconds-float(item.get('start', 0)))/length))
    return min(count-1, int(progress*count))


def text_word_range(item, step):
    """Return the inclusive-exclusive active word group for a bounded step count."""
    count = len(words(item.get('text', '')))
    steps = min(MAX_TEXT_STEPS, count)
    if not steps or step < 0:
        return 0, 0
    step = min(steps-1, step)
    return step*count//steps, (step+1)*count//steps


def duration(item):
    return max(0.001, min(float(normalise(item.get('animation'))['duration']),
                         max(.002, float(item.get('end', 0))-float(item.get('start', 0)))/2))


def _smooth(value):
    value = max(0, min(1, value))
    return value*value*(3-2*value)


def state(item, seconds):
    """Return a transform around the element's center, without mutating it."""
    animation = normalise(item.get('animation'))
    d = duration(item)
    enter = _smooth((seconds-float(item.get('start', 0)))/d)
    leave = _smooth((float(item.get('end', 0))-seconds)/d)
    result = {'dx': 0., 'dy': 0., 'opacity': 1., 'scale': 1.,
              'wipe': 1., 'blur': 0., 'text_step': text_step(item, seconds)}
    for phase, progress, sign in (('enter', enter, 1), ('exit', leave, -1)):
        effect = animation[phase]
        if item.get('_match_to') and phase == 'exit' or item.get('_match_from') and phase == 'enter':
            continue
        if effect in ('rise', 'pan', 'fade', 'pop'):
            result['opacity'] *= progress
        if effect == 'rise':
            result['dy'] += .08*(1-progress)*sign
        if effect == 'pan':
            direction = animation['direction']
            axis = 'dx' if direction in ('left', 'right') else 'dy'
            orientation = 1 if direction in ('left', 'up') else -1
            result[axis] += .12*(1-progress)*sign*orientation
        if effect == 'pop':
            result['scale'] *= .2+.8*progress+.3*math.sin(math.pi*progress)
        if effect == 'wipe':
            result['wipe'] *= progress
        if effect == 'blur':
            result['blur'] = max(result['blur'], 1-progress)
            result['opacity'] *= .25+.75*progress
    return result


def _number(value):
    return f'{float(value):.9f}'.rstrip('0').rstrip('.') or '0'


def _ease(expression):
    q = f'clip({expression},0,1)'
    return f'({q}*{q}*(3-2*{q}))'


def expressions(item, variable='t'):
    """FFmpeg expressions equivalent to :func:`state` (user text is excluded)."""
    animation = normalise(item.get('animation'))
    d = _number(duration(item))
    enter = _ease(f'({variable}-{_number(item.get("start", 0))})/{d}')
    leave = _ease(f'({_number(item.get("end", 0))}-{variable})/{d}')
    result = {'dx': '0', 'dy': '0', 'opacity': '1', 'scale': '1', 'wipe': '1', 'blur': '0'}
    for phase, progress, sign in (('enter', enter, 1), ('exit', leave, -1)):
        effect = animation[phase]
        if item.get('_match_to') and phase == 'exit' or item.get('_match_from') and phase == 'enter':
            continue
        if effect in ('rise', 'pan', 'fade', 'pop'):
            result['opacity'] += f'*{progress}'
        if effect == 'rise':
            result['dy'] += f'+{_number(.08*sign)}*(1-{progress})'
        if effect == 'pan':
            direction = animation['direction']
            axis = 'dx' if direction in ('left', 'right') else 'dy'
            orientation = 1 if direction in ('left', 'up') else -1
            result[axis] += f'+{_number(.12*sign*orientation)}*(1-{progress})'
        if effect == 'pop':
            result['scale'] += f'*(.2+.8*{progress}+.3*sin(PI*{progress}))'
        if effect == 'wipe':
            result['wipe'] += f'*{progress}'
        if effect == 'blur':
            result['blur'] = f'max({result["blur"]},1-{progress})'
            result['opacity'] += f'*(.25+.75*{progress})'
    return result


def animated(item):
    animation = normalise(item.get('animation'))
    return animation['enter'] != 'none' or animation['exit'] != 'none' or animation['text_effect'] != 'none' or bool(item.get('_match_to'))


def prepare_layers(project):
    """Pair explicitly identified matching elements over a Match & Move join.

    Raster videos themselves have no editable element identity. A matching key
    only pairs identical text/style or identical file-backed content, so it
    cannot silently morph unrelated objects or discard different wording.
    """
    from .model import timeline
    if not any(clip.get('transition')=='match_move' for clip in project.get('clips',[])):
        return project.get('layers',[])
    layers = [copy.deepcopy(item) for item in project.get('layers', [])]
    rows = timeline(project)
    for index, row in enumerate(rows[:-1]):
        if project['clips'][index].get('transition') != 'match_move' or row['overlap'] <= 0:
            continue
        begin = rows[index+1]['start']
        end = row['start']+row['duration']
        outgoing = [item for item in layers if item.get('match_id') and item['start'] < begin+.00001 and abs(item['end']-end)<.00001]
        incoming = [item for item in layers if item.get('match_id') and abs(item['start']-begin)<.00001 and item['end'] > end-.00001]
        for first in outgoing:
            second = next((item for item in incoming if item.get('match_id') == first['match_id'] and signature(item) == signature(first)), None)
            if second is not None:
                first.update(_match_to=second, _match_start=begin, _match_end=end)
                second.update(_match_from=first['id'], _suppress_until=end)
                incoming.remove(second)
    return layers


def signature(item):
    keys = ('kind', 'path', 'fit') if item.get('kind') != 'text' else (
        'kind', 'text', 'font_family', 'font_size', 'bold', 'italic', 'color', 'background',
        'outline_color', 'outline_width', 'align', 'shadow_color', 'background_mode')
    return tuple(item.get(key) for key in keys)


def placement(item, seconds):
    result = {key: float(item.get(key, default)) for key, default in (
        ('x', .5), ('y', .5), ('width', .7), ('height', .18), ('rotation', 0), ('opacity', 1))}
    target = item.get('_match_to')
    if target:
        progress = _smooth((seconds-item['_match_start'])/max(.001, item['_match_end']-item['_match_start']))
        for key in result:
            result[key] += (float(target.get(key, result[key]))-result[key])*progress
    return result


def placement_expressions(item, variable='t'):
    result = {key: _number(item.get(key, default)) for key, default in (
        ('x', .5), ('y', .5), ('width', .7), ('height', .18), ('rotation', 0), ('opacity', 1))}
    target = item.get('_match_to')
    if target:
        progress = _ease(f'({variable}-{_number(item["_match_start"])})/{_number(item["_match_end"]-item["_match_start"])}')
        for key, value in result.items():
            delta = float(target.get(key, float(value)))-float(value)
            if delta:
                result[key] = f'({value}+{_number(delta)}*{progress})'
    return result
