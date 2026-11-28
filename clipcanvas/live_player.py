"""Clocked native playback of the composition's unencoded video/audio stream."""
import copy
import json
import math
import os
from pathlib import Path
import threading
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

from . import engine, model, watermark
from . import lazy_preview


class PlaybackMetrics:
    """Lightweight monotonic timings for startup, refills, and frame cadence."""

    def __init__(self, project, start, preview_edge, lazy, reason):
        self._lock = threading.Lock()
        self.begun = time.monotonic()
        self.route = 'windowed' if lazy else 'continuous'
        self.route_reason = reason
        self.start_seconds = float(start)
        self.preview_edge = int(preview_edge)
        self.clips = len(project.get('clips', []))
        self.layers = len(project.get('layers', []))
        self.captions = len(project.get('captions', []))
        self.audio_tracks = len(project.get('audio', []))
        self.timeline_seconds = round(model.project_duration(project), 3)
        self.validate_ms = None
        self.first_video_prep_ms = None
        self.first_video_ffmpeg_ms = None
        self.first_audio_prep_ms = None
        self.first_audio_ffmpeg_ms = None
        self.first_picture_ms = None
        self.first_audio_ms = None
        self.first_gtk_frame_ms = None
        self.clock_start_ms = None
        self.stop_ms = None
        self.video_windows = 0
        self.audio_windows = 0
        self.frames_produced = 0
        self.frames_presented = 0
        self.refills = []
        self._flushed = False

    def _elapsed_ms(self):
        return round((time.monotonic() - self.begun) * 1000.0, 2)

    def mark_validated(self):
        with self._lock:
            if self.validate_ms is None:
                self.validate_ms = self._elapsed_ms()

    def record_video_window(self, window_index, prep_ms):
        with self._lock:
            self.video_windows = max(self.video_windows, window_index + 1)
            if window_index == 0 and self.first_video_prep_ms is None:
                self.first_video_prep_ms = round(prep_ms, 2)

    def mark_video_ffmpeg(self):
        with self._lock:
            if self.first_video_ffmpeg_ms is None:
                self.first_video_ffmpeg_ms = self._elapsed_ms()

    def record_audio_window(self, window_index, prep_ms):
        with self._lock:
            self.audio_windows = max(self.audio_windows, window_index + 1)
            if window_index == 0 and self.first_audio_prep_ms is None:
                self.first_audio_prep_ms = round(prep_ms, 2)

    def mark_audio_ffmpeg(self):
        with self._lock:
            if self.first_audio_ffmpeg_ms is None:
                self.first_audio_ffmpeg_ms = self._elapsed_ms()

    def mark_picture_produced(self):
        with self._lock:
            self.frames_produced += 1
            if self.first_picture_ms is None:
                self.first_picture_ms = self._elapsed_ms()

    def mark_audio_produced(self):
        with self._lock:
            if self.first_audio_ms is None:
                self.first_audio_ms = self._elapsed_ms()

    def mark_gtk_frame(self):
        with self._lock:
            self.frames_presented += 1
            if self.first_gtk_frame_ms is None:
                self.first_gtk_frame_ms = self._elapsed_ms()

    def mark_buffering(self, active, project_time, message):
        with self._lock:
            elapsed = self._elapsed_ms()
            if not active and self.clock_start_ms is None:
                self.clock_start_ms = elapsed
            if len(self.refills) < 128:
                self.refills.append({
                    'elapsed_ms': elapsed,
                    'project_time': round(float(project_time), 3),
                    'active': bool(active),
                    'message': str(message),
                })

    def mark_stop(self, duration_ms):
        with self._lock:
            self.stop_ms = round(float(duration_ms), 2)

    def snapshot(self):
        with self._lock:
            return {
                'route': self.route,
                'route_reason': self.route_reason,
                'start_seconds': self.start_seconds,
                'preview_edge': self.preview_edge,
                'clips': self.clips,
                'layers': self.layers,
                'captions': self.captions,
                'audio_tracks': self.audio_tracks,
                'timeline_seconds': self.timeline_seconds,
                'validate_ms': self.validate_ms,
                'first_video_prep_ms': self.first_video_prep_ms,
                'first_video_ffmpeg_ms': self.first_video_ffmpeg_ms,
                'first_audio_prep_ms': self.first_audio_prep_ms,
                'first_audio_ffmpeg_ms': self.first_audio_ffmpeg_ms,
                'first_picture_ms': self.first_picture_ms,
                'first_audio_ms': self.first_audio_ms,
                'first_gtk_frame_ms': self.first_gtk_frame_ms,
                'clock_start_ms': self.clock_start_ms,
                'stop_ms': self.stop_ms,
                'video_windows': self.video_windows,
                'audio_windows': self.audio_windows,
                'frames_produced': self.frames_produced,
                'frames_presented': self.frames_presented,
                'refills': list(self.refills),
            }

    def flush_log(self):
        target = os.environ.get('CLIPCANVAS_PERF_LOG')
        if not target:
            return
        with self._lock:
            if self._flushed:
                return
            self._flushed = True
        try:
            Path(target).expanduser().parent.mkdir(parents=True, exist_ok=True)
            with open(Path(target).expanduser(), 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(self.snapshot()) + '\n')
        except OSError:
            pass


