"""Small layout additions to the desktop's GTK and icon themes.

Keep form controls, menus and dialogs styled by GTK.  In particular, setting
label/entry/button colors globally breaks native popup menus in dark themes.
"""
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk


CSS = b'''
.clipcanvas .topbar {
    padding: 8px 12px;
    border-bottom: 1px solid alpha(@theme_fg_color, 0.14);
}
.clipcanvas .brand { font-size: 18px; font-weight: bold; }
.clipcanvas .heading { font-size: 22px; font-weight: bold; }
.clipcanvas .hero { font-size: 30px; font-weight: bold; }
.clipcanvas .section { font-weight: bold; }
.clipcanvas .muted { color: alpha(@theme_fg_color, 0.65); }
.clipcanvas .small { font-size: 0.9em; }
.clipcanvas .panel {
    background-color: @theme_base_color;
    border-right: 1px solid alpha(@theme_fg_color, 0.12);
}
.clipcanvas .preview-area { background-color: @theme_bg_color; }
.clipcanvas .card {
    border: 1px solid alpha(@theme_fg_color, 0.18);
    border-radius: 12px;
    padding: 18px;
}
.clipcanvas .asset { padding: 8px; }
.clipcanvas .inspector { padding: 14px; }
.clipcanvas .status {
    padding: 5px 12px;
    border-top: 1px solid alpha(@theme_fg_color, 0.12);
}
.clipcanvas .editor-toolbar { padding: 6px 10px; }
.clipcanvas .tool-rail {
    padding: 6px;
    border-right: 1px solid alpha(@theme_fg_color, 0.12);
}
.clipcanvas button.quiet { border-color: transparent; }
.clipcanvas .round-control { border-radius: 50%; padding: 10px; }
.clipcanvas .transition-card { padding: 10px; }
'''


def install(window=None, screen=None):
    """Install app-scoped layout CSS without replacing desktop preferences.

    Pass the main window to opt it into these styles.  Menus, file choosers,
    native control colors and the selected font remain owned by the system
    theme, including Yaru's dark variant and user-selected accent color.
    """
    screen = screen or Gdk.Screen.get_default()
    if screen is None:
        raise RuntimeError('ClipCanvas needs a graphical desktop session.')
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    if window is not None:
        window.get_style_context().add_class('clipcanvas')
    return provider


def palette(widget):
    """Return opaque theme colors for Cairo surfaces outside GTK's renderer.

    Resolve these when drawing rather than caching them: GTK updates named
    colors when the user changes the desktop theme while the app is open.
    """
    context = widget.get_style_context()

    def lookup(name):
        found, color = context.lookup_color(name)
        return color if found else None

    def hex_color(color):
        return '#' + ''.join(f'{round(max(0, min(1, value)) * 255):02x}'
                             for value in (color.red, color.green, color.blue))

    def rgba(value):
        color = Gdk.RGBA()
        color.parse(value)
        return color

    def blend(foreground, background, amount):
        color = Gdk.RGBA()
        color.red = foreground.red * amount + background.red * (1 - amount)
        color.green = foreground.green * amount + background.green * (1 - amount)
        color.blue = foreground.blue * amount + background.blue * (1 - amount)
        color.alpha = 1
        return color

    background = lookup('theme_bg_color')
    if background is None:
        native_background = context.get_background_color(Gtk.StateFlags.NORMAL)
        if native_background.alpha > 0.5:
            background = native_background
    settings = Gtk.Settings.get_default()
    dark = bool(settings and settings.get_property('gtk-application-prefer-dark-theme'))
    if background is not None:
        dark = (background.red * 0.2126 + background.green * 0.7152
                + background.blue * 0.0722) < 0.5
    background = background or rgba('#242424' if dark else '#f6f5f4')
    surface = lookup('theme_base_color') or rgba('#303030' if dark else '#ffffff')
    text = lookup('theme_fg_color') or rgba('#eeeeec' if dark else '#2e3436')
    accent = lookup('theme_selected_bg_color') or rgba('#3584e4')
    accent_text = lookup('theme_selected_fg_color') or rgba('#ffffff')
    return {
        'background': hex_color(background),
        'surface': hex_color(surface),
        'text': hex_color(text),
        'muted': hex_color(blend(text, background, 0.65)),
        'border': hex_color(lookup('borders') or blend(text, background, 0.18)),
        'accent': hex_color(accent),
        'accent_text': hex_color(accent_text),
    }


def icon(name, size=Gtk.IconSize.BUTTON, fallback='image-missing-symbolic'):
    """Create a recolorable icon from the active desktop icon theme."""
    theme = Gtk.IconTheme.get_default()
    for candidate in (name, fallback, 'image-missing'):
        if candidate and theme is not None and theme.has_icon(candidate):
            return Gtk.Image.new_from_icon_name(candidate, size)
    return Gtk.Image()


def icon_button(name, callback, tooltip, label=None, style=None):
    """Native button with an accessible name, optional label and themed icon."""
    widget = Gtk.Button()
    widget.set_image(icon(name))
    if label:
        widget.set_label(label)
        widget.set_always_show_image(True)
    widget.set_tooltip_text(tooltip)
    widget.get_accessible().set_name(label or tooltip)
    widget.connect('clicked', lambda _: callback())
    if style:
        widget.get_style_context().add_class(
            'suggested-action' if style == 'primary' else style)
    return widget
