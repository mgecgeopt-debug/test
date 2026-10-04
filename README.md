# Kleinanzeigen Deal-Finder

Sammelt rund um die Uhr Kleinanzeigen für festgelegte Suchbegriffe (Apify), bewertet sie
gegen den eigenen Marktpreis (SQLite + Gemini) und meldet echte Deals per Telegram.
Start-Nische: PC-Hardware.

## Schnellstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # Schlüssel eintragen
python -m deal_finder.main
```

Alle Schwellen und Suchen stehen in `config.yaml`. Geheimnisse nur in `.env`
oder Umgebungsvariablen, nie im Repository.

Tests: `pytest`
