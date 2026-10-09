"""Reproducible latency benchmark for the room-speech bubbles (speech onset -> first text / translation / final).

All times are time.monotonic(), which on macOS stops while the Mac sleeps (both processes freeze together),
so a sleeping laptop (lid closed on battery) does not spoil the numbers. Nothing is played: the test sentences are synthesized to files only (`say -o`), and the backend reads the
session file through a patched FileSource that starts when the benchmark client asks for it.

    python scripts/bench_bubbles.py make  DIR
    python scripts/bench_bubbles.py serve DIR calm|busy EVENTS.jsonl -- --port 8791 [reachy-captions args]
    python scripts/bench_bubbles.py run   DIR calm|busy EVENTS.jsonl --port 8791 [--out result.json]
    python scripts/bench_bubbles.py report result1.json [result2.json ...]
    python scripts/bench_bubbles.py models DIR tiny base small         # partial-model candidates, offline
    python scripts/bench_bubbles.py sim DIR calm|busy old|new [--first 0.5 --every 0.6 --partial-prio 1]

Sessions: `calm` = 3 s silence between sentences, `busy` = 0.9 s (the final of one sentence is still on the
Whisper worker when the next sentence starts). The server logs per-stage events (Whisper calls, partial
segments from the segmenter, partial jobs started, DeepL calls) to EVENTS.jsonl; `run` combines them with
the time every caption message reached the client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

SR = 16000

SENTENCES = [
    ("de1", "Markus", "Ja, das passt mir gut."),
    ("en1", "Samantha", "Can we move the meeting to tomorrow?"),
    ("de2", "Petra (Premium)", "Ich habe die Folien gestern Abend fertig gemacht und schicke sie euch gleich per Mail."),
    ("en2", "Daniel", "I think we need one more week for the calibration of the camera."),
    ("de3", "Anna (Premium)", "Wir sollten das Budget für das nächste Quartal noch einmal anschauen, weil die Kosten "
                              "für die Server deutlich gestiegen sind."),
    ("en3", "Samantha", "The prototype is stable now, so next week we will test it with real users in the lab."),
    ("de4", "Markus", "Habt ihr das gehört?"),
    ("de5", "Petra (Premium)", "Simon kümmert sich bis Freitag um die Kalibrierung der Kamera."),
]
GAPS = {"calm": 3.0, "busy": 0.9}
LEAD_S = 1.5


def synth(text: str, voice: str, path: Path) -> np.ndarray:
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), "--", text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(aiff), str(path)], check=True)
    aiff.unlink()
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    path.unlink()
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def make(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    clips = []
    for name, voice, text in SENTENCES:
        x = synth(text, voice, d / f"{name}.wav")
        loud = np.nonzero(np.abs(x) > 0.02 * np.abs(x).max())[0]
        x = x[max(0, loud[0] - 160):loud[-1] + 160]   # trim say's own silence: onset = 10 ms into the clip
        clips.append((name, text, x))
    rng = np.random.default_rng(0)
    for session, gap in GAPS.items():
        parts, meta, n = [np.zeros(int(LEAD_S * SR), np.float32)], [], int(LEAD_S * SR)
        for name, text, x in clips:
            meta.append({"name": name, "text": text, "lang": name[:2],
                         "onset": (n + 160) / SR, "end": (n + len(x) - 160) / SR})
            parts += [x, np.zeros(int(gap * SR), np.float32)]
            n += len(x) + int(gap * SR)
        audio = np.concatenate(parts)
        audio += (rng.standard_normal(len(audio)) * 1e-3).astype(np.float32)   # quiet room noise
        write_wav(d / f"{session}.wav", audio)
        (d / f"{session}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        print("made", d / f"{session}.wav", f"{len(audio) / SR:.0f} s")


def serve(d: Path, session: str, events: Path, argv: list[str]) -> None:
    """reachy-captions on the session file, started by the client ({"type": "bench_start"}), events logged."""
    import threading

    from reachy_meetings_backend import audio, captions, segmenter, stt

    ev = events.open("a", buffering=1)
    lock = threading.Lock()

    def log(kind: str, **kw) -> None:
        with lock:
            ev.write(json.dumps({"ev": kind, "t": time.monotonic(), **kw}) + "\n")

    start = threading.Event()
    pipe: list = []

    orig_init = captions.Pipeline.__init__

    def init(self, args):
        orig_init(self, args)
        pipe.append(self)
    captions.Pipeline.__init__ = init

    orig_msg = captions.Pipeline._client_message

    def client_message(self, msg):
        if msg.get("type") == "bench_start":
            start.set()
        return orig_msg(self, msg)
    captions.Pipeline._client_message = client_message

    async def file_run(self, out):
        while not start.is_set():
            await asyncio.sleep(0.005)
        block = int(self.block_s * SR)
        x = np.concatenate([self.audio, np.zeros(SR, np.float32)])
        t0 = time.monotonic()
        log("start", t0=t0)
        for k, i in enumerate(range(0, len(x), block)):
            await asyncio.sleep(max(0.0, t0 + (k + 1) * self.block_s - time.monotonic()))   # a mic delivers a block once recorded
            out.put_nowait(x[i:i + block])
    audio.FileSource.run = file_run

    orig_push = segmenter.Segmenter.push

    def push(self, chunk):
        segs = orig_push(self, chunk)
        if pipe and self is pipe[0].segmenter:
            for s in segs:
                log("seg", id=s.id, final=s.final, t_start=s.t_start, t_end=s.t_end)
                if not s.final and not s.early:
                    log("partial_seg", id=s.id, audio_s=len(s.audio) / SR, pending=pipe[0].pending)
        return segs
    segmenter.Segmenter.push = push

    orig_partial = captions.Pipeline._partial

    async def partial(self, seg, *a, **kw):
        log("partial_job", id=seg.id, audio_s=len(seg.audio) / SR)
        return await orig_partial(self, seg, *a, **kw)
    captions.Pipeline._partial = partial

    orig_call = stt.Transcriber.__call__

    def call(self, audio_, language=None):
        t = time.monotonic()
        r = orig_call(self, audio_, language)
        log("whisper", model=self.repo.split("whisper-")[-1].split("/")[0] if hasattr(self, "repo") else "?",
            audio_s=len(audio_) / SR, start=t, dur=time.monotonic() - t, text=r[0])
        return r
    stt.Transcriber.__call__ = call

    from reachy_meetings_backend import translate

    orig_tr = translate.DeepLTranslator.__call__

    async def tr(self, text, remember=True):
        t = time.monotonic()
        r = await orig_tr(self, text, remember)
        log("deepl", partial=not remember, chars=len(text), start=t, dur=time.monotonic() - t)
        return r
    translate.DeepLTranslator.__call__ = tr

    sys.argv = ["reachy-captions", "--file", str(d / f"{session}.wav")] + argv
    captions.cli()


async def run(d: Path, session: str, events: Path, port: int, out: Path | None) -> dict:
    import websockets

    meta = json.loads((d / f"{session}.json").read_text())
    msgs: list[dict] = []
    gap, last_audio = 0.0, None   # room audio arrives every 40 ms: a long gap = the Mac slept, run invalid
    for _ in range(600):   # the server is still loading its models
        try:
            ws = await websockets.connect(f"ws://localhost:{port}", max_size=None)
            break
        except OSError:
            await asyncio.sleep(0.5)
    else:
        raise SystemExit("server not reachable")
    async with ws:
        await ws.send(json.dumps({"type": "auth", "hf_token": None}))
        await ws.send(json.dumps({"type": "bench_start"}))
        try:
            async for m in ws:
                if isinstance(m, bytes):
                    now = time.monotonic()
                    gap = max(gap, now - last_audio) if last_audio else 0.0
                    last_audio = now
                elif isinstance(m, str):
                    msg = json.loads(m)
                    if msg.get("type") in ("caption", "vad"):
                        msgs.append({**msg, "recv": time.monotonic()})
        except websockets.ConnectionClosed:
            pass   # the server exits after the file
    evs = [json.loads(line) for line in events.read_text().splitlines()]
    t0 = [e for e in evs if e["ev"] == "start"][-1]["t0"]
    evs = [e for e in evs if e["t"] >= t0]
    result = analyse(meta, msgs, evs, t0)
    result["session"] = session
    result["max_audio_gap_s"] = round(gap, 2)
    if gap > 1.0:
        print(f"INVALID: no room audio for {gap:.1f} s (the Mac slept?)")
    if out:
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print_table([result])
    return result


def models(d: Path, names: list[str], reps: int = 3) -> None:
    """Partial-model candidates on what a partial sees: the first 0.6 / 0.9 / 1.5 s of speech (+ 0.3 s pre-roll)
    and the whole sentence. Time (median of `reps`), word errors against the same stretch of the reference
    (the first len(hyp) words), language detected."""
    from reachy_meetings_backend.stt import Transcriber

    meta = json.loads((d / "calm.json").read_text())
    with wave.open(str(d / "calm.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    for name in names:
        tr = Transcriber(name)
        for cut in (0.6, 0.9, 1.5, None):
            durs, errs, n, wrong_lang, empty = [], 0, 0, 0, 0
            for s in meta:
                a = x[int((s["onset"] - 0.3) * SR):int(((s["onset"] + cut) if cut else s["end"] + 0.1) * SR)]
                for _ in range(reps):
                    for _try in range(5):   # MLX aborts GPU work while a closed-lid Mac is in dark wake
                        try:
                            t = time.monotonic()
                            text, lang = tr(a)
                            durs.append(time.monotonic() - t)
                            break
                        except RuntimeError as e:
                            print("   retry:", str(e)[:80], flush=True)
                hyp = words(text)
                ref = words(s["text"])[:len(hyp)] if cut else words(s["text"])
                e, k = word_errors(" ".join(ref), text)
                errs, n = errs + e, n + max(k, len(hyp))
                wrong_lang += lang != s["lang"]
                empty += not text
                if cut == 0.9:
                    print(f"   {name} {s['name']} {cut}s: {text!r} ({lang})")
            print(f"{name:6} {'full' if cut is None else f'{cut}s':5} time median {np.median(durs):.3f} s  "
                  f"word errors {errs}/{n}  wrong language {wrong_lang}/{len(meta)}  empty {empty}/{len(meta)}",
                  flush=True)


def sim(d: Path, session: str, policy: str, first: float, every: float, small_s: float, turbo_s: float,
        deepl_s: float, partial_prio: float, text_after: float = 0.75) -> dict:
    """Discrete-event simulation of the room caption path, independent of the wall clock (a sleeping Mac).
    Real: Silero VAD + Segmenter on the session file (when each partial/final is cut). Modelled: whether a partial
    has text (see text_of), Whisper and DeepL times (constants, measured awake), one Whisper worker with a
    priority queue. policy "old" = a partial only while no Whisper job is
    pending (main until 2026-10-09), "new" = captions.PartialGate."""
    import heapq

    from reachy_meetings_backend import captions
    from reachy_meetings_backend.segmenter import Segmenter, SegmenterCfg
    from reachy_meetings_backend.vad import SileroVad

    meta = json.loads((d / f"{session}.json").read_text())
    with wave.open(str(d / f"{session}.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    x = np.concatenate([x, np.zeros(SR, np.float32)])
    seg = Segmenter(SegmenterCfg(first_partial_s=first, partial_every_s=every), vad=SileroVad())
    emitted = []   # (stream time, segment)
    vad_on = {}
    block = 640
    for i in range(0, len(x), block):
        for sg in seg.push(x[i:i + block]):
            emitted.append(((i + block) / SR, sg))
        if seg.active and seg._next_id not in vad_on:
            vad_on[seg._next_id] = (i + block) / SR
    def text_of(sg) -> tuple[str, str]:
        """What the small model returns for this partial, modelled from `models` (2026-10-09): empty for 2/8
        sentences at 0.6 s of speech, text (and the right language) for 8/8 at 0.9 s -> text from `text_after`
        seconds of speech, as long as the sentence's characters per second say."""
        end = sg.t_end
        for m in meta:
            if m["onset"] - 1.0 < sg.t_start < m["end"]:
                spoken = min(end, m["end"]) - m["onset"]
                if spoken < text_after:
                    return "", m["lang"]
                return "x" * int(len(m["text"]) * spoken / (m["end"] - m["onset"])), m["lang"]
        return "", "en"

    # --- worker simulation
    queue, seq, running = [], 0, None   # running = (finish, job)
    pending, gate = 0, captions.PartialGate()
    out = {"text": {}, "tr": {}, "final": {}, "final_tr": {}, "jobs": 0, "skipped": 0, "dl_chars": 0}
    ptr: dict[int, tuple[float, int]] = {}
    finalized: set[int] = set()

    def submit(t, kind, sg):
        nonlocal seq, pending
        pending += 1
        heapq.heappush(queue, (partial_prio if kind == "partial" else 1, seq, kind, sg))
        seq += 1

    def start(t):
        nonlocal running
        if running is None and queue:
            _, _, kind, sg = heapq.heappop(queue)
            if kind == "partial" and policy == "new":
                sg = gate.start(sg, t)[0]
            running = (t + (small_s if kind == "partial" else turbo_s), kind, sg)

    def finish(t, kind, sg):
        nonlocal pending
        pending -= 1
        out["jobs"] += 1
        if kind == "final":
            finalized.add(sg.id)
            out["final"].setdefault(sg.id, t)
            out["final_tr"].setdefault(sg.id, t + deepl_s)   # only counted for non-target sentences below
            return
        if policy == "new":
            nxt = gate.done()
            if nxt:
                submit(t, "partial", nxt[0])
        text, lang = text_of(sg)
        if not text or sg.id in finalized:
            return
        out["text"].setdefault(sg.id, t)
        if lang != "en":
            last_t, last_len = ptr.get(sg.id, (0.0, 0))
            ok = (captions.translate_partial(len(text), last_len, last_t, t) if policy == "new"
                  else len(text) - last_len >= 15 and t - last_t >= 1.2)
            if ok:
                ptr[sg.id] = (t, len(text))
                out["dl_chars"] += len(text)
                out["tr"].setdefault(sg.id, t + deepl_s)

    k = 0
    while k < len(emitted) or running or queue:
        t_next = emitted[k][0] if k < len(emitted) else float("inf")
        if running and running[0] <= t_next:
            t, kind, sg = running
            running = None
            finish(t, kind, sg)
            start(t)
            continue
        t, sg = emitted[k]
        k += 1
        if sg.final:
            gate.end(sg.id)
            submit(t, "final", sg)
        elif sg.early:
            pass
        elif policy == "old":
            if pending == 0:
                submit(t, "partial", sg)
            else:
                out["skipped"] += 1
        elif (job := gate.offer(sg, t)):
            submit(t, "partial", job[0])
        start(t)

    rows = []
    ends = {}
    for t, sg in emitted:
        if sg.final:
            ends[sg.id] = sg
    for s in meta:
        ids = sorted({sg.id for _, sg in emitted if s["onset"] - 1.0 < sg.t_start < s["end"]})
        first_text = min([out["text"][i] for i in ids if i in out["text"]] +
                         [out["final"][i] for i in ids if i in out["final"]], default=None)
        first_tr = min([out["tr"][i] for i in ids if i in out["tr"]] +
                       [out["final_tr"][i] for i in ids if i in out["final_tr"]], default=None)
        fin = max([out["final"][i] for i in ids if i in out["final"]], default=None)
        de = s["lang"] != "en"
        rows.append({"name": s["name"], "lang": s["lang"],
                     "vad": min([vad_on[i] for i in ids if i in vad_on], default=None),
                     "first_text": first_text, "first_tr": first_tr if de else None, "final": fin,
                     "final_tr": fin + deepl_s if de and fin else None,
                     "first_text_final": bool(first_text is not None and all(
                         i not in out["text"] or out["text"][i] > first_text for i in ids)),
                     "onset": s["onset"], "end": s["end"]})
    for r in rows:
        for k_ in ("vad", "first_text", "first_tr"):
            r[k_] = r[k_] - r["onset"] if r[k_] is not None else None
        for k_ in ("final", "final_tr"):
            r[k_] = r[k_] - r["end"] if r[k_] is not None else None
    res = {"rows": rows, "jobs": out["jobs"], "skipped": out["skipped"], "dl_chars": out["dl_chars"]}
    f = lambda v: f"{np.median([u for u in v if u is not None]):.2f}" if any(u is not None for u in v) else " -  "
    print(f"{session:5} {policy:3} first {first} every {every} prio {partial_prio} small {small_s} text@{text_after}: vad {f([r['vad'] for r in rows])}"
          f"  first text {f([r['first_text'] for r in rows])} (max {max(r['first_text'] for r in rows):.2f})"
          f"  first translation (de) {f([r['first_tr'] for r in rows])}  final {f([r['final'] for r in rows])}"
          f"  (max {max(r['final'] for r in rows):.2f})  first text = final {sum(r['first_text_final'] for r in rows)}/8"
          f"  whisper jobs {out['jobs']}  skipped {out['skipped']}  live DeepL chars {out['dl_chars']}", flush=True)
    return res


