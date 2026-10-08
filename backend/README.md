# backend

Streaming, speech-to-text, translation, text-to-speech and meeting copilot (notes, summary, action items).

## Live captions for the speech bubbles (`reachy-captions`)

Python package `reachy_meetings_backend`. The pipeline:

```
room mic ──► Segmenter (VAD) ──► Whisper ──► DeepL translation ──► ws://127.0.0.1:8766 ──► headset
                                                ▲
             robot bridge state (DoA) ──────────┘ speaker direction per utterance
```

- **Where it runs:** once, on the laptop the robot is plugged into (Mac, Windows or Linux). The headsets only open the web page and receive finished captions, so they need nothing installed.
- **Mic:** the Reachy Mini Lite shows up on the laptop as a USB audio device, so the captions read the room audio directly. The robot process doesn't have to forward it. If no device matches `--mic` (default `Reachy`), the default mic is used, which is handy for testing on a laptop.
- **Segmenter:** neural voice activity detection (**Silero VAD**, ONNX, ~2 MB, downloaded once into `~/.cache/reachy-meetings/`), so motor noise, clicks and the robot's own sounds don't start an utterance. Fallback without `onnxruntime`: loudness threshold over the room noise. First *partial* 0.5 s after someone starts talking, then every 0.6 s; *final* after 0.7 s without speech.
- **Hallucination filter:** Whisper invents text on noise ("Vielen Dank.", "Untertitelung des ZDF", "1,0, 1,0, …"). Segments with high no-speech probability, low confidence or heavy repetition are dropped, and so are these classic phrases (`stt.plausible`).
- **Live translation:** partials are translated too (DeepL, throttled: ≥ 15 new characters and ≥ 1.2 s apart), so the bubble is readable while the person still talks. This roughly doubles the DeepL character use.
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
reachy-captions --target en                    # any spoken language, bubbles in English
reachy-captions --lang de --target en          # force German
reachy-captions                                # auto-detect the spoken language per utterance
reachy-captions --file test.wav --translator none --robot ""   # without room/robot/DeepL key
python scripts/print_captions.py               # shows what the headset receives
```

Make a test file: `say -v Anna -o t.aiff "Guten Morgen zusammen." && afconvert -f WAVE -d LEI16@16000 -c 1 t.aiff test.wav`

Headset over USB: `adb reverse tcp:8766 tcp:8766`

### Wireless headset (`--tunnel`)

The page is served from GitHub Pages over https, so the headset can only reach this server over `wss` with a valid certificate; `ws://localhost` only works on the Mac itself or over USB. `reachy-captions --tunnel` opens a Cloudflare quick tunnel (`brew install cloudflared`, no account) and posts its address (random, new on every start) to the ntfy.sh topic `reachy-meetings-xr-captions`; the page looks it up there (`tunnel.py`, `captions.js`). Nothing to enter on the headset.

Access: the address is public, so clients through the tunnel must first send their Hugging Face sign-in (`{"type": "auth", "hf_token": ...}`, the page does it automatically); the server checks the account with Hugging Face and closes the connection (code 4001) unless it is allowed. Default: the account this Mac is signed in with (`huggingface-cli login`); teammates: `--allow-hf name1,name2`. Local and LAN clients are not checked (as before). Tested: no sign-in → nothing sent, closed after 10 s; wrong token → 4001; the Mac's account → hello, captions, audio.

Signed address (code review 2026-10-08): the ntfy topic is public, so anyone could post their own address and collect the HF sign-in token the page sends there. The backend therefore signs the address with an ECDSA P-256 key kept in `~/.config/reachy-meetings/tunnel_key.pem` (created on first start, never in the repo) and posts `{"url", "ts", "sig", "key"}`; the page only connects to `wss://<name>.trycloudflare.com` addresses with a valid signature (WebCrypto) from a trusted key, not older than 13 h. Dominic's Mac key is built into `captions.js`; the key of another laptop is logged when its backend starts (`Tunnel signing key ...`) and can be pasted into Settings → *Trusted backend keys*. Tests: signature roundtrip (Python) and verification with WebCrypto in node (valid: true, tampered address: false).

