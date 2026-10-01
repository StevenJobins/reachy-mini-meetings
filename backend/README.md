# backend

Streaming, speech-to-text, translation, text-to-speech and meeting copilot (notes, summary, action items).

## Live captions for the speech bubbles (`reachy-captions`)

Python package `reachy_meetings_backend`. The pipeline:

```
room mic ──► Segmenter (VAD) ──► Whisper ──► Claude translation ──► ws://0.0.0.0:8766 ──► headset
                                                ▲
             robot bridge state (DoA) ──────────┘ speaker direction per utterance
```

- **Mic:** the Reachy Mini Lite shows up on the Mac as a USB audio device, so the captions read the room audio directly. The robot process doesn't have to forward it. If no device matches `--mic` (default `Reachy`), the default mic is used, which is handy for testing on a laptop.
- **Segmenter:** energy-based VAD. The threshold follows the room noise. It emits *partials* every 0.8 s while someone talks, and a *final* after 0.6 s of silence.
- **Whisper:** runs on the Apple GPU via `mlx-whisper`. Finals use `large-v3-turbo`, and the live partials use `small`. Other platforms fall back to `faster-whisper` on the CPU, which is too slow for partials, so use `--partial-model none` there.
- **Translation:** Claude (`claude-opus-5-5`, effort `low`) translates each final utterance. It also gets the last 3 utterances as context, which lets it fix ASR errors like "Büdree" → "Budget". If an utterance is already in the target language, it isn't translated.
- **Direction:** reads `speaker_doa_rad` from the robot bridge (`ws://localhost:8765`). This only works when the robot runs *with* media, so not with `--no-media`.

Measured on an M1 Pro with a 4.5 s German sentence: the final text arrives about 1.6 s after the speaker stops (with `--lang de`). Auto language detection adds about 1 s.

### Setup

Keep the venv **outside OneDrive**. Inside the synced folder, OneDrive blocks reading freshly installed files, and imports hang for minutes.

```bash
cd backend
uv venv ~/.venvs/reachy-backend --python 3.12
VIRTUAL_ENV=~/.venvs/reachy-backend uv pip install -e ".[dev]"
source ~/.venvs/reachy-backend/bin/activate
export ANTHROPIC_API_KEY=...          # for translation; otherwise --translator none
```

On the first start, the Whisper models are downloaded (~1.6 GB) into the HF cache.

### Run

```bash
reachy-captions --lang de --target en          # room speaks German, bubbles in English
reachy-captions                                # auto-detect the spoken language per utterance
reachy-captions --file test.wav --translator none --robot ""   # without room/robot/API key
python scripts/print_captions.py               # shows what the headset receives
```

Make a test file: `say -v Anna -o t.aiff "Guten Morgen zusammen." && afconvert -f WAVE -d LEI16@16000 -c 1 t.aiff test.wav`

Headset over USB: `adb reverse tcp:8766 tcp:8766`

Tests, without models, mic or network: `pytest -q`

### Protocol (port 8766, server → headset, one JSON per message)

```jsonc
{"type": "hello", "version": "0.1", "target": "en"}
{"type": "caption", "id": 7, "final": true,
 "text": "Guten Morgen zusammen.", "lang": "de",
 "translation": "Good morning, everyone.", "target": "en",
 "azimuth_deg": 25.0,          // speaker direction, robot base frame, + = left, null = unknown
 "t_start": 1759326000.1, "t_end": 1759326002.4}   // wall clock
```

The same `id` arrives several times:

1. **Partials** (`final: false`) arrive while the person talks.
2. The **final** text follows.
3. The final arrives once more with `translation` filled in.

The client **upserts by `id`**. A final with empty `text` means nothing was said, so the bubble should be removed. New clients get the last 20 finals replayed.

For the bubbles in the headset: place each bubble at `azimuth_deg` around the robot, in the same frame as the video window. If `azimuth_deg` is `null`, fall back to a fixed spot, for example just above the video.

### Layout

```
src/reachy_meetings_backend/
├── captions.py   pipeline + caption WebSocket server + CLI
├── segmenter.py  VAD: mic stream -> partial/final utterances (pure logic, tested)
├── audio.py      mic (sounddevice) and WAV file sources
├── stt.py        Whisper: mlx (Apple GPU) or faster-whisper (CPU)
├── translate.py  Claude translation with context
└── doa.py        speaker direction from the robot bridge state
scripts/print_captions.py
tests/test_logic.py
```

### Open TODOs

- [ ] DoA on the real robot: is it relative to the head or the body, and is there front/back ambiguity? (`doa.py`)
- [ ] Tune the VAD in a real room (`SegmenterCfg`: `margin_db`, `silence_s`)
- [x] Bubble rendering in the xr-client (`xr-client/pages/captions.js`)
- [ ] Translation latency: measure, and switch the model or effort if it's too slow