def _pack_i420_rows(data, width, height):
    """Pad tightly packed FFmpeg I420 planes to GStreamer's 4-byte row stride."""
    view = memoryview(data)
    y_stride = (width + 3) & ~3
    uv_w = width // 2
    uv_h = height // 2
    uv_stride = (uv_w + 3) & ~3
    out = bytearray(y_stride * height + uv_stride * uv_h * 2)
    dst_view = memoryview(out)
    src_at = 0
    dst_at = 0
    for w, h, stride in ((width, height, y_stride), (uv_w, uv_h, uv_stride), (uv_w, uv_h, uv_stride)):
        plane_bytes = w * h
        if stride == w:
            dst_view[dst_at:dst_at + plane_bytes] = view[src_at:src_at + plane_bytes]
            src_at += plane_bytes
            dst_at += plane_bytes
        else:
            for row in range(h):
                s = src_at + row * w
                d = dst_at + row * stride
                dst_view[d:d + w] = view[s:s + w]
            src_at += plane_bytes
            dst_at += stride * h
    return bytes(out)


def _warmup_paths(project, start):
    """Order unpatched video clip paths from the current playhead forward."""
    rows = model.timeline(project)
    clips = project.get('clips', [])
    first_idx = next((i for i, row in enumerate(rows) if row['start'] + row['duration'] > start), 0)
    ordered = clips[first_idx:] + clips[:first_idx]
    return list(dict.fromkeys(
        clip['path'] for clip in ordered
        if not model.is_still(clip) and not watermark.active(clip)
    ))


def pipeline_description(audio_sink='autoaudiosink'):
    """One bounded, coordinated buffer for the picture and soundtrack."""
    return (
        'appsrc name=source block=true max-bytes=1048576 caps=video/x-matroska '
        '! matroskademux name=demux '
        'multiqueue name=buffer use-buffering=true max-size-buffers=0 '
        'max-size-bytes=67108864 max-size-time=1500000000 '
        'low-watermark=0.15 high-watermark=0.8 '
        'demux.video_0 ! buffer.sink_0 '
        'buffer.src_0 ! videoconvert ! video/x-raw,format=RGB,pixel-aspect-ratio=1/1 '
        '! appsink name=picture emit-signals=true sync=true max-buffers=1 drop=true '
        'demux.audio_0 ! buffer.sink_1 '
        'buffer.src_1 ! audioconvert ! audioresample ! volume name=volume ! ' + audio_sink)


