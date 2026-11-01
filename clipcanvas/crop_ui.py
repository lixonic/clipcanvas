"""Direct manipulation of a main clip inside its fixed output canvas."""
import copy

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GLib

from . import crop_geometry, engine, model, visuals


def is_crop_change(before, after):
    """Identify framing-only history entries so Undo keeps the inspected time."""
    if before == after:
        return False
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    for project in (left, right):
        for clip in project['clips']:
            for key in ('zoom', 'offset_x', 'offset_y'):
                clip.pop(key, None)
    return left == right


class CropTools:
    def __init__(self, owner):
        self.owner = owner
        self.active = False
        self.loading = False
        self.serial = 0
        self.clip_id = None
        self.index = None
        self.drag = None
        self.picture = None
        self.saved_view = None

    def item(self):
        clips = self.owner.project['clips']
        if self.index is not None and self.index < len(clips) and clips[self.index]['id'] == self.clip_id:
            return clips[self.index]
        return None

    def bounds(self):
        canvas = self.owner.canvas
        x, y, width, height = canvas._view_rect()
        vx, vy, vw, vh = canvas.viewport_rect
        left, top = max(x, vx), max(y, vy)
        return left, top, min(x+width, vx+vw)-left, min(y+height, vy+vh)-top

    def cursor(self, name=None):
        canvas = self.owner.canvas
        if canvas.get_window():
            canvas.get_window().set_cursor(Gdk.Cursor.new_from_name(canvas.get_display(), name) if name else None)

    def start(self):
        w = self.owner
        if w.busy or not w.project['clips']:
            return False
        rows = model.timeline(w.project)
        candidates = [i for i, row in enumerate(rows)
                      if row['start'] <= w.playhead < row['start']+row['duration']]
        if not candidates:
            return False
        index = w.selected[1] if w.selected and w.selected[0] == 'clips' and w.selected[1] in candidates else candidates[-1]
        item = w.project['clips'][index]
        if model.is_card(item):
            return False
        w.select('clips', index, seek=False, reveal=False)
        w.cancel_scrub()
        w.canvas.stop()
        w.preview_key = None
        w.update_play_button(False)
        self.active = self.loading = True
        self.index, self.clip_id = index, item['id']
        self.saved_view = (w.canvas.preview_zoom, w.canvas.pan_x, w.canvas.pan_y)
        w.canvas.reset_view()
        w.canvas.grab_focus()
        w.crop_done_button.show()
        self.serial += 1
        serial, snapshot = self.serial, copy.deepcopy(item)
        at = item.get('in', 0)+(w.playhead-rows[index]['start'])*item.get('speed', 1)
        target = w.temp/f'crop-source-{serial}.png'
        w.preview_label.set_text('Loading crop controls…')
        w.set_status('Drag the picture to position it. Drag a corner to zoom. Enter or Done finishes; Escape cancels the current drag.')

        def finish(picture, error):
            target.unlink(missing_ok=True)
            if not self.active or serial != self.serial or w.closed:
                return False
            self.loading = False
            if error:
                self.close()
                w.set_status('Could not open crop controls: '+str(error))
                w.seek(w.playhead)
                return False
            picture = visuals.graded_picture(picture, snapshot)
            rotation = {90: GdkPixbuf.PixbufRotation.CLOCKWISE,
                        180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
                        270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(snapshot.get('rotation', 0))
            if rotation is not None:
                picture = picture.rotate_simple(rotation)
            if snapshot.get('flip_x'):
                picture = picture.flip(True)
            if snapshot.get('flip_y'):
                picture = picture.flip(False)
            self.picture = picture
            w.canvas.set_still(picture)
            self.show(snapshot)
            self.cursor('grab')
            w.preview_label.set_text('Crop · drag picture or corners')
            return False

        def work():
            try:
                if model.is_image(snapshot):
                    # Editing needs only a screen-sized source. Apply grading
                    # and orientation after decoding at the requested bounds.
                    picture = GdkPixbuf.Pixbuf.new_from_file_at_scale(snapshot['path'], 1920, 1920, True).apply_embedded_orientation()
                else:
                    engine.thumbnail(snapshot['path'], target, at=max(0, at), width=1920, height=1920)
                    picture = GdkPixbuf.Pixbuf.new_from_file(str(target))
                error = None
            except Exception as problem:
                picture, error = None, problem
            GLib.idle_add(finish, picture, error)
        w.pool.submit(work)
        return True

    def show(self, item):
        canvas = self.owner.canvas
        canvas.fit = item.get('fit', 'fill')
        canvas.clip_zoom = item.get('zoom', 1)
        canvas.position_x, canvas.position_y = item.get('position_x', .5), item.get('position_y', .5)
        canvas.offset_x, canvas.offset_y = item.get('offset_x', 0), item.get('offset_y', 0)
        canvas.composited = False
        canvas.queue_draw()

    def press(self, event):
        if event.button != 1 or getattr(event, 'state', 0) & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
            return False
        x, y, width, height = self.bounds()
        inside = x <= event.x <= x+width and y <= event.y <= y+height
        if not inside:
            if self.active:
                self.finish()
            return False
        if not self.active:
            return self.start()
        if self.loading or self.picture is None or self.item() is None:
            return True
        corner = None
        for key, cx, cy in (('nw', x, y), ('ne', x+width, y), ('sw', x, y+height), ('se', x+width, y+height)):
            if abs(event.x-cx) <= 14 and abs(event.y-cy) <= 14:
                corner = key
                break
        self.drag = {'x': event.x, 'y': event.y, 'corner': corner,
                     'item': copy.deepcopy(self.item()), 'draft': None,
                     'rect': self.owner.canvas.canvas_rect, 'handles': self.bounds()}
        self.cursor('grabbing' if corner is None else 'nwse-resize' if corner in ('nw', 'se') else 'nesw-resize')
        return True

    def motion(self, event):
        if not self.active:
            return False
        if not self.drag:
            x, y, width, height = self.bounds()
            near_x = abs(event.x-x) <= 14 or abs(event.x-x-width) <= 14
            near_y = abs(event.y-y) <= 14 or abs(event.y-y-height) <= 14
            diagonal = (event.x < x+width/2) == (event.y < y+height/2)
            self.cursor(('nwse-resize' if diagonal else 'nesw-resize') if near_x and near_y else 'grab')
            return True
        d = self.drag
        dx, dy = event.x-d['x'], event.y-d['y']
        if abs(dx)+abs(dy) < 2 and d['draft'] is None:
            return True
        if d['corner']:
            changes = crop_geometry.resize(d['item'], d['rect'], d['corner'], dx, dy,
                                           self.picture.get_width(), self.picture.get_height(), d['handles'])
        else:
            changes = crop_geometry.move(d['item'], d['rect'], dx, dy)
        d['draft'] = {**d['item'], **changes}
        self.show(d['draft'])
        return True

    def release(self, event=None):
        if not self.drag:
            return False
        d, self.drag = self.drag, None
        current = self.item()
        if current is None:
            self.close()
            return True
        keys = ('zoom', 'offset_x', 'offset_y')
        if d['draft'] and any(abs(d['draft'].get(key, 1 if key == 'zoom' else 0)-current.get(key, 1 if key == 'zoom' else 0)) > 1e-8 for key in keys):
            w = self.owner
            w.checkpoint()
            for key in keys:
                current[key] = d['draft'].get(key, 1 if key == 'zoom' else 0)
            w.preview_key = None
            w.update_title()
            w.show_inspector()
            w.undo_button.set_sensitive(True)
            w.redo_button.set_sensitive(False)
            w.timeline.queue_draw()
        self.show(current)
        self.cursor('grab')
        return True

    def close(self):
        if not self.active:
            return
        self.active = self.loading = False
        self.serial += 1
        self.drag = None
        self.picture = None
        self.cursor()
        self.owner.crop_done_button.hide()
        if self.saved_view:
            zoom, px, py = self.saved_view
            canvas = self.owner.canvas
            canvas.set_preview_zoom(zoom)
            canvas.pan_x, canvas.pan_y = px, py
            canvas._view_rect()
            self.saved_view = None
        self.owner.canvas.queue_draw()

    def finish(self, cancel_drag=False, render_frame=True):
        if not self.active:
            return False
        if not cancel_drag:
            self.release()
        at = self.owner.playhead
        self.close()
        if render_frame:
            self.owner.seek(at)
        return True

    def draw(self, cr):
        if not self.active or self.loading:
            return
        x, y, width, height = self.bounds()
        cr.save()
        cr.set_line_width(1.5)
        cr.set_source_rgba(1, 1, 1, .95)
        cr.rectangle(x+1, y+1, max(1, width-2), max(1, height-2))
        cr.stroke()
        if self.drag:
            cr.set_line_width(1)
            cr.set_source_rgba(1, 1, 1, .4)
            for fraction in (1/3, 2/3):
                cr.move_to(x+width*fraction, y);cr.line_to(x+width*fraction, y+height)
                cr.move_to(x, y+height*fraction);cr.line_to(x+width, y+height*fraction)
            cr.stroke()
        for cx, cy in ((x+5, y+5), (x+width-5, y+5), (x+5, y+height-5), (x+width-5, y+height-5)):
            cr.set_source_rgb(.1, .1, .1);cr.rectangle(cx-5, cy-5, 10, 10);cr.fill()
            cr.set_source_rgb(1, 1, 1);cr.rectangle(cx-4, cy-4, 8, 8);cr.fill()
        cr.restore()
