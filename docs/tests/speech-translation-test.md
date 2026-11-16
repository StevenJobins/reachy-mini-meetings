# Testskript: Sprache und Übersetzung (Basistest)

Prüft die eingebauten Funktionen, kurz (etwa 15 Minuten). Zum Abhaken gemeinsam und live: das Artefakt
*Reachy Sprachtest* (https://claude.ai/artifact/TGqFEB5YbtZ65iJ287vQf1, Quelle `speech-translation-test.html`
daneben). Dieses Dokument ist dieselbe Liste zum Lesen.

- **R** (Raum): sitzt 1–1.5 m vor Reachy. **R2**: zweite Person im Raum für Block C und I (ohne sie spricht R auch
  die R2-Sätze, die Sprecher-Prüfung entfällt).
- **V** (VR-Brille): meldet in Teil 1, was erscheint (rechte Pinch-Geste halten, dann hört R es aus Reachy), und
  spricht in Teil 2.

Bewertung: ✓ erfolgreich, ~ teilweise, ✗ nicht erfolgreich, – übersprungen.

## 0. Vorbereitung

| Nr | Wer | Prüfen |
|---|---|---|
| 0.1 | R | Laptop-App *Reachy Meetings*: alle Punkte grün, kein Hinweis zu „DeepL key“. |
| 0.2 | V | App offen, Video da, Reachy wach. |
| 0.3 | V | Sprach-Kachel auf English, Kachel *Translate me* aus. |

## Teil 1: Raum → Brille

R sagt zuerst die Nummer („B zwei“), wartet 2 s und liest dann. V meldet Text, Übersetzung, Sprecher und Zeit
(Satzende bis der Untertitel nicht mehr kursiv ist).

**A. Untertitel** (Englisch, V liest Englisch). Erwartet: erst kursiv, dann normal, keine Übersetzung, „Speaker A“.

| Nr | Wer | sagt |
|---|---|---|
| A1 | R | Good morning everyone, can you all hear me clearly? |
| A2 | R | The deadline for the final report is the eleventh of January. |

**B. Übersetzung** (Deutsch, V liest Englisch). Erwartet: Punkte („…“), dann Englisch gross, Deutsch klein darunter.

| Nr | Wer | sagt | Referenz |
|---|---|---|---|
| B1 | R | Guten Morgen zusammen, wollen wir mit der Sitzung beginnen? | Good morning everyone, shall we start the meeting? |
| B2 | R | Das Budget für das nächste Quartal beträgt fünfzigtausend Franken. | The budget for the next quarter is fifty thousand francs. |
| B3 | R | Kevin zeigt nach der Pause die Demo mit dem Roboter. | After the break, Kevin will show the demo with the robot. |

**C. Zwei Sprecher** (nacheinander, Deutsch). Erwartet: R behält Farbe und Buchstaben, R2 bekommt einen anderen.

| Nr | Wer | sagt |
|---|---|---|
| C1 | R | Ich übernehme die Messung im Headset. |
| C2 | R2 | Gut, dann schreibe ich das Protokoll. |
| C3 | R | Perfekt, dann treffen wir uns am Donnerstag um zehn Uhr. |

**D. Zielsprache wechseln**

| Nr | Wer | Was | Erwartet |
|---|---|---|---|
| D1 | V | Sprach-Kachel → Deutsch | Kachel zeigt „Deutsch“. |
| D2 | R | *We should order two more batteries for the headset.* | Deutsch gross, Englisch klein. |
| D3 | V | Sprach-Kachel → English zurück | |

## Teil 2: Brille → Raum

V spricht mit gedrückter rechter Pinch-Geste. R notiert, was aus Reachy kommt, und sagt nur „weiter“: gesprochene
Rückmeldungen würden die Sprache verstellen, in der Reachy spricht (die im Raum am meisten gesprochene, nach Teil 1
Deutsch).

**F. Eigene Stimme** (*Translate me* aus). Erwartet: R hört die echte Stimme von V; in der Brille die Zeile „You“.

| Nr | Wer | Was | sagt |
|---|---|---|---|
| F1 | V | normal | Hallo zusammen, ich bin heute über den Roboter dabei. |
| F2 | V | nach „Woche“ loslassen: der Rest darf nicht kommen | Ich bin nächste Woche leider nicht da, weil ich an einer Konferenz bin. |

**G. Translate me** (an; V spricht Englisch, Reachy sagt es auf Deutsch, V sagt keine Nummern). R schreibt auf, was
Reachy gesagt hat; V misst die Zeit vom Satzende bis der Text unter der Menüzeile erscheint.

| Nr | Wer | sagt | Referenz |
|---|---|---|---|
| G0 | V | Hello everyone. *(danach „→ XX“ auf der Kachel ablesen, erwartet → DE)* | Hallo zusammen. |
| G1 | V | Could you move the camera a bit to the left? | Könntet ihr die Kamera etwas nach links bewegen? |
| G2 | V | Let's meet at two thirty on the fourteenth of December. | Treffen wir uns am vierzehnten Dezember um halb drei. |

**I. Gespräch** (nacheinander; Raum Deutsch, V Englisch mit *Translate me*). Erst sprechen, wenn der vorherige Satz
fertig ist. Erwartet: Raum-Sätze erscheinen bei V englisch mit dem richtigen Sprecher; Sätze von V spricht Reachy
auf Deutsch.

| Nr | Wer | sagt | Referenz |
|---|---|---|---|
| I1 | R | Kannst du von dort aus das Whiteboard sehen? | Can you see the whiteboard from there? |
| I2 | V | Yes, but the writing is too small for me. | Ja, aber die Schrift ist zu klein für mich. |
| I3 | R2 | Dann stellen wir Reachy einfach näher hin. | Then we'll just move Reachy closer. |
| I4 | V | Thanks, now I can read everything. | Danke, jetzt kann ich alles lesen. |
| I5 | R | Prüfen über G und I: gab es einen Untertitel von Reachys eigener Stimme? | Erfolgreich = nein. Danach *Translate me* aus. |

**K. Notizen** (am Schluss)

| Nr | Wer | Was | Erwartet |
|---|---|---|---|
| K1 | V | Notizen-Karte öffnen | Zusammenfassung; stimmen die Punkte (Budget, Demo, Donnerstag)? |
| K2 | V | *Mail* auf der Notizen-Karte | Mail-Entwurf mit den Notizen öffnet sich auf dem Laptop. |

## Ergebnisse

Im Artefakt *Als Markdown kopieren* und in `docs/measurements/speech-JJJJ-MM-TT.md` ablegen. Genaue Zeiten für
Teil 2 im Laptop-Log `~/Library/Logs/reachy-meetings/backend.log` (Zeilen `me timing`).
