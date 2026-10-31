"""Cancellable native grading editor with an asynchronous composition preview."""
import copy
import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf, GLib

from . import color_grading, engine, model


class ColorGradingDialog(Gtk.Dialog):
    def __init__(self, owner, project, index, at, selection_count=1, selection=None):
        super().__init__(title='Color grading', transient_for=owner, modal=True)
        self.owner = owner
        self.project = copy.deepcopy(project)
        self.index = index
        self.selection = selection or {('clips', project['clips'][index]['id'])}
        self.closed = False
        self.loading = False
        self.timer = 0
        self.serial = 0
        self.cancel_event = None
        self.pending = None
        self.rendering = False
        self.controls = {}
        row = model.timeline(project)[index]
        end = row['start']+row['duration']
        self.at = min(end-1/project['fps'], at if row['start'] <= at < end else row['start']+row['duration']/2)
        self.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Apply grade', Gtk.ResponseType.OK)
        self.set_default_size(900, 590)
        body = self.get_content_area()
        body.set_border_width(16)
        body.set_spacing(12)
        columns = Gtk.Box(spacing=20)
        body.pack_start(columns, True, True, 0)
        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        columns.pack_start(preview, True, True, 0)
        title = Gtk.Label(label=model.item_name(project['clips'][index]), xalign=0)
        title.set_max_width_chars(44)
        title.set_ellipsize(3)
        preview.pack_start(title, False, False, 0)
        self.picture = Gtk.Image()
        self.picture.set_size_request(440, 290)
        preview.pack_start(self.picture, True, True, 0)
        self.before = Gtk.ToggleButton(label='Compare with original color')
        preview.pack_start(self.before, False, False, 0)
        self.status = Gtk.Label(label='', xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(48)
        preview.pack_start(self.status, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(345)
        columns.pack_start(scroll, False, False, 0)
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        controls.set_margin_end(10)
        scroll.add(controls)
        self.enabled = Gtk.CheckButton(label='Enable color grading')
        controls.pack_start(self.enabled, False, False, 0)
        self.presets = Gtk.ComboBoxText()
        self.presets.append('custom', 'Custom settings')
        for name in color_grading.PRESETS:
            self.presets.append(name, name)
        controls.pack_start(self.presets, False, False, 0)
        self.look_hint = Gtk.Label(label='Choose a starting look, then adjust it for your footage.', xalign=0)
        self.look_hint.set_line_wrap(True)
        self.look_hint.set_max_width_chars(37)
        self.look_hint.get_style_context().add_class('dim-label')
        controls.pack_start(self.look_hint, False, False, 0)
        for key, title, low, high, step, mult in (
                ('strength', 'Look strength (%)', 0, 100, 1, 100),
                ('exposure', 'Exposure (stops)', -3, 3, .05, 1),
                ('contrast', 'Contrast (%)', 25, 200, 1, 100),
                ('gamma', 'Midtones / gamma', .25, 3, .05, 1),
                ('shadows', 'Shadows', -100, 100, 1, 100),
                ('highlights', 'Highlights', -100, 100, 1, 100),
                ('temperature', 'Temperature · cool / warm', -100, 100, 1, 100),
                ('tint', 'Tint · green / magenta', -100, 100, 1, 100),
                ('saturation', 'Saturation (%)', 0, 200, 1, 100)):
            controls.pack_start(Gtk.Label(label=title, xalign=0), False, False, 0)
            line = Gtk.Box(spacing=8)
            adjustment = Gtk.Adjustment(lower=low, upper=high, step_increment=step, page_increment=step*10)
            scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
            scale.set_draw_value(False)
            scale.set_size_request(170, -1)
            spin = Gtk.SpinButton(adjustment=adjustment, digits=2 if step < 1 else 0)
            spin.set_width_chars(5)
            line.pack_start(scale, True, True, 0)
            line.pack_start(spin, False, False, 0)
            controls.pack_start(line, False, False, 0)
            self.controls[key] = (spin, mult)
        reset = Gtk.Button(label='Reset all color adjustments')
        reset.connect('clicked', lambda *_: self.load(color_grading.defaults(), 1))
        controls.pack_start(reset, False, False, 4)
        self.apply_selection = Gtk.CheckButton(label=f'Apply to all {selection_count} selected clips')
        self.apply_selection.set_no_show_all(selection_count < 2)
        self.apply_selection.connect('toggled', lambda *_: self.schedule_preview())
        body.pack_start(self.apply_selection, False, False, 0)
        self.load(color_grading.settings(project['clips'][index]), project['clips'][index].get('saturation', 1))
        self.presets.connect('changed', self.choose_preset)
        self.enabled.connect('toggled', self.changed)
        self.before.connect('toggled', lambda *_: self.schedule_preview())
        for widget, _ in self.controls.values():
            widget.connect('value-changed', self.changed)
        self.connect('response', lambda *_: self.cleanup())
        self.connect('destroy', lambda *_: self.cleanup())
        self.show_all()
        self.schedule_preview()

    def read(self):
        grade = color_grading.defaults()
        grade['enabled'] = self.enabled.get_active()
        for key, (widget, mult) in self.controls.items():
            if key != 'saturation':
                grade[key] = widget.get_value()/mult
        return grade, self.controls['saturation'][0].get_value()/100

    def load(self, grade, saturation):
        self.loading = True
        self.enabled.set_active(grade['enabled'])
        for key, (widget, mult) in self.controls.items():
            widget.set_value((saturation if key == 'saturation' else grade[key])*mult)
        name = next((name for name, preset in color_grading.PRESETS.items()
            if all(grade[key] == {**color_grading.defaults(), **preset}[key] for key in color_grading.RANGES if key != 'strength')
            and saturation == color_grading.PRESET_SATURATION.get(name, 1)), 'custom')
        self.presets.set_active_id(name)
        self.look_hint.set_text(color_grading.PRESET_DESCRIPTIONS.get(name, 'Choose a starting look, then adjust it for your footage.'))
        self.loading = False
        self.schedule_preview()

    def choose_preset(self, *_):
        name = self.presets.get_active_id()
        if self.loading or name not in color_grading.PRESETS:
            return
        self.load({**color_grading.defaults(), **color_grading.PRESETS[name]}, color_grading.PRESET_SATURATION.get(name, 1))
        self.loading = True
        self.presets.set_active_id(name)
        self.loading = False
        self.look_hint.set_text(color_grading.PRESET_DESCRIPTIONS.get(name, 'An editable starting look for your footage.'))

    def changed(self, *_):
        if self.loading:
            return
        self.loading = True
        self.presets.set_active_id('custom')
        self.loading = False
        self.look_hint.set_text('Custom settings. Adjust the look for your footage.')
        self.schedule_preview()

    def schedule_preview(self):
        if self.closed or self.loading:
            return
        self.serial += 1
        if self.timer:
            GLib.source_remove(self.timer)
        if self.cancel_event:
            self.cancel_event.set()
        self.pending = None
        self.status.set_text('Updating preview…')
        self.timer = GLib.timeout_add(180, self.request_preview)

    def request_preview(self):
        self.timer = 0
        snapshot = copy.deepcopy(self.project)
        grade, saturation = self.read()
        if self.before.get_active():
            grade, saturation = color_grading.defaults(), 1
        for index, clip in enumerate(snapshot['clips']):
            if index == self.index or (self.apply_selection.get_active() and ('clips', clip['id']) in self.selection):
                clip.update(color_grade=copy.deepcopy(grade), saturation=saturation)
        self.pending = (self.serial, snapshot)
        self.start_preview()
        return False

    def start_preview(self):
        if self.closed or self.rendering or self.pending is None:
            return
        serial, snapshot = self.pending
        self.pending = None
        cancel = self.cancel_event = threading.Event()
        self.rendering = True
        def work():
            try:
                frame = engine.preview_frame(snapshot, self.at, cancel, preview_edge=560)
                error = None
            except Exception as caught:
                frame, error = None, caught
            GLib.idle_add(self.deliver, serial, frame, error)
        self.owner.pool.submit(work)

    def deliver(self, serial, frame, error):
        self.rendering = False
        if self.closed:
            return False
        if serial == self.serial:
            if error:
                self.status.set_text('Preview unavailable: '+str(error))
            else:
                pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(frame['pixels']),
                    GdkPixbuf.Colorspace.RGB, False, 8, frame['width'], frame['height'], frame['width']*3)
                factor = min(1, 480/frame['width'], 350/frame['height'])
                if factor < 1:
                    pixbuf = pixbuf.scale_simple(max(1, round(frame['width']*factor)), max(1, round(frame['height']*factor)), GdkPixbuf.InterpType.BILINEAR)
                self.picture.set_from_pixbuf(pixbuf)
                self.status.set_text(('Original color' if self.before.get_active() else 'Graded color' if self.enabled.get_active() else 'Grading bypassed')+f' · {self.at:.2f} s')
        self.start_preview()
        return False

    def cleanup(self):
        self.closed = True
        self.serial += 1
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.cancel_event:
            self.cancel_event.set()
        self.pending = None
