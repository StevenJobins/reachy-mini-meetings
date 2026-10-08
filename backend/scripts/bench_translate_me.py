"""Reproducible latency benchmark for "Translate me" (headset voice -> Reachy speaks the translation).

Nothing is played: the server runs with sounddevice's playback replaced by a sleep of the same length
(timestamps go to a JSONL file), and the test sentences are synthesized to files only.

    python scripts/bench_translate_me.py make  DIR                 # German test sentences (say -o) + room file
    python scripts/bench_translate_me.py serve DIR -- --port 8771 --meeting-lang en [reachy-captions args]
    python scripts/bench_translate_me.py run   DIR --port 8771 [--server-log server.log]

`run` streams each sentence like the headset does (mic.js: JSON auth first, then int16 PCM, 16 kHz mono,
640 samples = 40 ms per binary message, in real time, quiet noise in between) and measures from the end of
the speech in the file to the moment playback would start. With --server-log it also averages the server's
"me timing" lines (per stage). Word accuracy is checked against the reference text of each sentence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

SR = 16000
FRAME = 640

# (name, voice, text). "|" = a 0.5 s pause inside the sentence (a hesitation, must not cut the sentence).
SENTENCES = [
    ("s1", "Markus", "Ja, das passt mir gut."),
    ("s2", "Petra (Premium)", "Können wir das Meeting auf morgen verschieben?"),
    ("s3", "Markus", "Ich habe die Folien gestern Abend fertig gemacht und schicke sie euch gleich per Mail."),
    ("s4", "Petra (Premium)", "Wir sollten das Budget für das nächste Quartal noch einmal anschauen, weil die "
                              "Kosten für die Server deutlich gestiegen sind und wir sonst im Dezember ein "
                              "Problem bekommen."),
    ("s5", "Markus", "Der Prototyp läuft jetzt stabil. | Als nächstes testen wir ihn mit echten Nutzern im Labor."),
    ("s6", "Petra (Premium)", "Simon kümmert sich bis Freitag um die Kalibrierung der Kamera."),
    ("s7", "Markus", "Also ich glaube, | wir brauchen noch eine Woche mehr Zeit."),
    ("s8", "Petra (Premium)", "Habt ihr das gehört?"),
]
ROOM = ("Anna (Premium)", [
    "Gut, dann fangen wir mit dem ersten Punkt auf der Agenda an.",
    "Die Ergebnisse vom letzten Test sehen eigentlich ganz gut aus, aber die Latenz ist noch zu hoch.",
    "Ich würde vorschlagen, dass wir die Messungen nächste Woche noch einmal wiederholen.",
    "Hat jemand Einwände gegen den Termin am Donnerstag?",
])


def synth(text: str, voice: str, path: Path) -> np.ndarray:
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), "--", text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(aiff), str(path)], check=True)
    aiff.unlink()
    return read_wav(path)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def make(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    refs = {}
    for name, voice, text in SENTENCES:
        parts = [synth(p.strip(), voice, d / f"{name}_{i}.wav") for i, p in enumerate(text.split("|"))]
        pause = np.zeros(int(0.5 * SR), np.float32)
        audio = parts[0]
        for p in parts[1:]:
            audio = np.concatenate([audio, pause, p])
        for i in range(len(parts)):
            (d / f"{name}_{i}.wav").unlink()
        write_wav(d / f"{name}.wav", audio)
        refs[name] = text.replace(" |", "")
    (d / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=1))
    # room: someone talks most of the time (busy captions), 10 minutes; and a silent room
    voice, lines = ROOM
    room = [synth(t, voice, d / f"room{i}.wav") for i, t in enumerate(lines)]
    for i in range(len(lines)):
        (d / f"room{i}.wav").unlink()
    out, n = [], 0
    while n < 600 * SR:
        for r in room:
            out += [r, np.zeros(int(0.9 * SR), np.float32)]
            n += len(r) + int(0.9 * SR)
    write_wav(d / "room_busy.wav", np.concatenate(out))
    write_wav(d / "room_silent.wav", np.zeros(600 * SR, np.float32))
    print("made", d)


def serve(argv: list[str]) -> None:
    """reachy-captions with the playback stubbed: nothing audible, playback time logged."""
    import os

    from reachy_meetings_backend import captions, voice

    play_log = Path(os.environ["BENCH_PLAY_LOG"])

    class FakeSd:
        query_devices = staticmethod(voice.sd.query_devices)
        dur = 0.0

        @classmethod
        def play(cls, audio, sr, device=None):
            cls.dur = len(audio) / sr
            with play_log.open("a") as f:
                f.write(json.dumps({"t": time.time(), "dur": cls.dur}) + "\n")

        @classmethod
        def wait(cls):
            time.sleep(cls.dur)

    voice.sd = FakeSd
    sys.argv = ["reachy-captions"] + argv
    captions.cli()


def words(s: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", s.lower()).split()


def word_errors(ref: str, hyp: str) -> tuple[int, int]:
    """(edit distance in words, reference length)."""
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)], len(r)


async def run(d: Path, port: int, server_log: Path | None, play_log: Path, reps: int) -> None:
    import websockets

    refs = json.loads((d / "refs.json").read_text())
    rng = np.random.default_rng(0)
    log_start = server_log.stat().st_size if server_log and server_log.exists() else 0
    results = []
    async with websockets.connect(f"ws://localhost:{port}", max_size=None) as ws:
        await ws.send(json.dumps({"type": "auth", "hf_token": None}))
        me: list[dict] = []

        async def reader():
            async for m in ws:
                if isinstance(m, str):
                    msg = json.loads(m)
                    if msg.get("type") == "me":
                        me.append({**msg, "recv": time.time()})
        rd = asyncio.create_task(reader())

        async def stream(x: np.ndarray) -> list[float]:
            """Send in real time; returns the send time of every frame."""
            sent, t0 = [], time.time()
            for i in range(0, len(x), FRAME):
                await asyncio.sleep(max(0.0, t0 + i / SR - time.time()))
                await ws.send((np.clip(x[i:i + FRAME], -1, 1) * 32767).astype("<i2").tobytes())
                sent.append(time.time())
            return sent

        def noise(s: float) -> np.ndarray:
            return (rng.standard_normal(int(s * SR)) * 1e-3).astype(np.float32)

        await stream(noise(1.0))
        for rep in range(reps):
            for name, ref in refs.items():
                x = read_wav(d / f"{name}.wav")
                speech = np.nonzero(np.abs(x) > 0.02 * np.abs(x).max())[0]
                end_sample = speech[-1]
                n_me, plays_before = len(me), (play_log.read_text().count("\n") if play_log.exists() else 0)
                sent = await stream(np.concatenate([noise(0.3), x]))
                t_end = sent[int((0.3 * SR + end_sample) // FRAME)]
                # keep streaming quiet "mic" until Reachy would have finished speaking
                deadline, play = time.time() + 20, None
                while time.time() < deadline:
                    await stream(noise(0.2))
                    lines = play_log.read_text().splitlines() if play_log.exists() else []
                    if len(lines) > plays_before and len(me) > n_me:
                        p = json.loads(lines[plays_before])
                        if play is None:
                            play = p
                            deadline = p["t"] + p["dur"] + 1.0
                got = me[n_me:]
                hyp = " ".join(m["text"] for m in got)
                err, n = word_errors(ref, hyp)
                lat = play["t"] - t_end if play else float("nan")
                results.append({"name": name, "rep": rep, "latency": lat, "err": err, "n": n,
                                "parts": len(got), "audio_s": len(x) / SR, "text": hyp,
                                "say": " / ".join(m.get("translation") or m["text"] for m in got)})
                print(f"{name} rep{rep}: {lat:5.2f} s  parts={len(got)}  WER {err}/{n}  {hyp!r} -> "
                      f"{results[-1]['say']!r}", flush=True)
        rd.cancel()
    lats = np.array([r["latency"] for r in results])
    errs, ns = sum(r["err"] for r in results), sum(r["n"] for r in results)
    print(f"\nend of speech -> playback start: median {np.nanmedian(lats):.2f} s, mean {np.nanmean(lats):.2f} s, "
          f"min {np.nanmin(lats):.2f}, max {np.nanmax(lats):.2f} (n={len(lats)})")
    print(f"word error rate {errs}/{ns} = {100 * errs / ns:.1f} %, split sentences: "
          f"{sum(r['parts'] > 1 for r in results)}")
    if server_log:
        text = server_log.read_bytes()[log_start:].decode(errors="replace")
        rows = [list(map(float, re.findall(r"[\d.]+", line.split("me timing (s):")[1])))
                for line in text.splitlines() if "me timing (s):" in line]
        if rows:
            m = np.median(np.array(rows), axis=0)
            print("server stages (median): start {:.2f}  stt {:.2f} (wait {:.2f})  translate {:.2f}  synth {:.2f}"
                  "  -> playback {:.2f} after VAD speech end".format(*m[:6]))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "serve":
        serve(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
        raise SystemExit
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "run"])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--server-log", type=Path)
    ap.add_argument("--play-log", type=Path, help="default: DIR/play.jsonl (same as BENCH_PLAY_LOG of serve)")
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()
    if a.cmd == "make":
        make(a.dir)
    else:
        asyncio.run(run(a.dir, a.port, a.server_log, a.play_log or a.dir / "play.jsonl", a.reps))
