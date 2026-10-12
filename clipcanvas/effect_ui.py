"""Native, visual effect pickers with small original vector illustrations."""
from pathlib import Path
import math
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Gdk, GObject, Gst
from . import transitions, sound_effects

ANIMATIONS = (('none', 'None'), ('rise', 'Rise'), ('pan', 'Pan'),
              ('fade', 'Fade'), ('pop', 'Pop'), ('wipe', 'Wipe'), ('blur', 'Blur'))
TEXT_EFFECTS = (('none', 'None'), ('reveal', 'Reveal'),
                ('highlight', 'Highlight'), ('snake', 'Snake'))
_provider = None


def install_styles():
    global _provider
    if _provider is None and Gdk.Screen.get_default() is not None:
        _provider = Gtk.CssProvider()
        _provider.load_from_data(b'''
        .clipcanvas button.effect-tile { padding: 7px 3px; border-radius: 10px; min-height: 78px; }
        .clipcanvas button.effect-tile.selected { border: 2px solid @theme_selected_bg_color; background: alpha(@theme_selected_bg_color, 0.12); }
        .clipcanvas .effect-description { font-size: 0.9em; }
        ''')
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), _provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def box(vertical=True, spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)


def page_tabs(stack):
    """Keep five inspector pages within the native popover's width."""
    grid=Gtk.Grid(column_homogeneous=True,column_spacing=4,row_spacing=4)
    buttons={};syncing=[False]
    for index,child in enumerate(stack.get_children()):
        name=stack.child_get_property(child,'name')
        button=Gtk.ToggleButton(label=stack.child_get_property(child,'title'))
        def choose(widget,key=name):
            if not syncing[0]:stack.set_visible_child_name(key)
        button.connect('clicked',choose)
        grid.attach(button,index%3,index//3,1,1);buttons[name]=button
    def sync(*_):
        syncing[0]=True
        for key,button in buttons.items():button.set_active(stack.get_visible_child_name()==key)
        syncing[0]=False
    stack.connect('notify::visible-child-name',sync);sync()
    return grid


def label(text):
    widget = Gtk.Label(label=text, xalign=0); widget.set_line_wrap(True)
    widget.set_max_width_chars(32); return widget


def color_hex(control):
    colour = control.get_rgba()
    return '#' + ''.join(f'{round(channel * 255):02x}' for channel in (colour.red, colour.green, colour.blue))


def colour_button(value):
    widget = Gtk.ColorButton(); colour = Gdk.RGBA(); colour.parse(value); widget.set_rgba(colour)
    widget.set_title('Effect colour'); return widget


def duration_control(parent, value):
    parent.pack_start(label('Duration (seconds)'), False, False, 0)
    row = box(False)
    adjustment = Gtk.Adjustment(value=value, lower=.05, upper=10, step_increment=.05, page_increment=.1)
    slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
    slider.set_draw_value(False); slider.set_hexpand(True)
    slider.get_accessible().set_name('Effect duration')
    spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=.05, digits=2); spin.set_width_chars(4)
    row.pack_start(slider, True, True, 0); row.pack_start(spin, False, False, 0)
    parent.pack_start(row, False, False, 0)
    spin.slider=slider
    return spin


