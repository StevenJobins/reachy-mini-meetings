# Testskript: Sprache und Übersetzung

Ein fester Ablauf, damit jeder Test gleich läuft und die Ergebnisse vergleichbar sind. Zwei Rollen:

- **R** (Raum): sitzt 1–1.5 m vor Reachy. Für Block C braucht es eine zweite Person (**R2**).
- **V** (VR-Brille): trägt die Brille.

Zwei Richtungen, beide gleich wichtig:

| | Wer spricht | Wer prüft | Was geprüft wird |
|---|---|---|---|
| **Teil 1: Raum → Brille** (A–E) | R liest vor | V meldet, was in der Brille steht | Untertitel, Übersetzung, Sprecher |
| **Teil 2: Brille → Raum** (F–J) | V liest vor | R notiert, was aus Reachy kommt | eigene Stimme, *Translate me*, Gespräch |

Beide haben dieses Dokument offen und arbeiten es von oben nach unten ab. Dauer etwa 40 Minuten. Bewertung überall:
**✓** richtig, **~** kleiner Fehler (Sinn bleibt), **✗** falsch oder fehlt.

## 0. Vorbereitung (beide)

| | Wer | Prüfen |
|---|---|---|
| 0.1 | R | Laptop-App *Reachy Meetings*: alle Punkte grün (Robot, Camera, Captions, Door, Headset connected). Kein orangener Hinweis zu „DeepL key“. |
| 0.2 | V | App offen, Video da, Reachy **wach** (Kachel *Reachy wach*). |
| 0.3 | V | Sprach-Kachel (zeigt die Sprache, z. B. „English ›“) auf **English**, Kachel **Translate me aus** (deutsches Menü „aus“, englisches „off“). |
| 0.4 | R | Notieren: Datum, Raum, Laptop, App-Version (Fenstertitel), Git-Commit (`git log -1 --oneline`), Geräusche im Raum (leise / Lüftung / Gespräche nebenan), wo V sitzt (gleicher Raum mit Kopfhörern? anderer Raum? andere Wohnung?). |
| 0.5 | V | Neues Meeting: App in der Brille einmal schliessen und neu öffnen (leert die Notizen). |

---

# Teil 1: Raum → Brille

**Regeln**

1. R sagt zuerst die **Nummer** („B zwei“), wartet 2 Sekunden und liest dann den Satz in normalem Tempo. Die Nummer
   erscheint selbst als Untertitel und markiert, wo ein Satz beginnt.
2. Nach dem Satz schweigt R, bis V „weiter“ sagt.
3. V meldet pro Satz (rechte Pinch-Geste gedrückt halten, dann hört R es aus Reachy): **Text** stimmt / Fehler,
   **Übersetzung** stimmt / Fehler, **Sprecher**-Label, **Zeit** vom Satzende bis der Untertitel fertig ist (nicht
   mehr kursiv), gezählt in Sekunden („einundzwanzig, zweiundzwanzig …“).
4. R trägt alles in Tabelle 1 ein.

## A. Untertitel ohne Übersetzung (Raum spricht Englisch, V liest Englisch)

Erwartet: Untertitel erscheint zuerst kursiv (ändert sich noch), dann normal. Keine Übersetzung, kein kleiner
Originaltext darunter. Label „Speaker A“.

| Nr | R liest |
|---|---|
| A1 | Good morning everyone, can you all hear me clearly? |
| A2 | The deadline for the final report is the eleventh of January. |
| A3 | Did the headset reconnect after the Wi-Fi dropped? |

## B. Übersetzung (Raum spricht Deutsch, V liest Englisch)

Erwartet: während der Übersetzung Punkte („…“), dann die **englische Übersetzung gross** und das **deutsche
Original klein** darunter. Die Referenz ist zum Vergleich da (sinngemäss reicht).

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

## D. Zielsprache wechseln (V stellt um)

