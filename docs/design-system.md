# Designsystem Routenplaner

Grundlage: **Gestaltungshandbuch der Metropolregion Hamburg** (Stylequide MRH, 28 Seiten, Stand 20.09.2016) und der bestehende Planer (`map/static/route.css`, `route.js`).

## Wichtig vorab

- Das Handbuch ist ein **Print-Regelwerk der Metropolregion Hamburg**, kein HVV-Handbuch und keine Web-Vorlage. Übernommen werden die Farben, die Typografie-Regeln und das Layout-Denken (Weißraum, linksbündig, sparsame Akzente). Pixel- und Prozentmaße für Druckformate (Band 5 %, Bild 52 %, Bug 75 %) gelten für das Web nicht.
- **Nicht verwendet** werden die Wort-Bild-Marke, der rote Schiffsbug, das blaue Band mit Textkasten und die Bilder. Sie sind geschützte Kennzeichen der Metropolregion und würden den Planer wie ein offizielles Angebot aussehen lassen (Handbuch S. 4, 6–8).
- **HamburgSans** ist laut Handbuch (S. 11) nur über den Hamburger Mediaserver erhältlich. Die Lizenz für den Einsatz in Web-Apps ist ungeklärt. Das Handbuch nennt Arial als Ersatz (S. 12); der Planer nutzt deshalb den Systemfont-Stack und Arial als Rückfall.

## 1. Tokens

### Farben (aus dem Handbuch, S. 9–10)

| Token | Wert | Quelle | Kontrast auf Weiß | Einsatz im Planer |
|---|---|---|---|---|
| `--mrh-blau` | `#003064` | Dunkelblau, „Schrift- und Gestaltungsfarbe“ | 13,1 : 1 | Primärfarbe: Hauptknopf, Links, Überschriften |
| `--mrh-cyan` | `#40C0F0` | Logofarbe | 2,1 : 1 | nur Fläche/Deko (Fokusring, Balken), **nie Text** auf Weiß |
| `--mrh-rot` | `#E63328` | Logofarbe | 4,3 : 1 | Ausfall-Balken und Schiffsbug; als Text nur abgedunkelt `#b3241a` |
| `--mrh-gruen` | `#80BA27` | Logofarbe | 2,3 : 1 | Flächen („pünktlich“-Balken), nicht als Text |
| `--mrh-gruen-dunkel` | `#008035` | Alternativfarbe | 5,1 : 1 | Vorlage für Text „pünktlich“ (im Planer `#00722f` auf hellgrünem Grund, 5,3 : 1) |
| `--mrh-gold` | `#E4A519` | Alternativfarbe | 2,2 : 1 | Flächen für „leicht verspätet“, Text nur dunkel darauf |
| `--mrh-violett` | `#771C7F` | Alternativfarbe | 9,3 : 1 | Hervorhebung, sparsam |
| `--mrh-braun` | `#8E3322` | Alternativfarbe | 7,9 : 1 | Textfarbe für „stark verspätet“ auf hellem Grund |

Regeln aus dem Handbuch: Hauptfarben haben Vorrang, Alternativfarben werden **sparsam** und nie ganzflächig eingesetzt, Abstufungen sind in 40/70/100 % Farbton erlaubt (S. 10). Störer und Auszeichnungen in den drei Logofarben (S. 9).

Semantik im Planer (Pünktlichkeit) – feste Zuordnung, damit Farbe nie allein informiert (immer zusätzlich Text/Zahl):

| Zustand | Hintergrund (Pille) | Text/Symbol |
|---|---|---|
| gut (≥ 90 %) | 15 % Grün `#80BA27` | `--mrh-gruen-dunkel` |
| mittel (75–89 %) | 20 % Gold `#E4A519` | dunkles Braun |
| schlecht (< 75 %), Ausfall | 12 % Rot | `--mrh-braun` bzw. `--mrh-rot` ab 18 px fett |

