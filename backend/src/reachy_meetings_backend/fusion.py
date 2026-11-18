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

The voice id itself: a clear match (>= HIGH) is that voice; a doubtful one (LOW..HIGH) is the voice of the person
the mouth and the mic point at, if there is one and it is close enough; if that person has no voice yet and the
closest voice belongs to someone else, a new voice; else the closest above the threshold (not learnt from) or a
new voice. Only clear or confirmed utterances teach the voices, so one voice cannot drift
over everyone. Votes come only from the mouth (people.talker), never from the fused answer, so the voice cannot
confirm itself.

Pure logic, no models. Thresholds are first guesses from the 2026-11-18 log, to be set from real meetings.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .people import LEAD, MIN_S, TALK_STD, People, Person, angle_diff
from .speakers import NEW_S, SpeakerClusters, VoiceObs

TALK_W, MOVE_W, QUIET_W, UNJUDGED_W = 2.0, 1.0, -1.0, 1.0
QUIET_STD = 0.008     # mouth movement under this while someone talks: not them (2026-11-18: listeners 0.0002-0.005,
                      # talkers 0.04-0.19)
OWNER_W, OTHER_OWNER_W = 1.5, -1.0
DOA_W = 1.5
ACCEPT, MARGIN = 1.0, 1.5
HIGH, LOW = 0.88, 0.70   # voice similarity: clearly that voice / might be it (2026-11-18: the same voice 0.84-0.98,
                         # others 0.69-0.79)
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
    def scores(self, t0: float, t1: float, voice: int | None,
               doa: tuple[float, float] | None) -> dict[int, tuple[float, str]]:
        """pid -> (score, terms). doa = (azimuth in the robot base frame, spread) of the utterance, or None."""
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
            elif judge and t0 <= p.seen:
                terms.append(("unjudged", UNJUDGED_W))
            if owner is not None and owner.pid == p.pid:
                terms.append(("voice", OWNER_W))
            elif voice is not None and self.people.voice_of(p) not in (None, voice):
                terms.append(("other voice", OTHER_OWNER_W))
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
    def _voice(self, obs: VoiceObs | None, hint: Person | None) -> tuple[int | None, str]:
        c = self.clusters
        if obs is None or c is None:
            return None, "no voice"
        sims = c.similarities(obs.emb)
        can_new = obs.dur >= NEW_S
        if not sims:
            return (c.create(obs.emb), "first voice") if can_new else (None, "too short for a first voice")
        best = int(np.argmax(sims))
        sid, s = best + 1, sims[best]
        sim_txt = f"{obs.dur:.1f} s, similarity " + " ".join(f"{x:.2f}" for x in sims) + (
            f", windows {obs.windows}" + (" mixed" if obs.mixed else "") if obs.windows else "")
        if s >= HIGH:
            c.learn(sid, obs.emb)
            return sid, f"clear, {sim_txt}"
        hinted = self.people.voice_of(hint) if hint and self.people else None
        if hinted is not None and sims[hinted - 1] >= LOW:
            c.learn(hinted, obs.emb)
            return hinted, f"doubtful, the person seen/heard there owns it, {sim_txt}"
        if hint is not None and hinted is None and can_new and (o := self.people.owner(sid)) and o.pid != hint.pid:
            # the closest voice is someone else's, and the person seen/heard has none yet: theirs is new
            return c.create(obs.emb), f"new, voice {sid} is person {o.pid}'s, person {hint.pid} talks, {sim_txt}"
        if s >= c.threshold:
            return sid, f"doubtful, not learnt, {sim_txt}"
        if can_new:
            return c.create(obs.emb), f"new, {sim_txt}"
        return None, f"too short for a new voice, {sim_txt}"

    # ------------------------------------------------------------------ utterances
    def final(self, obs: VoiceObs | None, t0: float, t1: float, doa: tuple[float, float] | None) -> Decision:
        """A final utterance over [t0, t1] (wall clock): decides voice and person, and learns from it."""
        hint = self.pick(self.scores(t0, t1, None, doa))   # from the mouths and the mic, before the voice
        voice, how = self._voice(obs, hint)
        return self.attribute(voice, t0, t1, doa, how, mixed=bool(obs and obs.mixed))

    def attribute(self, voice: int | None, t0: float, t1: float, doa: tuple[float, float] | None = None,
                  how: str = "", mixed: bool = False) -> Decision:
        """The person who said a final utterance of `voice`; casts the mouth's vote and calibrates the mic."""
        scores = self.scores(t0, t1, voice, doa)
        d = Decision(voice, self.pick(scores), how, scores)
        if self.people and t1 - t0 >= MIN_S and (talker := self.people.talker(t0, t1)):
            if voice is not None and not mixed:   # mixed: the mouth window holds two people
                self.people.vote(voice, talker)
            if doa is not None and doa[1] <= DOA_STEADY:
                self.doa_samples.append((angle_diff(doa[0], talker.yaw), talker.pid, talker.yaw))
        return d

    def partial(self, obs: VoiceObs | None) -> Decision:
        """Live text: only a voice heard before, nothing learnt; its owner, if known."""
        if obs is None or self.clusters is None:
            return Decision(None, None)
        voice = self.clusters.match(obs.emb)[0]
        return Decision(voice, self.people.owner(voice) if self.people and voice else None)
