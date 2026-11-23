"""A meeting played through the "who said it" code (people.py, fusion.py, roster.py), to count how often the label
is right. Used by tests/test_scenarios.py (must stay above a floor) and scripts/eval_scenarios.py (the table).

Real: the voice embeddings (tests/data/voices.npz: 40 LibriSpeech speakers as a far microphone hears them, made by
scripts/eval_voices.py fixture), so two similar voices are two voices that really are similar.
Simulated, with numbers from the robot's logs: faces (found 20 times a second while in the camera's view, or only
now and then when far away), mouth movement (talkers 0.04-0.19, listeners 0.0002-0.005, a listener who laughs or
chews 0.03-0.08; backend log 2026-11-18), face embeddings (the same person 0.70 alike between two looks, two
people 0.10: scripts/eval_faces.py), the mic direction (a person's direction + an offset, 7 degrees of noise; or the
same for everyone, as on 2026-11-06), and Reachy's head turning to whoever talks while its reported angle lags.

What is counted, per utterance: the participant shown with it against who really spoke, after matching each real
speaker to one participant: right, wrong (someone else's label) or unknown (no label). And per second and face in
view: is the label above the head the one that person's speech gets.
"""

from __future__ import annotations

import inspect
import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from reachy_meetings_backend.fusion import Fusion
from reachy_meetings_backend.people import People
from reachy_meetings_backend.speakers import SpeakerClusters, VoiceObs

try:
    from reachy_meetings_backend.roster import Roster
except ImportError:   # the code before 2026-11-18, for the comparison: the label was the voice id
    Roster = None

FIXTURE = Path(__file__).parent / "data" / "voices.npz"
HZ = 20
FOV = 40.0            # the camera sees this far to each side
FACE_DIM = 128


def _flat(u, v):      # the camera of the simulation: 90 x 50 degrees, no distortion
    return (0.5 - u) * 90, (0.5 - v) * 50


@dataclass
class Voices:
    """The fixture: embeddings per speaker and clip length."""
    by_len: dict[float, np.ndarray]
    who: dict[float, np.ndarray]

    @classmethod
    def load(cls, path: Path = FIXTURE, room: bool = True) -> Voices:
        z = np.load(path)
        kind = "room" if room else "close"
        lens = sorted(float(k.split("_")[1]) for k in z.files if k.startswith(kind + "_"))
        return cls({n: z[f"{kind}_{n}"].astype(np.float32) for n in lens}, {n: z[f"who_{n}"] for n in lens})

    def speakers(self) -> int:
        return int(max(w.max() for w in self.who.values())) + 1

    def clips(self, speaker: int, length: float) -> np.ndarray:
        n = min(self.by_len, key=lambda x: abs(x - min(length, max(self.by_len))))
        return self.by_len[n][self.who[n] == speaker]

    def similar_pairs(self) -> list[tuple[float, int, int]]:
        """(similarity of the mean voices, a, b), the most similar first."""
        n = max(self.by_len)
        means = np.array([self.by_len[n][self.who[n] == s].mean(0) for s in range(self.speakers())])
        means /= np.linalg.norm(means, axis=1, keepdims=True)
        sims = means @ means.T
        return sorted(((float(sims[a, b]), a, b) for a in range(len(means)) for b in range(a + 1, len(means))), reverse=True)


@dataclass
class Seat:
    speaker: int              # index into the fixture
    yaw: float                # where they sit, robot base frame, + = left
    face: bool = True         # False: never in the picture (behind Reachy, on the phone)
    found: float = 1.0        # share of the frames in which their face is found while in view (far away: ~0.15)
    look: float = 0.84        # how well a look at the face shows who it is (two looks are look^2 alike); 0 = never
                              # (eyes covered, turned away, too small: vision.py gives no embedding)