Dunkelmodus: Das Handbuch kennt keinen. Dunkelblau ist auf dunklem Grund unbrauchbar (1,4 : 1). Im Dunkelmodus werden stattdessen Cyan (8,9 : 1) als Primärfarbe und Gold (8,6 : 1) bzw. Grün (7,9 : 1) als Textfarben genutzt.

### Typografie

| Rolle | Schrift | Gewicht | Größe/Zeile |
|---|---|---|---|
| Überschrift (H1) | HamburgSans → Arial | Bold | 26 px / 1,15 (ab 720 px: 32 px) |
| Zwischenüberschrift, Haltestelle | wie H1 | Bold/Semibold | 17 px / 1,3 |
| Fließtext, Hinweise | HamburgSans Regular → Arial | Regular | 16 px / 1,4; Hinweise 13–14 px |
| Hervorhebung im Text | Italic bzw. Bold | – | Handbuch: Hervorhebung durch Schriftstärke, Bildunterschriften in Italic |
| Zeiten, Zahlen | wie Fließtext | Bold, `tabular-nums` | 17–24 px |

Handbuch: linksbündig, keine Versalien in Headlines (S. 19), Fließtext mindestens 12 pt bei Barrierefreiheit (≈ 16 px auf dem Handy). Eingabefelder bleiben bei 16 px oder mehr (sonst zoomt iOS).

### Abstände, Radien, Schatten

Aus der Praxis des Planers (Handbuch gibt nur Druckränder vor: „Seitenränder einhalten“, viel Weißraum):

| Token | Wert |
|---|---|
| Abstandsskala | 4 · 8 · 12 · 14 · 16 · 24 · 40 px |
| `--tap` (Mindest-Tastfläche) | 48 px (nie unter 44 px) |
| Radius Karte / Feld / Pille | 18 px / 14 px / 12 px |
| Seitenrand | 14 px (≤ 380 px: 10 px), Spalte höchstens 640 px |
| Schatten Karte | `0 1px 2px rgb(15 23 42 / 8%), 0 4px 14px rgb(15 23 42 / 6%)` |
| Bewegung | 0,15 s für Auf-/Zuklappen, 0,9 s Drehung (Laden), 1,8 s Funkwelle; bei „Bewegung reduzieren“ alles aus |

## 2. Komponenten

Jeweils: Zweck · Zustände · Barrierefreiheit. Quelle ist `route.css`/`route.js`.

