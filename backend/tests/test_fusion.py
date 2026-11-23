"""Who said it (fusion.py): voice + mouths + mic direction. Pure logic, no models.   pytest -q"""

import numpy as np

from reachy_meetings_backend.doa import DoaTracker
from reachy_meetings_backend.fusion import Fusion
from reachy_meetings_backend.people import People
from reachy_meetings_backend.speakers import SpeakerClusters, VoiceObs, main_voice
from reachy_meetings_backend.vision import FaceFinder

rng = np.random.default_rng(7)
DIM = 256


def _flat(u, v):   # 90 x 50 degrees, no distortion
    return (0.5 - u) * 90, (0.5 - v) * 50


def _face(u, mouth=0.0):
    return {"cx": u, "cy": 0.5, "top": 0.4, "w": 0.1, "h": 0.2, "mouth": mouth}


def _watch(people, t0, t1, faces, head_yaw=0.0):
    for i in range(int((t1 - t0) * 20)):
        people.update([_face(u, m(i)) for u, m in faces.items()], t0 + i / 20, head_yaw)


def _still(i):
    return 0.01


def _talking(i):
    return 0.3 * (i % 4 < 2)


def unit(x):
    return x / np.linalg.norm(x)


def near(base, sim):
    """A unit vector with exactly this cosine similarity to `base`."""
    u = unit(base)
    w = rng.standard_normal(u.shape)
    w = unit(w - (w @ u) * u)
    return sim * u + np.sqrt(1 - sim**2) * w


def jitter(v):
    """The same voice again: similarity ~0.99."""
    return unit(v) + 0.1 * unit(rng.standard_normal(DIM))


def obs(emb, dur=3.0, mixed=False):
    return VoiceObs(unit(emb), dur, mixed)


def two_people():
    """Person 1 at +18 degrees (image u 0.3), person 2 at -18 (u 0.7)."""
    people = People(_flat)
    _watch(people, 0, 1, {0.3: _still, 0.7: _still})
    return people


# ---------------------------------------------------------------- mic direction
def test_mic_with_an_offset_is_calibrated_and_then_finds_people_out_of_the_picture():
    people = two_people()
    f = Fusion(people=people)
    for i in range(4):   # people take turns in view; the mic reads their direction + 60 degrees (body frame, bias)
        t = 1 + 4 * i
        _watch(people, t, t + 2, {0.3: _talking, 0.7: _still})
        f.attribute(None, t, t + 2, doa=(18 + 60, 5))
        _watch(people, t + 2, t + 4, {0.3: _still, 0.7: _talking})
        f.attribute(None, t + 2, t + 4, doa=(-18 + 60, 5))
    offset, sigma = f.doa_model()
    assert abs(offset - 60) < 1 and sigma < 10
    _watch(people, 20, 22, {}, head_yaw=90)                 # Reachy looks elsewhere: no faces at all
    assert f.attribute(None, 20, 22, doa=(-18 + 60, 5)).person.pid == 2
    assert f.attribute(None, 22, 24, doa=(18 + 60, 5)).person.pid == 1
    assert f.attribute(None, 24, 26, doa=(60, 5)).person is None, "half way: nobody stands out"
    assert f.attribute(None, 26, 28, doa=(-18 + 60, 50)).person is None, "the mic wandered during the utterance"


def test_a_mic_pointing_the_same_way_for_everyone_never_counts():
    people = two_people()
    f = Fusion(people=people)
    for i in range(6):   # 2026-11-06: 40-53 degrees whoever spoke
        t = 1 + 4 * i
        _watch(people, t, t + 2, {0.3: _talking, 0.7: _still})
        f.attribute(None, t, t + 2, doa=(45 + i % 3, 5))
        _watch(people, t + 2, t + 4, {0.3: _still, 0.7: _talking})
        f.attribute(None, t + 2, t + 4, doa=(47 - i % 3, 5))
    assert f.doa_model() is None
    _watch(people, 30, 32, {}, head_yaw=90)
    d = f.attribute(None, 30, 32, doa=(46, 5))
    assert d.person is None and all("mic" not in terms for _, terms in d.scores.values())