| Nr | Wer | Was | Erwartet |
|---|---|---|---|
| D1 | V | Sprach-Kachel → **Deutsch** | Liste geht auf und wieder zu, Kachel zeigt „Deutsch“. |
| D2 | R | liest Englisch: *We should order two more batteries for the headset.* | Deutsche Übersetzung gross, Englisch klein. |
| D3 | R | liest Deutsch: *Die Batterien bestelle ich morgen.* | Nur Deutsch, keine Übersetzung. |
| D4 | V | Sprach-Kachel → **English** zurück | |

## E. Grenzfälle im Raum (Ergebnis nur notieren, kein Muss)

| Nr | Wer | Was | Bekannt |
|---|---|---|---|
| E1 | R | Schweizerdeutsch: *Chönd mer am Dunschtig wiitermache?* | ausserhalb des Projekts, Ergebnis trotzdem festhalten |
| E2 | R + R2 | sprechen gleichzeitig je einen Satz aus C | Überlappung ausserhalb des Projekts |
| E3 | R | B1 aus 3 m Entfernung, normale Lautstärke | |
| E4 | R | B1 gedämpft (nicht flüstern) | |
| E5 | R | Fachwörter: *Die WebRTC-Latenz liegt bei zweihundertdreiunddreissig Millisekunden.* | Zahl und Begriff prüfen |

---

# Teil 2: Brille → Raum

**Regeln**

1. V hält die **rechte Pinch-Geste gedrückt**, solange er spricht, und lässt nach dem Satz los. Nur in Block F sagt
   V zuerst die Nummer. Ab Block G (*Translate me* an) **keine Nummern**: Reachy würde sie mitübersetzen. R folgt
   einfach der Reihenfolge der Tabelle.
2. R **spricht keine Rückmeldung** (das würde als Raum-Sprache gezählt und die Sprache von Reachy umstellen, siehe
   unten). R schreibt in Tabelle 2 und sagt nur „weiter“ (in Block H: „next“).
3. R bewertet pro Satz: **verstanden** (klar und laut genug), **richtig** (gegen die Referenz), **vollständig**
   (Anfang und Ende da), bei *Translate me* zusätzlich **Stimme** (natürlich / roboterhaft / stockend).
4. V misst die **Zeit** vom eigenen Satzende bis der Text unter der Menüzeile erscheint (das ist ungefähr der
   Moment, in dem Reachy loslegt). Die genauen Zeiten stehen im Log (Zeilen `me timing`, siehe unten).

**In welcher Sprache spricht Reachy?** Die Brille stellt das nicht ein. Reachy nimmt die Sprache, die im Raum in den
letzten 10 Minuten **am häufigsten** gesprochen wurde (gezählt nach Äusserungen, auch die gesprochenen Nummern).
Nach Teil 1 ist das Deutsch. Die Kachel zeigt sie als „Translate me on → XX“ (deutsches Menü „an → XX“), aber erst
nach dem ersten übersetzten Satz. Deshalb beginnt jeder Block mit einem Aufwärmsatz.

## F. Eigene Stimme (Translate me **aus**)

Erwartet: R hört die **echte Stimme von V** aus Reachy. In der Brille erscheint die Zeile „**You**“ mit dem
erkannten Text. Während V spricht, entsteht kein Untertitel vom Raum (das Raum-Mikrofon pausiert).

| Nr | Was V tut | V sagt (Deutsch) | R prüft |
|---|---|---|---|
| F1 | normal sprechen | Hallo zusammen, ich bin heute über den Roboter dabei. | verstanden, Lautstärke |
| F2 | leise sprechen | Könnt ihr mir die Folie mit dem Zeitplan noch einmal zeigen? | noch verständlich? |
| F3 | **sofort** nach dem Drücken lossprechen | Erstens, das Budget ist bestätigt. | fehlt das erste Wort? |
| F4 | **mitten im Satz loslassen** (nach „Woche“) | Ich bin nächste Woche leider nicht da, weil ich an einer Konferenz bin. | Ende muss fehlen, kein Rest hörbar |

## G. Translate me, Englisch → Deutsch