class EffectTiles(Gtk.Box):
    """Three-column choices, with ComboBox-compatible selection accessors."""
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, choices, active='none', transition=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        install_styles(); self.active = None; self.buttons = {}; self.transition = transition
        self.grid = Gtk.Grid(column_spacing=8, row_spacing=8); self.grid.set_column_homogeneous(True)
        self.pack_start(self.grid, False, False, 0)
        self.choices = list(choices)
        if active not in dict(self.choices):
            self.choices.append((active, transitions.get_spec(active)['label'] if transition else active.replace('_', ' ').title()))
        for index, (key, title) in enumerate(self.choices):
            button = Gtk.Button(); button.get_style_context().add_class('effect-tile')
            content = box(spacing=3); drawing = Gtk.DrawingArea(); drawing.set_size_request(84, 48)
            drawing.connect('draw', self.draw_icon, key); content.pack_start(drawing, False, False, 0)
            name = Gtk.Label(label=title); name.set_line_wrap(True); name.set_max_width_chars(12)
            content.pack_start(name, False, False, 0); button.add(content)
            button.set_tooltip_text(transitions.get_spec(key)['description'] if transition else title)
            button.get_accessible().set_name(title)
            button.connect('clicked', lambda _, choice=key: self.set_active_id(choice))
            self.grid.attach(button, index % 3, index // 3, 1, 1); self.buttons[key] = button
        self.set_active_id(active)

    def get_active_id(self):
        return self.active

    def set_active_id(self, key):
        if key not in self.buttons:
            return False
        if self.active == key:
            return True
        self.active = key
        for choice, button in self.buttons.items():
            context = button.get_style_context()
            (context.add_class if choice == key else context.remove_class)('selected')
            button.get_accessible().set_description('Selected' if choice == key else '')
        self.emit('changed'); return True

    def draw_icon(self, widget, cr, key):
        allocation = widget.get_allocation(); size = min(58, allocation.width - 12, allocation.height)
        x = (allocation.width - size) / 2; y = (allocation.height - size) / 2
        if self.transition:
            transitions.draw_icon(cr, key, x, y, size, colour=(.55, .24, 1))
            return False
        cr.save(); cr.translate(x, y); cr.scale(size / 60, size / 60)
        def rectangle(x, y, alpha=1, w=32, h=28):
            cr.set_source_rgba(.55, .24, 1, alpha); cr.rectangle(x, y, w, h); cr.fill()
        def arrow(x1, y1, x2, y2):
            cr.set_source_rgb(.55, .24, 1); cr.set_line_width(1.7)
            cr.move_to(x1, y1); cr.line_to(x2, y2); cr.stroke()
            angle = math.atan2(y2-y1, x2-x1)
            for delta in (-.6, .6):
                cr.move_to(x2, y2); cr.line_to(x2-6*math.cos(angle+delta), y2-6*math.sin(angle+delta)); cr.stroke()
        if key == 'none':
            arrow(12, 48, 48, 12)
        elif key in ('reveal', 'highlight', 'snake'):
            if key == 'highlight': rectangle(9, 16, .75, 18, 30)
            cr.select_font_face('Sans'); cr.set_font_size(25)
            for index, letter in enumerate('ABC'):
                cr.set_source_rgba(.55, .24, 1, .35 if key == 'reveal' and index == 2 else 1)
                cr.move_to(6+index*18, 40); cr.show_text(letter)
                if key == 'snake': rectangle(7+index*18, 44-index*2, 1, 15, 2)
        elif key == 'rise':
            for n in (2, 1, 0): rectangle(12, 6+n*9, .2 if n else 1)
            arrow(52, 46, 52, 12)
        elif key == 'pan':
            for n in (2, 1, 0): rectangle(20-n*8, 12, .2 if n else 1)
            arrow(8, 50, 52, 50)
        elif key == 'fade':
            for n in range(4): rectangle(9+n*10, 15, .2+n*.25, 10, 30)
        elif key == 'pop':
            for n in (0, 1, 2): rectangle(9+n*6, 9+n*6, .2+n*.4, 42-n*12, 42-n*12)
            for left in (True, False):
                cr.set_source_rgba(.55, .24, 1, .55); cr.set_line_width(1.5)
                cr.arc(30, 30, 27, .2 if not left else math.pi-.2, 1 if not left else math.pi-1)
                cr.stroke()
        elif key == 'wipe':
            rectangle(11, 14, .25, 38, 30); rectangle(11, 14, 1, 19, 30)
            cr.set_source_rgb(.55, .24, 1); cr.set_line_width(2); cr.move_to(30, 8); cr.line_to(30, 50); cr.stroke()
        else:
            # Soft concentric translucent shapes communicate a blur without a bitmap.
            for n in range(7, -1, -1): rectangle(16-n, 16-n, .025 if n else .65, 28+n*2, 28+n*2)
        cr.restore(); return False


class TransitionEditor(Gtk.Box):
    def __init__(self, item, choices=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        choices = choices or [(spec['key'], spec['label']) for spec in transitions.CATALOG]
        self.tiles = EffectTiles(choices, item.get('transition', 'none'), transition=True)
        self.pack_start(self.tiles, False, False, 0)
        self.duration = duration_control(self, item.get('transition_duration', .5))
        self.direction_row = box(False); self.direction_row.pack_start(label('Direction'), True, True, 0)
        self.direction = Gtk.ComboBoxText()
        for key in ('left', 'right', 'up', 'down'): self.direction.append(key, key.title())
        self.direction.set_active_id(item.get('transition_direction', 'left'))
        self.direction_row.pack_end(self.direction, False, False, 0); self.pack_start(self.direction_row, False, False, 0)
        self.colour_row = box(False); self.colour_row.pack_start(label('Colour'), True, True, 0)
        self.colour = colour_button(item.get('transition_colour', '#8b3dff')); self.colour_row.pack_end(self.colour, False, False, 0)
        self.pack_start(self.colour_row, False, False, 0)
        self.description = label(''); self.description.get_style_context().add_class('effect-description')
        self.description.get_style_context().add_class('dim-label'); self.pack_start(self.description, False, False, 0)
        self.tiles.connect('changed', self.selection_changed); self.selection_changed()

    def selection_changed(self, *_):
        spec = transitions.get_spec(self.tiles.get_active_id())
        self.description.set_text(spec['description'])
        for row, visible in ((self.direction_row, spec['directional']), (self.colour_row, spec['colour'])):
            row.set_no_show_all(not visible)
            if visible: row.show_all()
            else: row.hide()
        self.duration.set_sensitive(self.tiles.get_active_id() != 'none')
        self.duration.slider.set_sensitive(self.tiles.get_active_id() != 'none')

    def read_into(self, item):
        item.update(transition=self.tiles.get_active_id(), transition_duration=self.duration.get_value(),
                    transition_direction=self.direction.get_active_id(), transition_colour=color_hex(self.colour))
        return item


class AnimationEditor(Gtk.Box):
    def __init__(self, config=None, text=False, match_id=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        config = config or {}; stack = Gtk.Stack(); stack.set_hhomogeneous(False); stack.set_vhomogeneous(False)
        switch = Gtk.StackSwitcher(); switch.set_stack(stack); self.pack_start(switch, False, False, 0)
        self.enter = EffectTiles(ANIMATIONS, config.get('enter', 'none'))
        self.exit = EffectTiles(ANIMATIONS, config.get('exit', 'none'))
        stack.add_titled(self.enter, 'Enter', 'Enter'); stack.add_titled(self.exit, 'Exit', 'Exit')
        self.pack_start(stack, False, False, 0)
        self.duration = duration_control(self, config.get('duration', .5))
        row = box(False); row.pack_start(label('Direction'), True, True, 0); self.direction = Gtk.ComboBoxText()
        for key in ('left', 'right', 'up', 'down'): self.direction.append(key, key.title())
        self.direction.set_active_id(config.get('direction', 'up')); row.pack_end(self.direction, False, False, 0)
        self.pack_start(row, False, False, 0);self.direction_row=row
        self.text_effect = None; self.colour = colour_button(config.get('colour', '#8b3dff'))
        if text:
            self.pack_start(label('Text animation'), False, False, 0)
            self.text_effect = EffectTiles(TEXT_EFFECTS, config.get('text_effect', 'none')); self.pack_start(self.text_effect, False, False, 0)
            colour = box(False); colour.pack_start(label('Highlight colour'), True, True, 0); colour.pack_end(self.colour, False, False, 0)
            self.pack_start(colour, False, False, 0);self.colour_row=colour
        self.match_id = None
        if match_id is not None:
            self.pack_start(label('Match key'), False, False, 0); self.match_id = Gtk.Entry(); self.match_id.set_max_length(128)
            self.match_id.set_text(match_id); self.match_id.set_placeholder_text('Example: main-title')
            self.pack_start(self.match_id, False, False, 0)
            hint = label('For Match & Move, give the same key to corresponding text or image layers in neighboring scenes. Their position, size and opacity animate across the join.')
            hint.get_style_context().add_class('dim-label'); self.pack_start(hint, False, False, 0)
        clear = Gtk.Button(label='Clear animations'); clear.connect('clicked', self.clear); self.pack_start(clear, False, False, 0)
        for picker in (self.enter,self.exit,self.text_effect):
            if picker is not None:picker.connect('changed',self.selection_changed)
        self.selection_changed()

    def selection_changed(self,*_):
        effects=(self.enter.get_active_id(),self.exit.get_active_id())
        visible=any(effect in ('pan','wipe') for effect in effects)
        self.direction_row.set_no_show_all(not visible)
        if visible:self.direction_row.show_all()
        else:self.direction_row.hide()
        self.duration.set_sensitive(any(effect!='none' for effect in effects))
        self.duration.slider.set_sensitive(self.duration.get_sensitive())
        if self.text_effect is not None:
            visible=self.text_effect.get_active_id() in ('highlight','snake')
            self.colour_row.set_no_show_all(not visible)
            if visible:self.colour_row.show_all()
            else:self.colour_row.hide()

    def clear(self, *_):
        self.enter.set_active_id('none'); self.exit.set_active_id('none')
        if self.text_effect: self.text_effect.set_active_id('none')

    def read(self):
        return {'enter': self.enter.get_active_id(), 'exit': self.exit.get_active_id(),
                'duration': self.duration.get_value(), 'direction': self.direction.get_active_id(),
                'text_effect': self.text_effect.get_active_id() if self.text_effect else 'none',
                'colour': color_hex(self.colour)}


class SoundEditor(Gtk.Box):
    def __init__(self, owner, config=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.owner = owner; config = config or {}; self.path = config.get('path', ''); self.audition = None
        self.pack_start(label('Sound that follows this layer'), False, False, 0)
        self.preset = Gtk.ComboBoxText()
        for key, title in sound_effects.PRESETS: self.preset.append(key, title)
        self.preset.set_active_id(config.get('preset', 'none')); self.pack_start(self.preset, False, False, 0)
        self.file_row = box(); self.file_name = label(Path(self.path).name if self.path else 'No sound selected')
        self.file_row.pack_start(self.file_name, False, False, 0)
        choose = Gtk.Button(label='Choose WAV / MP3 sound…'); choose.connect('clicked', self.choose_file)
        self.file_row.pack_start(choose, False, False, 0); self.pack_start(self.file_row, False, False, 0)
        audition = Gtk.Button(label='▶ Preview sound'); audition.connect('clicked', self.preview)
        self.pack_start(audition, False, False, 0)
        self.pack_start(label('Volume'), False, False, 0)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 200, 1)
        self.volume.set_digits(0); self.volume.connect('format-value', lambda _, value: f'{value:.0f}%')
        self.volume.set_value(config.get('volume', .5)*100); self.pack_start(self.volume, False, False, 0)
        row = box(False); row.pack_start(label('Play when'), True, True, 0); self.trigger = Gtk.ComboBoxText()
        for key, title in (('enter', 'Layer appears'), ('exit', 'Layer disappears'), ('both', 'Both')): self.trigger.append(key, title)
        self.trigger.set_active_id(config.get('trigger', 'enter')); row.pack_end(self.trigger, False, False, 0)
        self.pack_start(row, False, False, 0)
        hint = label('Presets are original sounds generated locally. You can also use your own audio. Exit sounds finish at the layer’s end.')
        hint.get_style_context().add_class('dim-label'); self.pack_start(hint, False, False, 0)
        self.preset.connect('changed', self.selection_changed); self.selection_changed()
        self.connect('destroy', lambda _: self.stop_preview())

    def selection_changed(self, *_):
        self.stop_preview(); visible = self.preset.get_active_id() == 'file'; self.file_row.set_no_show_all(not visible)
        if visible: self.file_row.show_all()
        else: self.file_row.hide()

    def choose_file(self, *_):
        from .app import file_chooser, media_filters
        dialog = file_chooser(self.owner, 'Choose layer sound', Gtk.FileChooserAction.OPEN, 'Choose sound')
        for filt in media_filters('audio'): dialog.add_filter(filt)
        path = dialog.get_filename() if dialog.run() == Gtk.ResponseType.ACCEPT else None; dialog.destroy()
        if path:
            self.path = path; self.file_name.set_text(Path(path).name); self.preset.set_active_id('file')

    def preview(self, *_):
        self.stop_preview()
        try:
            config = self.read(); path = sound_effects.resolve(config)
            if path is None: return
            Gst.init(None); self.audition = Gst.ElementFactory.make('playbin', None)
            if self.audition is None: raise ValueError('The desktop audio player is not available.')
            self.audition.set_property('uri', path.resolve().as_uri()); self.audition.set_property('volume', config['volume'])
            self.audition.set_property('video-sink', Gst.ElementFactory.make('fakesink', None))
            bus = self.audition.get_bus(); bus.add_signal_watch(); bus.connect('message', self.sound_message)
            self.audition.set_state(Gst.State.PLAYING)
        except Exception as error:
            self.stop_preview(); self.owner.error(str(error))

    def sound_message(self, _, message):
        if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            error = message.parse_error()[0].message if message.type == Gst.MessageType.ERROR else None
            self.stop_preview()
            if error: self.owner.error('Could not preview this sound: ' + error)

    def stop_preview(self):
        if self.audition:
            bus = self.audition.get_bus(); bus.remove_signal_watch(); self.audition.set_state(Gst.State.NULL); self.audition = None

    def read(self):
        return {'preset': self.preset.get_active_id(), 'path': self.path,
                'volume': self.volume.get_value()/100, 'trigger': self.trigger.get_active_id()}
