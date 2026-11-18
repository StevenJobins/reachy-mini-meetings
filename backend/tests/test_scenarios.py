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
