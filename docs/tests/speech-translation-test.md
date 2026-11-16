# Testskript: Sprache und Übersetzung

Ein fester Ablauf, damit jeder Test gleich läuft und die Ergebnisse vergleichbar sind. Zwei Rollen:

- **R** (Raum): sitzt 1–1.5 m vor Reachy und liest die Sätze vor. Für Block C braucht es eine zweite Person (**R2**).
- **H** (Headset): trägt die Brille und meldet zurück, was erscheint. H sieht die Untertitel, R hört Reachy.

Beide haben dieses Dokument offen und arbeiten es von oben nach unten ab. Dauer etwa 25 Minuten.

**Regeln für alle Blöcke**

1. R sagt zuerst die **Nummer** („B zwei“), wartet 2 Sekunden und liest dann den Satz in normalem Tempo. Die Nummer
   erscheint selbst als Untertitel und markiert so, wo ein Satz beginnt.
2. Nach dem Satz schweigt R, bis H „weiter“ sagt.
3. H meldet pro Satz (mit gedrückter rechter Pinch-Geste, damit R es über Reachy hört, ausser in Block F):
   **Text** stimmt / Fehler, **Übersetzung** stimmt / Fehler, **Sprecher**-Label, **Zeit** vom Satzende bis zum
   fertigen (nicht mehr kursiven) Untertitel, gezählt in Sekunden („einundzwanzig, zweiundzwanzig …“).
4. R notiert alles in die Ergebnistabelle (unten). Bewertung: ✓ richtig, ~ kleiner Fehler (Sinn bleibt), ✗ falsch
   oder fehlt.

## 0. Vorbereitung (beide)

| | Wer | Prüfen |
|---|---|---|
| 0.1 | R | Laptop-App *Reachy Meetings*: alle Punkte grün (Robot, Camera, Captions, Door, Headset connected). Kein orangener Hinweis zu „DeepL key“. |
| 0.2 | H | App offen, Video da, Reachy **wach** (Kachel *Reachy wach*). |
| 0.3 | H | Sprach-Kachel (zeigt die Sprache, z. B. „English ›“) auf **English**, Kachel **Translate me aus** (im deutschen Menü „aus“, im englischen „off“). |
| 0.4 | R | Notieren: Datum, Raum, Laptop, App-Version (Fenstertitel), Git-Commit (`git log -1 --oneline`), Geräusche im Raum (leise / Lüftung / Gespräche nebenan). |
| 0.5 | H | Neues Meeting: App in der Brille einmal schliessen und neu öffnen (leert die Notizen). |

## A. Untertitel ohne Übersetzung (Raum spricht Englisch, H liest Englisch)

Erwartet: Untertitel erscheint zuerst kursiv (ändert sich noch), dann normal. Keine Übersetzung, kein kleiner
Originaltext darunter. Label „Speaker A“.

| Nr | R liest |
|---|---|
| A1 | Good morning everyone, can you all hear me clearly? |
| A2 | The deadline for the final report is the eleventh of January. |
| A3 | Did the headset reconnect after the Wi-Fi dropped? |

## B. Übersetzung Raum → Brille (Raum spricht Deutsch, H liest Englisch)

Erwartet: während der Übersetzung Punkte („…“), dann die **englische Übersetzung gross** und das **deutsche
Original klein** darunter. Referenz-Übersetzung zum Vergleich in der rechten Spalte (sinngemäss reicht).

| Nr | R liest | Referenz |
|---|---|---|
| B1 | Guten Morgen zusammen, wollen wir mit der Sitzung beginnen? | Good morning everyone, shall we start the meeting? |
| B2 | Das Budget für das nächste Quartal beträgt fünfzigtausend Franken. | The budget for the next quarter is fifty thousand francs. |
| B3 | Kevin zeigt nach der Pause die Demo mit dem Roboter. | After the break, Kevin will show the demo with the robot. |
| B4 | Ich schlage vor, dass wir die Benutzerstudie erst nach der Latenzmessung machen, weil wir sonst nicht wissen, welche Auflösung wir verwenden. | I suggest we only do the user study after the latency measurement, because otherwise we don't know which resolution to use. |
| B5 | Ähm, also, ich glaube … ja, das passt so. | Um, well, I think … yes, that works. |

## C. Zwei Sprecher (R und R2 abwechselnd, Deutsch)

Erwartet: R bekommt immer dieselbe Farbe und denselben Buchstaben, R2 einen anderen. Kurze Sätze unter ~1 s haben
manchmal noch kein Label (normal). Wenn die Brille die Person im Bild kennt, steht der Untertitel neben ihr.

| Nr | Wer | liest |
|---|---|---|
| C1 | R | Ich übernehme die Messung im Headset. |
| C2 | R2 | Gut, dann schreibe ich das Protokoll. |
| C3 | R | Brauchen wir dafür den Laptop von Dominic? |
| C4 | R2 | Ja, der hat Apple Silicon und ist schneller. |
| C5 | R | Perfekt, dann treffen wir uns am Donnerstag um zehn Uhr. |

## D. Zielsprache wechseln (H stellt um)

| Nr | Wer | Was | Erwartet |
|---|---|---|---|
| D1 | H | Sprach-Kachel → **Deutsch** | Liste geht auf und wieder zu, Kachel zeigt „Deutsch“. |
| D2 | R | liest Englisch: *We should order two more batteries for the headset.* | Deutsche Übersetzung gross, Englisch klein. |
| D3 | R | liest Deutsch: *Die Batterien bestelle ich morgen.* | Nur Deutsch, keine Übersetzung. |
| D4 | H | Sprach-Kachel → **English** zurück | |

