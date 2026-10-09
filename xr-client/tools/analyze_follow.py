"""Speaker following, measured from the headset log (~/Library/Logs/reachy-headset.log).

    python xr-client/tools/analyze_follow.py [log] [--after 17:00] [--before 18:30] [--headset-only] [-v]

The page logs "speech start" (backend neural VAD, after > 1.5 s of silence), "follow yaw <deg> by <source>"
(target moved > 4°) and "follow pitch ...". Two devices write into the same file: lines with AM/PM are the
headset, 24 h lines another browser (--headset-only keeps the AM/PM lines). The log has 1 s resolution, so
all times below are ±1 s.

Per speech episode (from "speech start" to the next one, at most --window s):
  first     s until the first yaw target change
  settle    s until the last yaw target change of the episode
  moves     yaw target changes, by source (face / mic / other)
  reversals two consecutive moves > 10° in opposite directions within 2 s (oscillation)
  back      a move back to within 10° of a target held earlier in the episode after being > 20° away
  fights    reversals where one move is by mic and the other by face (the two sources disagree)
  none      episodes without any yaw change
"""

from __future__ import annotations

import argparse
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

LINE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?: ([AP]M))? (.*)$")
YAW = re.compile(r"^follow yaw (-?\d+) by (.*)$")


def parse(path: Path, headset_only: bool):
    """[(t seconds of day, text)] in file order; times are made monotonic across midnight."""
    out = []
    for raw in path.read_text(errors="replace").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        h, mi, s, ampm, text = m.groups()
        if headset_only and not ampm:
            continue
        h = int(h)
        if ampm == "PM" and h != 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        out.append((h * 3600 + int(mi) * 60 + int(s), text))
    return out


def source_kind(src: str) -> str:
    if src.startswith("face"):
        return "face"
    if src.startswith("mic"):
        return "mic"
    return "other"


@dataclass
class Episode:
    t0: int
    moves: list = field(default_factory=list)   # (t, yaw, kind)
    start_yaw: float | None = None


def reversals(moves, prev_yaw):
    """Pairs of consecutive moves > 10° in opposite directions within 2 s -> [(kind1, kind2)]."""
    out, last = [], None   # last = (t, delta, kind)
    y = prev_yaw
    for t, yaw, kind in moves:
        if y is not None:
            d = yaw - y
            if abs(d) > 10:
                if last and t - last[0] <= 2 and d * last[1] < 0:
                    out.append((last[2], kind))
                last = (t, d, kind)
        y = yaw
    return out


def returns(moves, prev_yaw):
    """Moves back to within 10° of an earlier target of the episode after having been > 20° away."""
    hist = [prev_yaw] if prev_yaw is not None else []
    n = 0
    for _, yaw, _ in moves:
        away = False
        for old in reversed(hist[:-1]):
            if abs(yaw - old) <= 10 and any(abs(h - old) > 20 for h in hist[hist.index(old):]):
                away = True
                break
        n += away
        hist.append(yaw)
    return n


def fmt(v):
    return "-" if v is None else f"{v:.1f}"


def summary(xs):
    if not xs:
        return "n=0"
    return f"n={len(xs)} median {statistics.median(xs):.1f} s, mean {statistics.mean(xs):.1f} s, max {max(xs):.0f} s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default=str(Path.home() / "Library/Logs/reachy-headset.log"))
    ap.add_argument("--after", default="00:00", help="HH:MM, 24 h")
    ap.add_argument("--before", default="23:59")
    ap.add_argument("--window", type=float, default=10, help="max episode length, s")
    ap.add_argument("--headset-only", action="store_true")
    ap.add_argument("-v", action="store_true", help="one line per episode")
    a = ap.parse_args()
    hm = lambda s: int(s.split(":")[0]) * 3600 + int(s.split(":")[1]) * 60
    lo, hi = hm(a.after), hm(a.before) + 59

    lines = [(t, x) for t, x in parse(Path(a.log), a.headset_only) if lo <= t <= hi]
    episodes, cur, yaw_now = [], None, None
    all_moves = []
    for t, text in lines:
        if text.startswith("page loaded") or text.startswith("motors -> asleep"):
            cur, yaw_now = None, None
            continue
        if text == "speech start":
            cur = Episode(t, start_yaw=yaw_now)
            episodes.append(cur)
            continue
        m = YAW.match(text)
        if not m:
            continue
        yaw, kind = float(m.group(1)), source_kind(m.group(2))
        all_moves.append((t, yaw, kind))
        if cur and t - cur.t0 <= a.window:
            cur.moves.append((t, yaw, kind))
        yaw_now = yaw

    first, settle, kinds = [], [], {"face": 0, "mic": 0, "other": 0}
    rev = back = fights = none = 0
    for e in episodes:
        k = {"face": 0, "mic": 0, "other": 0}
        for _, _, kind in e.moves:
            k[kind] += 1
            kinds[kind] += 1
        r = reversals(e.moves, e.start_yaw)
        b = returns(e.moves, e.start_yaw)
        f = sum(1 for x, y in r if {x, y} == {"mic", "face"})
        rev += len(r); back += b; fights += f
        if e.moves:
            first.append(e.moves[0][0] - e.t0)
            settle.append(e.moves[-1][0] - e.t0)
        else:
            none += 1
        if a.v:
            h, rem = divmod(e.t0, 3600)
            path = " ".join(f"{y:.0f}{kind[0]}" for _, y, kind in e.moves)
            print(f"{h:02d}:{rem // 60:02d}:{rem % 60:02d} start {fmt(e.start_yaw)}  first {fmt(e.moves[0][0] - e.t0 if e.moves else None)}"
                  f"  settle {fmt(e.moves[-1][0] - e.t0 if e.moves else None)}  moves {len(e.moves)} (face {k['face']} mic {k['mic']} other {k['other']})"
                  f"  reversals {len(r)} (mic/face {f})  back {b}  | {path}")

    total = sum(kinds.values())
    span = (lines[-1][0] - lines[0][0]) / 60 if lines else 0
    print(f"log {a.log}, {a.after}-{a.before}{' headset only' if a.headset_only else ''}")
    print(f"speech episodes: {len(episodes)}, without any yaw change: {none}")
    print(f"first target change after speech start: {summary(first)}")
    print(f"last target change (settled):           {summary(settle)}")
    print(f"yaw moves in episodes: {total} (face {kinds['face']}, mic {kinds['mic']}, other {kinds['other']})"
          f" = {total / max(1, len(episodes)):.1f} per episode")
    print(f"oscillation: {rev} reversals > 10° within 2 s ({fights} of them mic vs face), "
          f"{back} returns to an earlier direction")
    r_all = reversals(all_moves, None)
    print(f"whole span ({span:.0f} min): {len(all_moves)} yaw moves, {len(r_all)} reversals"
          f" ({sum(1 for x, y in r_all if {x, y} == {'mic', 'face'})} mic vs face), "
          f"{len(r_all) / max(1e-9, span):.1f} reversals/min")


if __name__ == "__main__":
    main()