class StreamPlayer:
    """One seek generation, with bounded queues and asynchronous cancellation.

    Matroska transports raw I420 frames and PCM audio. Both sinks share Gst's
    clock, including pauses while the shared buffer refills.
    """
    def __init__(self, project, start, autoplay, sample, error, end, volume=1, preview_edge=640, buffering=None):
        self.preview_edge = preview_edge
        self.project = copy.deepcopy(project)
        model.adapt_frame_rate(self.project)
        project = self.project
        self.total = model.project_duration(project)
        self.start = max(0, min(start, max(0, self.total - 1 / project['fps'])))
        self.cancel = threading.Event()
        self._av_cond = threading.Condition()
        self.ended = False
        self.last_position = self.start
        self.buffering = True
        self._buffering_reported = False
        self._wanted_playing = autoplay
        self._buffering_callback = buffering
        self.lazy, route_reason = lazy_preview.route_reason(project)
        self.metrics = PlaybackMetrics(project, self.start, preview_edge, self.lazy, route_reason)
        description = pipeline_description()
        if self.lazy:
            from fractions import Fraction
            width, height = engine.preview_dimensions(project, preview_edge)
            rate = Fraction(project['fps']).limit_denominator(100000)
            frame_bytes = width * height * 3 // 2
            mq_bytes = max(67108864, min(201326592, frame_bytes * math.ceil(project['fps'] * 1.6) + 4 * 1024 * 1024))
            description = description.replace('max-size-bytes=67108864', f'max-size-bytes={mq_bytes}')
            description = description.replace(
                'appsrc name=source block=true max-bytes=1048576 caps=video/x-matroska ! matroskademux name=demux ', '')
            description = description.replace('demux.video_0 ! buffer.sink_0',
                f'appsrc name=video_source block=true max-bytes={frame_bytes} format=time '
                f'caps="video/x-raw,format=I420,width={width},height={height},framerate={rate.numerator}/{rate.denominator},pixel-aspect-ratio=1/1" ! buffer.sink_0')
            description = description.replace('demux.audio_0 ! buffer.sink_1',
                'appsrc name=audio_source block=true max-bytes=16384 format=time '
                'caps="audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved" ! buffer.sink_1')
        self.pipeline = Gst.parse_launch(description)
        self.pipeline.get_by_name('volume').set_property('volume', volume)
        sink = self.pipeline.get_by_name('picture')

        def on_picture(pulled):
            self.metrics.mark_picture_produced()
            return sample(pulled)

        sink.connect('new-sample', lambda s: on_picture(s.emit('pull-sample')))
        sink.connect('new-preroll', lambda s: on_picture(s.emit('pull-preroll')))
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus_handler = self.bus.connect('message', self._message, error, end)
        # Let the producer fill both streams before starting their shared clock.
        self.pipeline.set_state(Gst.State.PAUSED)
        self.thread = threading.Thread(target=self._feed, args=(error,), daemon=True)
        self.thread.start()

    def _feed(self, error):
        if self.lazy:
            self._feed_lazy(error)
            return
        stream = engine.live_stream(self.project, self.start, self.cancel, preview_edge=self.preview_edge,
                                    status=self._preparing)
        source = self.pipeline.get_by_name('source')
        try:
            for data in stream:
                if self.cancel.is_set():
                    return
                if source.emit('push-buffer', Gst.Buffer.new_wrapped(data)) != Gst.FlowReturn.OK:
                    return
            if not self.cancel.is_set():
                source.emit('end-of-stream')
        except Exception as problem:
            GLib.idle_add(self._report, error, str(problem))
        finally:
            stream.close()

    def _feed_lazy(self, error):
        try:
            model.validate_project(self.project, check_files=True, require_clips=True)
            self.metrics.mark_validated()
        except Exception as problem:
            GLib.idle_add(self._report, error, str(problem))
            return
        width, height = engine.preview_dimensions(self.project, self.preview_edge)
        needs_row_pad = bool(width % 8)
        av_cond = self._av_cond
        av_state = {'video_pts': -1.0, 'video_done': False}
        audio_max_lead = 0.08

        def feed(kind, stream):
            source = self.pipeline.get_by_name(kind + '_source')
            count = 0
            rate = self.project['fps'] if kind == 'video' else 48000
            offset = max(0, math.ceil(self.start * rate - 1e-7) / rate - self.start) if kind == 'video' else 0
            try:
                for data in stream:
                    if self.cancel.is_set():
                        break
                    units = 1 if kind == 'video' else len(data) // 4
                    if kind == 'video' and needs_row_pad:
                        data = _pack_i420_rows(data, width, height)
                    elif kind == 'audio':
                        if count == 0:
                            self.metrics.mark_audio_produced()
                        audio_pts = count / rate
                        with av_cond:
                            while (not self.cancel.is_set() and not av_state['video_done']
                                   and (av_state['video_pts'] < 0.0 or audio_pts > av_state['video_pts'] + audio_max_lead)):
                                av_cond.wait(timeout=0.05)
                        if self.cancel.is_set():
                            break
                    buffer = Gst.Buffer.new_wrapped(data)
                    buffer.pts = round((offset + count / rate) * Gst.SECOND)
                    buffer.duration = round((offset + (count + units) / rate) * Gst.SECOND) - buffer.pts
                    count += units
                    if kind == 'video':
                        with av_cond:
                            av_state['video_pts'] = offset + count / rate
                            av_cond.notify_all()
                    if source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
                        break
                if not self.cancel.is_set():
                    source.emit('end-of-stream')
            except Exception as problem:
                GLib.idle_add(self._report, error, str(problem))
            finally:
                if kind == 'video':
                    with av_cond:
                        av_state['video_done'] = True
                        av_cond.notify_all()
                stream.close()

        audio = threading.Thread(
            target=feed,
            args=('audio', lazy_preview.audio(self.project, self.start, self.cancel, metrics=self.metrics)),
            daemon=True,
        )
        from . import preview_cache
        paths = _warmup_paths(self.project, self.start) if self.preview_edge else []
        with preview_cache.PreviewWarmup(paths, self.preview_edge, self.cancel):
            audio.start()
            try:
                feed('video', lazy_preview.video(self.project, self.start, self.preview_edge, self.cancel, metrics=self.metrics))
            finally:
                audio.join()

    def _report(self, callback, *args):
        if not self.cancel.is_set():
            callback(*args)
        return GLib.SOURCE_REMOVE

    def _preparing(self, message):
        if self._buffering_callback is not None:
            def report():
                if not self.cancel.is_set() and self.buffering:
                    self._buffering_callback(True, message)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(report)

    def _message(self, bus, message, error, end):
        if self.cancel.is_set() or self.ended:
            return
        if message.type == Gst.MessageType.ERROR:
            problem, _ = message.parse_error()
            self.metrics.flush_log()
            error('Live preview failed: ' + problem.message)
        elif message.type == Gst.MessageType.EOS:
            self.ended = True
            self.last_position = self.total
            self.metrics.flush_log()
            end()
        elif message.type == Gst.MessageType.BUFFERING:
            active = message.parse_buffering() < 100
            if active == self.buffering and self._buffering_reported:
                return
            self.buffering = active
            self._buffering_reported = True
            self.metrics.mark_buffering(active, self.last_position, 'Buffering preview…')
            self.pipeline.set_state(Gst.State.PLAYING if self._wanted_playing and not self.buffering else Gst.State.PAUSED)
            if self._buffering_callback is not None:
                self._buffering_callback(self.buffering, 'Buffering preview…')

    def position(self):
        # Updated by the canvas when a decoded frame is actually presented.
        return self.last_position

    def play(self):
        self._wanted_playing = True
        self.pipeline.set_state(Gst.State.PAUSED if self.buffering else Gst.State.PLAYING)

    def pause(self):
        self._wanted_playing = False
        self.pipeline.set_state(Gst.State.PAUSED)
        self.position()

    def stop(self):
        if self.cancel.is_set():
            return
        stopping = time.monotonic()
        self.cancel.set()
        with self._av_cond:
            self._av_cond.notify_all()
        # NULL releases a worker blocked by appsrc backpressure. The worker
        # closes its generator, reaps FFmpeg and deletes its private sprites.
        self.pipeline.set_state(Gst.State.NULL)
        self.bus.disconnect(self.bus_handler)
        self.bus.remove_signal_watch()
        self.metrics.mark_stop((time.monotonic() - stopping) * 1000.0)
        self.metrics.flush_log()
