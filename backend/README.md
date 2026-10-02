# backend

Streaming, speech-to-text, translation, text-to-speech and meeting copilot (notes, summary, action items).

## Live captions for the speech bubbles (`reachy-captions`)

Python package `reachy_meetings_backend`. The pipeline:

```
room mic ──► Segmenter (VAD) ──► Whisper ──► DeepL translation ──► ws://0.0.0.0:8766 ──► headset
                                                ▲
             robot bridge state (DoA) ──────────┘ speaker direction per utterance
```

- **Where it runs:** once, on the laptop the robot is plugged into (Mac, Windows or Linux). The headsets only open the web page and receive finished captions, so they need nothing installed.
- **Mic:** the Reachy Mini Lite shows up on the laptop as a USB audio device, so the captions read the room audio directly. The robot process doesn't have to forward it. If no device matches `--mic` (default `Reachy`), the default mic is used, which is handy for testing on a laptop.
- **Segmenter:** energy-based VAD. The threshold follows the room noise. It emits *partials* every 0.8 s while someone talks, and a *final* after 0.6 s of silence.
- **Whisper:** the engine and models are picked from the hardware (override with `--engine`, `--model`, `--partial-model`):

  | Laptop | Engine | Final text | Live partials |
  |---|---|---|---|
  | Mac with Apple Silicon | `mlx-whisper` (GPU) | `large-v3-turbo` | `small` |
  | NVIDIA GPU (Windows/Linux) | `faster-whisper` (CUDA) | `large-v3-turbo` | `small` |
  | CPU only (Intel Mac, most Windows laptops) | `faster-whisper` (CPU) | `small` | off (too slow) |

- **Translation:** DeepL translates each final utterance. It gets the last 3 utterances as context. If an utterance is already in the target language, it isn't translated. Claude is an optional, paid alternative (`--translator claude`, install with `pip install -e ".[claude]"`), and it also fixes ASR errors like "Büdree" → "Budget" from context.
- **Direction:** reads `speaker_doa_rad` from the robot bridge (`ws://localhost:8765`). This only works when the robot runs *with* media, so not with `--no-media`.

Measured on an M1 Pro with a 4.5 s German sentence: the final text arrives about 1.6 s after the speaker stops (with `--lang de`). Auto language detection adds about 1 s. CPU-only path, measured on the same Mac: `small` takes ~3 s per sentence and makes more mistakes ("Budget" → "Büderey").

### Setup

Keep the venv **outside OneDrive**. Inside the synced folder, OneDrive blocks reading freshly installed files, and imports hang for minutes.

```bash
cd backend
uv venv ~/.venvs/reachy-backend --python 3.12
VIRTUAL_ENV=~/.venvs/reachy-backend uv pip install -e ".[dev]"
source ~/.venvs/reachy-backend/bin/activate
```

On the first start, the Whisper models are downloaded into the HF cache (~1.6 GB on Mac/GPU, ~0.5 GB on CPU).

**DeepL key:**

1. Sign up for the free *Developer* plan at deepl.com/pro-api. Our key reports a limit of 500,000 characters (check yours with `deepl.Translator(key).get_usage()`). A meeting hour is roughly 50,000 characters, so that's about 10 meeting hours. Measured: ~0.4 s per sentence. Once the credit is used up, you need the paid *Growth* plan (from 23.80 € per month, as of 2026-10).
2. Copy the key from deepl.com/your-account/keys.
3. Set it in your shell:
   - macOS / Linux: `echo 'export DEEPL_AUTH_KEY="..."' >> ~/.zshrc` (or `~/.bashrc`), then open a new terminal
   - Windows (PowerShell): `setx DEEPL_AUTH_KEY "..."`, then open a new terminal

Without a key, `reachy-captions` still runs, but shows untranslated captions.

### Run

```bash
reachy-captions --lang de --target en          # room speaks German, bubbles in English
reachy-captions                                # auto-detect the spoken language per utterance
reachy-captions --file test.wav --translator none --robot ""   # without room/robot/DeepL key
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
├── stt.py        Whisper: mlx (Apple GPU) or faster-whisper (CUDA/CPU)
├── translate.py  DeepL (default) or Claude translation, with context
└── doa.py        speaker direction from the robot bridge state
scripts/print_captions.py
tests/test_logic.py
```

### Open TODOs

- [ ] DoA on the real robot: is it relative to the head or the body, and is there front/back ambiguity? (`doa.py`)
- [ ] Tune the VAD in a real room (`SegmenterCfg`: `margin_db`, `silence_s`)
- [x] Bubble rendering in the xr-client (`xr-client/pages/captions.js`)
- [ ] Windows/Linux: not tested on real hardware yet (the CPU path was tested on the Mac with `--engine faster-whisper`)
