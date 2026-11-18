"""Who is who: faces known again (people.py), participants and their names (roster.py, names.py), the voice model's
input (speakers.fbank), the head angle at a time (doa.py). Pure logic, no models.   pytest -q"""

from pathlib import Path

import numpy as np
import pytest

from reachy_meetings_backend.doa import DoaTracker
from reachy_meetings_backend.fusion import Fusion
from reachy_meetings_backend.names import said_name
from reachy_meetings_backend.people import People
from reachy_meetings_backend.roster import Roster
from reachy_meetings_backend.speakers import SpeakerClusters, VoiceObs, fbank
from reachy_meetings_backend.vision import FaceId

rng = np.random.default_rng(3)


def _flat(u, v):   # 90 x 50 degrees, no distortion
    return (0.5 - u) * 90, (0.5 - v) * 50


def unit(x):
    return x / np.linalg.norm(x)


def look(face, alike=0.85):
    """The face as one look sees it: `alike` to what the person looks like."""
    w = rng.standard_normal(face.shape)
    w = unit(w - (w @ face) * face)
    return alike * face + np.sqrt(1 - alike**2) * w


def _face(u, track=None, emb=None, mouth=0.01):
    return {"cx": u, "cy": 0.5, "top": 0.4, "w": 0.1, "h": 0.2, "mouth": mouth, "track": track, "emb": emb}


def _watch(people, t0, t1, faces, head_yaw=0.0, steady=True):
    """faces: {u: (track, emb)} or {u: (track, emb, mouth function)}."""
    for i in range(int((t1 - t0) * 20)):
        frame = [_face(u, f[0], f[1], f[2](i) if len(f) > 2 else 0.01) for u, f in faces.items()]
        people.update(frame, t0 + i / 20, head_yaw, steady=steady)


ANNA, BEN = unit(rng.standard_normal(128)), unit(rng.standard_normal(128))


# ---------------------------------------------------------------- faces
def test_a_person_is_known_again_by_their_face_wherever_they_are():
    people = People(_flat)
    _watch(people, 0, 1, {0.3: (1, look(ANNA))})                       # Anna at +18 degrees
    _watch(people, 5, 6, {0.8: (2, look(ANNA))})                       # ... sits down at -27: the same person
    assert [p.pid for p in people.people] == [1] and abs(people.people[0].yaw + 27) < 1.5
    _watch(people, 10, 11, {0.8: (3, look(BEN))})                      # Ben takes that very seat: someone else
    assert [p.pid for p in people.people] == [1, 2]
    assert people.people[1].face @ BEN > 0.8 and people.people[1].face @ ANNA < 0.3


def test_while_the_head_turns_nobody_new_is_taken_in_and_a_followed_face_stays_who_it_was():
    """2026-11-18: one face became five people at -45, -29, 0, 27, 33 degrees while Reachy's head moved (the head
    angle lags the picture)."""
    people = People(_flat)
    emb = look(ANNA)
    _watch(people, 0, 1, {0.5: (1, emb)})
    for i in range(40):                                                # the head sweeps 60 degrees in 2 s; the angle the
        u = 0.5 + (i / 40) * 60 / 90                                   # backend has is 0.3 s old (9 degrees off)
        people.update([_face(u, 1, emb)], 1 + i / 20, max(0.0, (i - 6) / 40 * 60), steady=False)
    assert [p.pid for p in people.people] == [1] and not people.candidates
    assert abs(people.people[0].yaw) < 1, "and their place is not smeared along the way"
    for i in range(40):                                                # the same sweep, the face found anew each time
        u = 0.5 + (i / 40) * 60 / 90                                   # (track lost), no word that the head turns
        people.update([_face(u, 100 + i, look(ANNA))], 4 + i / 20, max(0.0, (i - 6) / 40 * 60))
    assert [p.pid for p in people.people] == [1], "by the face"
    old = People(_flat)                                                # as it was: by direction only
    _watch(old, 0, 1, {0.5: (None, None)})
    for i in range(80):
        u = 0.5 + (i / 80) * 60 / 90
        old.update([_face(u)], 1 + i / 20, max(0.0, (i - 12) / 80 * 60))
    _watch(old, 5, 6, {0.5: (None, None)}, head_yaw=60)
    assert len(old.people) > 1


