"""Shared Cairo/Pango visual layout for native preview and offline export.

All coordinates in project data refer to the full output canvas. Text is rendered
with Pango rather than interpolated into an FFmpeg expression, so quotes, Unicode,
multiline titles, and subtitle wording remain plain text.
"""

from functools import lru_cache
import math
from pathlib import Path

import cairo
import gi

gi.require_version('PangoCairo', '1.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Pango, PangoCairo

from . import animations, bundled_fonts, color_grading

bundled_fonts.register()


def rgba(value, default='#ffffff'):
    """Read CSS hex colors without consulting a desktop theme."""
    text = str(value or default).lstrip('#')
    if len(text) in (3, 4):
        text = ''.join(c * 2 for c in text)
    if len(text) == 6:
        text += 'ff'
    try:
        return tuple(int(text[i:i+2], 16) / 255 for i in (0, 2, 4, 6))
    except (ValueError, IndexError):
        return rgba(default, '#ffffff') if value != default else (1, 1, 1, 1)


def bounds(project, layer, rect):
    """Return the unrotated (left, top, width, height) of a visual layer."""
    x, y, w, h = rect
    bw, bh = float(layer.get('width', .7))*w, float(layer.get('height', .18))*h
    return (x + float(layer.get('x', .5))*w - bw/2,
            y + float(layer.get('y', .5))*h - bh/2, bw, bh)


def _layout(cr, project, item, width):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription()
    font.set_family(item.get('font_family', 'Sans'))
    font.set_absolute_size(max(1, float(item.get('font_size', 64))) * Pango.SCALE)
    font.set_weight(Pango.Weight.BOLD if item.get('bold') else Pango.Weight.NORMAL)
    font.set_style(Pango.Style.ITALIC if item.get('italic') else Pango.Style.NORMAL)
    layout.set_font_description(font)
    layout.set_text(item.get('text', ''), -1)
    layout.set_width(max(1, round(width * Pango.SCALE)))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_alignment({'left': Pango.Alignment.LEFT, 'right': Pango.Alignment.RIGHT}.get(
        item.get('align', 'center'), Pango.Alignment.CENTER))
    return layout


def _rounded_box(cr, x, y, width, height, radius):
    radius = max(0, min(radius, width/2, height/2))
    if not radius:
        cr.rectangle(x, y, width, height)
        return
    cr.new_sub_path()
    for cx, cy, angle in ((x+width-radius, y+radius, -math.pi/2),
                          (x+width-radius, y+height-radius, 0),
                          (x+radius, y+height-radius, math.pi/2),
                          (x+radius, y+radius, math.pi)):
        cr.arc(cx, cy, radius, angle, angle+math.pi/2)
    cr.close_path()


def _text(cr, project, item, width, height):
    outline = float(item.get('outline_width', 2))
    background = rgba(item.get('background', '#00000000'), '#00000000')
    shadow = rgba(item.get('shadow_color', '#00000000'), '#00000000')
    tight = item.get('background_mode', 'box') == 'text'
    background_padding = float(item.get('background_padding', 8))
    padding = max(4, outline+2, background_padding if tight and background[3] else 0)
    if shadow[3]:
        padding = max(padding, abs(float(item.get('shadow_x', 3)))+float(item.get('shadow_blur', 0)))
    layout = _layout(cr, project, item, max(1, width-padding*2))
    _, extents = layout.get_pixel_extents()
    tx = padding
    ty = (height-extents.height)/2-extents.y
    if background[3]:
        cr.set_source_rgba(*background)
        radius = float(item.get('background_radius', 0))
        if tight:
            iterator = layout.get_iter()
            while True:
                ink, _ = iterator.get_line_extents()
                if ink.width > 0:
                    _rounded_box(cr, tx+ink.x/Pango.SCALE-background_padding,
                                 ty+ink.y/Pango.SCALE-background_padding,
                                 ink.width/Pango.SCALE+2*background_padding,
                                 ink.height/Pango.SCALE+2*background_padding, radius)
                if not iterator.next_line():
                    break
        else:
            _rounded_box(cr, 0, 0, width, height, radius)
        cr.fill()
    effect = item.get('_text_effect', 'none')
    word_boxes = _word_boxes(layout, item.get('text', ''), tx, ty) if effect != 'none' else []
    first, last = animations.text_word_range(item, int(item.get('_text_step', -1)))
    active_boxes = [box for index, box in word_boxes if first <= index < last]
    if effect == 'highlight':
        cr.set_source_rgba(*rgba(animations.normalise(item.get('animation'))['colour']))
        for bx, by, bw, bh in active_boxes:
            _rounded_box(cr, bx-3, by-1, bw+6, bh+2, 4)
            cr.fill()
    cr.save()
    if effect == 'reveal':
        for index, box in word_boxes:
            if index < last:
                cr.rectangle(box[0]-outline-2, box[1]-outline-2,
                             box[2]+2*outline+4, box[3]+2*outline+4)
        cr.clip()
    if shadow[3] or outline:
        cr.move_to(tx, ty)
        PangoCairo.layout_path(cr, layout)
        path = cr.copy_path()
        cr.new_path()
        if shadow[3]:
            blur = float(item.get('shadow_blur', 0))
            samples = ((0, 0, 1),) if not blur else tuple(
                (x*blur, y*blur, weight_x*weight_y/16)
                for x, weight_x in ((-1, 1), (0, 2), (1, 1))
                for y, weight_y in ((-1, 1), (0, 2), (1, 1)))
            for dx, dy, weight in samples:
                cr.save()
                cr.translate(float(item.get('shadow_x', 3))+dx,
                             float(item.get('shadow_y', 3))+dy)
                cr.append_path(path)
                cr.set_source_rgba(*shadow[:3], shadow[3]*weight)
                cr.fill()
                cr.restore()
        if outline:
            cr.append_path(path)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_width(outline*2)
            cr.set_source_rgba(*rgba(item.get('outline_color', '#000000')))
            cr.stroke_preserve()
            cr.set_source_rgba(*rgba(item.get('color', '#ffffff')))
            cr.fill()
        else:
            cr.move_to(tx, ty)
            cr.set_source_rgba(*rgba(item.get('color', '#ffffff')))
            PangoCairo.show_layout(cr, layout)
    else:
        cr.move_to(tx, ty)
        cr.set_source_rgba(*rgba(item.get('color', '#ffffff')))
        PangoCairo.show_layout(cr, layout)
    cr.restore()
    if effect == 'snake':
        colour = rgba(animations.normalise(item.get('animation'))['colour'])
        cr.save()
        for box in active_boxes:
            cr.rectangle(*box)
        cr.clip()
        cr.move_to(tx, ty)
        cr.set_source_rgba(*colour)
        PangoCairo.show_layout(cr, layout)
        cr.restore()
        cr.set_source_rgba(*colour)
        cr.set_line_width(max(2, float(item.get('font_size', 48))*.045))
        for bx, by, bw, bh in active_boxes:
            cr.move_to(bx, by+bh-1)
            cr.line_to(bx+bw, by+bh-1)
            cr.stroke()


def _word_boxes(layout, text, tx, ty):
    """Pango character extents keep whole words shaped correctly in any script."""
    spans = animations.words(text)
    if not spans:
        return []
    # Pango indices use UTF-8 bytes; Python regular expressions use characters.
    byte_spans = [(len(text[:start].encode('utf-8')), len(text[:end].encode('utf-8')))
                  for start, end in spans]
    rectangles = {}
    iterator = layout.get_iter()
    while True:
        index = iterator.get_index()
        word = next((number for number, (begin, end) in enumerate(byte_spans)
                     if begin <= index < end), None)
        if word is not None:
            extent = iterator.get_char_extents()
            x, y = tx+extent.x/Pango.SCALE, ty+extent.y/Pango.SCALE
            w, h = extent.width/Pango.SCALE, extent.height/Pango.SCALE
            key = (word, round(y, 4))
            if key in rectangles:
                ox, oy, ow, oh = rectangles[key]
                rectangles[key] = (min(ox, x), oy, max(ox+ow, x+w)-min(ox, x), max(oh, h))
            else:
                rectangles[key] = (x, y, w, h)
        if not iterator.next_char():
            break
    return [(word, box) for (word, _), box in rectangles.items()]


@lru_cache(maxsize=64)
def _image(path, size, mtime):
    # GdkPixbuf handles transparent PNG/JPEG and the first frame of a GIF. Video
    # frame decoding is supplied by the preview worker, never done in a draw call.
    return GdkPixbuf.Pixbuf.new_from_file(path)


def load_image(path):
    path = Path(path).expanduser().resolve()
    stat = path.stat()
    return _image(str(path), stat.st_size, stat.st_mtime_ns)


def saturated_picture(pixbuf, saturation=1):
    """Adjust a copy so shared source frames and library thumbnails keep color."""
    if saturation == 1:
        return pixbuf
    result = pixbuf.copy()
    pixbuf.saturate_and_pixelate(result, saturation, False)
    return result


def saturated_rgb(color, saturation=1):
    red, green, blue = rgba(color)[:3]
    gray = .299*red + .587*green + .114*blue
    return tuple(min(1,max(0,gray+(channel-gray)*saturation)) for channel in (red, green, blue))


def graded_picture(pixbuf, clip):
    """Translate channel bytes in native code, preserving alpha and row padding."""
    if color_grading.rgb_active(clip):
        width, height = pixbuf.get_width(), pixbuf.get_height()
        channels, stride = pixbuf.get_n_channels(), pixbuf.get_rowstride()
        source = pixbuf.get_pixels()
        result = bytearray(source)
        tables = color_grading.tables(clip)
        for row in range(height):
            start, end = row*stride, row*stride+width*channels
            for channel in range(3):
                result[start+channel:end:channels] = source[start+channel:end:channels].translate(tables[channel])
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(result)),
            GdkPixbuf.Colorspace.RGB, pixbuf.get_has_alpha(), 8, width, height, stride)
    return saturated_picture(pixbuf, color_grading.saturation(clip))