Other hardening from the same review: the server listens on `127.0.0.1` by default (0.0.0.0 gave anyone on the Wi-Fi the room audio without sign-in; the https page cannot reach a LAN address anyway); an HF check that fails because of the network is no longer cached as "rejected"; `api.trycloudflare.com` (in cloudflared's error lines) is no longer mistaken for the tunnel address.

Unplugging and re-plugging the robot: the mic callback just stops (no exception) and PortAudio keeps its old device list, so captions, speech detection and room audio stopped for good. `MicSource` now reopens when no audio arrived for 2 s (re-initializing PortAudio for a fresh device list, waiting while the device is missing); a failed playback in `voice.py` asks for the same and retries once.

`--require-mic` exits at once when the Reachy mic is missing (instead of falling back to the Mac mic), for running it as a background service that is restarted until the robot is plugged in.

Tests, without models, mic or network: `pytest -q`

### Your voice in the meeting language ("Translate" button)

With 🌐 *Translate* on, the headset sends your voice to this server (binary frames, int16 PCM, 16 kHz mono) instead of straight to the robot. A second VAD cuts it into sentences; Whisper transcribes each one, DeepL translates it into the **meeting language**, and `say` (macOS speech synthesis) speaks it on the Reachy speaker, played straight to the USB audio device (`voice.py`). The meeting language is the language most spoken in the room over the last 10 minutes; before anyone has spoken it falls back to `--lang`, then `de`; `--meeting-lang` fixes it. The page shows what Reachy said above the dock (`me` message). While Reachy speaks, the room mic is ignored for captions and speech detection (+0.4 s echo), so its own voice neither becomes a bubble nor turns the head.

Voice: chosen by ear on 2026-10-08, switchable in VR (⋯ → 🗣 Viktor / Siri): **Viktor** (German, male; English: Daniel) or **Siri** (female; German: the macOS system voice, which must be set to the Siri voice in Spoken Content, because `say -v` cannot name Siri voices: an unknown name silently falls back to "Anna (Premium)", verified by comparing the output files; English: Samantha). Other languages: the best installed voice (Premium > Enhanced > compact; Eloquence voices like Eddy/Flo after that; Anna last, the team found her grating). macOS only ships compact voices; for natural speech, download e.g. a German and an English *Premium* voice under System Settings → Accessibility → Spoken Content → System voice → Manage Voices; they are picked up on the next start. Override: `--voice de=Markus,en=Ava`; off: `--voice-out none`.

Measured (synthetic English sentence fed over the WebSocket, playback stubbed): transcript + DeepL to German + synthesis call in one pass; synthesis of a 4.4 s German sentence takes 0.7 s.

### Faces for speaker following (`vision.py`)

The page decides who is talking from mouth movement (jaw opening over 1.5 s) plus the mic direction. On the headset the face detector reached only ~3 frames/s, too few for that. Now the backend reads the robot camera itself (found by name, `--vision-camera "Reachy Mini Camera"`; macOS, Windows and Linux allow a second reader next to the daemon), runs MediaPipe's FaceLandmarker and sends `{"type": "faces"}` to the page. The page uses them instead of its own detector, which pauses while they arrive.

Measured 2026-10-08 (M-series Mac): detector 54 frames/s at 960x540 (8 ms per frame); sent at `--vision-hz 20`, arriving at 18.6/s, 15 ms after capture. The camera is read in its own thread keeping only the newest frame (reading at the detection rate would let OpenCV queue old frames). `--vision-camera none` turns it off. Needs `mediapipe`, `opencv-python`, `cv2-enumerate-cameras` (in the dependencies).

### Room audio stream

The robot's own WebRTC audio drops ~55 % of the sound (measured on the page: 0 packets lost, low jitter, but more than half of the audio concealed, with and without this backend running, so it is the daemon's sender). This backend already reads the same microphone cleanly, so it also streams it to the page: binary WebSocket frames, int16 PCM, 16 kHz mono, 40 ms each (~32 KB/s per client). The page plays it with a small jitter buffer and mutes the WebRTC audio while the stream arrives. Between utterances the stream is turned down by 14 dB (`--pause-db`, noise gate driven by the neural VAD; 0 = off), so hum and fans stay out of the headset; voices of other people in the room count as speech and stay audible.

### Mic array tuning

On start the backend sets the robot's mic array (XMOS XVF3800) over USB to **stronger noise suppression** (`PP_MIN_NS` 0.15 → 0.05, `PP_MIN_NN` 0.51 → 0.30); the chip's adaptive beam keeps steering to whoever speaks. On exit (Ctrl-C or `kill`) the chip goes back to its defaults. `--no-focus-mic` leaves it alone. Measured: room background −8 dB.

A **fixed beam straight ahead** was tried and dropped: in an A/B test with a fixed sound source beside the robot, the fixed beam damped side voices so much that the VAD no longer detected speech, so Reachy never turned to a new speaker (with the adaptive beam it turned within ~5 s). The mic array only reports a horizontal direction; up/down comes from the camera (the page tilts up when someone speaks in front but no face is visible).

Direct USB (`micarray.py`, `pyusb` + `libusb-package`) because the daemon's `/api/audio/config/apply` cannot write integer parameters: values arrive as floats and `struct.pack("i", 1.0)` fails (reachy_mini 1.11). On Windows USB access may need a driver; then a warning and the defaults stay.

### Room-scan depth (WP2 extension)

The page sends each room-scan frame (JPEG) over the caption WebSocket; `depth.py` runs **Depth Anything V2, metric indoor small** on the laptop GPU (Apple `mps`, CUDA, else CPU) and returns metric depth at the page's 49×28 mesh grid, so the scanned room becomes 3D in the headset. Optional: `pip install -e ".[depth]"` (torch, transformers, pillow; the model, ~100 MB, downloads on first use). Without it the page shows the flat panorama. Tested: ~1.2 s per frame on an Intel Mac CPU (first call ~10 s with the model load); much faster on an M1.

### Meeting notes (summary, action items, next steps)

Every 45–60 s (`--summary-every`), if something new was said, the transcript goes to **Google Gemini** (free tier, `gemini-flash-lite-latest`; `gemini-flash-latest` ran out of free quota) and comes back as summary bullets, action items (who, what, by when) and next steps (open questions, decisions still to take), in the target language. It takes ~10 s and runs in the background.

Key from https://aistudio.google.com/apikey → `export GEMINI_API_KEY="..."` (macOS/Linux, e.g. in `~/.zshrc`) or `setx GEMINI_API_KEY "..."` (Windows). Without a key the captions run as before, just without notes. Note: on the free tier Google may use the content to improve its products — fine for the course project, not for confidential meetings.

**Speaker direction:** read from the daemon (`--daemon http://localhost:8000`, `GET /api/state/doa` + head yaw), no robot bridge needed.

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
