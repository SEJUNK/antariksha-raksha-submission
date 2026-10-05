# Jury Demonstration Video — Production Notes

| Item | Value |
|---|---|
| File | `ANTARIKSHA_RAKSHA_Jury_Demo.mp4` |
| Duration | 4:48.8 |
| Format | 1920 × 1080, 16:9, 30 fps, H.264 + AAC |
| Subtitles | `../narration/ANTARIKSHA_RAKSHA_Jury_Demo.srt` (sidecar; upload as captions where the player supports it) |
| Presenter | Sejal Khimani |
| Application | https://antariksha-raksha.vercel.app/ (backend on Railway), captured 5 Oct 2026, 17:38–17:41 UTC |

## How it was produced

1. **Capture.** A scripted Chrome session (DevTools protocol, 1920 × 1080, fresh profile) signed in to the deployed
   application and performed the demonstration: exit demo → live data, collision demo, inspection of the event, one
   Approve decision, analytics, exit demo. Screenshots are page-only (no browser chrome or taskbar). The exact backend
   API responses were recorded alongside (`source/capture_values.json`).
   (`source/capture.mjs`; credentials are read from environment variables and are not stored anywhere.)
2. **Narration.** `../narration/script.json` was synthesised sentence by sentence with a Microsoft neural voice
   (`en-IN-NeerjaNeural`, Indian English). Leading and trailing silence was trimmed, and fixed pauses were inserted
   (`source/tts.py`, `source/build_audio.py`). The narration is a synthetic voice.
3. **Composition.** `source/stage.html` + `source/scenes.mjs` define every scene. Real screenshots get camera moves and
   thin highlight boxes; explanatory scenes are HTML/canvas graphics. `source/render.mjs` renders 30 fps frames,
   encodes them with ffmpeg (x264, CRF 20) and muxes the narration (AAC, loudness-normalised).

No UI was mocked, and no value was typed in or changed. Highlight boxes and corner labels are post-production
annotations.

## Reproducing

Requires Node 22+, Python 3, ffmpeg, Google Chrome and `pip install edge-tts`.

```bash
# capture (performs real actions on the deployed system; use a test window)
AR_USER=<user> AR_PASS=<password> node capture.mjs
python tts.py && python build_audio.py
node render.mjs                    # → out/video_only.mp4
ffmpeg -i out/video_only.mp4 -i narration.wav -c:v copy -af loudnorm=I=-16:TP=-1.5 -c:a aac -b:a 160k -shortest final.mp4
```

The source scripts expect the screenshots in `shots/`, which correspond to `../screenshots/`.

See `FACTUAL_VALIDATION.md` for the claim-by-claim evidence and `../storyboard/` for the storyboard and the
screen/source mapping.