def graded_rgb(clip):
    color = clip['color']
    if color_grading.rgb_active(clip):
        tables = color_grading.tables(clip)
        color = '#'+''.join(f'{table[int(color[1+index*2:3+index*2],16)]:02x}' for index,table in enumerate(tables))
    return saturated_rgb(color, color_grading.saturation(clip))


def _picture(cr, pixbuf, width, height, fit='fit'):
    if isinstance(pixbuf, cairo.ImageSurface):
        sw, sh = pixbuf.get_width(), pixbuf.get_height()
    else:
        sw, sh = pixbuf.get_width(), pixbuf.get_height()
    factor = (min if fit == 'fit' else max)(width/sw, height/sh)
    cr.translate((width-sw*factor)/2, (height-sh*factor)/2)
    cr.scale(factor, factor)
    if isinstance(pixbuf, cairo.ImageSurface):
        cr.set_source_surface(pixbuf, 0, 0)
    else:
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    cr.paint()


def _content(cr, project, item, local_width, local_height, assets=None):
    if item.get('kind', 'text') == 'text':
        _text(cr, project, item, local_width, local_height)
    else:
        pixbuf = None
        if assets:
            pixbuf = assets.get(item.get('id'))
            if pixbuf is None:
                pixbuf = assets.get(item.get('path'))
        if pixbuf is None and item.get('kind') == 'image':
            try:
                pixbuf = load_image(item['path'])
            except (OSError, GLib.Error):
                pass
        if pixbuf is not None:
            _picture(cr, pixbuf, local_width, local_height, item.get('fit', 'fit'))