@dataclass
class Scenario:
    name: str
    seats: list[Seat]
    utterances: int = 60
    head: str = "still"       # "still" at 0 degrees, or "follows" the talker (as the remote user would)
    pose_lag: float = 0.15    # the head angle the backend has is this old
    doa: str = "good"         # "good": points at the talker (+ offset, noise), "same": the same for everyone, "none"
    doa_sigma: float = 7.0
    doa_offset: float = 20.0
    fidget: float = 0.15      # how often a listener's mouth moves during someone's utterance
    short: float = 0.3        # share of utterances of 1.5 s (the others 3 or 6 s)
    # What the headset test of 2026-11-23 had and the situations of 2026-11-18 above do not (all off by default):
    tiny: float = 0.0         # share of utterances under 1 s ("Mm-hmm", "Gut."): no voice at all
    track_s: float = 0.0      # a face is followed this long on average before it is lost and found again as a new
                              # track (0 = never lost)
    dup: float = 0.0          # share of the frames in which a face comes twice, as two tracks a little apart
    ghosts: float = 0.0       # things taken for a face (a hand, a poster) per second, each for 0.5-3 s


@dataclass
class Result:
    right: float
    wrong: float
    unknown: float
    faces_right: float        # label above the head = that person's speech label; nan if no face was in view
    participants: int
    people: int
    said: list[tuple[int, int | None]] = field(default_factory=list, repr=False)   # (seat, participant)
    tags: float = float("nan")      # name tags the headset would show, on average ...
    in_view: float = float("nan")   # ... and people really in the camera's view then

    def __str__(self) -> str:
        f = "   -" if math.isnan(self.faces_right) else f"{self.faces_right:4.0%}"
        t = "" if math.isnan(self.tags) else f"  tags {self.tags:.1f} for {self.in_view:.1f} in view"
        return (f"right {self.right:4.0%}  wrong {self.wrong:4.0%}  unknown {self.unknown:4.0%}  faces {f}  "
                f"participants {self.participants}  people {self.people}{t}")


def _unit(x):
    return x / np.linalg.norm(x)


