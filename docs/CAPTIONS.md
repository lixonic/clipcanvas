# Local captions in ClipCanvas 0.12.0

Click **Captions → Generate captions** to start immediately with the **Small**
model and automatic language detection. There is no required options dialog.
Progress and Cancel appear while the background job runs. The language selector
above Generate includes **Malayalam · മലയാളം**, English, Hindi, Tamil and automatic
detection. Selecting Malayalam chooses **Turbo** for stronger multilingual
recognition. The selected language/model is remembered for the next run. Other language
codes saved through the gear remain available in this selector.

The gear beside Generate opens **Caption settings** when you need to change
the source, model or language. **Apply** saves these choices across launches;
**Cancel** leaves them unchanged. Applying settings does not generate captions.
Small is the default for better accuracy; Tiny remains an optional quick draft.
Leave language empty to detect it, or enter a code such as `en`, `hi`, `ml` or `ta`.

Original video speech is the default. For narration in a separate WAV/MP3 track,
choose **Full mix · include soundtracks** once in Caption settings.
The job follows the edited sequence, source trims, speed and transition overlap.
Original-video mode restores muted clip speech for recognition without changing
export volume. Full-mix mode includes the current audio tracks and their levels.
Visual overlays are silent and do not contribute speech.

Select a generated cue and open **Text** to correct words; **Timing** edits its
start/end. Timeline dragging changes placement/duration. Changes support undo.
Hover over a caption in the sidebar to reveal its trash button. Clicking it
deletes only that cue from the sidebar and timeline, even if other items are
selected. **Ctrl+Z** restores it. Keyboard focus also reveals the trash button;
the caption body continues to open its text editor.
Caption style applies to all cues. MP4 exports burn in the appearance; SRT
import/export stores words and millisecond timing only. Replacing a caption
collection asks first and is undoable; failure/cancellation retains existing cues.

The cue's Text tab and **Caption style** offer the same four presets as titles:
**Clean**, **Outline**, **Shadow**, and **Highlight**. Choose a preset, optionally
adjust fonts/colors/effects, then apply. Words and timing remain editable;
appearance changes apply to every cue. Rounded line-following backgrounds and
shadow offsets/softness are editable under **Background and shadow adjustments**.
**Caption style** also controls shared vertical position and width. Select a cue
and use timeline **Up / Down** to move the entire Captions row; this does not
change its timing. The row layout is saved in the project.

## One-time setup

The first Generate downloads supporting Python libraries and the selected model.
Model files are approximately 75 MB (Tiny), 490 MB (Small) or 1.6 GB (Turbo), plus several hundred
MB for libraries/cache. Allow about 1 GB of free space for a Tiny installation,
and more for Small. No audio/video is uploaded, and no API key is required.
Subsequent generation with an installed model is forced offline. Model setup is
the only network-enabled speech operation.

The private runtime lives in:

```text
${XDG_DATA_HOME:-~/.local/share}/clipcanvas/transcription/
  venv/          isolated Python dependencies
  models/small/  default model and verification marker
  models/tiny/   present after choosing the optional draft model
  cache/         package/model download cache
  runtime.json   compatible runtime versions
```

It does not run system `pip` or change Ubuntu's Python packages. The Debian
installer requires `python3-venv` (Ubuntu 24.04's `python3.12-venv` also satisfies
it). Model files and optional speech libraries are downloaded separately and
are not bundled in the small `.deb`.

For a source checkout, optional command-line setup is:

```sh
/usr/bin/python3 tools/setup_transcription.py --model small
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
Review the result before sharing. Enabling Malayalam does not translate its
speech into English. The stock Small model performed poorly on our Malayalam
speech check; Turbo is the recommended Malayalam choice.

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

See [caption animations and attached sounds](EFFECTS.md) for Reveal, Highlight,
Snake and entry/exit effects. Translation and cloud/provider/MCP connections
remain deferred. Uninstalling the app leaves private
speech downloads and saved projects in your user data. Remove the transcription
folder separately if you no longer want those downloads.


## Malayalam captions and included fonts

Select **Malayalam · മലയാളം** above **Generate captions**, then Generate. Use the
multilingual **Turbo** model selected automatically when choosing Malayalam.
Its first download is about 1.6 GB; later generation is offline. Small and Tiny
remain available in Caption settings for smaller downloads, with lower Malayalam
accuracy. English-only `.en` models cannot transcribe Malayalam. For a
separate narration soundtrack choose **Full mix** in the gear settings. Generation
follows sequence timing and remains local after the initial speech setup.

The shared **Caption style** and title Text editor have a **Malayalam Google
Fonts** selector. ClipCanvas bundles these choices for offline use:

| Family | Suggested use |
| --- | --- |
| [Noto Sans Malayalam](https://fonts.google.com/specimen/Noto+Sans+Malayalam) | Clear everyday captions; default for Malayalam generation/SRT with a generic project font |
| [Manjari](https://fonts.google.com/specimen/Manjari) | Soft, rounded captions and conversational videos |
| [Anek Malayalam](https://fonts.google.com/specimen/Anek+Malayalam) | Strong caption emphasis and titles |

These are our video-legibility choices. Bold and size are editable. An explicitly
chosen project family stays selected when generating or importing captions. Font
shortcuts change the family without changing cue words, timing, or size. All
three fonts include their original SIL Open Font License 1.1 and a pinned Google
Fonts source manifest in `clipcanvas/fonts/`. Registration is private to the app;
preview and export share Pango shaping and the same bundled font files.

Regression checks cover Malayalam glyphs including conjuncts and chillus, regular
and bold styles, sample-identical preview/lossless-export pixels, distinct family
shapes, Unicode SRT round trips, language persistence, and undo/save/reopen.


Turbo uses the CTranslate2 conversion of
[Whisper large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo)
through the existing pinned faster-whisper runtime. It is an optional per-user
model download, not part of the Ubuntu installer. It still runs on the CPU and
may take longer than the video. Word and timing correction remain necessary.
