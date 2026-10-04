"""Schritt 3: Einen Sammel-Durchgang von Hand starten.

  python -m deal_finder.sammle              # alle aktiven Suchen
  python -m deal_finder.sammle "i7 13700K"  # nur eine Suche
"""
import logging
import sys

from deal_finder.apify import Apify
from deal_finder.collector import sammle_suche
from deal_finder.config import lade_config
from deal_finder.db import sync_suchen, verbinde

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str]) -> int:
    cfg = lade_config()
    if "APIFY_TOKEN" in cfg.fehlende_geheimnisse():
        print("Fehlt: APIFY_TOKEN")
        return 1
    con = verbinde(cfg.datenbank)
    sync_suchen(con, cfg.suchen)
    apify = Apify(cfg.geheimnis("APIFY_TOKEN"))
    suchen = [s for s in cfg.suchen if s.aktiv and (not argv or s.name in argv)]
    if not suchen:
        print("Keine passende aktive Suche. Bekannt:", ", ".join(s.name for s in cfg.suchen))
        return 1
    fehler = 0
    for s in suchen:
        try:
            sammle_suche(con, cfg, apify, s)
        except Exception as e:  # noqa: BLE001
            logging.error("Suche '%s' fehlgeschlagen: %s", s.name, e)
            fehler += 1
    anzahl = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    print(f"Anzeigen in der Datenbank: {anzahl}")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
