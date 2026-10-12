"""Clip join styles shared by the editor, live preview and export.

These are local implementations of familiar transition styles, not Canva's
proprietary renderer. Match & Move handles editable layers in layer_motion;
the already flattened video backgrounds dissolve underneath those layers.
"""

from __future__ import annotations

import math
import re


DEFAULT_COLOUR = "#8b3dff"
DIRECTIONS = ("left", "right", "up", "down")
CATALOG = (
    {"key": "none", "label": "None", "description": "Cut directly to the next clip.", "directional": False, "colour": False},
    {"key": "fade", "label": "Dissolve", "description": "Blend smoothly into the next clip.", "directional": False, "colour": False},
    {"key": "slide", "label": "Slide", "description": "Push the current clip away as the next clip slides in.", "directional": True, "colour": False},
    {"key": "circle_wipe", "label": "Circle Wipe", "description": "Reveal the next clip through an expanding circle.", "directional": False, "colour": False},
    {"key": "colour_wipe", "label": "Colour Wipe", "description": "Sweep a page of the chosen colour between the clips.", "directional": True, "colour": True},
    {"key": "line_wipe", "label": "Line Wipe", "description": "A coloured line reveals the next clip as it crosses the canvas.", "directional": True, "colour": True},
    {"key": "match_move", "label": "Match & Move", "description": "Move matching editable layers between their placements. Give both layers the same matching key and content. Video backgrounds dissolve; objects inside a video cannot be matched.", "directional": False, "colour": False},
    {"key": "flow", "label": "Flow", "description": "Blend the clips with smooth, eased movement.", "directional": True, "colour": False},
    {"key": "stack", "label": "Stack", "description": "Slide the next clip over the stationary current clip.", "directional": True, "colour": False},
)
KEYS = tuple(spec["key"] for spec in CATALOG)
LEGACY_KEYS = ("wipeleft", "slideright", "dissolve")
_SPECS = {spec["key"]: spec for spec in CATALOG}
_LEGACY_DISPLAY = {"wipeleft": "line_wipe", "slideright": "slide", "dissolve": "fade"}


def canonical_key(value):
    """Return the display equivalent without changing saved legacy rendering."""
    key = _LEGACY_DISPLAY.get(value, value)
    if key not in _SPECS:
        raise ValueError("Unsupported clip transition.")
    return key


def get_spec(value):
    spec = dict(_SPECS[canonical_key(value)])
    if value in LEGACY_KEYS:
        spec["legacy"] = True
        spec["description"] = {"wipeleft": "Keep the existing plain left wipe. Select Line Wipe to add a coloured boundary.",
                               "slideright": "Keep the existing right slide. Select Slide to choose its direction.",
                               "dissolve": "Keep the existing pixel dissolve. Select Dissolve to use a smooth blend."}[value]
    return spec


def _number(value, *, positive=False):
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError("Transition timing must be a finite number.") from error
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise ValueError("Transition timing must be a finite positive number.")
    return format(result, ".8f").rstrip("0").rstrip(".") or "0"


def _colour_plane(value):
    # xfade negotiates an 8-bit planar YUV format in the engine. Use the same
    # limited-range BT.601 conversion as FFmpeg's colour source at that stage.
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Transition colour must be a six-digit hex colour.")
    red, green, blue = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    y = round(16 + .256788 * red + .504129 * green + .097906 * blue)
    u = round(128 - .148223 * red - .290993 * green + .439216 * blue)
    v = round(128 + .439216 * red - .367788 * green - .071427 * blue)
    return f"if(eq(PLANE,0),{y},if(eq(PLANE,1),{u},{v}))"


def _axis(direction):
    return {"left": "(W-1-X)/W", "right": "X/W", "up": "(H-1-Y)/H", "down": "Y/H"}[direction]


def _sample(stream, x, y):
    return (f"if(eq(PLANE,0),{stream}0({x},{y}),"
            f"if(eq(PLANE,1),{stream}1({x},{y}),{stream}2({x},{y})))")


