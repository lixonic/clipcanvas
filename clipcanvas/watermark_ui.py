"""Mark a stationary watermark on the original, uncropped source frame."""
import tempfile
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

from . import engine, model


class RegionDialog(Gtk.Dialog):
    def __init__(self, owner, clip, source_time):
        super().__init__(title='Mark watermark region', transient_for=owner,
                         modal=True, destroy_with_parent=True)
        self.owner = owner
        self.path = clip['path']
        self.source_time = source_time
        self.region = clip.get('watermark_region')
        self.original = self.repaired = self.anchor = None
        self.generation = 0
        self.add_button('Cancel', Gtk.ResponseType.CANCEL)
        if self.region:
            self.add_button('Remove repair', Gtk.ResponseType.REJECT)
        self.apply = self.add_button('Apply to clip', Gtk.ResponseType.OK)
        self.info = Gtk.Label(label='Loading source frame…')
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(480, 270)
        self.set_default_size(760, 510)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                             Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.area.connect('draw', self.draw_frame)
        self.area.connect('button-press-event', self.press)
        self.area.connect('button-release-event', self.release)
        self.area.connect('motion-notify-event', self.motion)
        self.compare = Gtk.CheckButton(label='Preview repair')
        self.compare.connect('toggled', self.toggle_preview)
        content = self.get_content_area()
        content.set_border_width(10)
        content.set_spacing(6)
        content.pack_start(self.info, False, False, 0)
        content.pack_start(self.area, True, True, 0)
        content.pack_start(self.compare, False, False, 0)
        self.connect('destroy', self.invalidate)
        self.controls()
        self.show_all()
        self.load(None)

    def invalidate(self, *_):
        self.generation += 1

    def controls(self):
        ready = self.original is not None and self.region is not None
        self.apply.set_sensitive(ready)
        self.compare.set_sensitive(ready)

    def load(self, region):
        self.invalidate()
        generation = self.generation
        def finish(picture, error):
            if self.generation != generation:
                return False
            if error:
                self.compare.set_active(False)
                self.info.set_text(str(error))
            else:
                if region is None:
                    self.original = picture
                else:
                    self.repaired = picture
                self.info.set_text('Drag a tight box around the watermark. The same area is repaired throughout this clip.')
            self.controls()
            self.area.queue_draw()
            return False

        def work():
            try:
                with tempfile.TemporaryDirectory(prefix='clipcanvas-mark-') as folder:
                    path = Path(folder) / 'frame.png'
                    engine.thumbnail(self.path, path, self.source_time, 1200, 800, watermark_region=region)
                    picture = GdkPixbuf.Pixbuf.new_from_file(str(path))
                GLib.idle_add(finish, picture, None)
            except Exception as error:
                GLib.idle_add(finish, None, error)
        self.owner.pool.submit(work)

    def bounds(self):
        width, height = self.area.get_allocated_width(), self.area.get_allocated_height()
        w, h = self.original.get_width(), self.original.get_height()
        scale = min(width / w, height / h)
        return (width-w*scale)/2, (height-h*scale)/2, w*scale, h*scale

    def draw_frame(self, _, cr):
        cr.set_source_rgb(.10, .10, .10)
        cr.paint()
        if self.original is None:
            return
        image = self.repaired if self.compare.get_active() and self.repaired else self.original
        x, y, w, h = self.bounds()
        cr.save()
        cr.translate(x, y)
        cr.scale(w/image.get_width(), h/image.get_height())
        Gdk.cairo_set_source_pixbuf(cr, image, 0, 0)
        cr.paint()
        cr.restore()
        if self.region and not self.compare.get_active():
            left, top, right, bottom = self.region
            cr.rectangle(x+left*w, y+top*h, (right-left)*w, (bottom-top)*h)
            cr.set_source_rgb(1, .72, .12)
            cr.set_line_width(2)
            cr.stroke()

    def point(self, event):
        x, y, w, h = self.bounds()
        return (event.x-x)/w, (event.y-y)/h

    def press(self, _, event):
        if event.button != 1 or self.original is None:
            return False
        point = self.point(event)
        if all(0 <= v <= 1 for v in point):
            self.compare.set_active(False)
            self.anchor = point
            return True
        return False

    def motion(self, _, event):
        if self.anchor is None:
            return False
        x, y = (max(0, min(1, v)) for v in self.point(event))
        ax, ay = self.anchor
        if x != ax and y != ay:
            self.region = [min(x, ax), min(y, ay), max(x, ax), max(y, ay)]
            self.repaired = None
            self.controls()
            self.area.queue_draw()
        return True

    def release(self, widget, event):
        if event.button == 1:
            self.motion(widget, event)
            self.anchor = None

    def toggle_preview(self, button):
        if button.get_active() and self.repaired is None:
            self.info.set_text('Preparing repaired frame…')
            self.load(self.region)
        else:
            self.invalidate()
            self.info.set_text('Drag a tight box around the watermark. The same area is repaired throughout this clip.')
        self.area.queue_draw()


def edit_region(owner, index):
    clip = owner.project['clips'][index]
    at = owner.playhead
    row = model.timeline(owner.project)[index]
    local = at-row['start']
    if not 0 <= local < row['duration']:
        local = row['duration']/2
    owner.canvas.pause()
    owner.update_play_button(False)
    dialog = RegionDialog(owner, clip, clip['in']+local*clip.get('speed', 1))
    try:
        response = dialog.run()
        if response not in (Gtk.ResponseType.OK, Gtk.ResponseType.REJECT):
            return
        region = dialog.region if response == Gtk.ResponseType.OK else None
        if region == clip.get('watermark_region'):
            return
        owner.checkpoint()
        if region:
            clip['watermark_region'] = region
        else:
            clip.pop('watermark_region', None)
        owner.changed()
        owner.seek(row['start']+local)
    finally:
        dialog.destroy()