## E. Eigene Stimme (H spricht, Translate me **aus**)

H hält die **rechte Pinch-Geste gedrückt**, solange er spricht. Erwartet: R hört die echte Stimme von H aus Reachy;
in der Brille erscheint die Zeile „**You**“ mit dem erkannten Text. R meldet: hörbar / leise / abgehackt.

| Nr | H sagt (Deutsch) |
|---|---|
| E1 | Hallo zusammen, ich bin heute über den Roboter dabei. |
| E2 | Könnt ihr mir die Folie mit dem Zeitplan noch einmal zeigen? |

## F. Translate me (H spricht, Reachy übersetzt)

H stellt **Translate me an**. Die Kachel zeigt dann „Translate me on → XX“ (deutsches Menü: „an → XX“): XX ist die Sprache, in der Reachy
spricht (automatisch die meistgesprochene Sprache im Raum, nach Block B–C meist **DE**). **H notiert XX.**

Damit wirklich übersetzt wird, spricht H in der *anderen* Sprache: bei → DE Englisch (F1–F3), bei → EN Deutsch
(F4–F6). H hält zum Sprechen die rechte Pinch-Geste.

Erwartet: Reachy spricht den Satz übersetzt (nicht die Stimme von H); unter der Menüzeile steht ein paar Sekunden,
was Reachy gesagt hat. **R schreibt auf, was Reachy gesagt hat**, und misst die Zeit vom Satzende von H bis Reachy
zu sprechen beginnt. Während Reachy spricht, darf **kein Untertitel von Reachys eigener Stimme** erscheinen und
Reachy darf sich nicht zu sich selbst drehen.

| Nr | H sagt | erwartet von Reachy (sinngemäss) |
|---|---|---|
| F1 | I agree, let's continue on Thursday. | Ich bin einverstanden, machen wir am Donnerstag weiter. |
| F2 | Could you move the camera a bit to the left? | Könntest du die Kamera etwas nach links bewegen? |
| F3 | Thanks, that was very helpful. | Danke, das war sehr hilfreich. |
| F4 | Ich bin einverstanden, lasst uns am Donnerstag weitermachen. | I agree, let's continue on Thursday. |
| F5 | Können wir die Präsentation auf nächste Woche verschieben? | Can we move the presentation to next week? |
| F6 | Danke, das war sehr hilfreich. | Thanks, that was very helpful. |

F4–F6 nur, wenn die Kachel → EN zeigt (z. B. nachdem R ein paar Minuten Englisch gesprochen hat). Sonst leer lassen.
Danach **Translate me aus**.

## G. Grenzfälle (Ergebnis nur notieren, kein Muss)

| Nr | Wer | Was | Bekannt |
|---|---|---|---|
| G1 | R | Schweizerdeutsch: *Chönd mer am Dunschtig wiitermache?* | ausserhalb des Projekts, Ergebnis trotzdem festhalten |
| G2 | R + R2 | sprechen gleichzeitig je einen Satz aus C | Überlappung ausserhalb des Projekts |
| G3 | R | B1 aus 3 m Entfernung, normale Lautstärke | |
| G4 | R | B1 leise (Flüstern nicht, aber gedämpft) | |
| G5 | R | Fachwörter: *Die WebRTC-Latenz liegt bei zweihundertdreiunddreissig Millisekunden.* | Zahl und Begriff prüfen |

## H. Notizen (optional, am Schluss)

| Nr | Wer | Was | Erwartet |
|---|---|---|---|
| H1 | H | Notizen-Karte öffnen | Zusammenfassung des Gesprächs, in der eingestellten Sprache. Stimmen die Punkte (Budget, Demo, Donnerstag)? |
| H2 | H | *Mail* auf der Notizen-Karte | Auf dem Laptop öffnet sich ein Mail-Entwurf mit den Notizen (ohne Empfänger). |

## Ergebnisse

Kopiere die Tabelle in eine neue Datei `docs/measurements/speech-JJJJ-MM-TT.md` (zusammen mit den Angaben aus 0.4) und
fülle sie aus. Genaue Zeiten pro Satz stehen zusätzlich im Log des Laptops:
`~/Library/Logs/reachy-meetings/backend.log` (Zeilen mit `me timing` für Block F).

| Nr | Text | Übersetzung | Sprecher | Zeit (s) | Bemerkung |
|---|---|---|---|---|---|
| A1 | | – | | | |
| A2 | | – | | | |
| A3 | | – | | | |
| B1 | | | | | |
| B2 | | | | | |
| B3 | | | | | |
| B4 | | | | | |
| B5 | | | | | |
| C1–C5 | | | Labels konstant? | | |
| D2 | | | | | |
| D3 | | – | | | |
| E1 | hörbar? | – | You | | |
| E2 | hörbar? | – | You | | |
| F (→ XX = __) | | | – | | |
| F1 | | | – | | |
| F2 | | | – | | |
| F3 | | | – | | |
| F4 | | | – | | |
| F5 | | | – | | |
| F6 | | | – | | |
| G1–G5 | | | | | |
| H1–H2 | | | | | |

Zusammenfassung in einem Satz pro Block (was gut lief, was nicht) unter die Tabelle. Für einen reproduzierbaren
Zeitvergleich ohne Personen gibt es zusätzlich `backend/scripts/bench_translate_me.py` (synthetische Sätze, siehe
seinen Docstring).