def words(s: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", s.lower()).split()


def word_errors(ref: str, hyp: str) -> tuple[int, int]:
    r, h = words(ref), words(hyp)
    dist = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, dist[0] = dist[0], i
        for j in range(1, len(h) + 1):
            cur = min(dist[j] + 1, dist[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, dist[j] = dist[j], cur
    return dist[len(h)], len(r)


def analyse(meta: list[dict], msgs: list[dict], evs: list[dict], t0: float) -> dict:
    caps = [m for m in msgs if m["type"] == "caption"]
    rows = []
    for s in meta:
        on, end = t0 + s["onset"], t0 + s["end"]
        # caption ids of this sentence: the segmenter's stream time of the segment start (incl. pre-roll)
        mine = sorted({e["id"] for e in evs if e["ev"] == "seg" and s["onset"] - 1.0 < e["t_start"] < s["end"]})
        cm = [m for m in caps if m["id"] in mine]
        text_msgs = [m for m in cm if m["text"]]
        tr_msgs = [m for m in cm if m.get("translation")]
        finals = [m for m in cm if m["final"] and m["text"]]
        last_final = {}
        for m in finals:
            last_final[m["id"]] = m
        vad = next((m for m in msgs if m["type"] == "vad" and m["speaking"] and m["recv"] > on - 0.3), None)
        segs = [e for e in evs if e["ev"] == "partial_seg" and e["id"] in mine]
        jobs = [e for e in evs if e["ev"] == "partial_job" and e["id"] in mine]
        wh = [e for e in evs if e["ev"] == "whisper" and on - 0.5 < e["start"] < end + 3 and e["audio_s"] < 30]
        final_text = " ".join(last_final[i]["text"] for i in sorted(last_final))
        err, n = word_errors(s["text"], final_text)
        first = text_msgs[0] if text_msgs else None
        e1, n1 = word_errors(" ".join(words(s["text"])[:len(words(first["text"]))]), first["text"]) if first else (0, 0)
        rows.append({
            "name": s["name"], "lang": s["lang"], "speech_s": round(s["end"] - s["onset"], 2),
            "vad": vad and vad["recv"] - on,
            "first_text": first and first["recv"] - on,
            "first_text_final": bool(first and first["final"]),
            "first_text_str": first and first["text"],
            "first_err": [e1, n1],
            "first_tr": tr_msgs[0]["recv"] - on if tr_msgs else None,
            "final": finals[-1]["recv"] - end if finals else None,
            "final_tr": (tr_msgs[-1]["recv"] - end if tr_msgs and tr_msgs[-1]["final"] else None),
            "partial_segs": len(segs), "partial_jobs": len(jobs),
            "skipped_pending": sum(1 for e in segs if e["pending"] > 0),
            "partial_msgs": sum(1 for m in cm if not m["final"]),
            "ids": mine, "err": [err, n], "final_text": final_text,
        })
    wh_all = [e for e in evs if e["ev"] == "whisper"]
    models = {}
    for e in wh_all:
        models.setdefault(e["model"], []).append(e["dur"])
    dl = [e for e in evs if e["ev"] == "deepl"]
    return {"rows": rows,
            "whisper": {k: {"n": len(v), "median": float(np.median(v)), "max": float(np.max(v))}
                        for k, v in models.items()},
            "deepl": {"partial": sum(e["partial"] for e in dl), "final": sum(not e["partial"] for e in dl),
                      "chars": sum(e["chars"] for e in dl),
                      "median": float(np.median([e["dur"] for e in dl])) if dl else None}}


def med(vals):
    v = [x for x in vals if x is not None]
    return f"{np.median(v):5.2f}" if v else "  -  "


def print_table(results: list[dict]) -> None:
    rows = [r for res in results for r in res["rows"]]
    results = [res for res in results if res.get("max_audio_gap_s", 0) <= 1.0]
    rows = [r for res in results for r in res["rows"]]
    for res in results:
        for r in res["rows"]:
            f = lambda x: f"{x:5.2f}" if x is not None else "  -  "
            print(f"{res.get('session', ''):5} {r['name']:4} {r['speech_s']:4.1f}s  vad {f(r['vad'])}  "
                  f"text {f(r['first_text'])}{'F' if r['first_text_final'] else ' '}  tr {f(r['first_tr'])}  "
                  f"final {f(r['final'])}  final_tr {f(r['final_tr'])}  segs {r['partial_segs']} jobs "
                  f"{r['partial_jobs']} skip {r['skipped_pending']}  WER {r['err'][0]}/{r['err'][1]}  "
                  f"first {r['first_text_str']!r}")
        print("  whisper:", {k: f"n={v['n']} med {v['median']:.2f} max {v['max']:.2f}" for k, v in res["whisper"].items()},
              " deepl:", res["deepl"])
    de = [r for r in rows if r["lang"] != "en"]
    print(f"MEDIAN (n={len(rows)}): vad {med([r['vad'] for r in rows])}  first text {med([r['first_text'] for r in rows])}"
          f"  first translation (de) {med([r['first_tr'] for r in de])}  final after end {med([r['final'] for r in rows])}"
          f"  final translation (de) {med([r['final_tr'] for r in de])}  first text was final: "
          f"{sum(r['first_text_final'] for r in rows)}/{len(rows)}  skipped partials "
          f"{sum(r['skipped_pending'] for r in rows)}/{sum(r['partial_segs'] for r in rows)}  WER "
          f"{sum(r['err'][0] for r in rows)}/{sum(r['err'][1] for r in rows)}  first-text WER "
          f"{sum(r['first_err'][0] for r in rows)}/{sum(r['first_err'][1] for r in rows)}")


if __name__ == "__main__":
    if os.environ.get("BENCH_CPU"):   # MLX on the CPU: GPU work of a closed-lid Mac in dark wake aborts (texts only)
        import mlx.core as mx
        mx.set_default_device(mx.cpu)
    if len(sys.argv) > 2 and sys.argv[1] == "serve":
        rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        serve(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), rest)
        raise SystemExit
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "run", "report", "models", "sim"])
    ap.add_argument("--first", type=float, default=0.5)
    ap.add_argument("--every", type=float, default=0.6)
    ap.add_argument("--small-s", type=float, default=0.21)
    ap.add_argument("--turbo-s", type=float, default=0.9)
    ap.add_argument("--deepl-s", type=float, default=0.3)
    ap.add_argument("--partial-prio", type=float, default=1)
    ap.add_argument("--text-after", type=float, default=0.75, help="sim: seconds of speech before small has text")
    ap.add_argument("args", nargs="+")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if a.cmd == "make":
        make(Path(a.args[0]))
    elif a.cmd == "run":
        asyncio.run(run(Path(a.args[0]), a.args[1], Path(a.args[2]), a.port, a.out))
    elif a.cmd == "sim":   # DIR session policy [cache.json]
        d = Path(a.args[0])
        sim(d, a.args[1], a.args[2], a.first, a.every, a.small_s, a.turbo_s, a.deepl_s, a.partial_prio, a.text_after)
    elif a.cmd == "models":
        models(Path(a.args[0]), a.args[1:])
    else:
        print_table([json.loads(Path(p).read_text()) for p in a.args])
