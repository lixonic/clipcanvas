# Developing ClipCanvas

The current supported development environment is Ubuntu 24.04, Python 3.12,
GTK 3, GStreamer and FFmpeg. Windows is a planned target; see
[the development plan](docs/DEVELOPMENT-PLAN.md).

## Dependencies

```sh
sudo apt install python3 python3-venv python3-gi python3-cairo python3-gi-cairo \
  gir1.2-gtk-3.0 gir1.2-pango-1.0 gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-libav ffmpeg
```

Run with `/usr/bin/python3 -m clipcanvas` from the repository root. Use Ubuntu's
system Python so the GTK introspection bindings are available.

## Checks and packages

```sh
/usr/bin/python3 -m unittest discover -s tests -v
/usr/bin/python3 build_deb.py --output-dir dist
```

The tests generate their own short synthetic media and use temporary folders.
The UI test skips when no display is present. CI runs it under Xvfb. For an
equivalent local headless run, install `xvfb` and `xauth`, then use
`xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -v`.

Speech checks run only when `CLIPCANVAS_TEST_SPEECH_RUNTIME` and
`CLIPCANVAS_TEST_SPEECH_AUDIO` point to a provisioned Tiny runtime and a generated
English speech WAV. Ordinary CI does not download models. See
[caption setup](docs/CAPTIONS.md) and [0.4 validation](docs/LAYERS-CAPTIONS-VALIDATION.md).

The package builder produces a `.deb`, source archive, guide and checksums.
Generated artifacts go in `dist/` and are ignored by Git. The small committed
Ubuntu installers in `installers/ubuntu/` is a convenience download;
future releases should distribute installers through GitHub Releases.

## Work on a selected feature

Read [the feature inventory](docs/CANVA-FEATURES.md) and record the selected ID and
the user's intended outcome. Keep saved project compatibility, cancellation,
original-file protection and preview/export agreement in scope for each change.
Do not implement the entire inventory at once.

Rendering and media probes belong in `clipcanvas/engine.py`; project validation
and timing belong in `clipcanvas/model.py`; GTK controls belong in
`clipcanvas/app.py`; playback belongs in `clipcanvas/player.py`. Recovery and
portable collection belong in `clipcanvas/storage.py`. Layer/caption controls
belong in `clipcanvas/layer_ui.py`; shared Pango/Cairo layout belongs in
`clipcanvas/visuals.py`; speech and SRT workers belong in `clipcanvas/captions.py`. Media operations
must not block GTK's main thread. FFmpeg calls use argument lists, never shell
interpolation.

Add meaningful generated-media tests when composition, timing, mixing, loading
or file safety changes. Tests should verify the output behavior, rather than
merely copy implementation calculations. Include a repeatable manual preview
and export check for visual features.