def run(scn: Scenario, voices: Voices, seed: int = 0, clusters: SpeakerClusters | None = None) -> Result:
    rng = np.random.default_rng(seed)
    rng2 = np.random.default_rng(seed + 7919)   # for what was added later, so the meetings above stay as they were
    people = People(_flat)
    clusters = clusters or SpeakerClusters()
    fusion = Fusion(clusters, people)
    roster = Roster(people) if Roster else None
    steady_kw = "steady" in inspect.signature(People.update).parameters

    common = _unit(rng.standard_normal(FACE_DIM))
    faces = [_unit(0.33 * common + _unit(rng.standard_normal(FACE_DIM))) for _ in scn.seats]   # two people ~0.10 alike
    order = {i: iter(()) for i in range(len(scn.seats))}

    def clip(seat: int, length: float) -> np.ndarray:
        pool = voices.clips(scn.seats[seat].speaker, length)
        key = (seat, len(pool))
        if key not in order or (nxt := next(order[key], None)) is None:
            order[key] = iter(rng.permutation(len(pool)))
            nxt = next(order[key])
        return pool[nxt]

    # the plan: who talks when
    t, plan, last = 2.0, [], None
    for _ in range(scn.utterances):
        seat = int(rng.choice([i for i in range(len(scn.seats)) if i != last or rng.random() < 0.3]))
        dur = 1.5 if rng.random() < scn.short else float(rng.choice([3.0, 6.0]))
        if scn.tiny and rng2.random() < scn.tiny:
            dur = 0.7
        plan.append((t, t + dur, seat))
        t += dur + rng.uniform(0.9, 2.0)   # the segmenter needs 0.8 s of silence between utterances
        last = seat
    end = t + 1.0

    head_true = np.zeros(int(end * HZ) + 1)
    if scn.head == "follows":
        target, yaw = 0.0, 0.0
        starts = {int(t0 * HZ) + 8: scn.seats[s].yaw for t0, _, s in plan}   # turns 0.4 s after they start
        for k in range(len(head_true)):
            target = max(-60.0, min(60.0, starts.get(k, target)))
            yaw += max(-6.0, min(6.0, target - yaw))                          # at most 120 degrees a second
            head_true[k] = yaw

    if scn.head == "wanders":   # the remote user looks around: a new direction every 2-5 s, up to 60 degrees a second
        target, yaw, until = 0.0, 0.0, 0
        for k in range(len(head_true)):
            if k >= until:
                target, until = float(rng2.uniform(-35, 35)), k + int(rng2.uniform(2, 5) * HZ)
            yaw += max(-3.0, min(3.0, target - yaw))
            head_true[k] = yaw

    tracks = {}            # seat -> [track id, mean embedding, looks, time of the last look]
    twins: dict[int, int] = {}      # seat -> track id of its second track (scn.dup)
    ghosts: list[dict] = []         # {"track", "yaw" in the room, "until", "emb"}
    shown: list[tuple[int, int]] = []   # (tags the headset would show, people in view), once a second
    next_track = itertools.count(1)
    said: list[tuple[int, int | None]] = []
    seen: list[tuple[int, int | None]] = []   # (seat, participant shown above that face), once a second
    by_start = iter(plan)
    current = next(by_start, None)
    fidgets: dict[int, float] = {}
    amp = 0.2
    for k in range(len(head_true)):
        now = k / HZ
        if current and now >= current[1]:   # the utterance is over: who said it?
            t0, t1, seat = current
            doa = None
            if scn.doa == "good":   # the array cannot tell front from back: behind Reachy reads as mirrored to the front
                yaw = scn.seats[seat].yaw
                yaw = yaw if abs(yaw) <= 90 else math.copysign(180 - abs(yaw), yaw)
                doa = (yaw + scn.doa_offset + rng.normal(0, scn.doa_sigma), 8.0)
            elif scn.doa == "same":
                doa = (45.0 + rng.normal(0, 4.0), 8.0)
            if roster and t1 - t0 < 1.0:   # too short for a voice (speakers.py MIN_S)
                d = fusion.final(None, t0, t1, doa, key=len(said))
                roster.sync()
                said.append((seat, roster.speaker(d.voice, d.person)))
            elif roster:
                d = fusion.final(VoiceObs(clip(seat, t1 - t0), t1 - t0), t0, t1, doa, key=len(said))
                roster.sync()
                said.append((seat, roster.speaker(d.voice, d.person)))
                for key, voice in d.relabel if t1 - t0 >= 1.0 else []:   # an earlier utterance turned out to be this new voice
                    said[key] = (said[key][0], roster.speaker(voice, None))
            else:
                d = fusion.final(VoiceObs(clip(seat, t1 - t0), t1 - t0), t0, t1, doa)
                said.append((seat, d.voice))
            current = next(by_start, None)
            fidgets = {}
        talking = current[2] if current and current[0] <= now < current[1] else None
        if current and abs(now - current[0]) < 0.5 / HZ:   # a new utterance: how they move
            amp = rng.uniform(0.11, 0.5)                     # mouth movement (std) 0.04 .. 0.19
            fidgets = {i: rng.uniform(0.08, 0.2) for i in range(len(scn.seats)) if i != current[2] and rng.random() < scn.fidget}
        yaw_true = head_true[k]
        yaw_told = head_true[max(0, k - round(scn.pose_lag * HZ))]
        rate = abs(head_true[k] - head_true[max(0, k - 2)]) * HZ / 2
        frame, frame_seats = [], []
        for i, s in enumerate(scn.seats):
            rel = s.yaw - yaw_true
            if not s.face or abs(rel) > FOV:
                tracks.pop(i, None)
                continue
            if rng.random() > s.found:
                continue
            if scn.track_s and i in tracks and rng2.random() < 1 / (scn.track_s * HZ):
                del tracks[i]          # lost: found again as a new track that knows nothing of the face yet
            tr = tracks.setdefault(i, [next(next_track), None, 0, -1e9])
            if s.look and now - tr[3] >= 0.4:   # a new look at who it is: 0.84 alike to their face, so two looks are 0.70 alike
                look = _unit(s.look * faces[i] + math.sqrt(1 - s.look**2) * _unit(rng.standard_normal(FACE_DIM)))
                n = min(tr[2], 11)
                tr[1], tr[2], tr[3] = _unit(look if tr[1] is None else tr[1] * n + look), tr[2] + 1, now
            if i == talking:
                mouth = amp * (k % 4 < 2) + rng.normal(0, 0.01)
            elif i in fidgets and talking is not None:
                mouth = fidgets[i] * (k % 6 < 3) + rng.normal(0, 0.005)
            else:
                mouth = 0.01 + rng.normal(0, 0.003)
            frame.append({"cx": 0.5 - rel / 90, "cy": 0.5, "top": 0.4, "w": 0.06, "h": 0.2, "mouth": max(0.0, mouth),
                          "track": tr[0], "emb": tr[1]})
            frame_seats.append(i)
            if scn.dup and rng2.random() < scn.dup:   # the same face again, as another track (vision.py keeps two
                twins.setdefault(i, next(next_track))  # tracks apart if their centres are over 0.6 face widths apart)
                frame.append({**frame[-1], "cx": frame[-1]["cx"] + 0.05, "track": twins[i], "emb": None})
                frame_seats.append(i)
        if scn.ghosts:
            if rng2.random() < scn.ghosts / HZ:
                ghosts.append({"track": next(next_track), "yaw": yaw_true + float(rng2.uniform(-FOV, FOV)),
                               "until": now + float(rng2.uniform(0.5, 3.0)),
                               "emb": _unit(rng2.standard_normal(FACE_DIM)) if rng2.random() < 0.5 else None})
            ghosts = [g for g in ghosts if g["until"] > now]
            for g in ghosts:
                rel = g["yaw"] - yaw_true
                if abs(rel) <= FOV:
                    frame.append({"cx": 0.5 - rel / 90, "cy": 0.55, "top": 0.47, "w": 0.05, "h": 0.16,
                                  "mouth": float(rng2.uniform(0, 0.12)), "track": g["track"], "emb": g["emb"]})
                    frame_seats.append(-1)
        if not steady_kw:      # the code before: no track, no embedding, no word about the head turning
            people.update([{a: b for a, b in f.items() if a not in ("track", "emb")} for f in frame], now, yaw_told)
        else:
            people.update(frame, now, yaw_told, steady=rate < 20.0)
        if roster and k % HZ == 0 and now > 3.0:   # the label above each head right now
            roster.sync()
            for i, f in zip(frame_seats, frame):
                person = people.tracks.get(f["track"])
                if i >= 0:
                    seen.append((i, roster.of_person.get(person.pid) if person is not None else None))
            tagged = people.shown(now, yaw_told) if hasattr(people, "shown") else [
                q for q in people.people if People.place(q, now, yaw_told)]
            shown.append((len(tagged), sum(1 for s in scn.seats if s.face and abs(s.yaw - yaw_true) <= FOV)))

    if roster:
        said = [(seat, roster.resolve(p)) for seat, p in said]
        seen = [(seat, roster.resolve(p)) for seat, p in seen]
    else:
        seen = []
    r = _score(scn, said, seen, len({p for _, p in said if p is not None}), len(people.people))
    if shown:
        r.tags, r.in_view = float(np.mean([a for a, _ in shown])), float(np.mean([b for _, b in shown]))
    return r