def test_a_mic_checked_against_one_person_only_does_not_count():
    people = two_people()
    f = Fusion(people=people)
    for i in range(6):
        _watch(people, 1 + 2 * i, 3 + 2 * i, {0.3: _talking, 0.7: _still})
        f.attribute(None, 1 + 2 * i, 3 + 2 * i, doa=(30, 5))
    assert f.doa_model() is None, "a mic stuck at 30 would look perfect"


def test_the_mic_breaks_the_tie_with_a_face_seen_too_rarely():
    people = two_people()
    f = Fusion(people=people)
    for i in range(4):
        t = 1 + 4 * i
        _watch(people, t, t + 2, {0.3: _talking, 0.7: _still})
        f.attribute(None, t, t + 2, doa=(18, 5))
        _watch(people, t + 2, t + 4, {0.3: _still, 0.7: _talking})
        f.attribute(None, t + 2, t + 4, doa=(-18, 5))
    for i in range(40):   # person 2 found only 3 times in 2 s, and their mouth moved; person 1's mouth moves
        faces = [_face(0.3, 0.01 + 0.05 * (i % 2))] + ([_face(0.7, _talking(i))] if i in (10, 20, 30) else [])
        people.update(faces, 20 + i / 20, 0)
    assert f.attribute(None, 20, 22).person is None, "without the mic: it may have been person 2"
    assert f.attribute(None, 20, 22, doa=(18, 5)).person.pid == 1
    # seen just as rarely, but with a still mouth: that says nothing for them (2026-11-23: such faces blocked 7 of
    # the 11 utterances in which a mouth clearly talked)
    for i in range(40):
        faces = [_face(0.3, 0.01 + 0.05 * (i % 2))] + ([_face(0.7, 0.01)] if i in (10, 20, 30) else [])
        people.update(faces, 30 + i / 20, 0)
    assert f.attribute(None, 30, 32).person.pid == 1


# ---------------------------------------------------------------- voice
def test_a_doubtful_voice_goes_to_the_voice_of_the_person_seen_talking_and_is_learnt():
    a = rng.standard_normal(DIM)
    people = two_people()
    clusters = SpeakerClusters()
    f = Fusion(clusters, people)
    for i in range(2):                                     # voice 1 is person 1's
        _watch(people, 1 + 2 * i, 3 + 2 * i, {0.3: _talking, 0.7: _still})
        assert f.final(obs(near(a, 0.95)), 1 + 2 * i, 3 + 2 * i, None).voice == 1
    assert people.owner(1).pid == 1
    _watch(people, 5, 7, {0.3: _talking, 0.7: _still})
    d = f.final(obs(near(a, 0.45)), 5, 7, None)            # a poor match (a short or noisy clip) ...
    assert d.voice == 1 and d.person.pid == 1 and clusters.counts == [3], "... but person 1 is seen saying it"
    _watch(people, 7, 9, {0.3: _still, 0.7: _still})
    d = f.final(obs(near(a, 0.45)), 7, 9, None)            # the same poor match, nobody seen talking
    assert d.voice is None and d.person is None and clusters.counts == [3], \
        "no name, nothing learnt, no place: with one voice known, anyone new would match it like this (2026-11-23)"


def test_doubtful_matches_do_not_drag_a_voice_over_everyone():
    """2026-11-18: everyone ended up as speaker 1: its mean had learnt from everything above the threshold."""
    a = rng.standard_normal(DIM)
    b = near(a, 0.50)                                      # another person, a similar voice
    clusters = SpeakerClusters()
    f = Fusion(clusters)
    assert f.final(obs(a), 0, 3, None).voice == 1
    before = clusters.means[0].copy()
    for i in range(10):   # the closest voice or no name (around LONE), never a new one from a clip this short
        assert f.final(obs(jitter(b)), 3 + i, 4 + i, None).voice in (1, None)
    assert clusters.means[0] @ before > 0.999 and clusters.counts == [1], "not learnt"
    old = SpeakerClusters()                                # as it was: every match taught the mean
    old.add(a)
    for _ in range(10):
        old.add(jitter(b))
    assert old.means[0] @ unit(a) < 0.95


