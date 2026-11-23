"""Play a recording of scripts/record_meeting.py through "who said it", as if it were live, and count how often the
label is right. For comparing versions of people.py / fusion.py / roster.py on real faces and voices.

    python scripts/replay_meeting.py prepare ~/reachy-data/2026-11-23_110108     # faces + voices, once (minutes)
    python scripts/replay_meeting.py run ~/reachy-data/2026-11-23_110108         # this checkout's code (seconds)
    python scripts/replay_meeting.py run DIR --code main                         # another git revision's code

prepare runs the slow, version-independent parts and caches them next to the recording (replay/):
  faces.jsonl   vision.py FaceFinder on the video at --hz (20, as live), with the frame times of frames.csv
  voices.npz    the utterances found by the segmenter (Silero VAD) in room.wav, with their CAM++ voice embedding
run feeds faces (with the head pose of state.jsonl at their time) and finished utterances (with the mic direction)
to People / Fusion / Roster in time order, as captions.py does, and prints one line per utterance and a score for the
utterances inside a "read" label (who read is known). The recording stays where it is: it shows people.
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
def run(rec: Path, code: str | None, verbose: bool) -> dict:
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
    res = {"code": code or "this checkout", "read": len(truth), "right": right, "wrong": len(truth) - right - unknown,
           "unknown": unknown, "participants": len({p for p in said.values() if p is not None}),
           "people": len(people.people), "tags": float(np.mean([n for _, n in tags])) if tags else 0.0,
           "tags_max": max((n for _, n in tags), default=0),
           "free": Counter(roster_mod.Roster.label(said[i]) or "(no name)" for i in free)}
    print(f"{res['code']}: read utterances {res['read']}: right {right}, wrong {res['wrong']}, no name {unknown}; "
          f"participants {res['participants']}, people {res['people']}, tags {res['tags']:.1f} on average "
          f"(max {res['tags_max']}) for 2 in view; free part: {dict(res['free'])}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["prepare", "run"])
    ap.add_argument("recording")
    ap.add_argument("--hz", type=float, default=20)
    ap.add_argument("--code", help="a git revision whose people/fusion/roster to use (default: this checkout)")
    ap.add_argument("-v", "--verbose", action="store_true", help="every utterance, not only the read ones")
    args = ap.parse_args()
    rec = Path(args.recording).expanduser()
    if args.what == "prepare":
        prepare(rec, args.hz)
    else:
        run(rec, args.code, args.verbose)


if __name__ == "__main__":
    main()
