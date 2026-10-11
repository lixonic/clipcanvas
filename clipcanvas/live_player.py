"""Clocked native playback of the composition's unencoded video/audio stream."""
import copy
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

from . import engine, model


class StreamPlayer:
    """One seek generation, with bounded queues and asynchronous cancellation.

    Matroska is only a pipe transport for raw I420 frames and PCM audio. No preview
    media file or video/audio encoder is involved. Both sinks share Gst's clock.
    """
    def __init__(self, project, start, autoplay, sample, error, end, volume=1, preview_edge=640):
        self.preview_edge = preview_edge
        self.project = copy.deepcopy(project)
        self.total = model.project_duration(project)
        self.start = max(0, min(start, max(0, self.total-1/project['fps'])))
        self.cancel = threading.Event()
        self.ended = False
        self.last_position = self.start
        self.pipeline = Gst.parse_launch(
            'appsrc name=source block=true max-bytes=1048576 caps=video/x-matroska '
            '! matroskademux name=demux '
            'demux.video_0 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=300000000 '
            '! videoconvert ! video/x-raw,format=RGB,pixel-aspect-ratio=1/1 '
            '! appsink name=picture emit-signals=true sync=true max-buffers=1 drop=true '
            'demux.audio_0 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=300000000 '
            '! audioconvert ! audioresample ! volume name=volume ! autoaudiosink')
        self.pipeline.get_by_name('volume').set_property('volume', volume)
        sink = self.pipeline.get_by_name('picture')
        sink.connect('new-sample', lambda s: sample(s.emit('pull-sample')))
        sink.connect('new-preroll', lambda s: sample(s.emit('pull-preroll')))
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus_handler = self.bus.connect('message', self._message, error, end)
        self.pipeline.set_state(Gst.State.PLAYING if autoplay else Gst.State.PAUSED)
        self.thread = threading.Thread(target=self._feed, args=(error,), daemon=True)
        self.thread.start()

    def _feed(self, error):
        stream = engine.live_stream(self.project, self.start, self.cancel, preview_edge=self.preview_edge)
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

    def _report(self, callback, *args):
        if not self.cancel.is_set():
            callback(*args)
        return GLib.SOURCE_REMOVE

    def _message(self, bus, message, error, end):
        if self.cancel.is_set():
            return
        if message.type == Gst.MessageType.ERROR:
            problem, _ = message.parse_error()
            error('Live preview failed: ' + problem.message)
        elif message.type == Gst.MessageType.EOS:
            self.ended = True
            self.last_position = self.total
            end()

    def position(self):
        if not self.cancel.is_set() and not self.ended:
            ok, value = self.pipeline.query_position(Gst.Format.TIME)
            if ok and value >= 0:
                self.last_position = min(self.total, self.start + value/Gst.SECOND)
        return self.last_position

    def play(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        self.pipeline.set_state(Gst.State.PAUSED)
        self.position()

    def stop(self):
        if self.cancel.is_set():
            return
        self.cancel.set()
        # NULL releases a worker blocked by appsrc backpressure. The worker
        # closes its generator, reaps FFmpeg and deletes its private sprites.
        self.pipeline.set_state(Gst.State.NULL)
        self.bus.disconnect(self.bus_handler)
        self.bus.remove_signal_watch()
