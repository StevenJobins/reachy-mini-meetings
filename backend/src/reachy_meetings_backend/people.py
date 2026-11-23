"""Who the people in the room are, where they are, and which voice belongs to whom. Pure logic, no camera, no model.

Bubbles next to the speaker need "who talks" as a place. Per utterance that failed on the robot (2026-11-06: mouth
movement too weak to pick a face, mic direction the same for everyone). The voice works (speakers.py), so here the
voice is tied to a person over several utterances:

  people   faces from vision.py become people. A face is a known person if (1) it is the face vision.py has
           followed without a break ("track"), else (2) it looks like them (the face embedding "emb", FaceId in
           vision.py), else (3) it shows up where they were (head yaw + angle in the image) and does not look
           clearly different. Until 2026-11-18 only (3) was used, and while Reachy's head turned one face became
           up to five people (the head angle is read 5-20 times a second and lags the picture), which split the
           votes below. Two people found to have the same face are merged, unless they were seen side by side.
  votes    every final utterance of voice k is one vote for the person whose mouth moved most meanwhile, if one
           clearly did (fusion.py casts them); a voice belongs to the person with enough votes and a clear lead
  where    fusion.py weighs this together with the mouths and the mic direction: the caption of voice k can go to its
           owner also when the mouth is not seen this time or the person is out of the picture

Copies of one person (headset test 2026-11-23, backend log: two people became seven, three of them at the same
place at the end, and their mouth votes were spread over the copies so no voice ever got an owner):
  - a second detection of a face in the same frame (boxes overlapping) is that face, not somebody next to them;
    only faces whose boxes are clearly apart are "seen side by side" (which forbids merging them for good)
  - two people at the same place, seen in turns within seconds and never side by side, are one
  - a face does not jump to someone who was just seen somewhere else because it looks like them
  - someone seen only briefly (a hand taken for a face) is forgotten soon, and the headset is only told of people
    seen lately (`shown`): before, a tag stayed at an empty place for up to 15 minutes

Angles in degrees, yaw + = left, pitch + = up, in the robot base frame. The thresholds are first guesses, to be set
from recordings (scripts/record_meeting.py).
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

# Calibration of the real camera, 2026-10-08 (xr-client/pages/camera.json), for 1920x1080
FX, FY, CX, CY = 992.076281828573, 992.8403520277743, 946.0789017717469, 548.5234281247938
DIST = np.array([-0.05038947568459324, 0.08391509940058939, 0.0018669674758364737, 0.0008246953938521169,
                 -0.04830911465110975])
WIDTH, HEIGHT = 1920, 1080

REID_DEG = 12.0       # a face within this of a known person's direction is that person
# Face embeddings (cosine), from scripts/eval_faces.py on 1000 LFW pairs at 18-60 px between the eyes, 2026-11-18:
# two people 0.10 in the middle, 0.34 at the 99th percentile, 0.45 at most; the same person 0.70 in the middle,
# 0.46-0.49 at the 5th percentile (0.33 in dim light). Those are single photos years apart; here means of several
# looks in one meeting are compared, which lie further apart.
FACE_SAME = 0.42      # at least this alike: the same person, wherever they are now
FACE_OTHER = 0.15     # at most this alike: someone else, even at the same place
FACE_MEAN_OF = 30     # a person's face is the mean of about this many looks
CONFIRM = 8           # sightings before a new direction counts as a person (a turning head smears directions)
MERGE_DEG = 20.0      # a new direction this close to a known person who is not in the picture is that person: the
                      # head yaw lags while Reachy turns (2026-11-11: one person became two, 8 degrees apart)
CANDIDATE_S = 4.0     # a direction not confirmed within this long is dropped (2 s until 2026-11-16: a small face
                      # far away is found about 3 times a second, so it never got its 8 sightings)
MEMORY_S = 900.0      # forget people not seen for this long
HISTORY_S = 30.0      # mouth readings kept, to judge a whole utterance
MIN_SAMPLES = 5       # fewer mouth readings in an utterance say nothing
TALK_STD = 0.02       # mouth movement (standard deviation) above this may be talking
LEAD = 1.5            # ... if it is this many times the next person's
MIN_VOTES = 1.5       # votes a person needs to own a voice (two in a row = 1.7 with the fading)
VOTE_LEAD = 2.0       # ... and this many times the next person's votes
SECOND_VOTES = 2.5    # ... and this many for a second voice of the same person (four in a row)
AGAINST, AGAINST_MAX = 0.5, 3.0   # seen still while the voice talks: this much off, down to this
MIN_S = 1.0           # shorter utterances do not vote
FADE = 0.7            # older votes of a voice count this much less with every new one: people change seats, and a
                      # face on a phone held up once must not own the voice for good (2026-11-11: 3 old votes
                      # kept the bubble at an empty place against the speaker in view)
VIEW_DEG = 30.0       # the camera sees this far to each side for sure
GONE_S = 3.0          # looked at their place this long without seeing them: they are not there any more
SAME_DEG = 6.0        # two people this close, seen in turns within SAME_S and never side by side: one person
SAME_S = 3.0
ELSEWHERE_DEG = 30.0  # seen this far away within ELSEWHERE_S: a face here is not them, however alike it looks (the
ELSEWHERE_S = 1.0     # head angle lags by up to ~20 degrees while Reachy turns, so not less)
TAG_S = 10.0          # the headset shows a tag for people seen within this long
FLEETING_N = 100      # seen in fewer frames than this in all (5 s at 20 a second): maybe not a person at all (a
                      # hand, a poster: "it knows my hand, and my face twice", 2026-11-23), so
FLEETING_TAG_S = 2.0  # ... the tag goes sooner
FLEETING_S = 20.0     # ... and they are forgotten after this long
MOVED = 0.05          # mouth opening changed by this much within a few readings: it moved (talkers' standard
                      # deviation is 0.04-0.19, listeners' 0.0002-0.005)
LIVE_SAMPLES = 3      # mouth readings needed to say who talks right now (the web app's rule, speakers.js)


def image_to_angles(u: float, v: float) -> tuple[float, float]:
    """Normalized image point (0..1, origin top left) to (yaw, pitch) relative to the camera axis."""
    import cv2

    pt = np.array([[[u * WIDTH, v * HEIGHT]]], np.float64)
    x, y = cv2.undistortPoints(pt, np.array([[FX, 0, CX], [0, FY, CY], [0, 0, 1]]), DIST)[0, 0]
    return -math.degrees(math.atan2(x, 1)), -math.degrees(math.atan2(y, math.hypot(x, 1)))


def to_base(yaw: float, pitch: float, head: tuple[float, float, float]) -> tuple[float, float]:
    """A direction relative to the camera axis to the robot base frame. head = (roll, pitch, yaw) of the head as
    the daemon gives it, in degrees (R = Rz(yaw) Ry(pitch) Rx(roll), pitch + = look down); the camera looks
    along the head's x axis."""
    y, p = math.radians(yaw), math.radians(pitch)
    d = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])   # x forward, y left, z up
    r, q, w = (math.radians(a) for a in head)
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    ry = np.array([[math.cos(q), 0, math.sin(q)], [0, 1, 0], [-math.sin(q), 0, math.cos(q)]])
    rz = np.array([[math.cos(w), -math.sin(w), 0], [math.sin(w), math.cos(w), 0], [0, 0, 1]])
    x, yy, z = rz @ ry @ rx @ d
    return math.degrees(math.atan2(yy, x)), math.degrees(math.atan2(z, math.hypot(x, yy)))


