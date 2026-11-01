"""Geometry for moving and resizing a clip behind a fixed canvas crop.

The source image is already oriented (rotation, flips and sample aspect applied).
Offsets are measured in canvas widths/heights, so placement is independent of
preview resolution, editor size and view magnification.
"""
import math


def _positive(*values):
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError('Crop dimensions must be positive and finite.')


def _clamp(value, low, high):
    return max(low, min(high, value))


def picture_rect(clip, source_width, source_height, canvas_rect):
    """Place the complete oriented source behind a fixed canvas rectangle."""
    x, y, width, height = canvas_rect
    _positive(source_width, source_height, width, height)
    zoom = _clamp(float(clip.get('zoom', 1)), 1., 4.)
    fit = min if clip.get('fit', 'fill') == 'fit' else max
    scale = fit(width/source_width, height/source_height)*zoom
    picture_w, picture_h = source_width*scale, source_height*scale
    px = _clamp(clip.get('position_x', .5), 0., 1.) if picture_w > width else .5
    py = _clamp(clip.get('position_y', .5), 0., 1.) if picture_h > height else .5
    x += (width-picture_w)*px + _clamp(clip.get('offset_x', 0), -1., 1.)*width
    y += (height-picture_h)*py + _clamp(clip.get('offset_y', 0), -1., 1.)*height
    return x, y, picture_w, picture_h


def move(clip, canvas_rect, dx, dy):
    """Return the saved offsets after moving the picture by dx, dy."""
    _positive(canvas_rect[2], canvas_rect[3])
    return {'offset_x': _clamp(clip.get('offset_x', 0)+dx/canvas_rect[2], -1., 1.),
            'offset_y': _clamp(clip.get('offset_y', 0)+dy/canvas_rect[3], -1., 1.)}


def resize(clip, canvas_rect, corner, dx, dy, source_width=None, source_height=None,
           handle_rect=None):
    """Resize a corner drag while preserving the opposite content anchor.

    Corners are nw, ne, sw or se. Dragging outward enlarges the picture within
    the saved 100–400% zoom range. Supply source dimensions for asymmetric crop
    positions and Fit borders; otherwise matching source/canvas ratios are used.
    handle_rect can be the visible viewport intersection when the canvas is
    magnified. Its opposite corner remains fixed while saved offsets continue
    to use the complete canvas dimensions.
    """
    if corner not in ('nw', 'ne', 'sw', 'se'):
        raise ValueError('Unsupported crop corner.')
    x, y, width, height = canvas_rect
    _positive(width, height)
    hx, hy, hw, hh = canvas_rect if handle_rect is None else handle_rect
    _positive(hw, hh)
    source_width = width if source_width is None else source_width
    source_height = height if source_height is None else source_height
    west, north = corner[1] == 'w', corner[0] == 'n'
    sx, sy = (-1 if west else 1), (-1 if north else 1)
    # Project the dragged diagonal onto the original aspect ratio. Both mouse
    # axes contribute, so horizontal and vertical drags behave consistently.
    factor = (hw*(hw+sx*dx)+hh*(hh+sy*dy))/(hw*hw+hh*hh)
    zoom = _clamp(float(clip.get('zoom', 1)), 1., 4.)
    new_zoom = _clamp(zoom*factor, 1., 4.)
    factor = new_zoom/zoom
    anchor_x, anchor_y = (hx+hw if west else hx), (hy+hh if north else hy)
    before = picture_rect(clip, source_width, source_height, canvas_rect)
    enlarged = dict(clip, zoom=new_zoom, offset_x=0., offset_y=0.)
    base = picture_rect(enlarged, source_width, source_height, canvas_rect)
    left = anchor_x+(before[0]-anchor_x)*factor
    top = anchor_y+(before[1]-anchor_y)*factor
    return {'zoom': new_zoom,
            'offset_x': _clamp((left-base[0])/width, -1., 1.),
            'offset_y': _clamp((top-base[1])/height, -1., 1.)}