def soften(surface):
    """A bounded, deterministic soft-focus sprite used by both rendering paths."""
    width, height = surface.get_width(), surface.get_height()
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, math.ceil(width/12)), max(1, math.ceil(height/12)))
    cr = cairo.Context(small)
    cr.scale(small.get_width()/width, small.get_height()/height)
    cr.set_source_surface(surface)
    cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    cr.paint()
    result = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(result)
    cr.scale(width/small.get_width(), height/small.get_height())
    cr.set_source_surface(small)
    cr.get_source().set_filter(cairo.FILTER_BILINEAR)
    cr.paint()
    return result


def _paint(cr, project, item, rect, assets=None, seconds=None):
    x, y, width, height = bounds(project, item, rect)
    scale = rect[2]/float(project['width'])
    local_width, local_height = width/scale, height/scale
    motion = animations.state(item, seconds) if seconds is not None else animations.state({}, 1)
    placement = animations.placement(item, seconds) if seconds is not None else animations.placement(item, -1e9)
    content = dict(item)
    if seconds is not None:
        content['_text_effect'] = animations.normalise(item.get('animation'))['text_effect']
        content['_text_step'] = motion['text_step']
    cr.save()
    cr.translate(rect[0]+(placement['x']+motion['dx'])*rect[2], rect[1]+(placement['y']+motion['dy'])*rect[3])
    cr.rotate(math.radians(placement['rotation']))
    cr.scale(scale*motion['scale']*placement['width']/float(item.get('width', .7)),
             scale*motion['scale']*placement['height']/float(item.get('height', .18)))
    cr.translate(-local_width/2, -local_height/2)
    cr.rectangle(0, 0, local_width, local_height)
    cr.clip()
    if motion['wipe'] < 1:
        direction = animations.normalise(item.get('animation'))['direction']
        fraction = motion['wipe']
        if direction in ('left', 'right'):
            cr.rectangle(local_width*(1-fraction) if direction == 'left' else 0, 0,
                         local_width*fraction, local_height)
        else:
            cr.rectangle(0, local_height*(1-fraction) if direction == 'up' else 0,
                         local_width, local_height*fraction)
        cr.clip()
    cr.push_group()
    if motion['blur'] > 0:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, math.ceil(local_width)), max(1, math.ceil(local_height)))
        _content(cairo.Context(surface), project, content, local_width, local_height, assets)
        cr.set_operator(cairo.OPERATOR_ADD)
        cr.set_source_surface(surface)
        cr.paint_with_alpha(1-motion['blur'])
        cr.set_source_surface(soften(surface))
        cr.paint_with_alpha(motion['blur'])
        cr.set_operator(cairo.OPERATOR_OVER)
    else:
        _content(cr, project, content, local_width, local_height, assets)
    cr.pop_group_to_source()
    cr.paint_with_alpha(placement['opacity']*motion['opacity'])
    cr.restore()


