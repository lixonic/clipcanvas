"""Clocked native playback of the composition's unencoded video/audio stream."""
import copy
import math
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst

from . import engine, model
from . import lazy_preview


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
        'buffer.src_1 ! audioconvert ! audioresample ! volume name=volume ! '+audio_sink)


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
        self.start = max(0, min(start, max(0, self.total-1/project['fps'])))
        self.cancel = threading.Event()
        self.ended = False
        self.last_position = self.start
        self.buffering = True
        self._buffering_reported = False
        self._wanted_playing = autoplay
        self._buffering_callback = buffering
        self.lazy = lazy_preview.supported(project)
        description = pipeline_description()
        if self.lazy:
            from fractions import Fraction
            width, height = engine.preview_dimensions(project, preview_edge)
            rate = Fraction(project['fps']).limit_denominator(100000)
            description = description.replace(
                'appsrc name=source block=true max-bytes=1048576 caps=video/x-matroska ! matroskademux name=demux ', '')
            description = description.replace('demux.video_0 ! buffer.sink_0',
                f'appsrc name=video_source block=true max-bytes={width*height*3//2} format=time '
                f'caps="video/x-raw,format=I420,width={width},height={height},framerate={rate.numerator}/{rate.denominator},pixel-aspect-ratio=1/1" ! buffer.sink_0')
            description = description.replace('demux.audio_0 ! buffer.sink_1',
                'appsrc name=audio_source block=true max-bytes=16384 format=time '
                'caps="audio/x-raw,format=S16LE,rate=48000,channels=2,layout=interleaved" ! buffer.sink_1')
        self.pipeline = Gst.parse_launch(description)
        self.pipeline.get_by_name('volume').set_property('volume', volume)
        sink = self.pipeline.get_by_name('picture')
        sink.connect('new-sample', lambda s: sample(s.emit('pull-sample')))
        sink.connect('new-preroll', lambda s: sample(s.emit('pull-preroll')))
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
            model.validate_project(self.project,check_files=True,require_clips=True)
        except Exception as problem:
            GLib.idle_add(self._report,error,str(problem))
            return
        def feed(kind, stream):
            source = self.pipeline.get_by_name(kind+'_source')
            count = 0
            rate = self.project['fps'] if kind == 'video' else 48000
            offset = max(0, math.ceil(self.start*rate-1e-7)/rate-self.start) if kind == 'video' else 0
            try:
                for data in stream:
                    if self.cancel.is_set():break
                    units = 1 if kind == 'video' else len(data)//4
                    if kind == 'video':
                        width, height = engine.preview_dimensions(self.project,self.preview_edge)
                        if width % 8:
                            # Gst raw I420 rows align to four bytes; FFmpeg's
                            # pipe is tightly packed, including chroma planes.
                            planes=[];at=0
                            for w,h in ((width,height),(width//2,height//2),(width//2,height//2)):
                                padding=bytes((-w)%4)
                                planes.extend(data[at+row*w:at+(row+1)*w]+padding for row in range(h))
                                at+=w*h
                            data=b''.join(planes)
                    buffer = Gst.Buffer.new_wrapped(data)
                    buffer.pts = round((offset+count/rate)*Gst.SECOND)
                    buffer.duration = round((offset+(count+units)/rate)*Gst.SECOND)-buffer.pts
                    count += units
                    if source.emit('push-buffer',buffer)!=Gst.FlowReturn.OK:break
                if not self.cancel.is_set():source.emit('end-of-stream')
            except Exception as problem:
                GLib.idle_add(self._report,error,str(problem))
            finally:stream.close()
        audio = threading.Thread(target=feed,args=('audio',lazy_preview.audio(self.project,self.start,self.cancel)),daemon=True)
        from . import preview_cache
        paths = list(dict.fromkeys(clip['path'] for clip in self.project['clips'] if not model.is_still(clip)))
        with preview_cache.PreviewWarmup(paths if self.preview_edge else [],self.preview_edge,self.cancel):
            audio.start()
            try:
                feed('video',lazy_preview.video(self.project,self.start,self.preview_edge,self.cancel))
            finally:audio.join()

    def _report(self, callback, *args):
        if not self.cancel.is_set():
            callback(*args)
        return GLib.SOURCE_REMOVE

    def _preparing(self, message):
        if self._buffering_callback is not None:
            def report():
                if not self.cancel.is_set() and self.buffering:self._buffering_callback(True, message)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(report)

    def _message(self, bus, message, error, end):
        if self.cancel.is_set() or self.ended:
            return
        if message.type == Gst.MessageType.ERROR:
            problem, _ = message.parse_error()
            error('Live preview failed: ' + problem.message)
        elif message.type == Gst.MessageType.EOS:
            self.ended = True
            self.last_position = self.total
            end()
        elif message.type == Gst.MessageType.BUFFERING:
            active = message.parse_buffering() < 100
            if active == self.buffering and self._buffering_reported:return
            self.buffering = active
            self._buffering_reported = True
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
        self.cancel.set()
        # NULL releases a worker blocked by appsrc backpressure. The worker
        # closes its generator, reaps FFmpeg and deletes its private sprites.
        self.pipeline.set_state(Gst.State.NULL)
        self.bus.disconnect(self.bus_handler)
        self.bus.remove_signal_watch()
