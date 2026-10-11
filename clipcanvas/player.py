"""Local video preview, painted into a native GTK canvas.

The appsink keeps playback independent of X11/Wayland window handles.  It also
allows the exact same canvas treatment for a thumbnail and a playing video.
"""

import logging
import math
from pathlib import Path
import threading

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gst, Gtk, Pango, PangoCairo
from . import ui_theme
from .live_player import StreamPlayer


Gst.init(None)
_LOG = logging.getLogger(__name__)


class VideoCanvas(Gtk.DrawingArea):
    """A lightweight player with callbacks expressed in seconds.

    ``set_still`` supports a crop position from 0 to 1 on each axis. Rendered
    project previews should use ``fit``: they already contain their canvas.
    """

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_size_request(240, 180)
        self.aspect_width = 16
        self.aspect_height = 9
        self.frame = None
        self.fit = "fit"
        self.position_x = 0.5
        self.position_y = 0.5
        self.playing = False
        self.on_position = None
        self.on_error = None
        self.on_end = None
        self.on_overlay_draw = None
        self.canvas_rect = None
        self.composited = False
        self.hide_placeholder = False
        self._loaded = False
        self._destroyed = False
        self._generation = 0
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._idle_id = 0
        self._last_position = 0.0
        self._last_duration = 0.0
        self._pending_seek = None
        self._live = None
        self._pipeline = Gst.ElementFactory.make("playbin", None)
        self._sink = Gst.ElementFactory.make("appsink", None)
        if self._pipeline is None or self._sink is None:
            raise RuntimeError("GStreamer playback components are missing.")
        self._sink.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGB,pixel-aspect-ratio=1/1"))
        self._sink.set_property("emit-signals", True)
        self._sink.set_property("max-buffers", 1)
        self._sink.set_property("drop", True)
        self._sink.set_property("sync", True)
        self._sample_handler = self._sink.connect("new-sample", self._new_sample)
        self._preroll_handler = self._sink.connect("new-preroll", self._new_preroll)
        self._pipeline.set_property("video-sink", self._sink)
        audio = Gst.ElementFactory.make("autoaudiosink", None)
        if audio is not None:
            self._pipeline.set_property("audio-sink", audio)
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus_handler = self._bus.connect("message", self._message)
        self.connect("draw", self._draw)
        self.connect("style-updated", lambda _: self.queue_draw())
        self.connect("destroy", self._cleanup)
        self._timer_id = GLib.timeout_add(100, self._tick)

    def set_aspect(self, width, height):
        if width <= 0 or height <= 0:
            raise ValueError("The canvas dimensions must be positive.")
        self.aspect_width, self.aspect_height = width, height
        self.queue_draw()

    def set_still(self, pixbuf, fit="fill", position_x=0.5, position_y=0.5):
        self.stop()
        self.composited = False
        self._loaded = False
        self.frame = pixbuf
        self.fit = "fit" if fit == "fit" else "fill"
        self.position_x = max(0.0, min(1.0, float(position_x)))
        self.position_y = max(0.0, min(1.0, float(position_y)))
        self.queue_draw()

    def load(self, path, autoplay=True):
        self.stop()
        media = Path(path).expanduser().resolve()
        if not media.is_file():
            self._notify(self.on_error, "The preview video could not be found.")
            return
        self._pipeline.set_property("uri", media.as_uri())
        self.fit = "fit"
        self.position_x = self.position_y = 0.5
        self._loaded = True
        self._last_position = self._last_duration = 0.0
        self._pending_seek = None
        self.playing = bool(autoplay)
        result = self._pipeline.set_state(Gst.State.PLAYING if autoplay else Gst.State.PAUSED)
        if result == Gst.StateChangeReturn.FAILURE:
            self.playing = False
            self._notify(self.on_error, "This video could not be played. Try exporting the preview again.")

    @property
    def live(self):
        return self._live is not None

    def load_project(self, project, start=0, autoplay=True):
        self.stop()
        generation = self._generation
        self.fit = 'fit'
        self.composited = True
        self._loaded = True
        self._last_position = start
        self._last_duration = 0
        self.playing = autoplay
        def error(message):
            if generation == self._generation:
                self.stop()
                self._notify(self.on_error, message)
        def end():
            if generation == self._generation:
                self.playing = False
                self._notify(self.on_position, self.duration(), self.duration())
                self._notify(self.on_end)
        try:
            self._live = StreamPlayer(project, start, autoplay,
                lambda sample: self._receive_sample(sample, generation), error, end,
                volume=self._pipeline.get_property('volume'))
        except Exception as problem:
            self._loaded = self.playing = self.composited = False
            self._notify(self.on_error, 'Live preview is unavailable: ' + str(problem))

    def play(self):
        if self._destroyed or not self._loaded:
            return
        if self.duration() > 0 and self.position() >= self.duration() - 0.05:
            self.seek(0)
        if self._live is not None:
            self._live.play()
            self.playing = True
            return
        if self._pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE:
            self.playing = True

    def pause(self):
        if self._live is not None:
            self._live.pause()
            self.playing = False
            return
        if not self._destroyed:
            self._pipeline.set_state(Gst.State.PAUSED if self._loaded else Gst.State.NULL)
        self.playing = False

    def stop(self):
        if self._destroyed:
            return
        self.playing = False
        self._generation += 1
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._pipeline.set_state(Gst.State.NULL)
        self._loaded = False
        with self._frame_lock:
            self._latest_frame = None
        self._last_position = 0.0
        self._last_duration = 0.0
        self._pending_seek = None

    def seek(self, seconds):
        if self._destroyed or not self._loaded:
            return
        seconds = max(0.0, float(seconds))
        duration = self.duration()
        if duration > 0:
            seconds = min(seconds, max(0.0, duration - 0.001))
        if self._live is not None:
            self.load_project(self._live.project, seconds, self.playing)
            return
        result = self._pipeline.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            int(seconds * Gst.SECOND),
        )
        if result:
            self._pending_seek = None
            self._last_position = seconds
        else:
            self._pending_seek = seconds

    def position(self):
        if self._live is not None:
            return self._live.position()
        if not self._destroyed and self._loaded:
            ok, value = self._pipeline.query_position(Gst.Format.TIME)
            if ok and value >= 0:
                self._last_position = value / Gst.SECOND
        return self._last_position

    def duration(self):
        if self._live is not None:
            return self._live.total
        if not self._destroyed and self._loaded:
            ok, value = self._pipeline.query_duration(Gst.Format.TIME)
            if ok and value > 0:
                self._last_duration = value / Gst.SECOND
        return self._last_duration

    def _new_sample(self, sink):
        return self._receive_sample(sink.emit("pull-sample"))

    def _new_preroll(self, sink):
        return self._receive_sample(sink.emit("pull-preroll"))

    def _receive_sample(self, sample, generation=None):
        generation = self._generation if generation is None else generation
        if sample is None or self._destroyed or generation != self._generation:
            return Gst.FlowReturn.OK
        try:
            structure = sample.get_caps().get_structure(0)
            width, height = structure.get_value("width"), structure.get_value("height")
            buffer = sample.get_buffer()
            size = buffer.get_size()
            # GStreamer's packed RGB rows are aligned to four bytes.
            stride = (width * 3 + 3) & ~3
            if size < stride * height:
                stride = width * 3
            if width <= 0 or height <= 0 or size < stride * (height - 1) + width * 3:
                return Gst.FlowReturn.OK
            payload = (generation, buffer.extract_dup(0, size), width, height, stride)
            with self._frame_lock:
                self._latest_frame = payload
                # Retain only the newest frame if GTK is busy. There is never
                # a growing queue of idle callbacks or decoded frame buffers.
                if not self._idle_id and not self._destroyed:
                    self._idle_id = GLib.idle_add(self._deliver_frame)
        except Exception:
            _LOG.exception("Could not decode a preview frame")
        return Gst.FlowReturn.OK

    def _deliver_frame(self):
        with self._frame_lock:
            payload, self._latest_frame = self._latest_frame, None
            self._idle_id = 0
        if self._destroyed or payload is None or payload[0] != self._generation:
            return GLib.SOURCE_REMOVE
        _, pixels, width, height, stride = payload
        self.frame = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB, False, 8,
            width, height, stride,
        )
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _message(self, bus, message):
        if self._destroyed:
            return
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            _LOG.warning("Preview playback failed: %s (%s)", error.message, debug)
            self.pause()
            self._notify(self.on_error, "Preview playback failed: " + error.message)
        elif message.type == Gst.MessageType.EOS:
            self.playing = False
            self._last_position = self.duration()
            self._notify(self.on_position, self._last_position, self._last_duration)
            self._notify(self.on_end)
        elif message.type == Gst.MessageType.ASYNC_DONE and self._pending_seek is not None:
            requested, self._pending_seek = self._pending_seek, None
            self.seek(requested)

    def _tick(self):
        if self._destroyed:
            return GLib.SOURCE_REMOVE
        if self._loaded:
            self._notify(self.on_position, self.position(), self.duration())
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _notify(callback, *args):
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                _LOG.exception("Preview callback failed")

    @staticmethod
    def _text(cr, text, x, y, width, size, color, bold=False):
        layout = PangoCairo.create_layout(cr)
        description = Pango.FontDescription(Gtk.Settings.get_default().get_property('gtk-font-name'))
        description.set_absolute_size(size * Pango.SCALE)
        if bold:
            description.set_weight(Pango.Weight.SEMIBOLD)
        layout.set_font_description(description)
        layout.set_text(text, -1)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_width(max(1, int(width * Pango.SCALE)))
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        cr.set_source_rgb(*color)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    def _draw(self, widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        color=Gdk.RGBA();color.parse(ui_theme.palette(self)['background'])
        cr.set_source_rgb(color.red,color.green,color.blue)
        cr.paint()
        padding = min(28, width / 12, height / 12)
        scale = min(max(1, width - padding * 2) / self.aspect_width,
                    max(1, height - padding * 2) / self.aspect_height)
        cw, ch = self.aspect_width * scale, self.aspect_height * scale
        cx, cy = (width - cw) / 2, (height - ch) / 2
        self.canvas_rect = (cx, cy, cw, ch)
        for spread in range(9, 0, -1):
            cr.set_source_rgba(0.12, 0.10, 0.23, 0.008)
            cr.rectangle(cx - spread, cy - spread / 2 + 3, cw + spread * 2, ch + spread)
            cr.fill()
        cr.save()
        cr.rectangle(cx, cy, cw, ch)
        cr.clip()
        cr.set_source_rgb(0.015, 0.015, 0.02) if self.frame else cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if self.frame is not None:
            cr.save()
            fw, fh = self.frame.get_width(), self.frame.get_height()
            factor = (min if self.fit == "fit" else max)(cw / fw, ch / fh)
            align_x, align_y = (0.5, 0.5) if self.fit == "fit" else (self.position_x, self.position_y)
            dx = cx + (cw - fw * factor) * align_x
            dy = cy + (ch - fh * factor) * align_y
            cr.translate(dx, dy)
            cr.scale(factor, factor)
            Gdk.cairo_set_source_pixbuf(cr, self.frame, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            cr.restore()
        elif not self.hide_placeholder:
            icon_size = min(54, cw * 0.18, ch * 0.25)
            center_x, center_y = cx + cw / 2, cy + ch / 2 - 35
            cr.set_source_rgb(0.943, 0.914, 1.0)
            cr.arc(center_x, center_y, icon_size / 1.5, 0, math.tau)
            cr.fill()
            cr.set_source_rgb(0.49, 0.27, 0.85)
            cr.move_to(center_x - icon_size * 0.15, center_y - icon_size * 0.22)
            cr.line_to(center_x + icon_size * 0.23, center_y)
            cr.line_to(center_x - icon_size * 0.15, center_y + icon_size * 0.22)
            cr.close_path()
            cr.fill()
            text_size = min(20, max(12, cw / 19))
            self._text(cr, "Your story starts here", cx + 16, center_y + icon_size * 0.9,
                       cw - 32, text_size, (0.21, 0.18, 0.30), True)
            self._text(cr, "Import clips to bring your canvas to life", cx + 20,
                       center_y + icon_size * 0.9 + text_size + 13,
                       cw - 40, min(13, max(10, cw / 29)), (0.49, 0.46, 0.56))
        if self.on_overlay_draw:
            self.on_overlay_draw(cr, self.canvas_rect)
        cr.restore()
        return False

    def _cleanup(self, *args):
        if self._destroyed:
            return
        self.stop()
        self._destroyed = True
        self.playing = False
        self._pipeline.set_state(Gst.State.NULL)
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        with self._frame_lock:
            if self._idle_id:
                GLib.source_remove(self._idle_id)
                self._idle_id = 0
            self._latest_frame = None
        self._sink.disconnect(self._sample_handler)
        self._sink.disconnect(self._preroll_handler)
        self._bus.disconnect(self._bus_handler)
        self._bus.remove_signal_watch()