def test_one_person_taken_in_twice_becomes_one_again_but_two_seen_side_by_side_stay_two():
    people = People(_flat)
    _watch(people, 0, 1, {0.3: (1, None)})                              # no good look at the face yet
    _watch(people, 2, 3, {0.7: (2, None)})                              # ... the same person, seen elsewhere: person 2
    assert len(people.people) == 2
    people.vote(7, people.people[1])
    _watch(people, 4, 5, {0.3: (3, look(ANNA))})                        # now their face is seen, at both places
    _watch(people, 6, 7, {0.7: (4, look(ANNA))})
    assert [p.pid for p in people.people] == [1] and people.merged == [(2, 1)]
    assert people.votes[7] == {1: 1}, "what was known of the one goes to the other"
    twins = People(_flat)
    _watch(twins, 0, 2, {0.3: (1, look(ANNA)), 0.7: (2, look(ANNA, 0.8))})   # alike, but two faces in one picture
    assert len(twins.people) == 2


def test_face_points_are_ordered_and_small_or_turned_faces_are_not_judged():
    pts = [(140, 100), (100, 101), (121, 125), (135, 150), (104, 149)]   # right eye first, as MediaPipe may give them
    assert FaceId.order(pts)[:2].tolist() == [[100, 101], [140, 100]] and FaceId.order(pts)[3].tolist() == [104, 149]
    assert FaceId.usable(pts)
    assert not FaceId.usable([(x * 0.3, y * 0.3) for x, y in pts]), "eyes 12 px apart: too small"
    assert not FaceId.usable([(100, 100), (112, 100), (108, 125), (102, 150), (114, 150)]), "seen from the side"


# ---------------------------------------------------------------- participants
def test_everyone_seen_is_a_participant_and_a_voice_without_a_face_gets_its_own_until_its_face_is_found():
    people = People(_flat)
    roster = Roster(people)
    _watch(people, 0, 1, {0.3: (1, look(ANNA)), 0.7: (2, look(BEN))})
    roster.sync()
    anna, ben = people.people
    assert (roster.for_person(anna), roster.for_person(ben)) == (1, 2), "before anyone said a word"
    assert roster.speaker(None, None) is None
    assert roster.speaker(5, None) == 3, "a voice nobody was seen speaking with: Speaker C"
    assert roster.name(3, "Ben") and roster.name_of(3) == "Ben" and not roster.name(3, "Ben")
    assert roster.speaker(5, anna) == 1, "seen saying it: theirs, whatever the voice"
    people.vote(5, ben)
    people.vote(5, ben)                                                # the camera shows whose voice 5 is
    roster.sync()
    assert roster.changes == [(3, 2)] and roster.resolve(3) == 2 and roster.speaker(5, None) == 2
    assert roster.name_of(2) == "Ben" and roster.name_of(3) == "Ben", "the name said under the old label is theirs"
    assert Roster.label(2) == "Speaker B" and Roster.label(2, "Ben") == "Ben" and Roster.label(None) == ""
    assert roster.speaker(6, None) == 4, "numbers are not used twice"


def test_two_people_found_to_be_one_are_one_participant():
    people = People(_flat)
    roster = Roster(people)
    _watch(people, 0, 1, {0.3: (1, None)})
    _watch(people, 2, 3, {0.7: (2, None)})
    roster.sync()
    assert roster.of_person == {1: 1, 2: 2}
    _watch(people, 4, 5, {0.3: (3, look(ANNA))})
    _watch(people, 6, 7, {0.7: (4, look(ANNA))})
    roster.sync()
    assert roster.of_person == {1: 1} and roster.changes == [(2, 1)]


def test_without_a_camera_voices_are_the_participants():
    clusters = SpeakerClusters()
    f, roster = Fusion(clusters), Roster()
    a, b = unit(rng.standard_normal(64)), unit(rng.standard_normal(64))
    said = []
    for i, v in enumerate([a, b, a, b, b]):
        d = f.final(VoiceObs(look(v, 0.9) if i > 1 else v, 3.0), 4 * i, 4 * i + 3, None)
        roster.sync()
        said.append(roster.speaker(d.voice, d.person))
    assert said == [1, 2, 1, 2, 2]