| Komponente | Zweck | Zustände | Barrierefreiheit |
|---|---|---|---|
| **Ortsfeld** (`.field`, Combobox) | Start/Ziel: Haltestelle, Adresse, Ort, Standort | leer · Fokus (Rand + 3 px Ring) · gefüllt (Löschen-Knopf) · Standort sucht (drehendes Symbol) · Vorschlagsliste offen | `role=combobox`, `aria-expanded`, `aria-activedescendant`, Pfeiltasten, Esc schließt |
| **Umschalter** (`.seg`) | Abfahrt/Ankunft, zwei gleich breite Hälften | gewählt · nicht gewählt | `role=radiogroup`/`radio`, `aria-checked` |
| **Datum + „Jetzt“** (`.when`) | Zeitpunkt, immer zweizeilig (nie breiter als die Karte) | Standard · „Jetzt“ hält den Zeitpunkt aktuell | Label per `aria-label`, native Eingabe |
| **Hauptknopf** (`.primary`) | „Verbindungen suchen“ | normal · gedrückt (heller) · gesperrt/lädt | native Schaltfläche, 54 px hoch |
| **Sekundärknopf** (`.refresh`, `.chip-btn`) | Aktualisieren, Jetzt | normal · gedrückt · lädt (Symbol dreht) · gesperrt | ≥ 44 px, sichtbarer Fokus |
| **Aktualitätszeile** (`.age`) | „Echtzeit-Stand: vor 12 s“ + Aktualisieren; automatisch alle 30 s | zählt · aktualisiert · Fehler (rote Zeile, alte Daten bleiben) | `aria-live="off"` (Zähler wird nicht vorgelesen) |
| **Fahrtkarte** (`.trip`) | eine Verbindung: Zeiten, Dauer, Linienschilder, Pillen | zugeklappt · aufgeklappt (Chevron dreht) | Kopf ist Knopf mit `aria-expanded` |
| **Pille** (`.pill`) | Kennzahl: Umstiege, % pünktlich, Ausfall, Anschluss | neutral · gut · mittel · schlecht | Farbe nie allein: immer Text/Zahl; darf umbrechen |
| **Zeitleiste** (`.tl`) | Halt – Fahrt – Umstieg, Schiene in Linienfarbe | Abfahrt · Ankunft · Zwischenhalt · Umstieg (gepunktet) | Zeiten `tabular-nums`; Verspätung als Zahl (`+4 min`) |
| **Linienschild** (`.lb`) | Logo der Linie aus der HVV-Quelle | Bild · Textschild als Ersatz bei Ladefehler | `alt` = Liniennummer |
| **Echtzeit-Symbol** (`.live`) | Antenne neben der Zeit: Zeit ist Prognose | animiert (zwei Wellen nacheinander) · still bei „Bewegung reduzieren“ | dekorativ (`aria-hidden`) |
| **Zuverlässigkeitsbox** (`.rel`, `.meter`) | Balken: pünktlich, Anschlüsse, Ausfallrisiko | grün/gelb/rot nach Schwelle | Zahl steht neben dem Balken, Hinweistext erklärt Datenbasis |
| **Meldung** (`.msg`) | Fehler, leere Suche | Fehler (roter Titel) · neutral · Wiederholen-Knopf | `aria-live="polite"` am Ergebnisbereich |

### Linienschilder (festgelegte Regel)

- Die Schilder der HVV-Quelle sind **2,5 : 1 bis 3 : 1** (z. B. 160 × 64, 192 × 64). Höhe festlegen, Breite `auto`; **nie** Breite und Höhe gleichzeitig festlegen oder `max-width` setzen. Das hatte sie auf der Schiene gestaucht.
- Höhe: 24 px in Karte und Kopf, 20 px auf der Schiene (18 px ab ≤ 380 px). Die Schiene ist 60 px breit (54 px), damit ein Schild hineinpasst.
- RE/RB-Schilder sind hell und bekommen einen weißen Grund mit 1 px Innenabstand.

## 3. Muster

- **Ergebnisliste:** Aktualitätszeile, dann Fahrtkarten untereinander, die erste ist aufgeklappt. „Von … nach …“ steht nicht noch einmal da, es steht schon in den Eingabefeldern.
- **Anschluss-Hinweis:** Pille an der Umstiegszeile; der Text darf umbrechen, damit nichts über den Rand läuft.
- **Fehlende Messdaten:** immer benennen, welcher Abschnitt betroffen ist („Für U3 (Rathaus → Hauptbahnhof Süd) erfasst der Dienst keine Ausfälle“), nie pauschal.
- **Layout-Prinzip aus dem Handbuch:** viel Weißraum, linksbündig, Akzentfarbe sparsam, die Primärfarbe trägt Schrift und Knöpfe, die hellen Logofarben nur als Flächen.

## 4. Audit des Ist-Stands (`route.css`, 16.11.2026)

