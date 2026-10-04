# Kleinanzeigen Deal-Finder

Sammelt rund um die Uhr Kleinanzeigen für festgelegte Suchbegriffe (über Apify),
speichert alles in einer SQLite-Datenbank, berechnet daraus den Marktpreis je Produkt,
prüft auffällig günstige Anzeigen mit Gemini (Text und Bilder) und meldet echte Deals
per Telegram, mit fertigem Verhandlungstext. Verkäufer anschreiben bleibt Handarbeit.

Start-Nische: PC-Hardware (i5 12600K, i7 13700K, RTX 3070).

## Wie es arbeitet

```
Apify-Task je Suche ──> Sammler ──> SQLite (alle Anzeigen, Preisverlauf)
                                       │
                                       ▼
                        Analyst Stufe 1: Vorfilter, Marktpreis, Marge (ohne KI)
                                       │  Kandidaten
                                       ▼
                        Stufe 2+3: Gemini prüft Text und Bilder, Score 0–10
                                       │  Deals
                                       ▼
                        Telegram: Bild, Zahlen, Risiken, Verhandlungstext,
                        Knöpfe „Angeschrieben / Gekauft / Uninteressant“
```

Ein Durchgang läuft alle `intervall_minuten` (Start: 60), nachts 0–7 Uhr nicht.
Erster Lauf je Suche ist die Komplettaufnahme („Tag Null“, 200 Anzeigen), danach
liefert Apify nur noch neue, geänderte und verschwundene Anzeigen.

## Einrichtung (einmalig)

### 1. Konten und Schlüssel

| Dienst | Was du brauchst | Wo |
|---|---|---|
| Apify | API-Token | apify.com → Settings → API & Integrations. Dort auch ein Ausgabenlimit setzen (z. B. 10 $/Monat). |
| Gemini | API-Key | aistudio.google.com → Get API key |
| Telegram | Bot-Token | In Telegram @BotFather → `/newbot`. Dem neuen Bot danach einmal „Hallo“ schreiben. |
| Telegram | Chat-ID | @userinfobot → `/start` zeigt deine Id |

Die Apify-Tasks legt das Programm beim ersten Lauf selbst an (eine je Suche,
Name `deal-finder-<suche>`, 1 GB Speicher).

### 2. Installation auf Windows

1. Python 3.11 installieren: https://www.python.org/downloads/ (beim Setup
   **„Add python.exe to PATH“** anhaken).
2. Dieses Repository als ZIP laden (GitHub → Code → Download ZIP) oder mit
   `git clone` holen und in einen Ordner entpacken, z. B. `C:\deal-finder`.
3. Im Ordner die Datei `.env.example` kopieren, Kopie in `.env` umbenennen und
   die vier Schlüssel eintragen.
4. `start.bat` doppelklicken. Beim ersten Mal werden die Pakete installiert
   (1–2 Minuten). Danach läuft das Programm, das Log liegt in `logs\deal_finder.log`.

**Autostart:** `autostart_einrichten.bat` doppelklicken. Der Deal-Finder startet dann
bei jeder Anmeldung unsichtbar im Hintergrund. Stoppen mit `stopp.bat`.
Damit es durchläuft: Energieoptionen → Ruhezustand/Standby aus (Bildschirm aus ist ok).

**Nur einen Durchgang testen:** in der Eingabeaufforderung im Ordner
`.venv\Scripts\python -m deal_finder.main --einmal`

### 3. Installation auf Linux / Raspberry Pi

```bash
git clone <repo-url> deal-finder && cd deal-finder
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # Schlüssel eintragen
.venv/bin/python -m deal_finder.main --einmal   # Test
```

Dauerbetrieb als Dienst: siehe `deploy/deal-finder.service` (Pfade anpassen, dann
`sudo systemctl enable --now deal-finder`).

### 4. Erste Woche

Nach dem Start: eine Woche nur sammeln lassen. Erst dann gibt es genug
Vergleichsanzeigen für verlässliche Marktpreise (mindestens 8 je Produkt), vorher
steht in den Bewertungen „zu wenig Daten“. Danach `intervall_minuten` auf 30 senken,
wenn gewünscht.

## Bedienung

Alles Einstellbare steht in `config.yaml`:

| Einstellung | Startwert | Bedeutung |
|---|---|---|
| `suchen` | i5 12600K, i7 13700K, RTX 3070 | Neue Suche = neue Zeile mit `name` und `produkt_key`. Pausieren: `aktiv: false`. |
| `intervall_minuten` | 60 | Abstand der Durchgänge |
| `ruhezeit_von` / `_bis` | 0 / 7 | Keine Läufe in diesem Zeitfenster |
| `mindestabstand_prozent` | 25 | Preis + Versand muss so weit unter Markt liegen |
| `mindestmarge_euro` | 30 | Marge = Markt × 0,95 − Einkauf − Versand − 6 € |
| `mindest_ki_score` | 7 | Gemini-Score 0–10 |
| `mindest_vergleichsanzeigen` | 8 | Darunter: „zu wenig Daten“ |
| `marktpreis_tage` | 30 | Zeitraum für den Median |
| `angebotsfaktor` | 0.80 | Verhandlungsangebot = 80 % des Preises |
| `bundles_melden` | false | Bundles werden gespeichert, aber nicht gemeldet |

Änderungen gelten nach Neustart (`stopp.bat`, dann `start.bat`).

**Telegram-Knöpfe:** Jede Meldung hat „Angeschrieben“, „Gekauft“, „Uninteressant“.
Die Antwort landet in der Datenbank (`evaluations.carl_reaktion`). Damit lässt sich
später prüfen, welche Schwellen wirklich gute Deals liefern.

**Warnungen:** Fällt eine Apify-Suche zweimal hintereinander aus, kommt eine
Telegram-Warnung. Die anderen Suchen laufen weiter.

## Einzelne Schritte von Hand

```
python -m deal_finder.telegram_test        # Testnachricht an dich
python -m deal_finder.sammle "i7 13700K"   # eine Suche sammeln
python -m deal_finder.main --einmal        # ein kompletter Durchgang inkl. Analyse
pytest                                     # Tests (ohne Netz, ohne Schlüssel)
```

## Datenbank

`data/deal_finder.sqlite`, vier Tabellen: `searches`, `listings` (jede Anzeige einmal,
Kleinanzeigen-ID als Schlüssel, komplette Apify-Zeile in `raw_json`), `price_history`,
`evaluations` (Marktpreis, Abstand, Marge, KI-Score, Risiken, Reaktion). Ansehen z. B.
mit „DB Browser for SQLite“.

## Kosten (Schätzung)

3 Suchen stündlich 7–24 Uhr: ca. 4–5 $/Monat Apify, Gemini unter 1 €, Telegram
kostenlos. Bei 30 Minuten etwa das Doppelte. Rechner zu Hause: 0 €.

## Grenzen

- Kein automatisches Anschreiben: verstößt gegen die Kleinanzeigen-Nutzungsbedingungen.
- Der Marktpreis ist ein Angebotspreis, kein Verkaufspreis. Wie schnell Anzeigen
  verschwinden, ist der bessere Hinweis.
- Ändert sich der Apify-Actor oder blockiert Kleinanzeigen, fällt der Sammler aus,
  deshalb die Warnung per Telegram.

Schlüssel stehen nur in `.env` (steht in `.gitignore`) oder in Umgebungsvariablen,
nie im Code oder im Repository.
