"""Record a test meeting with the robot: camera, room sound, mic direction and head pose, with labels.

For working on "who is talking" offline and for the measurements (speaker attribution, word errors, delay).
Reads only: no motion command is ever sent to the robot.

    python scripts/record_meeting.py --people 3 --note "table, 1.5 m, narrow"

Stop the caption backend first if the camera or the mic is busy. Written to ~/reachy-data/<date_time>/
(outside the repo: the recordings show people):

  video.mp4     robot camera, as delivered (1920x1080)
  frames.csv    frame index, wall clock of each frame
  room.wav      channel 0 of the robot mic, 16 kHz (what the caption backend hears; replay with --file)
  room_all.wav  all channels of the device, if it has more than one
  state.jsonl   {"t", "doa": /api/state/doa, "full": /api/state/full} about 10 times a second
  labels.jsonl  {"kind": "read" | "doa_check" | "free", "person", "lang", "text", "t_start", "t_end", ...}
  meta.json     devices, start time, note

Parts (Enter starts and ends each item, q + Enter ends a part early):
  read       every person reads sentences in English and German: speaker labels and reference text
  doa_check  one person talks from one spot while the head is at 0, +30, -30 degrees (turn it yourself, e.g. with
             the headset): is the mic direction relative to the head or the body?
  free       a few minutes of normal discussion, labelled later from the video
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

SENTENCES = {
    "en": [
        "I think we should move the deadline to the end of next week.",
        "Could you send me the slides before the meeting on Thursday?",
        "The second prototype is cheaper, but it takes longer to build.",
        "Let us first agree on the budget and then talk about the schedule.",
        "I did not understand the last point, can you explain it again?",
        "We measured a delay of one hundred and forty milliseconds yesterday.",
        "Who is going to write the summary and share it with the team?",
        "That sounds good to me, as long as the customer agrees.",
    ],
    "de": [
        "Ich finde, wir sollten den Termin auf Ende nächster Woche verschieben.",
        "Kannst du mir die Folien vor der Sitzung am Donnerstag schicken?",
        "Der zweite Prototyp ist günstiger, aber der Bau dauert länger.",
        "Lasst uns zuerst das Budget klären und danach über den Zeitplan reden.",
        "Den letzten Punkt habe ich nicht verstanden, kannst du ihn nochmals erklären?",
        "Gestern haben wir eine Verzögerung von hundertvierzig Millisekunden gemessen.",
        "Wer schreibt die Zusammenfassung und schickt sie an das Team?",
        "Das klingt gut für mich, solange der Kunde einverstanden ist.",
    ],
}


def record_video(camera: str, out: Path, stop: threading.Event, info: dict) -> None:
    import cv2

    from reachy_meetings_backend.vision import find_camera

    index = find_camera(camera)
    if index is None:
        print(f"!! no camera matching {camera!r}: no video")
        return
    cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    ok, frame = cap.read()
    if not ok:
        print("!! camera read failed (busy?): no video")
        return
    h, w = frame.shape[:2]
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    # mp4v: 17 ms per 1080p frame here, avc1 33 ms (too slow for 30 frames/s in this thread)
    writer = cv2.VideoWriter(str(out / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    info["video"] = {"width": w, "height": h, "nominal_fps": fps}
    n = 0
    with open(out / "frames.csv", "w") as times:
        times.write("frame,t\n")
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                print("!! camera read failed, video ends here")
                break
            times.write(f"{n},{time.time():.4f}\n")
            writer.write(frame)
            n += 1
    writer.release()
    cap.release()
    info["video"]["frames"] = n


def record_audio(mic: str, out: Path, stop: threading.Event, info: dict) -> None:
    import sounddevice as sd

    from reachy_meetings_backend.audio import find_input_device

    device = find_input_device(mic)
    if device is None:
        print(f"!! no input device matching {mic!r}: no sound")
        return
    dev = sd.query_devices(device, "input")
    channels = dev["max_input_channels"]
    files = [wave.open(str(out / "room.wav"), "wb")]
    if channels > 1:
        files.append(wave.open(str(out / "room_all.wav"), "wb"))
    for f, ch in zip(files, (1, channels), strict=False):
        f.setnchannels(ch)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
    info["audio"] = {"device": dev["name"], "channels": channels, "rate": SAMPLE_RATE}

    def callback(indata, frames, t, status):
        if "t_first_block_end" not in info["audio"]:
            info["audio"]["t_first_block_end"] = time.time()
            info["audio"]["block"] = frames
        pcm = (np.clip(indata, -1, 1) * 32767).astype("<i2")
        files[0].writeframes(pcm[:, 0].tobytes())
        if channels > 1:
            files[1].writeframes(pcm.tobytes())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=channels, dtype="float32", blocksize=640,
                        device=device, callback=callback):
        stop.wait()
    for f in files:
        f.close()


def record_state(daemon: str, out: Path, stop: threading.Event, info: dict) -> None:
    def get(path: str):
        with urllib.request.urlopen(daemon.rstrip("/") + path, timeout=1) as r:
            return json.loads(r.read())

    n, warned = 0, False
    with open(out / "state.jsonl", "w") as f:
        while not stop.is_set():
            t = time.time()
            try:
                f.write(json.dumps({"t": round(t, 3), "doa": get("/api/state/doa"), "full": get("/api/state/full")}) + "\n")
                n += 1
            except (OSError, ValueError):
                if not warned:
                    print(f"!! daemon not reachable at {daemon}: no mic direction, no head pose")
                    warned = True
                stop.wait(2)
            stop.wait(max(0.0, 0.1 - (time.time() - t)))
    info["state_samples"] = n


def ask(prompt: str) -> bool:
    """False = quit this part."""
    return input(prompt).strip().lower() != "q"


def item(labels, kind: str, show: str, **more) -> bool:
    if not ask(f"\n  {show}\n  Enter = start (q = end this part) "):
        return False
    t0 = time.time()
    ok = ask("  ... recording, Enter when done ")
    labels.write(json.dumps({"kind": kind, "t_start": round(t0, 3), "t_end": round(time.time(), 3), **more},
                            ensure_ascii=False) + "\n")
    labels.flush()
    return ok


def run_parts(args, labels) -> None:
    people = [chr(ord("A") + i) for i in range(args.people)]
    if "read" in args.parts:
        print("\n=== Reading: each person reads the sentence shown, in a normal voice ===")
        todo = [(lang, p) for _ in range(args.rounds) for lang in args.langs for p in people]
        used = dict.fromkeys(args.langs, 0)
        for lang, p in todo:
            s = SENTENCES[lang][used[lang] % len(SENTENCES[lang])]
            used[lang] += 1
            if not item(labels, "read", f"Person {p} [{lang}]:  {s}", person=p, lang=lang, text=s):
                break
    if "doa_check" in args.parts:
        print("\n=== Mic direction: ONE person stays at ONE spot and counts aloud; turn the head yourself ===")
        for yaw in (0, 30, -30):
            if not item(labels, "doa_check", f"Head at about {yaw:+d} degrees (+ = left), body still. Person A counts to 15.",
                        person="A", head_yaw_nominal=yaw):
                break
    if "free" in args.parts:
        print(f"\n=== Free discussion, about {args.free_s:.0f} s; talk normally, also across each other ===")
        item(labels, "free", "Start the discussion.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(Path.home() / "reachy-data" / time.strftime("%Y-%m-%d_%H%M%S")))
    ap.add_argument("--mic", default="Reachy", help="input device (name substring); 'none' = off")
    ap.add_argument("--camera", default="Reachy Mini Camera", help="camera (name substring); 'none' = off")
    ap.add_argument("--daemon", default="http://localhost:8000", help="Reachy Mini daemon; '' = off")
    ap.add_argument("--people", type=int, default=3, help="people in the room (A, B, ...)")
    ap.add_argument("--langs", default="en,de", type=lambda s: s.split(","))
    ap.add_argument("--rounds", type=int, default=2, help="reading rounds (each: one sentence per person and language)")
    ap.add_argument("--parts", default="read,doa_check,free", type=lambda s: s.split(","))
    ap.add_argument("--free-s", type=float, default=180)
    ap.add_argument("--note", default="", help="seating, room, distance: anything worth knowing later")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # this checkout, not an installed copy
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    info = {"t_start": round(time.time(), 3), "people": args.people, "note": args.note,
            "mic": args.mic, "camera": args.camera, "daemon": args.daemon}
    stop = threading.Event()
    threads = []
    for on, fn, arg in ((args.camera != "none", record_video, args.camera), (args.mic != "none", record_audio, args.mic),
                        (bool(args.daemon), record_state, args.daemon)):
        if on:
            threads.append(threading.Thread(target=fn, args=(arg, out, stop, info), daemon=True))
            threads[-1].start()
    time.sleep(2.0)   # devices open, warnings printed
    print(f"Recording to {out}")
    try:
        with open(out / "labels.jsonl", "w") as labels:
            run_parts(args, labels)
    except (KeyboardInterrupt, EOFError):
        print("\nstopped")
    stop.set()
    for t in threads:
        t.join(timeout=5)
    info["t_end"] = round(time.time(), 3)
    (out / "meta.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    print(f"Done: {out}")
    for f in sorted(out.iterdir()):
        print(f"  {f.name:14s} {f.stat().st_size / 1e6:8.2f} MB")


if __name__ == "__main__":
    main()
