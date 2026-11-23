"""Whole meetings through "who said it" (tests/sim.py: real voices, simulated camera and mic). The floors are a little
under what scripts/eval_scenarios.py measured on 2026-11-18 (10 meetings each); a change that makes a situation
worse fails here.   pytest -q tests/test_scenarios.py"""

import pytest
import sim

VOICES = sim.Voices.load()
#                                                              right at least, wrong at most, faces right at least
FLOORS = {
    "two at the table, in view": (0.96, 0.02, 0.97),
    "two with similar voices, in view": (0.95, 0.03, 0.97),
    "two close together (8 degrees), in view": (0.95, 0.03, 0.97),
    "similar voices and close together": (0.94, 0.04, 0.97),
    "one in view, one never in the picture": (0.90, 0.06, 0.97),
    "nobody in the picture": (0.88, 0.05, None),
    "nobody in the picture, similar voices": (0.80, 0.15, None),
    "nobody in the picture, similar voices, mic the same for all": (0.80, 0.15, None),
    "three, Reachy's head follows the talker": (0.95, 0.03, 0.97),
    "three, head follows, similar voices": (0.94, 0.04, 0.97),
    "three, head follows, mic the same for all": (0.94, 0.04, 0.97),
    "far faces found now and then": (0.93, 0.04, 0.95),
    "four, two of them never in the picture": (0.85, 0.10, 0.85),
}


def test_every_scenario_has_a_floor():
    assert {s.name for s in sim.scenarios(VOICES)} == set(FLOORS)


@pytest.mark.parametrize("scn", sim.scenarios(VOICES), ids=lambda s: s.name)
def test_who_said_it(scn):
    r = sim.mean([sim.run(scn, VOICES, seed) for seed in range(4)])
    right, wrong, faces = FLOORS[scn.name]
    assert r.right >= right and r.wrong <= wrong, str(r)
    if faces is not None:
        assert r.faces_right >= faces, str(r)
    assert r.people <= sum(s.face for s in scn.seats), f"one person became several: {r}"


# What the headset test of 2026-11-23 was like (tests/sim.py field_scenarios). Measured with the code of that day
# (10 meetings): a face that comes twice now and then gave 4 tags for 2 people and faces right 0 %; things taken for
# faces left 5.6 tags for 2 people; all of it together: right 68 %, faces right 33 %, 8.4 tags for 1.4 in view.
# Floors a little under what the changes of 2026-11-23 reach (4 meetings).
#                                                                right ≥, wrong ≤, faces right ≥, tags ≤ (per person in view)
FIELD_FLOORS = {
    "field: faces lost and found again every 3 s": (0.97, 0.02, 0.97, 1.1),
    "field: a face comes twice now and then": (0.96, 0.02, 0.97, 1.1),
    "field: things taken for faces": (0.95, 0.02, 0.97, 1.4),
    "field: half the utterances short, a fifth under 1 s": (0.87, 0.02, 0.97, 1.1),
    "field: the head looks around": (0.96, 0.03, 0.97, 1.5),
    "field: one face cannot be known (headset on)": (0.96, 0.02, 0.97, 1.1),
    "field: all of it, as on 2026-11-23": (0.70, 0.10, 0.80, 2.2),
}


def test_every_field_scenario_has_a_floor():
    assert {s.name for s in sim.field_scenarios(VOICES)} == set(FIELD_FLOORS)


@pytest.mark.parametrize("scn", sim.field_scenarios(VOICES), ids=lambda s: s.name)
def test_who_said_it_in_the_field(scn):
    r = sim.mean([sim.run(scn, VOICES, seed) for seed in range(4)])
    right, wrong, faces, tags = FIELD_FLOORS[scn.name]
    assert r.right >= right and r.wrong <= wrong, str(r)
    assert r.faces_right >= faces, str(r)
    assert r.tags <= tags * max(r.in_view, 1.0), f"tags left over: {r}"


def test_a_still_jaw_and_a_smiling_listener_are_told_apart_by_the_lips():
    """The recording of 2026-11-23 (tests/sim.py recorded_scenarios): with jawOpen alone the tags went to the wrong
    face half the time (on the recording itself every sentence went to one person); with vision.py's lip gap, as the
    camera now gives it, they are right."""
    jaw, lips = sim.recorded_scenarios(VOICES)
    r = sim.mean([sim.run(lips, VOICES, seed) for seed in range(4)])
    assert r.right >= 0.95 and r.wrong <= 0.02 and r.faces_right >= 0.95, str(r)
    old = sim.mean([sim.run(jaw, VOICES, seed) for seed in range(4)])
    assert old.faces_right < r.faces_right - 0.2, f"the scenario no longer shows the trouble: {old}"