def join_filter(clip, overlap, offset):
    """Build only whitelisted xfade options; no project string becomes code.

    ``P`` is FFmpeg's outgoing weight, from 1 to 0. Legacy keys keep their
    original FFmpeg operation even when the editor displays a newer label.
    """
    key = clip.get("transition", "fade")
    canonical_key(key)
    duration = _number(overlap, positive=True)
    start = _number(offset)
    direction = clip.get("transition_direction", "left")
    if direction not in DIRECTIONS:
        raise ValueError("Unsupported transition direction.")
    colour = _colour_plane(clip.get("transition_colour", DEFAULT_COLOUR))
    suffix = f":duration={duration}:offset={start}"
    if key == "none":
        raise ValueError("A cut has no transition overlap.")
    if key in LEGACY_KEYS or key == "fade":
        return f"xfade=transition={key}{suffix}"
    if key == "match_move":
        # Layer matching is composed after this raster background transition.
        return f"xfade=transition=fade{suffix}"
    if key in ("slide", "stack"):
        prefix = "slide" if key == "slide" else "cover"
        return f"xfade=transition={prefix}{direction}{suffix}"
    if key == "circle_wipe":
        expression = "if(gte(P,1),A,if(lte(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*(1-P)),B,A))"
    elif key == "colour_wipe":
        axis = _axis(direction)
        expression = f"if(lt({axis},1-2*P),B,if(lt({axis},2-2*P),{colour},A))"
    elif key == "line_wipe":
        axis = _axis(direction)
        expression = f"if(lte(P,0),B,if(gte(P,1),A,if(lt({axis},1-P-0.02),B,if(lt({axis},1-P+0.02),{colour},A))))"
    elif key == "flow":
        # Translate both scenes while blending, with zero velocity at either
        # end. This is a motion crossfade, distinct from both push and cover.
        eased = "((1-P)*(1-P)*(1+2*P))"
        axis, sign = ("W", 1 if direction == "left" else -1) if direction in ("left", "right") else ("H", 1 if direction == "up" else -1)
        shift_a, shift_b = f"{sign}*{axis}*0.2*{eased}", f"-{sign}*{axis}*0.2*(1-{eased})"
        if direction in ("left", "right"):
            a, b = _sample("a", f"X+({shift_a})", "Y"), _sample("b", f"X+({shift_b})", "Y")
        else:
            a, b = _sample("a", "X", f"Y+({shift_a})"), _sample("b", "X", f"Y+({shift_b})")
        expression = f"{a}*P+{b}*(1-P)"
    else:
        raise ValueError("Unsupported clip transition.")
    return f"xfade=transition=custom{suffix}:expr='{expression}'"


def _rounded(cr, x, y, width, height, radius=.06):
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    for cx, cy, a, b in ((x + width - radius, y + radius, -math.pi / 2, 0),
                         (x + width - radius, y + height - radius, 0, math.pi / 2),
                         (x + radius, y + height - radius, math.pi / 2, math.pi),
                         (x + radius, y + radius, math.pi, math.pi * 1.5)):
        cr.arc(cx, cy, radius, a, b)
    cr.close_path()


def draw_icon(cr, key, x, y, size, colour=(.55, .24, 1)):
    """Draw our compact vector glyph at any scale, without raster assets."""
    key = canonical_key(key)
    cr.save()
    cr.translate(x, y)
    cr.scale(size, size)
    cr.set_line_width(.025)
    cr.set_line_cap(1)

    def ink(alpha=1):
        cr.set_source_rgba(*colour[:3], alpha)

    def pale(strength=.45):
        cr.set_source_rgb(*(1 - strength + value * strength for value in colour[:3]))

    def box(x0, y0, width, height, alpha=1):
        pale(alpha)
        _rounded(cr, x0, y0, width, height)
        cr.fill()

    def arrow(x0, y0, x1, y1):
        ink()
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        cr.stroke()
        angle = math.atan2(y1 - y0, x1 - x0)
        for delta in (-.7, .7):
            cr.move_to(x1, y1)
            cr.line_to(x1 - .09 * math.cos(angle + delta), y1 - .09 * math.sin(angle + delta))
        cr.stroke()

    if key == "none":
        ink()
        cr.move_to(.23, .77)
        cr.line_to(.77, .23)
        cr.stroke()
    elif key == "fade":
        for i in range(4):
            box(.19 + .145 * i, .28, .18, .44, .25 + .25 * i)
    elif key == "slide":
        box(.18, .28, .48, .44, .3)
        box(.39, .28, .43, .44)
        arrow(.48, .86, .31, .86)
    elif key == "circle_wipe":
        box(.18, .28, .64, .44)
        pale(.6)
        cr.arc(.5, .5, .22, 0, math.pi * 2)
        cr.fill()
        pale(.4)
        cr.arc(.5, .5, .11, 0, math.pi * 2)
        cr.fill()
    elif key == "colour_wipe":
        box(.18, .28, .64, .44)
        box(.18, .28, .31, .44, .3)
        box(.28, .28, .08, .44, .5)
        arrow(.47, .86, .29, .86)
    elif key == "line_wipe":
        box(.18, .28, .64, .44, .3)
        box(.4, .28, .42, .44)
        ink()
        cr.move_to(.4, .22)
        cr.line_to(.4, .79)
        cr.stroke()
        arrow(.52, .87, .32, .87)
    elif key == "match_move":
        box(.12, .28, .76, .44)
        pale(.55)
        cr.move_to(.18, .5)
        cr.line_to(.78, .5)
        cr.stroke()
        for cx in (.29, .49, .71):
            pale(.5 if cx < .6 else .18)
            cr.move_to(cx, .38)
            cr.line_to(cx + .07, .5)
            cr.line_to(cx, .62)
            cr.line_to(cx - .07, .5)
            cr.close_path()
            cr.fill()
    elif key == "flow":
        box(.32, .28, .31, .44, .3)
        box(.66, .28, .25, .44)
        ink()
        for x0, x1, y0 in ((.11, .34, .38), (.05, .28, .5), (.12, .38, .61)):
            cr.move_to(x0, y0)
            cr.line_to(x1, y0)
            cr.stroke()
    elif key == "stack":
        box(.24, .38, .58, .4, .3)
        box(.17, .23, .58, .44)
        arrow(.91, .46, .91, .71)
    cr.restore()
