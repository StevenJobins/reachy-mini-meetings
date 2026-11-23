"""Who said it: the voice, the mouths in the picture and the mic direction, weighed together per utterance.

Each cue alone failed in a real meeting: the voice merged two people into one speaker (2026-11-18), the mouth is
only seen for people in the picture, and the mic direction sat at 40-53 degrees for everyone (2026-11-06). So no
cue decides alone, and each counts only as far as it has proved itself in this meeting:

  mouth   per person in the room (people.py): clearly moving most during the utterance +2, moving but not clearly
          most +1, seen with a still mouth -1, in the picture but too rarely to judge +1 ("it may have been them")
  voice   the voice (speakers.py) belongs to a person through earlier mouth votes: that person +1.5, someone who
          owns another voice -1
  doa     the mic direction, only once it has been calibrated in this meeting: utterances where the mouth alone
          showed the talker give the error (mic direction - face direction); with samples from at least two people
          apart and a small spread, the mic counts +-1.5 by how far a person is from where it points (after the
          learnt offset, so a mic relative to the body or a fixed bias still works). A mic that points the same way
          for everyone never gets calibrated and never counts.

The caption goes to the best person if their score is at least ACCEPT and MARGIN ahead of the next; else no
place (subtitle panel): a bubble at the wrong face is worse than none.

Changed after the headset test of 2026-11-23 (backend log: 22 of 33 utterances went to the one known voice, which
never got an owner; a mouth clearly talked in 11 utterances and a person was chosen in 4):
  - someone seen too rarely to judge counts as "it may have been them" only if what was seen of their mouth moved
    (before: anyone seen at all, which with copies of people around blocked nearly every decision)
  - a doubtful match is no longer given to the closest voice when that is the only voice known (then everything
    said by anyone new goes to it: a 2.8 s sentence of a second speaker matched at 0.46), or when the next voice
    is nearly as close: no name is better than the wrong one, and the mouth can still say who it was. Such an
    utterance casts no mouth vote either: the four votes voice 1 got that day came from doubtful matches (0.42-0.52),
    for four different people. (Votes only for sure voices was tried and dropped: a voice founded from one sentence
    rarely matches clearly again, so it never got an owner; tests/sim.py.)

The voice id itself (similarities are to each voice's mean; the mic direction the voice usually comes from moves
them a little, see _similarities):
  - the person the mouth and the mic point at owns a voice and it might be this one (>= LOW): theirs, learnt
  - that person has no voice yet and the closest voice is someone else's: a new voice
  - a clear match (>= HIGH, and not heard from clearly elsewhere than that voice comes from): that voice, learnt
  - unlike every voice (< NEW_BELOW) and long enough: a new voice
  - else doubtful: the closest voice above the threshold, not learnt (so one voice cannot drift over everyone),
    or no voice. Doubtful utterances are kept: when a second one sounds like an earlier one, and both less like
    any known voice, the two are a new voice (`relabel` names the earlier one). So a new voice needs either to
    be clearly new or to be heard twice: one odd-sounding sentence of a known speaker founds no "Speaker C", and
    a speaker who sounds like someone else is still found by their second sentence.
Votes come only from the mouth (people.talker), never from the fused answer, so the voice cannot confirm itself.

Pure logic, no models. The voice thresholds are read from scripts/eval_voices.py and checked in
tests/test_scenarios.py (simulated meetings with real voices); the mouth and mic numbers are from the robot's logs.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .people import LEAD, MIN_S, TALK_STD, People, Person, angle_diff  # noqa: F401
from .speakers import NEW_S, SpeakerClusters, VoiceObs

TALK_W, MOVE_W, QUIET_W, UNJUDGED_W = 2.0, 1.0, -1.0, 1.0   # unjudged: seen rarely, and the mouth moved (people.moved)
QUIET_STD = 0.008     # mouth movement under this while someone talks: not them (2026-11-18: listeners 0.0002-0.005,
                      # talkers 0.04-0.19)
OWNER_W, OTHER_OWNER_W = 1.5, -1.0
NOT_THEIRS_W = -2.0   # the voice is clearly a known one and the person owns another: a moving mouth is not talking
DOA_W = 1.5
ACCEPT, MARGIN = 1.0, 1.5
# Voice similarity (cosine to a voice's mean; CAM++, speakers.py). From scripts/eval_voices.py, 40 speakers as a far
# microphone hears them, 3 s clips: the same speaker 0.68 in the middle, 0.52 at the 5th percentile; other speakers
# 0.34 at the 95th; the most similar other speaker 0.40 in the middle, 0.51 at the 95th. At 1.5 s everything is
# 0.15 lower and the ranges overlap; at 6 s the same speaker is 0.65 at the 5th percentile.
HIGH = 0.60           # clearly that voice: it learns from the utterance
SURE = 0.70           # ... and so clearly that where the mic heard it from does not matter
LOW = 0.30            # might be that voice, if the camera or the mic says so too
NEW_BELOW = 0.25      # unlike every known voice: a new one at once
LONE = 0.50           # a doubtful match to the only known voice counts from here on (2026-11-23: the known speaker's
                      # short sentences 0.41-0.52, another speaker's 0.15-0.46)
LEAD_VOICE = 0.15     # ... and with several voices known, if it is this much closer than the next one
# A new voice that is not that unlike the known ones (a similar voice: the most similar pair of the 40 speakers is
# 0.48 in the middle to the other's mean, 0.60 at the 95th percentile) is found by its second utterance: two
# doubtful utterances that are alike (two clips of one speaker: 0.60 in the middle, 0.45 at the 5th percentile; of
# the most similar pair: 0.40, 0.52 at the 95th) and both not much like any known voice.
PAIR, PAIR_LEAD, PAIR_BELOW = 0.45, 0.05, 0.45
PEND_S = 2.0          # ... each at least this long
PENDING, PENDING_S = 8, 300.0   # doubtful utterances kept for that, and how long
DIR_MIN = 3           # a voice's usual mic direction counts from this many sure utterances on
DIR_W = 0.10          # ... and moves the similarity by up to this much (the voices' directions compared)
DIR_SIGMA = 10.0      # the least spread assumed for a voice's direction (2026-11-18: 6.5 degrees for one speaker)
DIR_ELSEWHERE = 3.0   # this many spreads away (at least 30 degrees): heard from elsewhere than the voice comes from
DOA_SAMPLES = 30         # calibration samples kept (the latest)
DOA_MIN = 4              # samples needed ...
DOA_APART = 25.0         # ... from at least two people this far apart (degrees)
DOA_MAX_SIGMA = 20.0     # ... with a spread (robust standard deviation) under this
DOA_MIN_SIGMA = 8.0
DOA_STEADY = 30.0        # an utterance's mic direction counts only if its own spread is under this


@dataclass
class Decision:
    voice: int | None
    person: Person | None
    how: str = ""                                   # how the voice was decided, for the log
    scores: dict[int, tuple[float, str]] = field(default_factory=dict)   # pid -> (score, terms), for the log
    relabel: list[tuple[object, int]] = field(default_factory=list)      # (key of an earlier utterance, its voice now)

    def why(self) -> str:
        people = " | ".join(f"p{pid} {s:+.1f} ({t})" for pid, (s, t) in sorted(self.scores.items()))
        return f"voice {self.voice or '?'} ({self.how}); person {self.person.pid if self.person else '-'}; {people or 'nobody'}"


def circular_stats(angles: list[float]) -> tuple[float, float]:
    """(mean, robust spread) in degrees; spread from the median absolute deviation."""
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    mean = math.degrees(math.atan2(s, c))
    mad = float(np.median([abs(angle_diff(a, mean)) for a in angles]))
    return mean, 1.4826 * mad


class Fusion:
    def __init__(self, clusters: SpeakerClusters | None = None, people: People | None = None) -> None:
        self.clusters, self.people = clusters, people
        self.doa_samples: deque[tuple[float, int, float]] = deque(maxlen=DOA_SAMPLES)   # (error, pid, face yaw)
        self.voice_dirs: dict[int, deque[float]] = {}     # voice -> mic directions of its sure utterances
        self.pending: deque[tuple[object, np.ndarray, float, float]] = deque(maxlen=PENDING)   # (key, emb, t, best)

    # ------------------------------------------------------------------ mic direction
    def doa_model(self) -> tuple[float, float] | None:
        """(offset, sigma) of the mic direction against the faces, once it has proved itself; else None."""
        if len(self.doa_samples) < DOA_MIN:
            return None
        yaws = [y for _, _, y in self.doa_samples]
        if max(abs(angle_diff(a, b)) for a in yaws for b in yaws) < DOA_APART:
            return None   # all from one place: cannot tell a mic that points there anyway from one that works
        offset, sigma = circular_stats([e for e, _, _ in self.doa_samples])
        return (offset, max(sigma, DOA_MIN_SIGMA)) if sigma <= DOA_MAX_SIGMA else None

    def _doa_term(self, p: Person, doa: tuple[float, float] | None) -> float | None:
        model = self.doa_model()
        if model is None or doa is None or doa[1] > DOA_STEADY:
            return None
        offset, sigma = model
        d = angle_diff(doa[0] - offset, p.yaw)
        return DOA_W * (2 * math.exp(-0.5 * (d / sigma) ** 2) - 1)

    # ------------------------------------------------------------------ people
    def scores(self, t0: float, t1: float, voice: int | None, doa: tuple[float, float] | None,
               sure: bool = False) -> dict[int, tuple[float, str]]:
        """pid -> (score, terms). doa = (azimuth in the robot base frame, spread) of the utterance, or None;
        sure = the voice was a clear match."""
        if not self.people:
            return {}
        people = self.people.people
        judge = t1 - t0 >= MIN_S
        act = {p.pid: p.activity(t0, t1) for p in people} if judge else {}
        owner = self.people.owner(voice) if voice is not None else None
        out = {}
        for p in people:
            terms: list[tuple[str, float]] = []
            a = act.get(p.pid)
            if a is not None:
                others = [b for pid, b in act.items() if pid != p.pid and b is not None]
                if a >= TALK_STD:
                    terms.append(("talks", TALK_W) if all(a >= LEAD * b for b in others) else ("moves", MOVE_W))
                elif a < QUIET_STD:
                    terms.append(("still", QUIET_W))
            elif judge and p.moved(t0, t1):
                terms.append(("unjudged", UNJUDGED_W))
            if owner is not None and owner.pid == p.pid:
                terms.append(("voice", OWNER_W))
            elif voice is not None and (mine := self.people.voices_of(p)):
                # they have a voice and this is another one. Clearly this one, and unlike theirs: a mouth that
                # moves is not talking (they laugh, nod, chew). Like theirs: it may be them all the same.
                means = self.clusters.means if self.clusters else []
                unlike = sure and all(max(voice, m) <= len(means) and float(means[voice - 1] @ means[m - 1]) < LOW for m in mine)
                terms.append(("not their voice", NOT_THEIRS_W) if unlike else ("other voice", OTHER_OWNER_W))
            if (d := self._doa_term(p, doa)) is not None:
                terms.append(("mic", d))
            out[p.pid] = (sum(w for _, w in terms), " ".join(f"{n} {w:+.1f}" for n, w in terms))
        return out

    def pick(self, scores: dict[int, tuple[float, str]]) -> Person | None:
        ranked = sorted(((s, pid) for pid, (s, _) in scores.items()), reverse=True)
        if not ranked or ranked[0][0] < ACCEPT or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MARGIN):
            return None
        return next(p for p in self.people.people if p.pid == ranked[0][1])

    # ------------------------------------------------------------------ voice
    def _similarities(self, emb: np.ndarray, doa: tuple[float, float] | None) -> tuple[list[float], str]:
        """Similarity to every known voice, moved a little by where the mic heard the utterance: a voice that
        usually comes from there gains, one that comes from elsewhere loses. Voices that all come from the same
        direction (or a mic that points the same way for everyone) change nothing."""
        sims = self.clusters.similarities(emb)
        if doa is None or doa[1] > DOA_STEADY:
            return sims, ""
        fits = {}
        for voice, dirs in self.voice_dirs.items():
            if len(dirs) >= DIR_MIN and voice <= len(sims):
                mean, sigma = circular_stats(list(dirs))
                fits[voice] = math.exp(-0.5 * (angle_diff(doa[0], mean) / max(sigma, DIR_SIGMA)) ** 2)
        if len(fits) < 2:
            return sims, ""
        middle = sum(fits.values()) / len(fits)
        for voice, fit in fits.items():
            sims[voice - 1] += DIR_W * (fit - middle)
        return sims, ", mic " + " ".join(f"{v}:{DIR_W * (f - middle):+.2f}" for v, f in sorted(fits.items()))

    def _elsewhere(self, voice: int, doa: tuple[float, float] | None) -> bool:
        """The mic heard the utterance from clearly elsewhere than this voice usually comes from."""
        dirs = self.voice_dirs.get(voice, ())
        if doa is None or doa[1] > DOA_STEADY or len(dirs) < DIR_MIN:
            return False
        mean, sigma = circular_stats(list(dirs))
        return abs(angle_diff(doa[0], mean)) > DIR_ELSEWHERE * max(sigma, DIR_SIGMA)

    def _partner(self, emb: np.ndarray, best: float, now: float):
        """An earlier doubtful utterance that sounds like this one, more than either sounds like a known voice."""
        fits = [(float(e @ emb), i) for i, (_, e, t, b) in enumerate(self.pending)
                if now - t < PENDING_S and float(e @ emb) >= max(PAIR, best + PAIR_LEAD, b + PAIR_LEAD)]
        return max(fits, default=(0.0, None))[1]

    def _voice(self, obs: VoiceObs | None, hint: Person | None, doa: tuple[float, float] | None = None,
               key: object = None, now: float = 0.0) -> tuple[int | None, str, bool, list[tuple[object, int]]]:
        """(voice, how it was decided, sure, earlier utterances that are this voice too)."""
        c = self.clusters
        if obs is None or c is None:
            return None, "no voice", False, []
        emb = obs.emb / np.linalg.norm(obs.emb)
        can_new = obs.dur >= NEW_S
        if not c.means:
            return (c.create(emb), "first voice", True, []) if can_new else (None, "too short for a first voice", False, [])
        sims, mic = self._similarities(emb, doa)
        best = int(np.argmax(sims))
        sid, s = best + 1, sims[best]
        txt = f"{obs.dur:.1f} s, similarity " + " ".join(f"{x:.2f}" for x in sims) + mic + (
            f", windows {obs.windows}" + (" mixed" if obs.mixed else "") if obs.windows else "")
        mine = self.people.voices_of(hint) if hint and self.people else []
        owner = self.people.owner(sid) if self.people else None
        if mine:   # the person the mouth and the mic point at has a voice: is this it?
            h = max(mine, key=lambda v: sims[v - 1])
            if sims[h - 1] >= LOW:
                c.learn(h, emb)
                return h, f"the person seen/heard there owns it, {txt}", True, []
        if hint is not None and not mine and owner is not None and owner.pid != hint.pid and can_new:
            # someone seen talking who has no voice yet, and the closest voice is another person's: theirs is new
            return c.create(emb), f"new, voice {sid} is person {owner.pid}'s, person {hint.pid} talks, {txt}", True, []
        elsewhere = s < SURE and self._elsewhere(sid, doa)
        if s >= HIGH and not elsewhere:
            c.learn(sid, emb)
            return sid, f"clear, {txt}", True, []
        if s < NEW_BELOW and can_new:
            return c.create(emb), f"new, {txt}", True, []
        if elsewhere:
            txt += f", heard from elsewhere than voice {sid}"
        if obs.dur >= PEND_S and not obs.mixed and (s < PAIR_BELOW or elsewhere):
            i = self._partner(emb, 0.0 if elsewhere else s, now)
            if i is not None:   # the second utterance of a voice not known yet
                other_key, other, _, _ = self.pending[i]
                del self.pending[i]
                new = c.create(emb)
                if new > len(sims):   # (not when the voices are full and it went to the closest)
                    c.learn(new, other)
                    return new, f"new, like an earlier doubtful utterance, {txt}", True, [(other_key, new)]
                return new, f"doubtful, voices full, {txt}", False, []
            self.pending.append((key, emb, now, 0.0 if elsewhere else s))
        if s >= c.threshold and not elsewhere:
            rest = [x for i, x in enumerate(sims) if i != best]
            if (s >= LONE) if not rest else (s - max(rest) >= LEAD_VOICE):
                return sid, f"doubtful, not learnt, {txt}", False, []
            return None, f"doubtful, no name ({'the only voice known' if not rest else 'another voice is close'}), {txt}", False, []
        return None, f"not known, {txt}", False, []

    # ------------------------------------------------------------------ utterances
    def final(self, obs: VoiceObs | None, t0: float, t1: float, doa: tuple[float, float] | None,
              key: object = None) -> Decision:
        """A final utterance over [t0, t1] (wall clock): decides voice and person, and learns from it. `key` names
        the utterance (the caption id): if it later turns out to be a new voice, a Decision's `relabel` says so."""
        hint = self.pick(self.scores(t0, t1, None, doa))   # from the mouths and the mic, before the voice
        voice, how, sure, relabel = self._voice(obs, hint, doa, key, t1)
        if voice is not None and sure and doa is not None and doa[1] <= DOA_STEADY:
            self.voice_dirs.setdefault(voice, deque(maxlen=20)).append(doa[0])
        d = self.attribute(voice, t0, t1, doa, how, mixed=bool(obs and obs.mixed), sure=sure)
        d.relabel = relabel
        return d

    def attribute(self, voice: int | None, t0: float, t1: float, doa: tuple[float, float] | None = None,
                  how: str = "", mixed: bool = False, sure: bool = False) -> Decision:
        """The person who said a final utterance of `voice`; casts the mouth's votes and calibrates the mic."""
        scores = self.scores(t0, t1, voice, doa, sure)
        d = Decision(voice, self.pick(scores), how, scores)
        if not self.people or t1 - t0 < MIN_S:
            return d
        talker = self.people.talker(t0, t1)
        if voice is not None and not mixed:   # mixed: the mouth window holds two people
            if talker:
                self.people.vote(voice, talker)
            for p in self.people.people:
                if p is not talker and (a := p.activity(t0, t1)) is not None and a < QUIET_STD:
                    self.people.vote_against(voice, p)
        if talker and doa is not None and doa[1] <= DOA_STEADY:
            self.doa_samples.append((angle_diff(doa[0], talker.yaw), talker.pid, talker.yaw))
        return d

    def partial(self, obs: VoiceObs | None, t0: float | None = None, t1: float | None = None) -> Decision:
        """Live text: whoever's mouth moves most right now (the last 1.5 s, as the web app did), else the owner of a
        voice heard before; nothing is learnt."""
        voice = self.clusters.match(obs.emb)[0] if obs is not None and self.clusters is not None else None
        person = None
        if self.people:
            if t0 is not None and t1 is not None:
                person = self.people.liveliest(max(t0, t1 - 1.5), t1)
            person = person or (self.people.owner(voice) if voice else None)
        return Decision(voice, person)
