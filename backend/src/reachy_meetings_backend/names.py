"""Names people say of themselves in the meeting: "My name is Anna", "Hallo zusammen, ich bin Dominic".

The headset then writes the name instead of "Speaker A" (roster.py). Pure text rules, no model, no network: a
wrong name above someone's head is worse than "Speaker A", so only clear introductions count.

  sure     a phrase only used for names: "my name is", "ich heisse", "je m'appelle", "mi chiamo", "me llamo", ...
  maybe    "I'm X", "ich bin X", "this is X", "je suis X", "sono X", "soy X": people also say "I'm sorry" and
           "ich bin Student", and German writes every noun with a capital. So X counts only if it is written with
           a capital, is not a word people say there for other reasons (NOT_NAMES), and the sentence is an
           introduction: it has a greeting ("Hi, I'm Kevin"), or it is nothing but the name ("Ich bin Kevin.",
           "I'm Kevin from the design team.")

Not found: names said about someone else ("thanks, Anna"); that needs understanding who is addressed, and a wrong
guess would name the wrong person. Whisper writes names as it hears them ("Kewin"): the name is shown as written.
"""

from __future__ import annotations

import re

NAME = r"([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’]+(?:-[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+)?)"
END = r"(?=\s*(?:[.,!?;:]|$|\s(?:and|from|at|with|und|von|vom|aus|bei|et|de|du|e|y|da|di)\b))"

SURE = [re.compile(p + r"\s+(?:the\s+|der\s+|die\s+)?" + NAME, re.IGNORECASE) for p in (
    r"\bmy name(?:\s+is|'s|’s)", r"\b(?:you can |please |just )?call me", r"\bmy name here is",
    r"\bich hei(?:ss|ß)e", r"\bmein name ist", r"\bmein vorname ist", r"\bnennt mich", r"\bnenn mich",
    r"\bje m[e'’]\s?appelle", r"\bmon (?:pré)?nom(?: est|,? c[’']est)",
    r"\bmi chiamo", r"\bil mio nome è",
    r"\bme llamo", r"\bmi nombre es",
)]
MAYBE = [re.compile(p + r"\s+(?:the\s+|der\s+|die\s+|d[’']\s?)?" + NAME + END, re.IGNORECASE) for p in (
    r"\b(?:i'm|i’m|i am|this is|it's|it’s)", r"\b(?:ich bin|hier ist|hier spricht|ich bin's,?)",
    r"\b(?:je suis|c[’']est|ici)", r"\b(?:io sono|sono|qui è)", r"\b(?:yo soy|soy)",
)]
GREETING = re.compile(
    r"\b(?:hi|hello|hey|hallo|hoi|grüezi|grüessech|servus|moin|salü|sali|guten (?:morgen|tag|abend)|"
    r"good (?:morning|afternoon|evening)|nice to meet you|freut mich|bonjour|salut|bonsoir|enchanté|ciao|"
    r"buongiorno|buonasera|piacere|hola|buenos días|buenas tardes|encantado|encantada|zusammen|everyone|everybody)\b",
    re.IGNORECASE)

# Words with a capital that follow "I'm" / "ich bin" and are no names (German nouns above all)
NOT_NAMES = {w.lower() for w in """
    I OK Okay Ok Sorry Fine Good Great Sure Here There Back Done Ready Happy Not Just Also Still Really Very So
    American English British German Swiss French Italian Spanish Austrian Dutch European Canadian Indian Chinese
    Monday Tuesday Wednesday Thursday Friday Saturday Sunday
    Meinung Ansicht Auffassung Überzeugung Lage Sicher Fertig Dabei Dafür Dagegen Bereit Gespannt Froh Müde Neu Hier
    Da Jetzt Noch Auch Nicht Schon Ja Nein Mir Der Die Das Ein Eine Einer Student Studentin Ingenieur Ingenieurin
    Entwickler Entwicklerin Designer Designerin Lehrer Lehrerin Arzt Ärztin Chef Chefin Leiter Leiterin Manager
    Managerin Schweizer Schweizerin Deutscher Deutsche Österreicher Österreicherin Franzose Französin Italiener
    Italienerin Mitglied Teil Fan Typ Mensch Mann Frau Kollege Kollegin Erste Erster Letzte Letzter Einzige
    Einziger Neue Neuer Verantwortliche Verantwortlicher Projektleiter Projektleiterin Doktorand Doktorandin
    Professor Professorin Assistent Assistentin Praktikant Praktikantin Gast Kunde Kundin Reachy
    Désolé Désolée Sûr Prêt Prête Content Contente Étudiant Étudiante Français Française Suisse Allemand
    Sicuro Pronto Pronta Felice Studente Studentessa Italiano Italiana Svizzero Svizzera
    Seguro Listo Lista Feliz Estudiante Español Española
""".split()}


def _ok(name: str) -> bool:
    return 2 <= len(name) <= 24 and name.lower() not in NOT_NAMES


def said_name(text: str) -> str | None:
    """The name the speaker gives as their own in this utterance, or None."""
    for pattern in SURE:
        m = pattern.search(text)
        if m and _ok(m.group(1)) and m.group(1)[0].isupper():
            return m.group(1)
    greeting = bool(GREETING.search(text))
    for pattern in MAYBE:
        for m in pattern.finditer(text):
            name = m.group(1)
            if not _ok(name) or not name[0].isupper():
                continue
            # nothing but the introduction: the sentence starts with it (after "well", "so", "also", "ja" ...)
            before = re.sub(r"^(?:\W|\b(?:well|so|okay|ok|and|yes|yeah|also|ja|und|genau|gut|alors|donc|et|bene|allora|"
                            r"e|bueno|pues|y)\b)*", "", text[: m.start()], flags=re.IGNORECASE)
            if greeting or not before.strip():
                return name
    return None
