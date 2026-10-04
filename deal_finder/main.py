"""Einstieg. Schritt 1: Config laden, Datenbank anlegen, Status ausgeben."""
import logging

from deal_finder.config import lade_config
from deal_finder.db import sync_suchen, verbinde

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("deal_finder")


def main() -> None:
    cfg = lade_config()
    con = verbinde(cfg.datenbank)
    sync_suchen(con, cfg.suchen)
    anzahl = con.execute("SELECT COUNT(*) FROM searches WHERE aktiv = 1").fetchone()[0]
    log.info("Datenbank: %s, aktive Suchen: %d", cfg.datenbank, anzahl)
    fehlend = cfg.fehlende_geheimnisse()
    if fehlend:
        log.warning("Fehlende Umgebungsvariablen: %s", ", ".join(fehlend))
    else:
        log.info("Alle Schlüssel vorhanden.")


if __name__ == "__main__":
    main()
