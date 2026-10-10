# Local captions in ClipCanvas 0.4

Use **Captions → Generate captions from speech**. Choose original video speech
or the full mix, Tiny or Small, and a language code or automatic detection.
The job follows the edited sequence, source trims, speed and transition overlap.
Original-video mode restores muted clip speech for recognition without changing
export volume. Full-mix mode includes the current audio tracks and their levels.
Visual overlays are silent and do not contribute speech.

Select a generated cue and open **Text** to correct words; **Timing** edits its
start/end. Timeline dragging changes placement/duration. Changes support undo.
Caption style applies to all cues. MP4 exports burn in the appearance; SRT
import/export stores words and millisecond timing only. Replacing a caption
collection asks first and is undoable; failure/cancellation retains existing cues.

## One-time setup

The first Generate downloads supporting Python libraries and the selected model.
Model files are approximately 75 MB (Tiny) or 490 MB (Small), plus several hundred
MB for libraries/cache. Allow about 1 GB of free space for a Tiny installation,
and more for Small. No audio/video is uploaded, and no API key is required.
Subsequent generation with an installed model is forced offline. Model setup is
the only network-enabled speech operation.

The private runtime lives in:

```text
${XDG_DATA_HOME:-~/.local/share}/clipcanvas/transcription/
  venv/          isolated Python dependencies
  models/tiny/   downloaded model and verification marker
  models/small/  present only after selecting Small
  cache/         package/model download cache
  runtime.json   compatible runtime versions
```

It does not run system `pip` or change Ubuntu's Python packages. The Debian
installer requires `python3-venv` (Ubuntu 24.04's `python3.12-venv` also satisfies
it). Model files and optional speech libraries are downloaded separately and
are not bundled in the small `.deb`.

For a source checkout, optional command-line setup is:

```sh
/usr/bin/python3 tools/setup_transcription.py --model tiny
```

An explicit `--runtime-dir /absolute/path` can be used for development tests.
The UI uses the normal user data location.

## Implementation and attribution

Recognition uses [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1)
and CPU/int8 inference. The models come from the
[SYSTRAN faster-whisper model repositories](https://huggingface.co/Systran/faster-whisper-tiny),
converted from Whisper. Their MIT notices and the optional packages' own licenses
remain with their distributions. ClipCanvas itself is custom application code;
it is not a Canva or CapCut fork.

The installer pins faster-whisper 1.2.1, Hugging Face Hub 0.36.0, and PyAV 16.1.0.
Actual speech recognition exposed PyAV 19's removed `metadata_errors` argument
([upstream report](https://github.com/SYSTRAN/faster-whisper/issues/1589)); the
compatible PyAV pin is intentional. A runtime marker from an incompatible setup
is not treated as ready.

Words are grouped into short editable cues using recognition timestamps. Tiny
is suitable for drafts and can omit words or get singular/plural forms wrong.
Small is slower and usually a better choice for difficult audio. Accuracy,
language detection, and timing vary with music, accents, noise and language.
No language-specific accuracy claim has been validated beyond the generated
English fixture. Review the result before sharing.

## Troubleshooting

- **Setup failed:** check the connection, free disk space and `python3-venv`.
  Retry Generate. Unsupported CPU/wheel combinations may need a newer machine
  or a separately validated runtime; GPU acceleration is not included.
- **Wrong/no words:** try an explicit language, Small, or original-video speech
  instead of a music-heavy full mix. Choose full mix when narration is a separate
  WAV soundtrack. Empty results are reported as no detected speech.
- **Caption timing shifted after editing clips:** timing is absolute; regenerate
  after changing the base sequence, or move the affected cues manually.
- **Missing glyphs:** choose an installed font covering that language. Text uses
  Pango for shaping and installed font fallback.
- **Long text is clipped:** reduce font size/expand layer height. The text-layer
  Picture controls expose width and height; captions share a bounded box.

Animated word highlighting, translation, caption packs, and cloud/provider/MCP
connections are not part of this release. Uninstalling the app leaves private
speech downloads and saved projects in your user data. Remove the transcription
folder separately if you no longer want those downloads.