def test_the_camera_splits_a_similar_voice_off_into_a_new_one():
    a = rng.standard_normal(DIM)
    people = two_people()
    clusters = SpeakerClusters()
    f = Fusion(clusters, people)
    for i in range(2):                                     # voice 1 is person 1's
        _watch(people, 1 + 2 * i, 3 + 2 * i, {0.3: _talking, 0.7: _still})
        f.final(obs(near(a, 0.97)), 1 + 2 * i, 3 + 2 * i, None)
    b = near(a, 0.55)                                      # person 2, a voice above the threshold to voice 1
    _watch(people, 5, 7, {0.3: _still, 0.7: _talking})
    d = f.final(obs(b), 5, 7, None)
    assert d.voice == 2 and d.person.pid == 2
    _watch(people, 7, 9, {0.3: _still, 0.7: _talking})
    f.final(obs(jitter(b)), 7, 9, None)
    assert people.owner(1).pid == 1 and people.owner(2).pid == 2
    _watch(people, 9, 11, {}, head_yaw=90)                 # both out of the picture: the voice says who
    assert f.final(obs(jitter(b)), 9, 11, None).person.pid == 2
    assert f.final(obs(near(a, 0.95)), 11, 13, None).person.pid == 1


def test_a_similar_voice_is_found_by_its_second_utterance_and_the_first_is_relabelled():
    a = rng.standard_normal(DIM)
    b = unit(near(a, 0.42))                                # someone else: not unlike voice 1, not clearly it either
    clusters = SpeakerClusters()
    f = Fusion(clusters)
    assert f.final(obs(a), 0, 3, None, key="first").voice == 1
    d = f.final(obs(b), 4, 7, None, key="second")
    assert d.voice is None and not d.relabel and clusters.counts == [1], "for now no name, nothing learnt"
    d = f.final(obs(near(b, 0.75)), 8, 11, None, key="third")
    assert d.voice == 2 and d.relabel == [("second", 2)], "two alike, both unlike voice 1: a new voice, from both"
    assert f.final(obs(near(b, 0.7)), 12, 15, None).voice == 2
    assert f.final(obs(near(a, 0.7)), 16, 19, None).voice == 1


def test_one_odd_sounding_sentence_founds_no_new_voice():
    a = rng.standard_normal(DIM)
    clusters = SpeakerClusters()
    f = Fusion(clusters)
    f.final(obs(a), 0, 3, None)
    assert f.final(obs(near(a, 0.33)), 4, 7, None).voice is None, "not known: a cough in it, a laugh"
    assert f.final(obs(near(a, 0.75)), 8, 11, None).voice == 1 and len(clusters.means) == 1
    assert f.final(obs(near(a, 0.10)), 12, 15, None).voice == 2, "unlike every known voice: new at once"


def test_where_the_mic_hears_a_voice_from_breaks_a_tie_between_similar_voices():
    a = rng.standard_normal(DIM)
    b = unit(near(a, 0.5))
    f = Fusion(SpeakerClusters())
    f.final(obs(a), 0, 3, (40, 5))
    f.final(obs(b), 3, 6, None)                            # no direction, doubtful: kept
    assert f.final(obs(near(a, 0.1)), 6, 9, (-40, 5)).voice == 2      # (a third voice, to have two with a direction)
    for i in range(3):
        assert f.final(obs(near(a, 0.8)), 10 + 6 * i, 13 + 6 * i, (40 + i, 5)).voice == 1
        assert f.final(obs(near(unit(f.clusters.means[1]), 0.8)), 13 + 6 * i, 16 + 6 * i, (-40 - i, 5)).voice == 2
    c1, c2 = unit(f.clusters.means[0]), unit(f.clusters.means[1])
    between = unit(c1 + c2 + 0.0 * a)                     # as like the one as the other
    s1, s2 = between @ c1, between @ c2
    assert abs(s1 - s2) < 0.02
    assert f._similarities(between, (40, 5))[0][0] > f._similarities(between, (40, 5))[0][1] + 0.05
    assert f._similarities(between, (-40, 5))[0][1] > f._similarities(between, (-40, 5))[0][0] + 0.05
    same = f._similarities(between, (0, 50))[0]
    assert abs(same[0] - s1) < 1e-6, "the mic wandered during the utterance: it says nothing"
    assert f._elsewhere(1, (-40, 5)) and not f._elsewhere(1, (45, 5)) and not f._elsewhere(1, None)


