# Play startup — ClipCanvas 0.10.1

The reusable scrub decoder in 0.10.0 improved paused dragging/seeking. Pressing
Play still started the full composition pipeline, decoding all preceding frames
inside the active clip before delivering the requested picture. The delay grew
with the position inside that clip, even when its preview picture was already visible.

Live playback now seeks the main video input close to the playhead. The seek is
aligned to the source/composition frame grid, leaves a small frame margin and
preserves outgoing transition overlap. Picture fades keep their original clip
age; captions and overlays keep their global timeline positions.

An audible active clip uses a separate input for audio from its original trim
point. Only that input's audio stream is decoded, so echo, compressor, tempo,
limiter and fade history are retained without decoding all the old pictures.
Independent voice/music tracks retain their existing processing. Future clip,
music and overlay input indices account for the extra audio input.

The button changes to Pause immediately. Loading preview remains visible until
GTK commits the first picture for the current playback generation. Pausing
during startup works; resuming shows Loading until a picture arrives. Obsolete
frames/errors cannot clear a newer loading state. Decoder failures clear the
state and remove the frame callback.

## Reproducible local measurement

Run from the source checkout:

```sh
/usr/bin/python3 tools/benchmark_startup.py --repeats 2 --echo-track --output /tmp/startup.json
```

The benchmark generates a 50-second 1920×1080, 30 fps H.264/AAC clip, with a
60-frame keyframe interval, and adds an independent stereo WAV with echo.
It feeds the actual live stream into GStreamer's appsrc/Matroska/RGB appsink
path at full resolution. Both variants use identical transport options;
the reference disables only the new input video seek. The probe cache is warm,
as after importing media. Alternating variant order helps reduce warm-up bias.

Local medians from two runs on 2026-10-12:

| Play position inside clip | Reference | Optimized |
|---|---:|---:|
| Beginning | 119 ms | 142 ms |
| 15 seconds | 1,708 ms | 415 ms |
| 45 seconds | 5,515 ms | 701 ms |

These times include composition graph preparation and end at the first complete
native RGB sample. They exclude GTK painting, input-event scheduling and a real
audio device: the benchmark uses a synchronized silent audio sink. They are
measurements of generated media on this computer, not a guarantee for every
project. The small beginning-of-clip difference is timing variability; there is
no earlier picture decoding to remove at time zero.

## Validation and limits

The correctness checks compare deep-seek frames and PCM audio byte for byte
against a complete stream. They cover an audible trimmed clip at 1.5× speed,
echo plus compression, audio/picture fades, independent echo audio, a following
clip transition, animated text and captions. Native UI checks cover startup,
pause before the first frame, resume, superseded generations and decoder errors.

Full-resolution preview remains the default. The transport still carries raw
I420 pictures and PCM audio; this change creates no lossy proxy or preview file.
Final export uses its existing settings and graph path. The seek decoder still
closes before Play, and no second hidden playback pipeline is kept warm.

Some startup work remains: the audio graph processes earlier effect history,
FFmpeg opens sources, and layers/sound cues may need preparation. Video overlays,
many inputs, long keyframe intervals, slow storage and CPU load can still add
delay. This improvement specifically removes redundant earlier main-video
decoding; it does not promise instant playback for every composition.