def angle_diff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180


def overlap(f: dict, g: dict) -> bool:
    """Two detections in one frame whose boxes overlap: the same face found twice, or two people too close in the
    picture to call them two."""
    return (abs(f["cx"] - g["cx"]) < (f["w"] + g["w"]) / 2
            and abs(f["cy"] - g["cy"]) < (f.get("h", f["w"]) + g.get("h", g["w"])) / 2)


def alike(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    """How alike two faces are (cosine of their embeddings); None if one is not known."""
    return None if a is None or b is None else float(a @ b)


class Person:
    def __init__(self, pid: int, yaw: float, t: float) -> None:
        self.pid, self.yaw, self.pitch, self.top, self.h = pid, yaw, 0.0, 0.0, 0.0
        self.first, self.seen, self.sightings = t, t, 0
        self.mouth: deque[tuple[float, float]] = deque()
        self.face: np.ndarray | None = None   # what they look like (unit vector), None = not seen well enough yet
        self.looks = 0
        self.last_emb = None                  # the track embedding learnt from last (it changes ~2.5 times a second)

    def learn_face(self, emb: np.ndarray | None) -> None:
        if emb is None or emb is self.last_emb:
            return
        self.last_emb = emb
        n = min(self.looks, FACE_MEAN_OF - 1)
        m = emb if self.face is None else (self.face * n + emb) / (n + 1)
        self.face, self.looks = m / np.linalg.norm(m), self.looks + 1

    @property
    def fleeting(self) -> bool:
        """Seen only briefly so far: perhaps not a person."""
        return self.sightings < FLEETING_N

    def activity(self, t0: float, t1: float, need: int = MIN_SAMPLES) -> float | None:
        """Mouth movement over [t0, t1]: standard deviation of the opening; None = not seen enough to say."""
        m = [v for t, v in self.mouth if t0 <= t <= t1]
        return float(np.std(m)) if len(m) >= need else None

    def moved(self, t0: float, t1: float) -> bool:
        """Seen too rarely over [t0, t1] to judge the mouth, but what was seen of it moved: it may have been them."""
        m = [v for t, v in self.mouth if t0 <= t <= t1]
        return 2 <= len(m) < MIN_SAMPLES and max(m) - min(m) >= MOVED


class People:
    def __init__(self, angles=image_to_angles) -> None:
        self.angles = angles
        self.people: list[Person] = []
        self.candidates: list[Person] = []
        self.votes: dict[int, dict[int, float]] = {}   # voice -> person -> votes
        self.owners: dict[int, int] = {}               # voice -> person it belongs to (see vote)
        self.tracks: dict[int, Person] = {}            # vision.py's track -> who it was last
        self.apart: set[frozenset[int]] = set()        # pairs of people seen in one frame: never the same person
        self.merged: list[tuple[int, int]] = []        # (person that turned out to be, this person), for roster.py
        self.next_pid = 1

    def _who(self, f: dict, yaw: float, taken: set[int], t: float = 0.0) -> Person | None:
        """The known person (or candidate) this face is, None = nobody known."""
        free = [p for p in self.people + self.candidates if id(p) not in taken]
        emb = f.get("emb")
        p = self.tracks.get(f.get("track"))
        if p is not None and any(p is q for q in free) and (alike(emb, p.face) or 1.0) > FACE_OTHER:
            return p                                       # followed without a break
        # by the face: not someone who was seen elsewhere a moment ago (in a dark picture, or with the eyes
        # covered, faces look alike that are not: people's places jumped by 60 degrees, 2026-11-23)
        looks = [(a, q) for q in free if (a := alike(emb, q.face)) is not None
                 and not (t - q.seen < ELSEWHERE_S and abs(angle_diff(q.yaw, yaw)) > ELSEWHERE_DEG)]
        a, p = max(looks, key=lambda x: x[0], default=(0.0, None))
        if p is not None and a >= FACE_SAME:
            return p                                       # looks like them, wherever they are now
        near = [q for q in free if abs(angle_diff(q.yaw, yaw)) < REID_DEG and (alike(emb, q.face) or 1.0) > FACE_OTHER]
        return min(near, key=lambda q: abs(angle_diff(q.yaw, yaw)), default=None)

    def update(self, faces: list[dict], t: float, head_yaw_deg: float, head_pitch_deg: float = 0.0,
               head_roll_deg: float = 0.0, key: str = "mouth", steady: bool = True) -> None:
        """Faces of one camera frame (vision.py), taken at wall clock t with the head at these angles.
        steady=False: the head is turning, the angles lag the picture; known people keep their place, their mouths
        are still read, and nobody new is taken in."""
        head = (head_roll_deg, head_pitch_deg, head_yaw_deg)
        gone = [p for p in self.people if t - p.seen >= (FLEETING_S if p.fleeting else MEMORY_S)]
        if gone:
            self.people = [p for p in self.people if not any(p is g for g in gone)]
            self._forget(gone)
        self.candidates = [p for p in self.candidates if t - p.first < CANDIDATE_S]
        taken: set[int] = set()
        boxes: list[tuple[dict, Person]] = []   # the faces of this frame and who they are
        for f in faces:
            twin = next((q for g, q in boxes if overlap(f, g)), None)
            if twin is not None:   # the same face found a second time: not somebody new next to them
                if f.get("track") is not None:
                    self.tracks[f["track"]] = twin
                continue
            yaw, pitch = to_base(*self.angles(f["cx"], f["cy"]), head)
            _, top = to_base(*self.angles(f["cx"], f["top"]), head)
            _, bottom = to_base(*self.angles(f["cx"], f["top"] + f["h"]), head)
            p = self._who(f, yaw, taken, t)
            if p is None:
                if not steady:
                    continue
                p = Person(0, yaw, t)
                p.pitch, p.top, p.h = pitch, top, top - bottom
                self.candidates.append(p)
            taken.add(id(p))
            boxes.append((f, p))
            if f.get("track") is not None:
                self.tracks[f["track"]] = p
            if steady:
                p.yaw += 0.2 * angle_diff(yaw, p.yaw)
                p.pitch += 0.2 * (pitch - p.pitch)
                p.top += 0.2 * (top - p.top)
                p.h += 0.2 * (top - bottom - p.h)
            p.learn_face(f.get("emb"))
            p.seen = t
            p.sightings += 1
            if f.get(key) is not None:
                p.mouth.append((t, f[key]))
                while p.mouth[0][0] < t - HISTORY_S:
                    p.mouth.popleft()
        here = [p.pid for p in self.people if p.seen == t]   # (boxes that overlap never got two people above)
        self.apart.update(frozenset((a, b)) for i, a in enumerate(here) for b in here[i + 1:])
        # After all faces of the frame have found their person: a known person seen in this very frame cannot be
        # the new one (decided inside the loop before, a second face under 20 degrees away could be folded into
        # the first, depending on the order of the faces).
        for p in [c for c in self.candidates if c.seen == t and c.sightings >= CONFIRM]:
            self.candidates.remove(p)
            known = self._known(p, [q for q in self.people if q.seen < t])
            if known:   # the same person, seen at another direction (they moved, or the head yaw lagged)
                self._fold(known, p)
                continue
            p.pid, self.next_pid = self.next_pid, self.next_pid + 1
            self.people.append(p)
            self.apart.update(frozenset((p.pid, q)) for q in here)
        self._merge_same_faces(t)
        if len(self.tracks) > 200:
            alive = {id(p) for p in self.people + self.candidates}
            self.tracks = {k: p for k, p in self.tracks.items() if id(p) in alive}

    @staticmethod
    def _known(p: Person, others: list[Person]) -> Person | None:
        """The one of `others` (people not in this frame) that the new face `p` is."""
        looks = [(a, q) for q in others if (a := alike(p.face, q.face)) is not None]
        a, q = max(looks, key=lambda x: x[0], default=(0.0, None))
        if q is not None and a >= FACE_SAME:
            return q
        near = [q for q in others if abs(angle_diff(q.yaw, p.yaw)) < MERGE_DEG and (alike(p.face, q.face) or 1.0) > FACE_OTHER]
        return min(near, key=lambda q: abs(angle_diff(q.yaw, p.yaw)), default=None)

    def _fold(self, keep: Person, p: Person) -> None:
        """`p` is `keep`, seen later: place and mouth readings go over."""
        if p.seen >= keep.seen:
            keep.yaw, keep.pitch, keep.top, keep.h, keep.seen = p.yaw, p.pitch, p.top, p.h, p.seen
        keep.mouth = deque(sorted([*keep.mouth, *p.mouth]))
        keep.sightings += p.sightings
        keep.learn_face(p.face)
        for track, q in list(self.tracks.items()):
            if q is p:
                self.tracks[track] = keep

    def _merge(self, a: Person, b: Person) -> Person:
        """`a` and `b` are one person: the one known longer stays, with everything known of the other."""
        keep, gone = (a, b) if a.pid < b.pid else (b, a)
        self._fold(keep, gone)
        self.people.remove(gone)
        for votes in self.votes.values():
            if gone.pid in votes:
                votes[keep.pid] = votes.get(keep.pid, 0) + votes.pop(gone.pid)
        self.owners = {v: keep.pid if pid == gone.pid else pid for v, pid in self.owners.items()}
        self.apart = {frozenset(keep.pid if x == gone.pid else x for x in pair) for pair in self.apart}
        self.apart = {pair for pair in self.apart if len(pair) == 2}
        self.merged.append((gone.pid, keep.pid))
        return keep

    def _merge_same_faces(self, t: float) -> None:
        """One person taken in twice becomes one again: two people with the same face, or a person whose face is
        known, seen now where someone sat whose face never was (taken in before the first good look at them).
        Never two people who were seen side by side."""
        for i, a in enumerate(self.people):
            for b in self.people[i + 1:]:
                if frozenset((a.pid, b.pid)) in self.apart:
                    continue
                same = (alike(a.face, b.face) or 0.0) >= FACE_SAME
                # at the same place in turns, within seconds, never side by side: one person found as two (a face
                # lost and found again before it could be known by its looks)
                same = same or (abs(angle_diff(a.yaw, b.yaw)) < SAME_DEG and t - a.seen < SAME_S and t - b.seen < SAME_S)
                for here, ghost in ((a, b), (b, a)):   # `here` is seen now, by their face, at the ghost's place
                    same = same or (here.seen == t and here.face is not None and ghost.face is None and ghost.seen < t
                                    and abs(angle_diff(here.yaw, ghost.yaw)) < REID_DEG)
                if same:
                    self._merge(a, b)
                    return self._merge_same_faces(t)

    def _forget(self, gone: list[Person]) -> None:
        pids = {p.pid for p in gone}
        for votes in self.votes.values():
            for pid in pids:
                votes.pop(pid, None)
        self.owners = {v: pid for v, pid in self.owners.items() if pid not in pids}
        self.apart = {pair for pair in self.apart if not pair & pids}

    def shown(self, now: float, head_yaw_deg: float | None = None) -> list[Person]:
        """The people the headset gets a name tag for: seen lately, and not missing from where the camera looks.
        Everyone else is still known (their voice, their face) and comes back with the same label."""
        return [p for p in self.people
                if now - p.seen <= (FLEETING_TAG_S if p.fleeting else TAG_S)
                and self.place(p, now, head_yaw_deg) is not None]

    def liveliest(self, t0: float, t1: float) -> Person | None:
        """Who talks right now, for the live text and the mark on the tag: the mouth that moved most over [t0, t1],
        if it moved like talking. No "clearly more than the others" and no waiting for the end of the sentence,
        as in the web app (speakers.js `pick`), which got this right in the headset; the final decision (fusion.py)
        may correct it."""
        scores = [(a, p) for p in self.people if (a := p.activity(t0, t1, LIVE_SAMPLES)) is not None and a >= TALK_STD]
        return max(scores, key=lambda s: s[0], default=(0.0, None))[1]

    def talker(self, t0: float, t1: float) -> Person | None:
        """The person whose mouth clearly moved most during [t0, t1], None if nobody stands out."""
        scores = sorted(((a, p) for p in self.people if (a := p.activity(t0, t1)) is not None),
                        key=lambda s: -s[0])
        if not scores or scores[0][0] < TALK_STD:
            return None
        # Someone else was in the picture meanwhile, too rarely to judge their mouth, and what was seen of it
        # moved: it may have been them. Without this the one face that is seen well won every utterance (headset
        # test 2026-11-16: two faces, every bubble at the same one). Until 2026-11-23 anyone seen at all since the
        # start blocked, also with a still mouth or seen once: with copies of people around, a mouth clearly talked
        # in 11 utterances and a person was chosen in 4.
        if any(p.moved(t0, t1) for p in self.people):
            return None
        if len(scores) > 1 and scores[0][0] < LEAD * scores[1][0]:
            return None
        return scores[0][1]

    def vote(self, voice: int, person: Person) -> None:
        """`person` was seen saying an utterance of `voice` (older votes of the voice fade)."""
        v = self.votes.setdefault(voice, {})
        for pid in v:
            v[pid] *= FADE
        v[person.pid] = v.get(person.pid, 0) + 1
        self._settle(voice)

    def vote_against(self, voice: int, person: Person) -> None:
        """`person` was in the picture with a still mouth while `voice` talked: it is not theirs. Without this, a
        listener who laughs or nods now and then while someone outside the picture talks collected that voice
        (tests/sim.py, "one in view, one never in the picture": 34 % of the labels wrong)."""
        v = self.votes.setdefault(voice, {})
        v[person.pid] = max(-AGAINST_MAX, v.get(person.pid, 0) - AGAINST)
        self._settle(voice)

    def _settle(self, voice: int) -> None:
        lead = self._leader(voice)
        if lead is not None:   # else it stays with whoever it was: a name that comes and goes is worse than a late one
            self.owners[voice] = lead
        elif self.votes[voice].get(self.owners.get(voice), 0) <= 0:
            self.owners.pop(voice, None)   # ... unless nothing speaks for them any more

    def _leader(self, voice: int) -> int | None:
        """The person with enough votes for this voice and a clear lead, if any."""
        v = sorted(self.votes.get(voice, {}).items(), key=lambda kv: -kv[1])
        if not v or v[0][1] < MIN_VOTES or (len(v) > 1 and v[0][1] < VOTE_LEAD * v[1][1]):
            return None
        pid, n = v[0]
        # A second voice for someone who has one needs more: two sentences of someone else's voice can land on a
        # person by mistake, but a person can also sound like two voices (near and far, laughing).
        if n < SECOND_VOTES and any(other != voice and self.owners.get(other) == pid and w.get(pid, 0) > n
                                    for other, w in self.votes.items()):
            return None
        return pid

    def voices_of(self, person: Person) -> list[int]:
        """The voices this person owns."""
        return [v for v, pid in self.owners.items() if pid == person.pid]

    def voice_of(self, person: Person) -> int | None:
        """The voice this person owns (the one with the most votes), if any."""
        return max(self.voices_of(person), key=lambda v: self.votes[v].get(person.pid, 0), default=None)

    def owner(self, voice: int | None) -> Person | None:
        """The person this voice belongs to, once that is clear."""
        pid = self.owners.get(voice)
        return next((p for p in self.people if p.pid == pid), None)

    @staticmethod
    def place(p: Person | None, now: float, head_yaw_deg: float | None = None) -> dict | None:
        """For the caption: where the person's head is (top = top of the face), and how long ago it was seen.
        None when the camera looks at their place and they are not there."""
        if p is None:
            return None
        if head_yaw_deg is not None and now - p.seen > GONE_S and abs(angle_diff(p.yaw, head_yaw_deg)) < VIEW_DEG:
            return None
        return {"person": p.pid, "yaw_deg": round(p.yaw, 1), "pitch_deg": round(p.pitch, 1),
                "top_deg": round(p.top, 1), "h_deg": round(p.h, 1), "seen_s": round(max(0.0, now - p.seen), 1)}
