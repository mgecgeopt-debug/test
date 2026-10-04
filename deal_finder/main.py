"""Einstieg für den Dauerbetrieb: python -m deal_finder.main

Optionen:
  --einmal   nur einen Durchgang, dann Ende (zum Testen, auch per Cron nutzbar)
"""
import logging
import sys

from deal_finder.analyst import bewerte_alle, unbewertete_ids
from deal_finder.apify import Apify
from deal_finder.config import lade_config
from deal_finder.db import sync_suchen, verbinde
from deal_finder.notifier import Telegram
from deal_finder.scheduler import Durchgang, starte_dauerbetrieb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("deal_finder")


def main(argv: list[str]) -> int:
    cfg = lade_config()
    con = verbinde(cfg.datenbank)
    sync_suchen(con, cfg.suchen)
    con.close()
    log.info("Datenbank: %s, aktive Suchen: %s", cfg.datenbank,
             ", ".join(s.name for s in cfg.suchen if s.aktiv))

    fehlend = cfg.fehlende_geheimnisse()
    if "APIFY_TOKEN" in fehlend:
        log.error("APIFY_TOKEN fehlt, ohne ihn kann der Sammler nicht laufen.")
        return 1
    telegram = None
    if {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"} & set(fehlend):
        log.warning("Telegram nicht konfiguriert, Warnungen gehen nur ins Log.")
    else:
        telegram = Telegram(cfg.geheimnis("TELEGRAM_BOT_TOKEN"), cfg.geheimnis("TELEGRAM_CHAT_ID"))

    durchgang = Durchgang(cfg, Apify(cfg.geheimnis("APIFY_TOKEN")), telegram)
    durchgang.nach_durchgang = lambda con, _: bewerte_alle(con, unbewertete_ids(con), cfg.analyst)
    if "--einmal" in argv:
        ergebnisse = durchgang.laufe()
        return 0 if all(e is not None for e in ergebnisse.values()) else 1
    starte_dauerbetrieb(durchgang)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
