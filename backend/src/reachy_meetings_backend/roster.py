"""Who is in the meeting, as the headset shows them: one number per participant, for the label above their head
("Speaker A", or their name) and the same label on what they say. Pure logic.

Underneath there are two kinds of identity: people the camera sees (people.py, by face) and voices (speakers.py).
A participant is a person, with the voices that belong to them, or a voice nobody has been seen speaking with yet.

  person seen                  a participant at once, also before they say a word (the label above the head)
  voice with an owner          the owner's participant
  voice without an owner       a participant of its own; when the camera later shows whose voice it is, that
                               participant turns out to be the person's: `changes` says (was, is), and
                               captions.py sends the lines on show again under the right label
  two people found to be one   (people.py, same face) likewise

Numbers are never used twice, so a label cannot move to someone else; a participant that turned out to be another
leaves a gap (Speaker A, B, D).

Names: names.py finds "I'm Anna" in what is said; `name()` gives it to the participant who said it.
"""

from __future__ import annotations

from .people import People, Person


class Roster:
    def __init__(self, people: People | None = None) -> None:
        self.people = people
        self.of_person: dict[int, int] = {}   # people.py pid -> participant
        self.of_voice: dict[int, int] = {}    # voice -> its own participant, while nobody owns the voice
        self.names: dict[int, str] = {}       # participant -> name
        self.is_now: dict[int, int] = {}      # a participant that turned out to be another one
        self.changes: list[tuple[int, int]] = []   # (was, is), taken by captions.py
        self.next = 1

    def _new(self) -> int:
        self.next += 1
        return self.next - 1

    def resolve(self, participant: int | None) -> int | None:
        """The participant as it is called now (after "turned out to be")."""
        while participant in self.is_now:
            participant = self.is_now[participant]
        return participant

    def _becomes(self, was: int, now: int) -> None:
        if was == now:
            return
        self.is_now[was] = now
        if was in self.names:   # the name said under the old label is this participant's
            self.names.setdefault(now, self.names[was])
            del self.names[was]
        self.changes.append((was, now))

    def for_person(self, person: Person) -> int:
        if person.pid not in self.of_person:
            self.of_person[person.pid] = self._new()
        return self.of_person[person.pid]

    def sync(self) -> None:
        """After faces or an utterance: people merged in people.py, voices that found their owner."""
        if not self.people:
            return
        for gone, kept in self.people.merged:
            was = self.of_person.pop(gone, None)
            keeper = next((p for p in self.people.people if p.pid == kept), None)
            if was is not None and keeper is not None:
                if kept in self.of_person:
                    self._becomes(was, self.of_person[kept])
                else:
                    self.of_person[kept] = was
        self.people.merged.clear()
        for p in self.people.people:
            self.for_person(p)
        for voice in list(self.of_voice):
            owner = self.people.owner(voice)
            if owner is not None:
                self._becomes(self.of_voice.pop(voice), self.for_person(owner))

    def speaker(self, voice: int | None, person: Person | None) -> int | None:
        """The participant who said an utterance decided as (voice, person) by fusion.py; None = not known."""
        if person is not None:
            return self.for_person(person)
        if voice is None:
            return None
        owner = self.people.owner(voice) if self.people else None
        if owner is not None:
            return self.for_person(owner)
        if voice not in self.of_voice:
            self.of_voice[voice] = self._new()
        return self.of_voice[voice]

    def name(self, participant: int | None, name: str) -> bool:
        """`participant` said their name. True if that is new."""
        participant = self.resolve(participant)
        if participant is None or self.names.get(participant) == name:
            return False
        self.names[participant] = name
        return True

    def name_of(self, participant: int | None) -> str | None:
        return self.names.get(self.resolve(participant))

    @staticmethod
    def label(participant: int | None, name: str | None = None) -> str:
        """As the headset writes it (CaptionView.cs has the same rule)."""
        if name:
            return name
        return "" if participant is None else f"Speaker {chr(ord('A') + (participant - 1) % 26)}"
