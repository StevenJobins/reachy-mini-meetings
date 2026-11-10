"""Where the people in the room are, and which voice belongs to whom. Pure logic, no camera, no model.

Bubbles next to the speaker need "who talks" as a place. Per utterance that failed on the robot (2026-11-06: mouth
movement too weak to pick a face, mic direction the same for everyone). The voice works (speakers.py), so here the
voice is tied to a person over several utterances:

  people   faces from vision.py become people by their direction in the room (head yaw + angle in the image):
           in a meeting people sit still, so a face that shows up again where someone was is that person
  votes    every final utterance of voice k is one vote for the person whose mouth moved most meanwhile, if one
           clearly did; a voice belongs to the person with enough votes and a clear lead
  where    the caption of voice k goes to that person, also when the mouth is not seen this time or the person is
           out of the picture (the direction in the room stays)

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
CONFIRM = 8           # sightings before a new direction counts as a person (a turning head smears directions)
CANDIDATE_S = 2.0     # a direction not confirmed within this long is dropped
MEMORY_S = 900.0      # forget people not seen for this long
HISTORY_S = 30.0      # mouth readings kept, to judge a whole utterance
MIN_SAMPLES = 5       # fewer mouth readings in an utterance say nothing
TALK_STD = 0.02       # mouth movement (standard deviation) above this may be talking
LEAD = 1.5            # ... if it is this many times the next person's
MIN_VOTES = 2         # votes a person needs to own a voice
VOTE_LEAD = 2.0       # ... and this many times the next person's votes
MIN_S = 1.0           # shorter utterances do not vote


def image_to_angles(u: float, v: float) -> tuple[float, float]:
    """Normalized image point (0..1, origin top left) to (yaw, pitch) relative to the camera axis."""
    import cv2

    pt = np.array([[[u * WIDTH, v * HEIGHT]]], np.float64)
    x, y = cv2.undistortPoints(pt, np.array([[FX, 0, CX], [0, FY, CY], [0, 0, 1]]), DIST)[0, 0]
    return -math.degrees(math.atan2(x, 1)), -math.degrees(math.atan2(y, math.hypot(x, 1)))


def angle_diff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180


class Person:
    def __init__(self, pid: int, yaw: float, t: float) -> None:
        self.pid, self.yaw, self.pitch, self.top, self.h = pid, yaw, 0.0, 0.0, 0.0
        self.first, self.seen, self.sightings = t, t, 0
        self.mouth: deque[tuple[float, float]] = deque()

    def activity(self, t0: float, t1: float) -> float | None:
        """Mouth movement over [t0, t1]: standard deviation of the opening; None = not seen enough to say."""
        m = [v for t, v in self.mouth if t0 <= t <= t1]
        return float(np.std(m)) if len(m) >= MIN_SAMPLES else None


class People:
    def __init__(self, angles=image_to_angles) -> None:
        self.angles = angles
        self.people: list[Person] = []
        self.candidates: list[Person] = []
        self.votes: dict[int, dict[int, float]] = {}   # voice -> person -> votes
        self.next_pid = 1

    def update(self, faces: list[dict], t: float, head_yaw_deg: float, key: str = "mouth") -> None:
        """Faces of one camera frame (vision.py), taken at wall clock t with the head at head_yaw_deg."""
        self.people = [p for p in self.people if t - p.seen < MEMORY_S]
        self.candidates = [p for p in self.candidates if t - p.first < CANDIDATE_S]
        taken: set[int] = set()
        for f in faces:
            yaw, pitch = self.angles(f["cx"], f["cy"])
            _, top = self.angles(f["cx"], f["top"])
            _, bottom = self.angles(f["cx"], f["top"] + f["h"])
            yaw += head_yaw_deg
            near = [p for p in self.people + self.candidates
                    if id(p) not in taken and abs(angle_diff(p.yaw, yaw)) < REID_DEG]
            p = min(near, key=lambda q: abs(angle_diff(q.yaw, yaw)), default=None)
            if p is None:
                p = Person(0, yaw, t)
                p.pitch, p.top, p.h = pitch, top, top - bottom
                self.candidates.append(p)
            taken.add(id(p))
            p.yaw += 0.2 * angle_diff(yaw, p.yaw)
            p.pitch += 0.2 * (pitch - p.pitch)
            p.top += 0.2 * (top - p.top)
            p.h += 0.2 * (top - bottom - p.h)
            p.seen = t
            p.sightings += 1
            if f.get(key) is not None:
                p.mouth.append((t, f[key]))
                while p.mouth[0][0] < t - HISTORY_S:
                    p.mouth.popleft()
            if p.pid == 0 and p.sightings >= CONFIRM:
                p.pid, self.next_pid = self.next_pid, self.next_pid + 1
                self.candidates.remove(p)
                self.people.append(p)

    def talker(self, t0: float, t1: float) -> Person | None:
        """The person whose mouth clearly moved most during [t0, t1], None if nobody stands out."""
        scores = sorted(((a, p) for p in self.people if (a := p.activity(t0, t1)) is not None),
                        key=lambda s: -s[0])
        if not scores or scores[0][0] < TALK_STD:
            return None
        if len(scores) > 1 and scores[0][0] < LEAD * scores[1][0]:
            return None
        return scores[0][1]

    def heard(self, voice: int | None, t0: float, t1: float) -> Person | None:
        """A final utterance of `voice` during [t0, t1]: counts as a vote; returns whom the caption belongs to."""
        seen = self.talker(t0, t1) if t1 - t0 >= MIN_S else None
        if voice is None:
            return seen
        if seen:
            v = self.votes.setdefault(voice, {})
            v[seen.pid] = v.get(seen.pid, 0) + 1
        return self.owner(voice) or seen

    def owner(self, voice: int | None) -> Person | None:
        """The person this voice belongs to, once that is clear."""
        v = sorted(self.votes.get(voice, {}).items(), key=lambda kv: -kv[1])
        if not v or v[0][1] < MIN_VOTES or (len(v) > 1 and v[0][1] < VOTE_LEAD * v[1][1]):
            return None
        return next((p for p in self.people if p.pid == v[0][0]), None)

    @staticmethod
    def place(p: Person | None, now: float) -> dict | None:
        """For the caption: where the person's head is (top = top of the face), and how long ago it was seen."""
        if p is None:
            return None
        return {"person": p.pid, "yaw_deg": round(p.yaw, 1), "pitch_deg": round(p.pitch, 1),
                "top_deg": round(p.top, 1), "h_deg": round(p.h, 1), "seen_s": round(max(0.0, now - p.seen), 1)}
