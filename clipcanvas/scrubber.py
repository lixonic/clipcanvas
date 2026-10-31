"""Reusable, full-resolution native decoding for interactive picture seeks.

Ordinary scenes use one paused GStreamer decoder and the shared Cairo layer
renderer. Scene overlaps and video overlays keep the export composition graph.
No proxy file, reduced resolution or background movie render is required.
"""
import math
from pathlib import Path
import threading
import time

import cairo
import gi
gi.require_version('Gst', '1.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gst, Gdk, GdkPixbuf, GLib

from . import engine, model, visuals

Gst.init(None)


class ScrubSession:
    """Worker-owned seeking with one decoder and two buffered source frames."""
    def __init__(self):
        self.pipeline = self.sink = self.path = None
        self._lock = threading.Lock()
        self.last_backend = None
        self.open_count = 0
        self._source_pts = self._source_picture = None

    def close(self):
        with self._lock:
            pipeline, self.pipeline = self.pipeline, None
            self.sink = self.path = None
            self._source_pts = self._source_picture = None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)

    @staticmethod
    def _check(cancel):
        if cancel is not None and cancel.is_set():
            raise engine.RenderCancelled('Preview seek cancelled.')

    def _decoder(self, path):
        if self.pipeline is not None and self.path == path:
            return self.pipeline, self.sink
        self.close()
        pipeline = Gst.ElementFactory.make('playbin', None)
        sink = Gst.ElementFactory.make('appsink', None)
        if pipeline is None or sink is None:
            raise engine.RenderError('Native preview decoding is unavailable.')
        sink.set_property('caps', Gst.Caps.from_string('video/x-raw,format=RGB'))
        sink.set_property('max-buffers', 2)
        sink.set_property('drop', False)
        sink.set_property('sync', False)
        pipeline.set_property('video-sink', sink)
        pipeline.set_property('audio-sink', Gst.ElementFactory.make('fakesink', None))
        pipeline.set_property('flags', 1)  # Decode the picture only.
        pipeline.set_property('uri', Path(path).resolve().as_uri())
        with self._lock:
            self.pipeline, self.sink, self.path = pipeline, sink, path
            self.open_count += 1
        pipeline.set_state(Gst.State.PAUSED)
        return pipeline, sink

    def _picture(self, path, at, cancel):
        pipeline, sink = self._decoder(path)
        bus = pipeline.get_bus()
        deadline = time.monotonic() + 8
        # Wait for the initial preroll so seek_simple can operate immediately.
        while self._source_pts is None and pipeline.get_state(0)[1] != Gst.State.PAUSED:
            self._check(cancel)
            message = bus.timed_pop_filtered(20*Gst.MSECOND, Gst.MessageType.ERROR)
            if message is not None:
                raise engine.RenderError(message.parse_error()[0].message)
            if time.monotonic() >= deadline:
                raise engine.RenderError('This clip took too long to open for scrubbing.')
        self._check(cancel)
        if self._source_pts is not None and abs(self._source_pts-at)<.001:
            return self._source_picture
        # Continue the warm decoder for short forward jumps. Flushing on every
        # half-second pointer move repeatedly decodes a long GOP from its start.
        nearby = self._source_pts is not None and 0<=at-self._source_pts<=2
        if not nearby:
            pipeline.set_state(Gst.State.PAUSED)
            if not pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                        round(max(0, at)*Gst.SECOND)):
                raise engine.RenderError('Native seeking is unavailable for this clip.')
        while time.monotonic() < deadline:
            self._check(cancel)
            sample = sink.emit('try-pull-sample' if nearby else 'try-pull-preroll', 20*Gst.MSECOND)
            if sample is not None:
                buffer = sample.get_buffer()
                timestamp=buffer.pts
                segment=sample.get_segment()
                if segment is not None and timestamp!=Gst.CLOCK_TIME_NONE:
                    timestamp=segment.to_stream_time(Gst.Format.TIME,timestamp)
                pts = timestamp/Gst.SECOND if timestamp != Gst.CLOCK_TIME_NONE else at
                # Ignore a preroll from the preceding seek if a flush raced it.
                if pts + .002 < at:
                    continue
                info = sample.get_caps().get_structure(0)
                width, height = info.get_value('width'), info.get_value('height')
                stride = (width*3+3) & ~3
                data = buffer.extract_dup(0, buffer.get_size())
                if len(data) < stride*height:
                    stride = width*3
                picture=GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data),
                    GdkPixbuf.Colorspace.RGB, False, 8, width, height, stride)
                self._source_pts,self._source_picture=pts,picture
                if not nearby:pipeline.set_state(Gst.State.PLAYING)
                return picture
            message = bus.pop_filtered(Gst.MessageType.ERROR)
            if message is not None:
                raise engine.RenderError(message.parse_error()[0].message)
        raise engine.RenderError('This clip took too long to seek.')

    def frame(self, project, seconds, cancel=None, preview_edge=0):
        try:
            return self._frame(project,seconds,cancel,preview_edge)
        except engine.RenderCancelled:
            self.close()
            raise

    def _frame(self, project, seconds, cancel=None, preview_edge=0):
        self._check(cancel)
        fps = project['fps']
        seconds = max(0, min(float(seconds), model.project_duration(project)-1/fps))
        seconds = math.ceil(seconds*fps-1e-7)/fps
        active = [(clip, row) for clip, row in zip(project['clips'], model.timeline(project))
                  if row['start'] <= seconds < row['start']+row['duration']]
        video_overlay = any(item.get('kind') == 'video' and item['start'] <= seconds < item['end']
                            for item in project.get('layers', []))
        if len(active) != 1 or video_overlay or model.is_image(active[0][0]):
            self.last_backend = 'composition'
            return engine.preview_frame(project, seconds, cancel, preview_edge)
        clip, row = active[0]
        width, height = engine.preview_dimensions(project, preview_edge)
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
        cr = cairo.Context(surface)
        if model.is_card(clip):
            cr.set_source_rgb(*visuals.graded_rgb(clip)); cr.paint()
        else:
            metadata = engine.probe(clip['path'])
            # Preserve unusual aspect/rotation metadata through the established
            # composition renderer rather than guessing at display geometry.
            if metadata['sample_aspect_ratio'] != 1:
                self.last_backend = 'composition'
                return engine.preview_frame(project, seconds, cancel, preview_edge)
            source_at = clip.get('in', 0)+(seconds-row['start'])*clip.get('speed', 1)
            source_at = min(source_at, clip['out']-1/max(1, metadata['fps']))
            try:
                from . import preview_cache
                path = preview_cache.available(clip['path'], preview_edge) or clip['path']
                pixbuf = self._picture(path, source_at, cancel)
            except engine.RenderCancelled:
                raise
            except Exception:
                self.close(); self.last_backend = 'composition'
                return engine.preview_frame(project, seconds, cancel, preview_edge)
            pixbuf = visuals.graded_picture(pixbuf, clip)
            rotation = {90: GdkPixbuf.PixbufRotation.CLOCKWISE,
                        180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
                        270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(clip.get('rotation', 0))
            if rotation is not None: pixbuf = pixbuf.rotate_simple(rotation)
            if clip.get('flip_x'): pixbuf = pixbuf.flip(True)
            if clip.get('flip_y'): pixbuf = pixbuf.flip(False)
            factor = (min if clip.get('fit') == 'fit' else max)(width/pixbuf.get_width(), height/pixbuf.get_height())
            x, y = (width-pixbuf.get_width()*factor)*clip.get('position_x', .5), (height-pixbuf.get_height()*factor)*clip.get('position_y', .5)
            cr.save(); cr.translate(x, y); cr.scale(factor, factor)
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0); cr.get_source().set_filter(cairo.FILTER_BILINEAR); cr.paint(); cr.restore()
        age, length = seconds-row['start'], row['duration']
        opacity = 1
        if clip.get('video_fade_in', 0): opacity = min(opacity, age/clip['video_fade_in'])
        if clip.get('video_fade_out', 0): opacity = min(opacity, (length-age)/clip['video_fade_out'])
        if opacity < 1:
            cr.set_source_rgba(0, 0, 0, max(0, 1-opacity)); cr.paint()
        self._check(cancel)
        visuals.draw(cr, project, seconds, (0, 0, width, height))
        self._check(cancel)
        picture = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
        self.last_backend = 'native'
        return {'pixels': bytes(picture.get_pixels()), 'width': width, 'height': height,
                'stride': picture.get_rowstride(), 'position': seconds}
