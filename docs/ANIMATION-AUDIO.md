# Animation playback and audio — 0.10.3

Applying an animation to a title or caption could make the live soundtrack sound
silent or broken while its selected files remained intact. The saved audio and
rendered PCM samples were unchanged. The bottleneck was live video processing:
the previous RGBA filters evaluated easing and copied colour channels through
interpreted expressions for every pixel, even after the entry animation ended.
With a 1080p title, frame delivery fell to approximately 10 fps, leaving the
shared video/audio transport unable to keep up with the playback clock.

Version 0.10.3 evaluates easing at relevant frame boundaries and uses native
opacity lookup tables, alpha extraction/clipping/merge and blur blending. Wipes
keep the original RGB outside the visible region before scaling, preserving
transparent edge interpolation. Native scaling, position and rotation continue
to use the same placement expressions. Caption cues reset their coefficients
between animations, including returning to fully sharp rendering after Blur.

Sparse command files cover entry, exit, caption boundaries and matching
intervals. They do not contain video or a frame-sized Python array. Cancelling
playback or export releases their temporary workspace. This change requires no
new package, model, GPU, lossy proxy or reduced preview resolution. Project
format and export settings remain unchanged.

## Validation

- All six general animations and Reveal, Highlight and Snake retain
  byte-identical PCM audio compared with the same unanimated title/caption.
  Checks include a continuous WAV soundtrack with echo, an attached local WAV
  on entry/exit, playback started partway through the element, saving/reopening,
  and protecting the original project and media.
- Native editor checks apply animation without touching Sound, including
  applying an animation to all captions. They verify each caption's sound file,
  trigger, volume and timeline audio remain unchanged.
- Decoded frames are compared with the former renderer for Rise, Pop, Blur,
  all wipe directions, word animation, rotation, partial opacity and mixed
  caption effects. Small channel-rounding differences are tolerated.
- A read-only check of the reported project's ending title retained audible,
  byte-identical soundtrack samples with Rise enabled. The project and source
  media were not changed.

## Native delivery measurements

```sh
/usr/bin/python3 tools/benchmark_element_playback.py --output /tmp/animation-audio.json
```

This generates a continuous WAV and 1920×1080, 30 fps composition, then plays
Rise, Pop, Fade, Wipe and Blur through the actual live stream and synchronized
GStreamer RGB/PCM sinks. The sinks consume audio silently; they do not play
through the user's speakers. Local measurements on 2026-10-12:

| Elements | Frames per 4-second run | Maximum video interval | Maximum audio arrival lateness |
|---|---:|---:|---:|
| Titles | 120/120 | 36.16 ms | 6.97 ms |
| Captions | 120/120 | 39.90 ms | 8.27 ms |

Expected frame spacing is 33.33 ms. All cases contained audible PCM buffers.
The previous title path delivered many frames roughly 100–110 ms apart and
occasional gaps around 200 ms. These measurements cover native delivery, not GTK
painting or a real sound device. CPU contention, many simultaneous full-size
overlays and slow source decoding can still exceed the short preview buffer;
**Render smooth preview** remains available for such projects.