# ---------------------------------------------------------------- names
NAMES = {
    "My name is Anna.": "Anna", "Hi everyone, I'm Kevin.": "Kevin", "Hello, this is Dominic from the robotics team.": "Dominic",
    "I'm Simon.": "Simon", "I am Simon and I work on the headset.": "Simon", "You can call me Kev.": "Kev",
    "Hallo zusammen, ich bin Kevin.": "Kevin", "Ich heisse Dominic.": "Dominic",
    "Ich heiße Anna-Lena und bin neu hier.": "Anna-Lena", "Mein Name ist Stöckli.": "Stöckli", "Ich bin der Simon.": "Simon",
    "Also, ich bin Kevin vom Team XR.": "Kevin", "Grüezi, ich bin die Petra.": "Petra", "Bonjour, je m'appelle Élodie.": "Élodie",
    "Salut, je suis Marc.": "Marc", "Ciao, mi chiamo Luca.": "Luca", "Buongiorno, sono Giulia.": "Giulia",
    "Hola, me llamo Carlos.": "Carlos", "Hola, soy María.": "María", "Yeah, I'm Kevin.": "Kevin",
    "Guten Morgen, hier ist Dominic.": "Dominic",
}
NO_NAMES = [
    "I'm sorry, I did not hear that.", "I'm not sure about the budget.", "I am really happy with that.", "This is great.",
    "This is the second prototype.", "I'm Swiss, so I know that.", "It's Monday tomorrow.", "Thanks, Anna.",
    "Kevin, what do you think?", "Ich bin der Meinung, dass wir das verschieben sollten.", "Ich bin Student an der ETH.",
    "Ich bin sicher, dass das geht.", "Ich bin mir nicht sicher.", "Ich bin auch dafür.", "Hallo zusammen, ich bin bereit.",
    "Das ist Kevin, er macht das Backend.", "Ich bin Schweizer.", "Hier ist das Budget.", "Ich bin müde.",
    "Je suis désolé.", "Je suis sûr que ça marche.", "Sono sicuro.", "Soy estudiante.", "I think Anna is right.",
    "Hallo zusammen, ich bin Projektleiter bei uns.", "Hi, I'm here.", "Ich bin's.", "Hello, I'm OK with that.",
    "Ich bin da ganz bei dir.", "I'm going to send it to Dominic.", "Wir sind Kevin und Simon.", "I'm with Kevin on this.",
    "Ich bin Kevins Meinung.",
]


@pytest.mark.parametrize("text,name", NAMES.items())
def test_a_name_someone_gives_of_themselves_is_found(text, name):
    assert said_name(text) == name


@pytest.mark.parametrize("text", NO_NAMES)
def test_other_sentences_name_nobody(text):
    assert said_name(text) is None


# ---------------------------------------------------------------- inputs
def test_fbank_is_kaldis():
    """The first frames of a fixed signal, as kaldi-native-fbank 1.x gives them (hamming, 80 bins, no dither)."""
    r = np.random.default_rng(0)
    x = (0.1 * r.standard_normal(48000) * np.sin(np.arange(48000) / 900.0)).astype(np.float32)
    ref = np.load(Path(__file__).parent / "data" / "fbank_ref.npy")
    mine = fbank(x * 32768.0, "hamming")
    assert mine.shape == (298, 80) and np.abs(mine[:5] - ref).max() < 1e-3
    assert fbank(x[:100]).shape == (1, 80), "shorter than one frame: padded"


def test_head_angle_at_the_time_of_a_camera_frame_and_whether_it_is_turning():
    d = DoaTracker("http://unused")
    assert d.pose_at(5.0) == (0.0, 0.0, 0.0, True)
    for i in range(11):                                                # still, then turning 40 degrees a second
        d.set_pose(0.0 if i < 5 else (i - 5) * 4.0, 2.0, 0.0, now=10 + i / 10)
    assert d.pose_at(10.2) == (0.0, 2.0, 0.0, True)
    yaw, _, _, steady = d.pose_at(10.75)
    assert abs(yaw - 10.0) < 1e-6 and not steady, "between two readings, and not to be trusted for directions"
    assert d.pose_at(11.5)[:3] == (20.0, 2.0, 0.0), "after the last reading: the last one"
    assert not d.pose_at(13.0)[3], "no reading for a while: who knows"
