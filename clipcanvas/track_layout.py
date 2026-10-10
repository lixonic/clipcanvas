"""Timeline row placement, independent of playback and visual stacking."""


def key_for(kind, item=None):
    if kind == 'clips':return 'video'
    if kind == 'captions':return 'captions'
    return f'{kind}:{item["id"]}'


def rows(project):
    """Resolve saved row identities; append new rows and ignore deleted rows."""
    available = [('video', 'clips', None, 79)]
    for kind, height in (('layers',34), ('audio',42)):
        available.extend((key_for(kind,item),kind,i,height)
                         for i,item in enumerate(project.get(kind,[])))
    if project.get('captions'):available.append(('captions','captions',None,34))
    by_key = {row[0]:row for row in available}
    order = [key for key in project.get('timeline_order',[]) if key in by_key]
    order.extend(row[0] for row in available if row[0] not in order)
    result=[];y=40
    for key in order:
        _,kind,index,height=by_key[key]
        result.append((key,kind,index,y,height))
        y += height + (20 if kind=='clips' else 12 if kind=='audio' else 10)
    return result


def moved_order(project, key, direction):
    order=[row[0] for row in rows(project)]
    if key not in order or direction not in (-1,1):return None
    index=order.index(key);target=index+direction
    if not 0<=target<len(order):return None
    order[index],order[target]=order[target],order[index]
    return order


def validate_order(order):
    if not isinstance(order,list) or len(order)>10000:
        raise ValueError('Timeline row order must be a list of row identifiers.')
    seen=set()
    for key in order:
        if (not isinstance(key,str) or len(key)>136 or key in seen
            or not (key in ('video','captions')
                    or any(key.startswith(prefix) and len(key)>len(prefix)
                           for prefix in ('layers:','audio:')))):
            raise ValueError('Timeline row identifiers must be valid and unique.')
        seen.add(key)