def _score(scn: Scenario, said, seen, participants: int, n_people: int) -> Result:
    labels = sorted({p for _, p in said if p is not None} | {p for _, p in seen if p is not None})
    seats = range(len(scn.seats))
    count = {(s, p): sum(1 for a, b in said if (a, b) == (s, p)) for s in seats for p in labels}
    best, best_map = -1, {}
    for combo in itertools.permutations(labels + [None] * len(scn.seats), len(scn.seats)):   # one label per speaker
        used = [c for c in combo if c is not None]
        if len(used) != len(set(used)):
            continue
        n = sum(count.get((s, p), 0) for s, p in zip(seats, combo))
        if n > best:
            best, best_map = n, dict(zip(seats, combo))
    right = sum(1 for s, p in said if p is not None and best_map[s] == p)
    unknown = sum(1 for _, p in said if p is None)
    f_right = sum(1 for s, p in seen if p is not None and best_map[s] == p)
    for s in seats:        # someone who never spoke has no speech label to compare with: any one label, kept, is right
        mine = [p for a, p in seen if a == s]
        if mine and not any(a == s for a, _ in said):
            f_right += max(mine.count(p) for p in set(mine) if p is not None) if any(p is not None for p in mine) else 0
    return Result(right / len(said), (len(said) - right - unknown) / len(said), unknown / len(said),
                  f_right / len(seen) if seen else float("nan"), participants, n_people, said)


