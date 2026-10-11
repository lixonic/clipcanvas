"""Group timeline operations with stable selection IDs and one undo per edit."""
import copy
import json
import uuid
from . import model

GROUPS = ('clips', 'audio', 'layers', 'captions')
CLIPBOARD_PREFIX = 'ClipCanvas timeline\n'


def selected_items(project, selection):
    return [(group, index, item) for group in GROUPS for index, item in enumerate(project[group])
            if (group, item['id']) in selection]


def copy_items(project, selection):
    rows = model.timeline(project)
    items = [{'group': group, 'item': copy.deepcopy(item),
              'time': rows[index]['start'] if group == 'clips' else item['start']}
             for group, index, item in selected_items(project, selection)]
    return CLIPBOARD_PREFIX + json.dumps({'items': items}, ensure_ascii=False) if items else None


def paste_items(project, text, at, clip_index):
    if not isinstance(text, str) or not text.startswith(CLIPBOARD_PREFIX) or len(text) > 2*1024*1024:
        raise ValueError('Copy timeline items in ClipCanvas first.')
    try:
        entries = json.loads(text[len(CLIPBOARD_PREFIX):])['items']
        if not isinstance(entries, list) or not entries or len(entries) > 1000: raise ValueError()
        origin = min(float(entry['time']) for entry in entries)
        candidate = copy.deepcopy(project);selection = set();new_ids = {}
        for entry in entries:
            group = entry['group']
            if group not in GROUPS: raise ValueError()
            item = copy.deepcopy(entry['item']);old_id = item['id'];item['id'] = uuid.uuid4().hex
            new_ids[old_id] = item['id']
            if group == 'clips':
                candidate[group].insert(clip_index, item);clip_index += 1
            else:
                start = max(0, float(at)+float(entry['time'])-origin)
                if group in ('layers', 'captions'): item['end'] = start+item['end']-item['start']
                item['start'] = start;candidate[group].append(item)
            selection.add((group, item['id']))
        for track in candidate['audio']:
            if ('audio', track['id']) in selection and track.get('source_clip_id') in new_ids:
                track['source_clip_id'] = new_ids[track['source_clip_id']]
        model.validate_project(candidate, check_files=False)
        return candidate, selection
    except (KeyError, TypeError, OverflowError, ValueError) as error:
        raise ValueError('The copied timeline items are invalid or exceed project limits.') from error
