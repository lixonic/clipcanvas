"""One native text-style editor shared by titles and editable captions."""
import copy
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, Pango
from . import text_styles, visuals


def column(spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


class TextStyleEditor:
    def __init__(self, parent, style):
        self.base = copy.deepcopy(style)
        self.loading = True
        self.sample = 'Your words stay editable'
        self.preset_buttons = {}
        label = Gtk.Label(label='Text styles', xalign=0)
        label.get_style_context().add_class('section')
        parent.pack_start(label, False, False, 0)
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        for index, (key, title, description) in enumerate(text_styles.PRESETS):
            button = Gtk.Button()
            content = column(3);content.set_border_width(4)
            preview = Gtk.DrawingArea();preview.set_size_request(126, 44)
            preview.connect('draw', self.draw_preset, key)
            content.pack_start(preview, False, False, 0)
            content.pack_start(Gtk.Label(label=title), False, False, 0)
            button.add(content);button.set_tooltip_text(description+' Words, timing and placement stay unchanged.')
            button.connect('clicked', lambda _, p=key:self.choose(p))
            grid.attach(button, index % 2, index // 2, 1, 1)
            self.preset_buttons[key] = button
        parent.pack_start(grid, False, False, 0)
        self.preview = Gtk.DrawingArea();self.preview.set_size_request(300, 66)
        self.preview.connect('draw', self.draw_preview)
        parent.pack_start(self.preview, False, False, 0)
        self.font = Gtk.FontButton()
        parent.pack_start(self.font, False, False, 0)
        row = Gtk.Box(spacing=12)
        self.bold = Gtk.CheckButton(label='Bold');self.italic = Gtk.CheckButton(label='Italic')
        row.pack_start(self.bold, True, True, 0);row.pack_start(self.italic, True, True, 0)
        parent.pack_start(row, False, False, 0)
        self.colors = {}
        for key, title in (('color','Text color'), ('background','Background'),
                           ('outline_color','Outline'), ('shadow_color','Shadow color')):
            picker = Gtk.ColorButton();picker.set_use_alpha(True);self.colors[key] = picker
            self.row(parent, title, picker)
        self.outline = self.number(parent, 'Outline thickness', 0, 64, .5)
        self.align = Gtk.ComboBoxText()
        for value in ('left', 'center', 'right'):self.align.append(value, value.capitalize())
        self.row(parent, 'Alignment', self.align)
        details = column()
        self.background_mode = Gtk.ComboBoxText()
        self.background_mode.append('box', 'Full text box')
        self.background_mode.append('text', 'Follow each line')
        self.row(details, 'Background shape', self.background_mode)
        self.background_radius = self.number(details, 'Roundness', 0, 256, 1)
        self.background_padding = self.number(details, 'Padding', 0, 128, 1)
        self.shadow_x = self.number(details, 'Shadow horizontal', -256, 256, .5)
        self.shadow_y = self.number(details, 'Shadow vertical', -256, 256, .5)
        self.shadow_blur = self.number(details, 'Shadow softness', 0, 64, .5)
        expander = Gtk.Expander(label='Background and shadow adjustments');expander.add(details)
        parent.pack_start(expander, False, False, 0)
        self.set_style(style)
        self.loading = False
        self.font.connect('font-set', self.changed)
        for widget in (self.bold, self.italic):widget.connect('toggled', self.changed)
        for widget in self.colors.values():widget.connect('color-set', self.changed)
        for widget in (self.align, self.background_mode):widget.connect('changed', self.changed)
        for widget in (self.outline, self.background_radius, self.background_padding,
                       self.shadow_x, self.shadow_y, self.shadow_blur):widget.connect('value-changed', self.changed)

    @staticmethod
    def row(parent, title, widget):
        line = Gtk.Box(spacing=8)
        line.pack_start(Gtk.Label(label=title, xalign=0), True, True, 0)
        line.pack_end(widget, False, False, 0);parent.pack_start(line, False, False, 0)

    def number(self, parent, title, low, high, step):
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_digits(1 if step < 1 else 0);spin.set_width_chars(4)
        self.row(parent, title, spin);return spin

    def set_style(self, style):
        self.loading = True
        self.font.set_font_name(f'{style.get("font_family","Sans")} {style.get("font_size",48)}')
        self.bold.set_active(style.get('bold',False));self.italic.set_active(style.get('italic',False))
        for key, picker in self.colors.items():
            picker.set_rgba(Gdk.RGBA(*visuals.rgba(style.get(key,'#00000000'))))
        self.outline.set_value(style.get('outline_width',0))
        self.align.set_active_id(style.get('align','center'))
        self.background_mode.set_active_id(style.get('background_mode','box'))
        for key, default in text_styles.effect_defaults().items():
            widget = getattr(self, key, None)
            if isinstance(widget,Gtk.SpinButton):widget.set_value(style.get(key,default))
        self.loading = False
        self.preview.queue_draw()

    def read_into(self, item):
        desc = Pango.FontDescription(self.font.get_font_name())
        item['font_family'] = desc.get_family();item['font_size'] = desc.get_size()/Pango.SCALE
        item.update(bold=self.bold.get_active(), italic=self.italic.get_active(),
                    align=self.align.get_active_id(), outline_width=self.outline.get_value(),
                    background_mode=self.background_mode.get_active_id())
        for key, picker in self.colors.items():
            color = picker.get_rgba()
            item[key] = '#'+''.join(f'{round(v*255):02x}' for v in (color.red,color.green,color.blue,color.alpha))
        for key in ('background_radius','background_padding','shadow_x','shadow_y','shadow_blur'):
            item[key] = getattr(self,key).get_value()
        return item

    def choose(self, preset):
        style = self.read_into(copy.deepcopy(self.base))
        self.set_style(text_styles.apply_preset(style,preset))
        for key, button in self.preset_buttons.items():
            context = button.get_style_context()
            context.add_class('suggested-action') if key==preset else context.remove_class('suggested-action')

    def changed(self, *_):
        if not self.loading:
            self.preview.queue_draw()
            for button in self.preset_buttons.values():button.get_style_context().remove_class('suggested-action')

    def set_sample(self, text):
        self.sample = text[:150] or 'Your words stay editable';self.preview.queue_draw()

    def paint_sample(self, widget, cr, style, text):
        width,height = widget.get_allocated_width(),widget.get_allocated_height()
        cr.set_source_rgb(.12,.22,.28);cr.paint()
        cr.set_source_rgba(.28,.48,.49,.5)
        cr.move_to(0,height);cr.line_to(width*.35,0);cr.line_to(width*.7,height);cr.fill()
        # Fixed preview font size keeps font-picker changes legible in this small
        # sample; effect widths are scaled by the same factor as the real glyphs.
        scale = 22/max(1,style.get('font_size',48))
        for key in ('outline_width','shadow_x','shadow_y','shadow_blur',
                    'background_radius','background_padding'):
            style[key] = style.get(key,0)*scale
        style.update(kind='text',text=text,font_size=22,x=.5,y=.5,width=.96,height=.92,
                     rotation=0,opacity=1)
        visuals._paint(cr,{'width':width,'height':height},style,(0,0,width,height))
        return False

    def draw_preset(self, widget, cr, key):
        from . import model
        self.paint_sample(widget,cr,text_styles.apply_preset(model.text_style(),key),'Aa')
        return False

    def draw_preview(self, widget, cr):
        self.paint_sample(widget,cr,self.read_into(copy.deepcopy(self.base)),self.sample)
        return False
