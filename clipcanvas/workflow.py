"""Group timeline operations with stable selection IDs and one undo per edit."""
import copy
import json
import uuid
from . import model, track_layout, color_grading

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
        # Preserve copied lane groupings. A paste over an existing clip gets a
        # new lane for that whole copied group rather than hiding waveforms.
        pasted_audio=[item for item in candidate['audio'] if ('audio',item['id']) in selection]
        occupied={item['id'] for item in project['audio']}
        new_lanes={}
        for item in pasted_audio:
            lane=item.get('lane_id',item['id'])
            existing={**candidate,'audio':[a for a in candidate['audio'] if a['id'] in occupied]}
            if not track_layout.audio_fits(existing,item):new_lanes.setdefault(lane,uuid.uuid4().hex)
        for item in pasted_audio:
            lane=item.get('lane_id',item['id']);item['lane_id']=new_lanes.get(lane,lane)
        model.validate_project(candidate, check_files=False)
        return candidate, selection
    except (KeyError, TypeError, OverflowError, ZeroDivisionError, ValueError) as error:
        raise ValueError('The copied timeline items are invalid or exceed project limits.') from error


def with_volume(project, selection, volume):
    """Apply one level to selected video/audio, preserving all other edit data."""
    model._number(volume, 'Selection volume', 0, 2)
    candidate = copy.deepcopy(project)
    for group, _, item in selected_items(candidate, selection):
        if group in ('clips', 'audio') and not model.is_still(item):
            item['volume'] = volume
    model.validate_project(candidate, check_files=False)
    return candidate


def with_saturation(project, selection, saturation):
    """Adjust selected main-sequence pictures with one undoable operation."""
    model._number(saturation, 'Selection saturation', 0, 2)
    candidate = copy.deepcopy(project)
    for group, _, item in selected_items(candidate, selection):
        if group == 'clips':
            item['saturation'] = saturation
            if 'color_grade' in item:item['color_grade']['enabled'] = True
    model.validate_project(candidate, check_files=False)
    return candidate


def with_color_grade(project, selection, grade, saturation):
    """Replace selected picture grades; keep timing, sources and sound intact."""
    color_grading.validate(grade)
    model._number(saturation, 'Selection saturation', 0, 2)
    candidate = copy.deepcopy(project)
    for group, _, item in selected_items(candidate, selection):
        if group == 'clips':
            item.update(color_grade=copy.deepcopy(grade), saturation=saturation)
    model.validate_project(candidate, check_files=False)
    return candidate


def with_all_transitions(project, settings):
    """Copy transition controls to every existing join, preserving other edits."""
    if not isinstance(settings, dict):
        raise ValueError('Transition settings are invalid.')
    values = {'transition': settings.get('transition', 'none'),
              'transition_duration': settings.get('transition_duration', .5),
              'transition_direction': settings.get('transition_direction', 'left'),
              'transition_colour': settings.get('transition_colour', '#8b3dff')}
    candidate = copy.deepcopy(project)
    for clip in candidate['clips'][:-1]:
        clip.update(values)
    model.validate_project(candidate, check_files=False)
    return candidate


def with_audio_placement(project, index, start, lane_id):
    """Move one audio clip without changing its trim, fades, effects or source."""
    candidate=copy.deepcopy(project)
    candidate['audio'][index].update(start=start,lane_id=lane_id)
    model.validate_project(candidate,check_files=False)
    return candidate
