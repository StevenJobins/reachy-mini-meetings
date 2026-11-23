"""Play a recording of scripts/record_meeting.py through "who said it", as if it were live, and count how often the
label is right. For comparing versions of people.py / fusion.py / roster.py on real faces and voices.

    python scripts/replay_meeting.py prepare ~/reachy-data/2026-11-23_110108     # faces + voices, once (minutes)
    python scripts/replay_meeting.py run ~/reachy-data/2026-11-23_110108         # this checkout's code (seconds)
    python scripts/replay_meeting.py run DIR --code main                         # another git revision's code
    python scripts/replay_meeting.py video DIR                                   # the video with tags + subtitles

prepare runs the slow, version-independent parts and caches them next to the recording (replay/):
  faces.jsonl   vision.py FaceFinder on the video at --hz (20, as live), with the frame times of frames.csv
  voices.npz    the utterances found by the segmenter (Silero VAD) in room.wav, with their CAM++ voice embedding
run feeds faces (with the head pose of state.jsonl at their time) and finished utterances (with the mic direction)
to People / Fusion / Roster in time order, as captions.py does, and prints one line per utterance and a score for the
utterances inside a "read" label (who read is known). The recording stays where it is: it shows people.
video makes replay/headset_<code>.mp4: the recording with what the headset would have shown, the tag above each head
(lit while that person is taken to be talking) and the subtitles (live words as they are said, then the final with
its speaker). The text is Whisper's on the recorded sound (cached in replay/words.json), not translated.
video --split tests cutting a final where someone else takes over without a pause (SplitV2: mouths against their own
rest, turned faces not judged, the voice every 0.5 s, cuts at sentence boundaries); only the finals change.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import subprocess
import sys
import tempfile
import time
import wave
from collections import Counter
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[1] / "src"
DECIDE_S = 1.5   # a final is decided this long after its last word (0.8 s of silence + Whisper)


def load_package(code: str | None):
    """The reachy_meetings_backend modules of this checkout (None) or of a git revision."""
    if code is None:
        sys.path.insert(0, str(SRC))
        return importlib.import_module("reachy_meetings_backend")
    tmp = Path(tempfile.mkdtemp(prefix="replay_"))
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True,
                          cwd=SRC).stdout.strip()
    tar = subprocess.run(["git", "archive", code, "backend/src/reachy_meetings_backend"], capture_output=True,
                         check=True, cwd=root).stdout
    subprocess.run(["tar", "-x", "-C", str(tmp)], input=tar, check=True)
    pkg = tmp / "backend" / "src" / "reachy_meetings_backend"
    pkg.rename(tmp / "rmb_old")
    sys.path.insert(0, str(tmp))
    return importlib.import_module("rmb_old")


def read_states(rec: Path) -> list[dict]:
    return [json.loads(line) for line in open(rec / "state.jsonl")]


# ---------------------------------------------------------------- prepare
def prepare(rec: Path, hz: float) -> None:
    sys.path.insert(0, str(SRC))
    import cv2

    from reachy_meetings_backend.segmenter import SAMPLE_RATE, Segmenter, SegmenterCfg
    from reachy_meetings_backend.speakers import VoiceIds
    from reachy_meetings_backend.vad import SileroVad
    from reachy_meetings_backend.vision import FaceFinder, FaceId, FaceStream

    out = rec / "replay"
    out.mkdir(exist_ok=True)
    meta = json.loads((rec / "meta.json").read_text())

    # voices
    with wave.open(str(rec / "room.wav")) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    t_audio = meta["audio"]["t_first_block_end"] - meta["audio"]["block"] / SAMPLE_RATE
    seg = Segmenter(SegmenterCfg(), vad=SileroVad())
    finals = []
    for i in range(0, len(audio), 640):
        finals += [s for s in seg.push(audio[i:i + 640]) if s.final]
    if (last := seg.flush()) is not None:
        finals.append(last)
    voices = VoiceIds()
    deadline = time.time() + 120
    while voices.model is None and time.time() < deadline:
        time.sleep(0.2)
    rows, embs = [], []
    for s in finals:
        obs = voices._embed(s.audio, True)
        rows.append({"t0": t_audio + s.t_start, "t1": t_audio + s.t_end, "dur": len(s.audio) / SAMPLE_RATE,
                     "voice": obs is not None, "vdur": obs.dur if obs else 0.0, "mixed": bool(obs and obs.mixed),
                     "windows": obs.windows if obs else []})
        embs.append(obs.emb if obs is not None else np.zeros(512, np.float32))
    np.savez(out / "voices.npz", emb=np.array(embs, np.float32), rows=json.dumps(rows))
    print(f"{len(rows)} utterances, {sum(r['voice'] for r in rows)} with a voice")

    # faces
    finder = FaceFinder(FaceStream("x", hz)._landmarker(), FaceId())
    times = [float(line.split(",")[1]) for line in open(rec / "frames.csv").read().split("\n")[1:] if line]
    cap = cv2.VideoCapture(str(rec / "video.mp4"))
    next_t, n, t_start = 0.0, 0, time.time()
    with open(out / "faces.jsonl", "w") as f:
        for k, t in enumerate(times):
            ok = cap.grab()
            if not ok:
                break
            if t < next_t:
                continue
            next_t = t + 1 / hz
            ok, frame = cap.retrieve()
            faces = finder.step(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), t)
            f.write(json.dumps({"t": t, "faces": [{**x, "emb": None if x["emb"] is None else [round(float(v), 5) for v in x["emb"]]}
                                                  for x in faces]}) + "\n")
            n += 1
            if n % 200 == 0:
                print(f"  faces: {k}/{len(times)} frames, {time.time() - t_start:.0f} s")
    print(f"{n} frames with faces looked at")


# ---------------------------------------------------------------- run
def run(rec: Path, code: str | None, verbose: bool, snap: dict | None = None) -> dict:
    """snap: filled with what the headset would have got, for `video`: "faces" [(t, [(face, label, speaking)])],
    "live" [(t, utterance, label)] every 0.6 s while someone talks, "finals" [(t, utterance, label)],
    "voices" {utterance: {label: mean voice of the utterances given to that label before}}."""
    pkg = load_package(code)
    name = pkg.__name__
    people_mod = importlib.import_module(name + ".people")
    fusion_mod = importlib.import_module(name + ".fusion")
    speakers_mod = importlib.import_module(name + ".speakers")
    doa_mod = importlib.import_module(name + ".doa")
    roster_mod = importlib.import_module(name + ".roster")

    out = rec / "replay"
    z = np.load(out / "voices.npz")
    rows, embs = json.loads(str(z["rows"])), z["emb"]
    frames = [json.loads(line) for line in open(out / "faces.jsonl")]
    labels = [json.loads(line) for line in open(rec / "labels.jsonl")]
    states = read_states(rec)

    doa = doa_mod.DoaTracker("", keep_s=1e9)
    people = people_mod.People()
    clusters = speakers_mod.SpeakerClusters()
    fusion = fusion_mod.Fusion(clusters, people)
    roster = roster_mod.Roster(people)

    events = [(s["t"], 0, s) for s in states] + [(fr["t"], 1, fr) for fr in frames] + \
             [(r["t1"] + DECIDE_S, 2, (i, r)) for i, r in enumerate(rows)]
    if snap is not None:
        snap.update(faces=[], live=[], finals=[], lively=[])
        events += [(t, 3, i) for i, r in enumerate(rows) for t in np.arange(r["t0"] + 0.5, r["t1"] + DECIDE_S, 0.6)]
    label_of = (lambda p: roster_mod.Roster.label(p, roster.name_of(p))) if hasattr(roster, "name_of") else str
    events.sort(key=lambda e: (e[0], e[1]))
    said: dict[int, int | None] = {}
    why: dict[int, str] = {}
    tags: list[tuple[float, int]] = []
    last_tag = 0.0
    for t, kind, x in events:
        if kind == 0:
            pose = (x["full"] or {}).get("head_pose") or {}
            if pose.get("yaw") is not None:
                doa.set_pose(math.degrees(pose["yaw"]), math.degrees(pose.get("pitch") or 0.0),
                             math.degrees(pose.get("roll") or 0.0), now=x["t"])
            doa.add(x["doa"], doa.head_yaw_deg, now=x["t"])
        elif kind == 1:
            faces = [{**f, "emb": None if f.get("emb") is None else np.array(f["emb"], np.float32)} for f in x["faces"]]
            yaw, pitch, roll, steady = doa.pose_at(t) if hasattr(doa, "pose_at") else (doa.head_yaw_deg, 0.0, 0.0, True)
            try:
                people.update(faces, t, yaw, pitch, roll, steady=steady)
            except TypeError:
                people.update(faces, t, yaw)
            roster.sync()
            if t - last_tag >= 1.0:
                last_tag = t
                shown = people.shown(t, doa.head_yaw_deg) if hasattr(people, "shown") else \
                    [p for p in people.people if people.place(p, t, doa.head_yaw_deg)]
                tags.append((t, len(shown)))
            if snap is not None:
                talking = any(r["t0"] <= t <= r["t1"] for r in rows)
                lively = people.liveliest(t - 1.5, t) if talking and hasattr(people, "liveliest") else None
                speaking = roster.for_person(lively) if lively is not None else None
                out = []
                for f in x["faces"]:
                    p = people.tracks.get(f.get("track")) if hasattr(people, "tracks") else None
                    if p is not None and any(p is q for q in people.people):
                        pid = roster.for_person(p)
                        out.append((f, label_of(pid), pid == speaking))
                snap["faces"].append((t, out))
                snap["lively"].append((t, label_of(speaking) if speaking is not None else None))
        elif kind == 3:
            i, r = x, rows[x]
            if i in said:
                continue
            obs = speakers_mod.VoiceObs(embs[i], r["vdur"]) if r["voice"] else None
            d = fusion.partial(obs, r["t0"], min(t, r["t1"]))
            roster.sync()
            p = roster.speaker(d.voice, d.person)
            snap["live"].append((t, i, label_of(p) if p is not None else ""))
        else:
            i, r = x
            obs = speakers_mod.VoiceObs(embs[i], r["vdur"], r["mixed"], r["windows"]) if r["voice"] else None
            spread = doa.spread(r["t0"], r["t1"]) if hasattr(doa, "spread") else None
            try:
                d = fusion.final(obs, r["t0"], r["t1"], spread, key=i)
            except TypeError:
                d = fusion.final(obs, r["t0"], r["t1"], spread)
            roster.sync()
            said[i] = roster.speaker(d.voice, d.person)
            for key, v in getattr(d, "relabel", []):
                said[key] = roster.speaker(v, None)
            why[i] = d.why()
            if snap is not None:
                snap["finals"].append((t, i, label_of(said[i]) if said[i] is not None else ""))
                # each person's voice as the system knows it now: the mean of the utterances given to them so far
                # (for --split; the voice clusters are not all tied to a face yet)
                voices = {}
                for j, lab in [(j, lab) for _, j, lab in snap["finals"][:-1] if lab and rows[j]["voice"] and not rows[j]["mixed"]]:
                    voices.setdefault(lab, []).append(embs[j] / np.linalg.norm(embs[j]))
                voices = {lab: np.mean(v, 0) / np.linalg.norm(np.mean(v, 0)) for lab, v in voices.items()}
                snap.setdefault("voices", {})[i] = voices
    said = {i: roster.resolve(p) for i, p in said.items()}

    # who really spoke: the "read" label the utterance lies in (most of it)
    truth: dict[int, str] = {}
    for i, r in enumerate(rows):
        best = max(((min(r["t1"], lb["t_end"]) - max(r["t0"], lb["t_start"]), lb) for lb in labels if lb["kind"] == "read"),
                   key=lambda x: x[0], default=(0, None))
        if best[1] is not None and best[0] > 0.5 * (r["t1"] - r["t0"]):
            truth[i] = best[1]["person"]
    # one label per real person: the assignment with the most agreements
    pairs = Counter((truth[i], said[i]) for i in truth if said[i] is not None)
    mapping, used = {}, set()
    for (person, label), _ in pairs.most_common():
        if person not in mapping and label not in used:
            mapping[person] = label
            used.add(label)
    right = sum(1 for i in truth if said[i] is not None and mapping.get(truth[i]) == said[i])
    unknown = sum(1 for i in truth if said[i] is None)
    t_free = next((lb["t_start"] for lb in labels if lb["kind"] == "free"), float("inf"))
    for i, r in enumerate(rows):
        if verbose or i in truth:
            part = f"read by {truth[i]}" if i in truth else ("free" if r["t0"] >= t_free else "-")
            label = roster_mod.Roster.label(said[i]) if hasattr(roster_mod.Roster, "label") else str(said[i])
            print(f"  [{i:2d}] {r['t0'] - frames[0]['t']:6.1f}-{r['t1'] - frames[0]['t']:6.1f} s  {r['dur']:4.1f} s  {part:10s} -> "
                  f"{label or '(no name)':10s} | {why[i][:150]}")
    free = [i for i, r in enumerate(rows) if r["t0"] >= t_free]
    res = {"rows": rows, "code": code or "this checkout", "read": len(truth), "right": right, "wrong": len(truth) - right - unknown,
           "unknown": unknown, "participants": len({p for p in said.values() if p is not None}),
           "people": len(people.people), "tags": float(np.mean([n for _, n in tags])) if tags else 0.0,
           "tags_max": max((n for _, n in tags), default=0),
           "free": Counter(roster_mod.Roster.label(said[i]) or "(no name)" for i in free)}
    print(f"{res['code']}: read utterances {res['read']}: right {right}, wrong {res['wrong']}, no name {unknown}; "
          f"participants {res['participants']}, people {res['people']}, tags {res['tags']:.1f} on average "
          f"(max {res['tags_max']}) for 2 in view; free part: {dict(res['free'])}")
    return res


# ---------------------------------------------------------------- video
COLORS = [(64, 156, 255), (255, 149, 0), (52, 199, 89), (175, 82, 222), (255, 59, 48), (90, 200, 250)]
FONT = "/System/Library/Fonts/SFNS.ttf"


def transcribe(rec: Path, model: str) -> list[dict]:
    """Whisper on each utterance of voices.npz, with word times (wall clock); cached in replay/words.json."""
    path = rec / "replay" / f"words_{model}.json"
    if path.exists():
        return json.loads(path.read_text())
    from faster_whisper import WhisperModel

    meta = json.loads((rec / "meta.json").read_text())
    rows = json.loads(str(np.load(rec / "replay" / "voices.npz")["rows"]))
    with wave.open(str(rec / "room.wav")) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    t_audio = meta["audio"]["t_first_block_end"] - meta["audio"]["block"] / 16000
    fw = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=4)
    out = []
    for i, r in enumerate(rows):
        a = audio[max(0, int((r["t0"] - t_audio) * 16000)):int((r["t1"] - t_audio) * 16000)]
        lang = None
        if len(a) < 16000 * 0.5:
            out.append({"lang": "", "words": []})
            continue
        _, info = fw.transcribe(a[:16000 * 10], beam_size=1)   # only German or English, as captions.py --langs
        lang = info.language if info.language in ("de", "en") else "de"
        segs, _ = fw.transcribe(a, language=lang, beam_size=5, word_timestamps=True, condition_on_previous_text=False)
        words = [{"w": w.word, "t0": r["t0"] + w.start, "t1": r["t0"] + w.end} for sg in segs
                 if sg.no_speech_prob < 0.6 and sg.avg_logprob > -1.0 for w in sg.words]
        out.append({"lang": lang, "words": words})
        print(f"  [{i}] {lang}: {''.join(w['w'] for w in words).strip()[:100]}")
    path.write_text(json.dumps(out, ensure_ascii=False))
    return out


# --split v2: who talks when inside one utterance, from the mouths AND the voice, decided once it is over (as the final)
STEP_S = 0.25          # the timeline is looked at every this often
MOUTH_WIN_S = 1.5      # a mouth's activity: its lips over this long, centred on the moment
TURNED = 1.0           # a face turned this much further than people here usually sit (vision.turn, about 1 =
                       # profile; 2026-11-23: both mostly +0.8 towards the laptop, Dominic turned to Simon -1.3):
                       # its lips are not judged
SEEN_SHARE = 0.6       # ... nor when fewer than this share of the readings in the window are usable
REST_Q = 20            # a person's own rest: this percentile of their activity over the meeting so far
VOICE_WIN_S = 2.0      # the voice: CAM++ on windows this long, every 0.5 s
MOUTH_W, VOICE_W = 0.5, 0.5
SURE_GAP = 0.2         # the best person must lead the second by this much, else the moment is "not sure"
BOUNDARY_RUN_S = 0.75  # a turn this long is enough where the sentence allows a cut (punctuation or a pause) ...
MIDDLE_RUN_S = 1.5     # ... mid-sentence it takes this long and the voice of the part must agree
PAUSE_S = 0.25         # a gap between words this long is a natural place to cut


def _activity(v: list[float]) -> float | None:
    """As people.py: the spread of the fast part of the lip readings (reading minus the running mean of 9)."""
    if len(v) < 5:
        return None
    a = np.array(v)
    base = np.array([a[max(0, k - 4):k + 5].mean() for k in range(len(a))])
    return float(np.std(a - base))


class SplitV2:
    """--split: one subtitle per turn. Each moment of an utterance gets the person whose mouth moves most measured
    against their own rest (a turned or unseen face does not count either way) and whose voice it sounds like; runs
    of another person are a turn if long enough: BOUNDARY_RUN_S at a sentence boundary or pause, MIDDLE_RUN_S
    mid-sentence with the voice of that part agreeing. The cut lands on the gap between two words."""

    def __init__(self, rec: Path, faces: list, voices: dict):
        sys.path.insert(0, str(SRC))
        from reachy_meetings_backend.speakers import OnnxVoice

        meta = json.loads((rec / "meta.json").read_text())
        with wave.open(str(rec / "room.wav")) as w:
            self.audio = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        self.ta = meta["audio"]["t_first_block_end"] - meta["audio"]["block"] / 16000
        self.model = OnnxVoice()
        self.voices = voices
        self.lips: dict[str, list[tuple[float, float | None]]] = {}   # label -> (t, lips or None if not judgeable)
        seen: list[float] = []   # every face's turn so far: their median is how people here usually sit
        for t, out in faces:
            front = float(np.median(seen[-2000:])) if seen else 0.0
            for f, lab, _ in out:
                turned = f.get("turn") is not None and abs(f["turn"] - front) > TURNED
                ok = f.get("lips") is not None and not turned
                self.lips.setdefault(lab, []).append((t, f["lips"] if ok else None))
            seen += [f["turn"] for f, _, _ in out if f.get("turn") is not None]
        self.rest: dict[str, list[tuple[float, float]]] = {}   # label -> (t, activity) every 0.5 s, for the rest level
        for lab, v in self.lips.items():
            ts = np.array([t for t, _ in v])
            for a in np.arange(ts[0], ts[-1], 0.5):
                if (x := self.mouth(lab, a)) is not None:
                    self.rest.setdefault(lab, []).append((a, x))

    def emb(self, a: float, b: float) -> np.ndarray | None:
        x = self.audio[max(0, int((a - self.ta) * 16000)):int((b - self.ta) * 16000)]
        if len(x) < 16000 * 0.8:
            return None
        e = self.model(x)
        return e / np.linalg.norm(e)

    def mouth(self, lab: str, t: float) -> float | None:
        """Activity of that mouth around t; None = not judgeable (turned away, not seen)."""
        w = [x for tt, x in self.lips[lab] if t - MOUTH_WIN_S / 2 <= tt < t + MOUTH_WIN_S / 2]
        good = [x for x in w if x is not None]
        if len(w) < 5 or len(good) < SEEN_SHARE * len(w):
            return None
        return _activity(good)

    def rest_of(self, lab: str, t: float) -> float | None:
        past = [x for tt, x in self.rest.get(lab, []) if tt < t]
        return float(np.percentile(past, REST_Q)) if len(past) >= 20 else None

    def timeline(self, i: int, r: dict) -> list[tuple[float, str | None]]:
        profiles = self.voices.get(i, {})
        labs = sorted(set(self.lips) | set(profiles))
        vt = {}
        for c in np.arange(r["t0"] + VOICE_WIN_S / 2, r["t1"] - VOICE_WIN_S / 2 + 1e-6, 0.5):
            if (e := self.emb(c - VOICE_WIN_S / 2, c + VOICE_WIN_S / 2)) is not None:
                vt[c] = {lab: float(e @ p) for lab, p in profiles.items()}
        out = []
        for t in np.arange(r["t0"], r["t1"] + 1e-6, STEP_S):
            m = {}
            for lab in self.lips:
                a, rest = self.mouth(lab, t), self.rest_of(lab, t)
                if a is not None and rest:
                    m[lab] = math.log(max(a, 1e-5) / rest)
            v = vt[min(vt, key=lambda c: abs(c - t))] if vt else {}
            score = {}
            for lab in labs:
                s = 0.0
                if lab in m and len(m) >= 2:
                    others = [x for k, x in m.items() if k != lab]
                    s += MOUTH_W * float(np.clip(m[lab] - max(others), -1.5, 1.5)) / 1.5
                elif lab in m:   # the only mouth that can be judged: moving well above its own rest, or not
                    s += MOUTH_W * float(np.clip(m[lab] / math.log(3), -1, 1)) / 2
                if lab in v and len(v) >= 2:
                    others = [x for k, x in v.items() if k != lab]
                    s += VOICE_W * float(np.clip((v[lab] - max(others)) / 0.15, -1, 1))
                score[lab] = s
            best = sorted(score.items(), key=lambda x: -x[1])
            sure = len(best) >= 2 and best[0][1] - best[1][1] >= SURE_GAP
            out.append((t, best[0][0] if sure else None))
        return out

    def split(self, i: int, r: dict, ws: list[dict]) -> list[tuple] | None:
        """[(label, text, t_from, t_to)] if the utterance has turns, else None."""
        runs: list[list] = []
        for t, lab in self.timeline(i, r):
            if lab is None:
                continue
            if runs and runs[-1][0] == lab:
                runs[-1][2] = t + STEP_S
            else:
                runs.append([lab, t, t + STEP_S])
        if len(ws) < 2 or not runs:
            return None
        gaps = []   # (time of the cut, natural place?)
        for j in range(len(ws) - 1):
            pause = ws[j + 1]["t0"] - ws[j]["t1"]
            punct = ws[j]["w"].strip()[-1:] in ".,?!;:…"
            gaps.append(((ws[j]["t1"] + ws[j + 1]["t0"]) / 2, pause >= PAUSE_S or punct))
        # turns: runs long enough, merged with their own kind across what is too short to count
        kept: list[list] = []
        for lab, a, b in runs:
            if kept and kept[-1][0] == lab:
                kept[-1][2] = b
                continue
            near = [g for g, natural in gaps if natural and abs(g - a) <= 0.5]
            need = BOUNDARY_RUN_S if near else MIDDLE_RUN_S
            if b - a < need:
                continue   # a flicker: it belongs to the one before (at the start: to the one after)
            kept.append([lab, a, b])
        if len(kept) < 2:
            return None
        cuts = []
        for nxt in kept[1:]:
            change = nxt[1]
            natural = [g for g, n in gaps if n and abs(g - change) <= 0.6]
            pool = natural or [g for g, _ in gaps]
            cuts.append(min(pool, key=lambda g: abs(g - change)))
        edges = [r["t0"]] + cuts + [r["t1"] + 1]
        parts = []
        for (lab, _, _), a, b in zip(kept, edges, edges[1:]):
            text = "".join(w["w"] for w in ws if a <= (w["t0"] + w["t1"]) / 2 < b).strip()
            if not text:
                continue
            if parts and parts[-1][0] == lab:
                parts[-1] = (lab, parts[-1][1] + " " + text, parts[-1][2], min(b, r["t1"]))
                continue
            parts.append((lab, text, a, min(b, r["t1"])))
        # mid-sentence parts must sound like their person
        profiles = self.voices.get(i, {})
        checked = []
        for k, (lab, text, a, b) in enumerate(parts):
            natural_start = k == 0 or any(n and abs(g - a) < 0.05 for g, n in gaps)
            e = self.emb(a, min(b, r["t1"])) if not natural_start else None
            if (e is not None and lab in profiles and len(profiles) >= 2 and checked
                    and max(profiles, key=lambda x: float(e @ profiles[x])) != lab):
                p = checked[-1]
                checked[-1] = (p[0], p[1] + " " + text, p[2], b)
                continue
            checked.append((lab, text, a, b))
        return checked if len(checked) > 1 else None


def video(rec: Path, code: str | None, model: str, fps: float, width: int, split: bool = False) -> Path:
    import av
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    words = transcribe(rec, model)
    snap: dict = {}
    res = run(rec, code, False, snap)
    rev = subprocess.run(["git", "rev-parse", "--short", code or "HEAD"], capture_output=True, text=True, cwd=SRC).stdout.strip()
    labels = [json.loads(line) for line in open(rec / "labels.jsonl")]
    meta = json.loads((rec / "meta.json").read_text())
    note = meta.get("note", "")
    names = dict(x.split(" = ") for x in note.split(";")[0].split(", ") if " = " in x) if "=" in note else {}
    times = [float(line.split(",")[1]) for line in open(rec / "frames.csv").read().split("\n")[1:] if line]
    finals = {i: (t, lab) for t, i, lab in snap["finals"]}
    colors: dict[str, tuple] = {}

    def color(lab: str) -> tuple:
        return colors.setdefault(lab, COLORS[len(colors) % len(COLORS)]) if lab else (150, 150, 150)

    out_path = rec / "replay" / f"headset_{(code or 'this').replace('/', '_')}{'_split' if split else ''}.mp4"
    cut = {}
    if split:
        sp = SplitV2(rec, snap["faces"], snap.get("voices", {}))
        for i, r in enumerate(res["rows"]):
            if (parts := sp.split(i, r, words[i]["words"])) is not None:
                cut[i] = parts
    for i, parts in cut.items():
        print(f"  cut [{i}] at " + ", ".join(f"{p[2] - times[0]:.1f} s" for p in parts[1:]) + ": "
              + " | ".join(f"{lab}: {text[:50]}" for lab, text, _, _ in parts))
    W, H = width, round(width * 9 / 16)
    k = W / 1920
    font = ImageFont.truetype(FONT, round(30 * k))
    bold = ImageFont.truetype(FONT, round(30 * k))
    small = ImageFont.truetype(FONT, round(22 * k))
    try:
        bold.set_variation_by_name("Bold")
    except Exception:
        pass
    container = av.open(str(out_path), "w")
    vs = container.add_stream("h264", rate=round(fps))
    vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
    vs.options = {"crf": "23", "preset": "veryfast"}
    a_s = container.add_stream("aac", rate=16000)
    a_s.layout = "mono"

    def wrap(text: str, f, maxw: float) -> list[str]:
        lines, cur = [], ""
        for w in text.split():
            nxt = (cur + " " + w).strip()
            if f.getlength(nxt) > maxw and cur:
                lines.append(cur)
                cur = w
            else:
                cur = nxt
        return lines + ([cur] if cur else [])

    cap = cv2.VideoCapture(str(rec / "video.mp4"))
    t_first = times[0]
    fi, li = 0, 0
    next_t, n = t_first, 0
    for t in times:
        if not cap.grab():
            break
        if t < next_t:
            continue
        next_t += 1 / fps
        _, frame = cap.retrieve()
        img = Image.fromarray(cv2.cvtColor(cv2.resize(frame, (W, H)), cv2.COLOR_BGR2RGB)).convert("RGBA")
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        while fi + 1 < len(snap["faces"]) and snap["faces"][fi + 1][0] <= t:
            fi += 1
        if snap["faces"] and 0 <= t - snap["faces"][fi][0] < 0.3:
            for f, lab, speaking in snap["faces"][fi][1]:
                c = color(lab)
                x0, y0 = (f["cx"] - f["w"] / 2) * W, f["top"] * H
                x1, y1 = (f["cx"] + f["w"] / 2) * W, (f["top"] + f["h"]) * H
                d.rounded_rectangle([x0, y0, x1, y1], radius=12 * k, outline=c + (255 if speaking else 150,),
                                    width=max(2, round((5 if speaking else 2) * k)))
                text = lab + ("  spricht" if speaking else "")
                tw = bold.getlength(text)
                px, py = (x0 + x1) / 2 - tw / 2 - 16 * k, y0 - 62 * k
                if py < 66 * k:   # head at the top of the picture: the tag goes under the face
                    py = y1 + 12 * k
                d.rounded_rectangle([px, py, px + tw + 32 * k, py + 46 * k], radius=23 * k,
                                    fill=c + (235,) if speaking else (20, 20, 24, 190), outline=c + (255,), width=max(1, round(2 * k)))
                d.text((px + 16 * k, py + 7 * k), text, font=bold, fill=(255, 255, 255, 255))
        # subtitles: finals of the last 6 s and what is being said now, newest at the bottom
        lines = []
        for i, r in enumerate(res["rows"]):
            ws = words[i]["words"]
            if i in finals and finals[i][0] <= t:
                if t - finals[i][0] < 6 and ws:
                    if i in cut:   # --split: one subtitle per turn, marked
                        lines += [(lab + "  · geteilt", text, False) for lab, text, _, _ in cut[i]]
                    else:
                        lines.append((finals[i][1], "".join(w["w"] for w in ws).strip(), False))
            elif r["t0"] <= t:
                said_now = "".join(w["w"] for w in ws if w["t1"] <= t).strip()
                live = [lab for tt, j, lab in snap["live"] if j == i and tt <= t]
                if said_now or t <= r["t1"]:
                    lines.append((live[-1] if live else "", (said_now + " …").strip(), True))
        lines = lines[-3:]
        y = H - 30 * k
        for lab, text, live in reversed(lines):
            rows_ = wrap(text, font, W * 0.62)[-3:]
            hgt = (len(rows_) * 38 + 50) * k
            y -= hgt + 12 * k
            bx = W * 0.17
            d.rounded_rectangle([bx, y, W - bx, y + hgt], radius=18 * k, fill=(16, 16, 20, 205 if not live else 170))
            d.text((bx + 22 * k, y + 10 * k), lab or "(kein Name)", font=small, fill=color(lab.split("  · ")[0]) + (255,))
            for j, line in enumerate(rows_):
                d.text((bx + 22 * k, y + (42 + 38 * j) * k), line, font=font,
                       fill=(255, 255, 255, 255) if not live else (215, 215, 220, 255))
        # what really happened (the labels of the recording) and what this is
        while li + 1 < len(labels) and labels[li + 1]["t_start"] <= t:
            li += 1
        lb = labels[li] if labels and labels[li]["t_start"] <= t <= labels[li]["t_end"] else None
        truth = ("liest laut Ablauf: " + lb["person"] + (f" ({names[lb['person']]})" if lb["person"] in names else "")
                 if lb and lb["kind"] == "read" else "freies Gespräch" if lb else "")
        head = (f"So hätte es die Brille gezeigt · Code {rev}" + (" + Test: Teilen bei Sprecherwechsel (Mund + Stimme)" if split else "")
                + f" · Untertitel: Whisper {model}, ohne Übersetzung")
        d.rounded_rectangle([16 * k, 16 * k, 32 * k + small.getlength(head), 56 * k], radius=10 * k, fill=(0, 0, 0, 150))
        d.text((24 * k, 22 * k), head, font=small, fill=(230, 230, 230, 255))
        if truth:
            tw = small.getlength(truth)
            d.rounded_rectangle([W - 32 * k - tw, 16 * k, W - 16 * k, 56 * k], radius=10 * k, fill=(255, 214, 10, 220))
            d.text((W - 24 * k - tw, 22 * k), truth, font=small, fill=(0, 0, 0, 255))
        img = Image.alpha_composite(img, over).convert("RGB")
        vf = av.VideoFrame.from_ndarray(np.asarray(img), format="rgb24")
        vf.pts = None
        for pkt in vs.encode(vf):
            container.mux(pkt)
        n += 1
        if n % 600 == 0:
            print(f"  video: {t - t_first:.0f} s")
    for pkt in vs.encode():
        container.mux(pkt)
    # sound, from the first video frame on
    with wave.open(str(rec / "room.wav")) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    t_audio = meta["audio"]["t_first_block_end"] - meta["audio"]["block"] / 16000
    audio = audio[max(0, int((t_first - t_audio) * 16000)):][: int(n / fps * 16000)]
    for j in range(0, len(audio), 1024):
        af = av.AudioFrame.from_ndarray(audio[j:j + 1024][None, :].copy(), format="s16", layout="mono")
        af.sample_rate, af.pts = 16000, j
        for pkt in a_s.encode(af):
            container.mux(pkt)
    for pkt in a_s.encode():
        container.mux(pkt)
    container.close()
    print(f"{out_path} ({n} frames, {n / fps:.0f} s)")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["prepare", "run", "video"])
    ap.add_argument("recording")
    ap.add_argument("--hz", type=float, default=20)
    ap.add_argument("--code", help="a git revision whose people/fusion/roster to use (default: this checkout)")
    ap.add_argument("-v", "--verbose", action="store_true", help="every utterance, not only the read ones")
    ap.add_argument("--model", default="small", help="video: Whisper model for the subtitles")
    ap.add_argument("--fps", type=float, default=30, help="video: frames a second")
    ap.add_argument("--width", type=int, default=1280, help="video: width in pixels")
    ap.add_argument("--split", action="store_true", help="video: test cutting subtitles where another mouth takes over")
    args = ap.parse_args()
    rec = Path(args.recording).expanduser()
    if args.what == "prepare":
        prepare(rec, args.hz)
    elif args.what == "video":
        video(rec, args.code, args.model, args.fps, args.width, args.split)
    else:
        run(rec, args.code, args.verbose)


if __name__ == "__main__":
    main()