def test_a_listener_whose_mouth_moves_now_and_then_does_not_get_the_voice_of_someone_out_of_the_picture():
    a, b = rng.standard_normal(DIM), rng.standard_normal(DIM)
    people = People(_flat)
    _watch(people, 0, 1, {0.3: _still})
    f = Fusion(SpeakerClusters(), people)
    t = 1
    for i in range(3):                                     # person 1 talks: voice 1 is theirs
        _watch(people, t, t + 3, {0.3: _talking})
        f.final(obs(near(a, 0.8) if i else a), t, t + 3, None)
        t += 3
    assert people.owner(1).pid == 1
    for i in range(12):                                    # someone not in the picture talks; person 1 laughs at every fourth
        _watch(people, t, t + 3, {0.3: _talking if i % 4 == 3 else _still})
        d = f.final(obs(near(b, 0.8) if i else b), t, t + 3, None)
        assert d.voice == 2 and d.person is None, i
        t += 3
    assert people.owner(2) is None and people.voices_of(people.people[0]) == [1]


def test_a_short_first_clip_starts_no_voice_and_partials_learn_nothing():
    a = rng.standard_normal(DIM)
    clusters = SpeakerClusters()
    f = Fusion(clusters)
    assert f.final(obs(a, dur=1.5), 0, 1.5, None).voice is None and not clusters.means
    assert f.partial(obs(a)).voice is None and not clusters.means
    f.final(obs(a), 2, 5, None)
    assert f.partial(obs(near(a, 0.9))).voice == 1 and clusters.counts == [1]


def test_a_mixed_utterance_casts_no_vote():
    a = rng.standard_normal(DIM)
    people = two_people()
    f = Fusion(SpeakerClusters(), people)
    _watch(people, 1, 3, {0.3: _talking, 0.7: _still})
    d = f.final(obs(a, mixed=True), 1, 3, None)
    assert d.voice == 1 and d.person.pid == 1 and people.votes == {}


def test_main_voice_of_windows_notices_a_second_speaker():
    a, b = rng.standard_normal(DIM), rng.standard_normal(DIM)
    same = main_voice([a + 0.2 * rng.standard_normal(DIM) for _ in range(4)], 3.0)
    assert not same.mixed and same.dur == 12.0 and same.emb @ unit(a) > 0.95
    two = main_voice([a + 0.2 * rng.standard_normal(DIM) for _ in range(3)] + [b, b], 3.0)
    assert two.mixed and two.dur == 9.0 and two.emb @ unit(a) > 0.95
    assert sorted(two.windows)[:2] < [0.3, 0.3] and min(sorted(two.windows)[2:]) > 0.9
    one = main_voice([a], 4.2)
    assert not one.mixed and one.dur == 4.2


# ---------------------------------------------------------------- inputs
def test_mic_spread_over_an_utterance():
    tr = DoaTracker("http://unused")
    for i, d in enumerate([0.5, 0.52, 0.48]):                # radians: ~60 degrees to the left of the head
        tr.add({"angle": d}, 10, now=i)
    az, spread = tr.spread(0, 5)
    assert abs(az - 71.4) < 1 and spread < 2
    tr.add({"angle": np.pi}, 10, now=3)                      # one reading far to the right
    assert tr.spread(0, 5)[1] > 30
    assert tr.spread(10, 20) is None


def test_two_tracks_on_one_face_report_it_once():
    """2026-11-18: one face came as three people with the same mouth readings, so no mouth could stand out."""
    finder = FaceFinder(landmarker=None)
    face = {"cx": 0.4, "cy": 0.5, "top": 0.4, "w": 0.1, "h": 0.2, "mouth": 0.1}

    def detect(frame, x, y, w, h):   # the one face, found in any region that holds its centre
        return [dict(face)] if x <= face["cx"] <= x + w and y <= face["cy"] <= y + h else []

    finder._detect = detect
    finder.tracks = [finder._new_track(dict(face)), finder._new_track({**face, "cx": 0.43})]
    frame = np.zeros((1080, 1920, 3), np.uint8)
    for _ in range(3):
        assert len(finder.step(frame)) == 1
    assert len(finder.tracks) == 1