V stellt **Translate me an**. Erwartet: Reachy spricht die **deutsche Übersetzung** (nicht die Stimme von V), und unter
der Menüzeile steht ein paar Sekunden, was Reachy gesagt hat. **R schreibt auf, was Reachy wörtlich gesagt hat.**
Während Reachy spricht, darf **kein Untertitel von Reachys eigener Stimme** erscheinen, und Reachy darf sich nicht
zu sich selbst drehen.

| Nr | V sagt | Referenz (sinngemäss) |
|---|---|---|
| G0 | Hello everyone. *(Aufwärmen: V liest danach „→ XX“ ab und notiert es. Erwartet „→ DE“. Zeigt es etwas anderes, Block G mit den deutschen Sätzen aus H machen.)* | Hallo zusammen. |
| G1 | Could you move the camera a bit to the left? | Könntet ihr die Kamera etwas nach links bewegen? |
| G2 | Let's meet at two thirty on the fourteenth of December. | Treffen wir uns am vierzehnten Dezember um halb drei. |
| G3 | Please send the slides to Dominic and Kevin. | Bitte schickt die Folien an Dominic und Kevin. |
| G4 | I think the latency is fine for now, but we should measure it again in the headset before the user study. | Ich finde die Latenz vorerst in Ordnung, aber wir sollten sie vor der Nutzerstudie nochmals in der Brille messen. |
| G5 | Um, well … I'm not sure, maybe next week? | Ähm, also … ich bin nicht sicher, vielleicht nächste Woche? |
| G6 | *zwei Sätze direkt hintereinander, ohne loszulassen:* First, the budget is approved. Second, the demo is on Thursday. | Reihenfolge muss stimmen: zuerst Budget, dann Demo. |

## H. Translate me, Deutsch → Englisch

Damit Reachy Englisch spricht, wird die Sprach-Zählung zurückgesetzt:

1. R **beendet die Laptop-App** *Reachy Meetings* und öffnet sie wieder (Reachy Mini Control bleibt offen). Warten,
   bis wieder alles grün ist. Die Brille verbindet sich von selbst neu.
2. R liest **auf Englisch** zum Aufwärmen: *H zero. Good afternoon. We are continuing in English now. Can you hear us?*
3. Ab jetzt sagt R nur noch „next“ (nicht „weiter“).

| Nr | V sagt | Referenz (sinngemäss) |
|---|---|---|
| H0 | Hallo zusammen. *(Aufwärmen: V notiert „→ XX“. Erwartet „→ EN“. Zeigt es „→ DE“, ist die Zählung nicht zurückgesetzt: Schritt 1 wiederholen.)* | Hello everyone. |
| H1 | Könnt ihr die Kamera etwas nach links drehen? | Could you turn the camera a bit to the left? |
| H2 | Treffen wir uns am vierzehnten Dezember um halb drei. | Let's meet at half past two on the fourteenth of December. |
| H3 | Bitte schickt die Folien an Dominic und Kevin. | Please send the slides to Dominic and Kevin. |
| H4 | Ich finde die Latenz vorerst in Ordnung, aber wir sollten sie vor der Nutzerstudie nochmals in der Brille messen. | I think the latency is fine for now, but we should measure it again in the headset before the user study. |
| H5 | Ähm, also … ich bin nicht sicher, vielleicht nächste Woche? | Um, well … I'm not sure, maybe next week? |
| H6 | *zwei Sätze direkt hintereinander:* Erstens ist das Budget bestätigt. Zweitens ist die Demo am Donnerstag. | Reihenfolge: Budget, dann Demo. |

## I. Gespräch hin und zurück (Englisch im Raum, V mit Translate me an, Sprach-Kachel auf **Deutsch**)

Ein kurzes echtes Gespräch über beide Richtungen. Erwartet: V spricht Deutsch, Reachy sagt es auf Englisch; R
antwortet auf Englisch, V liest die Antwort **deutsch übersetzt**. R misst mit dem Handy die Zeit von seiner Frage
bis zum Anfang von Reachys Antwort (ein ganzer Wechsel).