def caption_item(project, text, cue=None):
    item = dict(project.get('caption_style', {}))
    item.update(kind='text', text=text)
    item.setdefault('x', .5)
    item.setdefault('y', .88)
    item.setdefault('width', .9)
    item.setdefault('height', .15)
    item.setdefault('font_size', 48)
    if cue is not None:
        item.update(start=cue['start'], end=cue['end'])
        if 'animation' in cue:
            item['animation'] = cue['animation']
    return item


def active_captions(project, seconds):
    return [cue for cue in project.get('captions', [])
            if float(cue['start']) <= seconds < float(cue['end'])]


def draw(cr, project, seconds, rect, selected=None, assets=None):
    """Paint active visual layers and captions, in the same order as export.

    ``assets`` optionally maps a layer ID or path to a decoded GdkPixbuf or Cairo
    surface. ``selected`` is an optional layer ID for a lightweight canvas outline.
    The caller must skip this for a prepared preview that already includes visuals.
    """
    cr.save()
    cr.rectangle(*rect)
    cr.clip()
    for item in animations.prepare_layers(project):
        if max(float(item.get('start', 0)), item.get('_suppress_until', 0)) <= seconds < float(item.get('end', 0)):
            _paint(cr, project, item, rect, assets, seconds)
            if selected in (item.get('id'), item):
                x, y, w, h = bounds(project, item, rect)
                cr.save()
                cr.translate(x+w/2, y+h/2)
                cr.rotate(math.radians(float(item.get('rotation', 0))))
                cr.set_source_rgba(.65, .40, 1, 1)
                cr.set_line_width(2)
                cr.rectangle(-w/2, -h/2, w, h)
                cr.stroke()
                cr.restore()
    cues = active_captions(project, seconds)
    for cue in cues:
        _paint(cr, project, caption_item(project, cue['text'], cue), rect, seconds=seconds)
    cr.restore()


def sprite_geometry(project, item, render_size=None):
    """Return a rotated sprite's integer (left, top, width, height) at output size."""
    w, h = render_size or (project['width'], project['height'])
    x, y, bw, bh = bounds(project, item, (0, 0, w, h))
    angle = math.radians(float(item.get('rotation', 0)))
    rw = abs(bw*math.cos(angle)) + abs(bh*math.sin(angle))
    rh = abs(bw*math.sin(angle)) + abs(bh*math.cos(angle))
    left, top = math.floor(x+bw/2-rw/2), math.floor(y+bh/2-rh/2)
    right, bottom = math.ceil(x+bw/2+rw/2), math.ceil(y+bh/2+rh/2)
    return left, top, max(1, right-left), max(1, bottom-top)


def rasterize(project, item, output, render_size=None, assets=None):
    """Write a bounded transparent PNG; return its output canvas left/top."""
    w, h = render_size or (project['width'], project['height'])
    left, top, sw, sh = sprite_geometry(project, item, (w, h))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
    cr = cairo.Context(surface)
    cr.translate(-left, -top)
    _paint(cr, project, item, (0, 0, w, h), assets)
    surface.write_to_png(str(output))
    return left, top


def rasterize_text(project, layer, output, render_size=None):
    return rasterize(project, dict(layer, kind='text'), output, render_size)