def mean(results: list[Result]) -> Result:
    faces = [r.faces_right for r in results if not math.isnan(r.faces_right)]
    tags = [r.tags for r in results if not math.isnan(r.tags)]
    return Result(float(np.mean([r.right for r in results])), float(np.mean([r.wrong for r in results])),
                  float(np.mean([r.unknown for r in results])), float(np.mean(faces)) if faces else float("nan"),
                  round(float(np.mean([r.participants for r in results]))), round(float(np.mean([r.people for r in results]))),
                  tags=float(np.mean(tags)) if tags else float("nan"),
                  in_view=float(np.mean([r.in_view for r in results if not math.isnan(r.in_view)])) if tags else float("nan"))


def scenarios(voices: Voices) -> list[Scenario]:
    """The situations asked for on 2026-11-18, each with ordinary voices and with the most similar pair."""
    pairs = voices.similar_pairs()
    (_, a, b), (_, c, d) = pairs[0], pairs[len(pairs) // 2]     # the most similar two, and an ordinary pair
    others = [s for s in range(voices.speakers()) if s not in (a, b, c, d)]
    e = others[0]
    return [
        Scenario("two at the table, in view", [Seat(c, 20), Seat(d, -20)]),
        Scenario("two with similar voices, in view", [Seat(a, 20), Seat(b, -20)]),
        Scenario("two close together (8 degrees), in view", [Seat(c, 4), Seat(d, -4)]),
        Scenario("similar voices and close together", [Seat(a, 4), Seat(b, -4)]),
        Scenario("one in view, one never in the picture", [Seat(c, 15), Seat(d, 120, face=False)]),
        Scenario("nobody in the picture", [Seat(c, 100, face=False), Seat(d, -110, face=False)]),
        Scenario("nobody in the picture, similar voices", [Seat(a, 100, face=False), Seat(b, -110, face=False)]),
        Scenario("nobody in the picture, similar voices, mic the same for all",
                 [Seat(a, 100, face=False), Seat(b, -110, face=False)], doa="same"),
        Scenario("three, Reachy's head follows the talker", [Seat(c, 55), Seat(d, 0), Seat(e, -55)], head="follows"),
        Scenario("three, head follows, similar voices", [Seat(a, 55), Seat(b, 0), Seat(e, -55)], head="follows"),
        Scenario("three, head follows, mic the same for all", [Seat(c, 55), Seat(d, 0), Seat(e, -55)], head="follows",
                 doa="same"),
        Scenario("far faces found now and then", [Seat(c, 20, found=0.15), Seat(d, -20, found=0.15)]),
        Scenario("four, two of them never in the picture",
                 [Seat(a, 20), Seat(c, -20), Seat(b, 130, face=False), Seat(d, -130, face=False)]),
    ]



def field_scenarios(voices: Voices) -> list[Scenario]:
    """What the headset test of 2026-11-23 was like (backend log, 09:48-10:13; two people in a dark picture, one of
    them wearing the headset; Reachy's head following the headset): faces found in half the frames and lost again
    and again, things taken for faces, half the utterances short. Each trouble alone, then all together."""
    pairs = voices.similar_pairs()
    _, c, d = pairs[len(pairs) // 2]
    two = lambda **kw: [Seat(c, 25, **kw), Seat(d, -30, **kw)]   # noqa: E731
    return [
        Scenario("field: faces lost and found again every 3 s", two(), track_s=3.0),
        Scenario("field: a face comes twice now and then", two(), dup=0.15),
        Scenario("field: things taken for faces", two(), ghosts=0.2),
        Scenario("field: half the utterances short, a fifth under 1 s", two(), short=0.5, tiny=0.2),
        Scenario("field: the head looks around", two(), head="wanders"),
        Scenario("field: one face cannot be known (headset on)", [Seat(c, 25), Seat(d, -30, look=0.0)], track_s=3.0),
        Scenario("field: all of it, as on 2026-11-23",
                 [Seat(c, 25, found=0.5), Seat(d, -30, found=0.5, look=0.0)],
                 head="wanders", track_s=3.0, dup=0.1, ghosts=0.2, short=0.5, tiny=0.2),
    ]