| Nr | Wer | sagt |
|---|---|---|
| I1 | R | Can you see the whiteboard from where Reachy is? |
| I2 | V | Ja, aber die Schrift ist zu klein. Kann jemand näher heranzoomen? |
| I3 | R | We can't zoom, but we can move Reachy closer. Is that better? |
| I4 | V | Ja, jetzt kann ich alles lesen, danke. |

Danach Sprach-Kachel zurück auf **English** und **Translate me aus**.

## J. Grenzfälle der Brille (Ergebnis nur notieren, kein Muss)

| Nr | Was | Bekannt |
|---|---|---|
| J1 | V spricht mit Translate me an Schweizerdeutsch: *Chönd mer am Dunschtig wiitermache?* | ausserhalb des Projekts |
| J2 | V spricht (Translate me aus), während R gleichzeitig einen Satz aus A liest | Überlappung ausserhalb des Projekts; hört R beide? gibt es Untertitel von R? |
| J3 | V spricht einen langen Satz (~15 s) mit Translate me an, z. B. B4 zweimal hintereinander | kommt alles an? wie lange dauert es? |
| J4 | Lärm bei V (Musik oder Fernseher im Hintergrund), Satz F1 mit Translate me aus | stört es die Erkennung der „You“-Zeile? |

## K. Notizen (optional, am Schluss)

| Nr | Wer | Was | Erwartet |
|---|---|---|---|
| K1 | V | Notizen-Karte öffnen | Zusammenfassung des Gesprächs in der eingestellten Sprache. Stimmen die Punkte (Budget, Demo, Donnerstag, Whiteboard)? |
| K2 | V | *Mail* auf der Notizen-Karte | Auf dem Laptop öffnet sich ein Mail-Entwurf mit den Notizen (ohne Empfänger). |

---

# Ergebnisse

Kopiere beide Tabellen in eine neue Datei `docs/measurements/speech-JJJJ-MM-TT.md` (zusammen mit den Angaben aus 0.4)
und fülle sie aus. Genaue Zeiten für Teil 2 im Log des Laptops: `~/Library/Logs/reachy-meetings/backend.log`, Zeilen
mit `me timing` (wann die Arbeit begann, Whisper, DeepL, Sprachsynthese, Start der Wiedergabe).

**Tabelle 1: Raum → Brille** (V meldet, R notiert)

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
| E1–E5 | | | | | |

**Tabelle 2: Brille → Raum** (R notiert, V misst die Zeit)

| Nr | Reachy sagte (wörtlich) | verstanden | richtig | vollständig | Stimme | Zeit (s) | Bemerkung |
|---|---|---|---|---|---|---|---|
| F1 | (Stimme von V) | | – | | – | – | |
| F2 | (Stimme von V) | | – | | – | – | |
| F3 | (Stimme von V) | | – | erstes Wort? | – | – | |
| F4 | (Stimme von V) | | – | Ende weg? | – | – | |
| G0 (→ XX = __) | | | | | | | |
| G1 | | | | | | | |
| G2 | | | | | | | |
| G3 | | | | | | | |
| G4 | | | | | | | |
| G5 | | | | | | | |
| G6 | | | | Reihenfolge? | | | |
| G: Untertitel von Reachys eigener Stimme? | | | | | | | ja / nein |
| H0 (→ XX = __) | | | | | | | |
| H1 | | | | | | | |
| H2 | | | | | | | |
| H3 | | | | | | | |
| H4 | | | | | | | |
| H5 | | | | | | | |
| H6 | | | | Reihenfolge? | | | |
| I1–I4 | Gesprächswechsel: ___ s | | | | | | |
| J1–J4 | | | | | | | |
| K1–K2 | | | | | | | |

Zusammenfassung in einem Satz pro Block (was gut lief, was nicht) unter die Tabellen. Für einen reproduzierbaren
Zeitvergleich ohne Personen gibt es zusätzlich `backend/scripts/bench_translate_me.py` (synthetische Sätze, siehe
seinen Docstring).
