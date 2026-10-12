"""Native sprite opacity, clipping and blur blending, timed once per frame.

The old RGBA geq/blend expressions evaluated animation easing at every pixel.
Here easing is evaluated only at relevant frame boundaries; FFmpeg applies
small lookup tables and native alpha/rectangle/blend operations to the picture.
RGB outside a wipe is preserved before scaling, so transparent edges retain
the same interpolation as the original sprite.
"""
import math
from pathlib import Path

from . import animations, visuals


def _frames(items, start, length, fps, cancel=None):
    intervals = [(0,0)]
    limit = max(0, math.ceil(length*fps)-1)

    def interval(begin, end):
        first = max(0, math.ceil((begin-start)*fps-1e-6))
        last = min(limit, math.ceil((end-start)*fps+1e-6)+1)
        if first<=last:intervals.append((first,last))

    for item in items:
        if cancel is not None and cancel.is_set():
            from .engine import RenderCancelled
            raise RenderCancelled('Preview cancelled.')
        config = animations.normalise(item.get('animation'))
        begin, end = float(item.get('start', 0)), float(item.get('end', length))
        duration = animations.duration(item)
        if config['enter'] != 'none':interval(begin, begin+duration)
        if config['exit'] != 'none':interval(end-duration, end)
        # Reset between caption cues even when the next cue has no animation.
        interval(begin, begin);interval(end, end)
        if item.get('_match_to'):
            interval(item['_match_start'], item['_match_end'])
    begin,end = 0,0
    for first,last in sorted(intervals):
        if first<=end+1:end=max(end,last)
        else:
            yield from range(begin,end+1)
            begin,end=first,last
    yield from range(begin,end+1)


def append(filters, source, output, item, width, height, fps, length, scratch,
           blurred=None, cues=None, project=None, cancel=None):
    """Apply opacity/wipe/blur without copying RGB through interpreted geq."""
    from .engine import _fmt, RenderCancelled
    start = 0. if cues is not None else float(item.get('start', 0))
    items = ([visuals.caption_item(project, cue['text'], cue) for cue in cues]
             if cues is not None else [item])
    if item['kind'] == 'video':
        sw, sh = max(1, round(width*item['width'])), max(1, round(height*item['height']))
    else:
        plain = dict(item, rotation=0, opacity=1)
        _, _, sw, sh = visuals.sprite_geometry(project or {'width':width, 'height':height},
                                               plain, (width,height))
    frames = _frames(items, start, length, fps, cancel)
    previous = {}
    active = 0
    has_wipe = any(animations.normalise(i.get('animation'))[phase]=='wipe'
                   for i in items for phase in ('enter','exit'))
    commands = Path(scratch)/f'{output}-native.cmd'
    with commands.open('w', encoding='utf-8') as stream:
        for frame in frames:
            if cancel is not None and cancel.is_set():
                raise RenderCancelled('Preview cancelled.')
            # Sprite timestamps use AVTB before these operations.
            seconds = math.floor((start+frame/fps)*1000000+.5)/1000000
            if cues is not None:
                while active<len(items) and items[active]['end']<=seconds:
                    active += 1
                current = items[active] if active<len(items) and items[active]['start']<=seconds else None
                motion = animations.state(current,seconds) if current else {'opacity':1.,'wipe':1.,'blur':0.}
                opacity = motion['opacity']*float(item.get('opacity',1))
            else:
                current = item
                motion = animations.state(item,seconds)
                opacity = motion['opacity']*animations.placement(item,seconds)['opacity']
            changes = {f'lutrgb@{output} a':f'val*{_fmt(opacity)}'}
            if blurred is not None:
                # FFmpeg's all_opacity shortcut does not reset component
                # overrides when changed back to 1. Set each plane explicitly
                # so a later sharp caption cannot retain the preceding blur.
                for plane in range(4):
                    changes[f'blend@{output} c{plane}_opacity'] = _fmt(1-motion['blur'])
            if has_wipe:
                progress = motion['wipe']
                direction = animations.normalise((current or {}).get('animation'))['direction']
                horizontal = direction in ('left','right')
                extent = sw if horizontal else sh
                if direction in ('left','up'):
                    position, size = 0, math.ceil(extent*(1-progress))
                else:
                    position = math.ceil(extent*progress)
                    size = extent-position
                # A zero drawbox width means full width. Park a one-pixel
                # box outside the sprite when the entire image is visible.
                if size<=0:position,size=extent,1
                rectangle = (position,0,size,sh) if horizontal else (0,position,sw,size)
                for key,value in zip(('x','y','w','h'),rectangle):
                    changes[f'drawbox@{output} {key}'] = str(value)
            updates = [f'{key} {value}' for key,value in changes.items() if previous.get(key)!=value]
            if updates:
                stream.write(f'{_fmt(max(0,seconds-.000002))} '+','.join(updates)+';\n')
            previous = changes
    sender = output+'commands'
    filters.append(f'[{source}]sendcmd=f={commands}[{sender}]')
    current = sender
    if blurred is not None:
        current = output+'soft'
        # Send on the sharp branch before the synchronized mixer, so the
        # current frame uses the updated coefficient on every component.
        filters.append(f'[{sender}][{blurred}]blend@{output}=all_mode=normal:all_opacity=1[{current}]')
    alpha = output+'alpha'
    filters.append(f'[{current}]format=rgba,lutrgb@{output}=a=val[{alpha}]')
    if has_wipe:
        filters.append(f'[{alpha}]split=2[{output}rgb][{output}maskinput]')
        filters.append(f'[{output}maskinput]drawbox@{output}=x={sw}:y=0:w=1:h={sh}:color=black@0:t=fill:replace=1,'
                       f'alphaextract[{output}mask]')
        filters.append(f'[{output}rgb][{output}mask]alphamerge[{output}]')
    else:
        filters.append(f'[{alpha}]null[{output}]')
    return output