| Bereich | Befund | Bewertung |
|---|---|---|
| Namen | Klassen deutsch/englisch gemischt (`.age`, `.refresh`, `.sname`, `.tm`, `.xfer`) | 🟡 Kurzformen dokumentiert (Tabelle oben), nicht umbenannt |
| Farb-Tokens | 18 Tokens in `:root` und im Dunkelmodus; **33 verschiedene Hex-Werte** insgesamt, davon die meisten in den Token-Blöcken, einzelne (Linienschilder, Schatten, Meter-Gelb `#e0a21c`, Schildgrau `#c9d0dc`, `#1b2636`) | 🟡 Meter-Gelb und Schilder-Grau auf Tokens ziehen |
| Primärfarbe | `#0a4fb8` (7,4 : 1) statt Dunkelblau `#003064` des Handbuchs | 🟡 Wechsel möglich, siehe unten |
| Abstände | keine Skala als Tokens, Werte frei (6, 8, 10, 12, 14 px …) | 🟡 Skala oben festgelegt, Umsetzung offen |
| Typografie | Systemfont-Stack, Größen 11–32 px als feste Werte | 🟢 passt zum Arial-Rückfall des Handbuchs |
| Zustände | Fokus (`:focus-visible`), gedrückt, gesperrt, Laden vorhanden; Hover nur am Löschen-Knopf | 🟢/🟡 |
| Barrierefreiheit | Kontraste im Hellmodus ≥ 4,5 : 1, Tastflächen ≥ 44 px, `prefers-reduced-motion` | 🟢 |
| Breite | Raster mit `minmax(0, 1fr)`, `overflow-x: clip` als Netz | 🟢 (auf dem Handy bestätigt zu prüfen) |

**Wert: etwa 78/100.** Die größten Lücken sind fehlende Spacing-Tokens und verstreute Hex-Werte.

## 5. Umsetzung (16.11.2026, angewendet in `route.css`)

Entscheidung des Auftraggebers: Der Planer passt optisch zur Metropolregion Hamburg und enthält Gestaltungselemente aus Hamburg. HamburgSans darf nicht verwendet werden, ein HVV-Handbuch gibt es nicht.

- **Palette:** Dunkelblau `#003064` ist Primär-, Schrift- und Knopffarbe. Cyan `#40C0F0` ist Glühen des Fokusrings und im Dunkelmodus die Primärfarbe. Text in Grün, Gold und Rot ist abgedunkelt (`#00722f`, `#7a4b00`, `#b3241a`, Kontrast ≥ 4,5 : 1), die Originalfarben stehen als `--good-fill`, `--mid-fill`, `--bad-fill` für Balken und Flächen. Wer die Werte ändert, wird vom Test `tests/js/route.test.js` auf unzureichenden Kontrast hingewiesen.
- **Schrift:** Arial (Rückfall Liberation Sans/Helvetica) als vom Handbuch genannter Ersatz für HamburgSans. Überschriften fett, linksbündig, keine Versalien.
- **Hamburger Elemente:** Dunkelblaues Band am Seitenende über die ganze Breite und roter Schiffsbug links mittig auf der Oberkante (75 % der Bandhöhe, 3,5 × so breit wie hoch, frei von Text), als eigene Nachzeichnung in CSS. **Nicht** enthalten sind Wort-Bild-Marke und Textkasten „Metropolregion Hamburg“. Das Angebot ist kein offizielles Angebot der Metropolregion; die Marke würde das nahelegen.
- **Dunkelmodus:** Abgedunkelte Blautöne als Flächen, Cyan als Primärfarbe (das Handbuch kennt keinen Dunkelmodus, die Werte sind abgeleitet).
- **Noch offen:** Abstands-Tokens `--s1…--s7` und die restlichen festen Hex-Werte der Linienschilder (`#fff`, `#c9d0dc`, `#1b2636`).

## 6. Offene Fragen

Beantwortet am 16.11.2026: Der Planer folgt dem Handbuch der Metropolregion, HamburgSans ist nicht erlaubt (Arial als Ersatz), ein HVV-Handbuch existiert nicht.

- Soll der rote Bug zusätzlich im Kopf der Seite oder als Ladeanimation auftauchen? Das Handbuch sieht ihn nur am Bandrand vor.
- Soll die Metropolregion als Herausgeber genannt werden? Dafür bräuchte es ihre Zustimmung.
