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


def _text(cr, project, item, width, height):
    outline = float(item.get('outline_width', 2))
    padding = max(4, outline + 2)
    layout = _layout(cr, project, item, max(1, width-padding*2))
    _, extents = layout.get_pixel_extents()
    tx = padding
    ty = (height - extents.height)/2 - extents.y
    background = rgba(item.get('background', '#00000000'), '#00000000')
    if background[3]:
        cr.set_source_rgba(*background)
        cr.rectangle(0, 0, width, height)
        cr.fill()
    cr.move_to(tx, ty)
    if outline > 0:
        PangoCairo.layout_path(cr, layout)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(outline*2)
        cr.set_source_rgba(*rgba(item.get('outline_color', '#000000')))
        cr.stroke_preserve()
        cr.set_source_rgba(*rgba(item.get('color', '#ffffff')))
        cr.fill()
    else:
        cr.set_source_rgba(*rgba(item.get('color', '#ffffff')))
        PangoCairo.show_layout(cr, layout)


@lru_cache(maxsize=64)
def _image(path, size, mtime):
    # GdkPixbuf handles transparent PNG/JPEG and the first frame of a GIF. Video
    # frame decoding is supplied by the preview worker, never done in a draw call.
    return GdkPixbuf.Pixbuf.new_from_file(path)


def load_image(path):
    path = Path(path).expanduser().resolve()
    stat = path.stat()
    return _image(str(path), stat.st_size, stat.st_mtime_ns)


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


def _paint(cr, project, item, rect, assets=None):
    x, y, width, height = bounds(project, item, rect)
    scale = rect[2]/float(project['width'])
    local_width, local_height = width/scale, height/scale
    cr.save()
    cr.translate(x+width/2, y+height/2)
    cr.rotate(math.radians(float(item.get('rotation', 0))))
    cr.scale(scale, scale)
    cr.translate(-local_width/2, -local_height/2)
    cr.rectangle(0, 0, local_width, local_height)
    cr.clip()
    cr.push_group()
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
    cr.pop_group_to_source()
    cr.paint_with_alpha(float(item.get('opacity', 1)))
    cr.restore()


def caption_item(project, text):
    item = dict(project.get('caption_style', {}))
    item.update(kind='text', text=text)
    item.setdefault('x', .5)
    item.setdefault('y', .88)
    item.setdefault('width', .9)
    item.setdefault('height', .15)
    item.setdefault('font_size', 48)
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
    for item in project.get('layers', []):
        if float(item.get('start', 0)) <= seconds < float(item.get('end', 0)):
            _paint(cr, project, item, rect, assets)
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
        _paint(cr, project, caption_item(project, cue['text']), rect)
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
