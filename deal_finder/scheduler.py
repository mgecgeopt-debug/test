"""Zeitsteuerung: Durchgang je Intervall, Ruhezeit, eine Wiederholung, Warnung per Telegram."""
import logging
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from deal_finder.apify import Apify
from deal_finder.collector import Ergebnis, sammle_suche
from deal_finder.config import Config
from deal_finder.db import sync_suchen, verbinde
from deal_finder.notifier import Telegram

log = logging.getLogger(__name__)


def in_ruhezeit(stunde: int, von: int, bis: int) -> bool:
    """True, wenn `stunde` im Fenster [von, bis) liegt. Fenster darf über Mitternacht gehen (z. B. 23–7)."""
    if von == bis:
        return False
    if von < bis:
        return von <= stunde < bis
    return stunde >= von or stunde < bis


class Durchgang:
    """Ein Sammel-Durchgang über alle aktiven Suchen, mit Wiederholung und Warnung."""

    def __init__(self, cfg: Config, apify: Apify, telegram: Telegram | None,
                 schlafen=time.sleep, jetzt=datetime.now):
        self.cfg = cfg
        self.apify = apify
        self.telegram = telegram
        self._schlafen = schlafen
        self._jetzt = jetzt
        self.nach_durchgang = None  # Hook für den Analysten (später): callable(con, Ergebnisse)

    def warne(self, text: str) -> None:
        log.error(text)
        if self.telegram:
            self.telegram.warnung(text)

    def _sammle_mit_wiederholung(self, con, suche) -> Ergebnis | None:
        s = self.cfg.sammler
        try:
            return sammle_suche(con, self.cfg, self.apify, suche)
        except Exception as e:  # noqa: BLE001
            log.warning("Suche '%s' fehlgeschlagen (%s), Wiederholung in %d Min.",
                        suche.name, e, s["wiederholung_nach_minuten"])
        self._schlafen(s["wiederholung_nach_minuten"] * 60)
        try:
            return sammle_suche(con, self.cfg, self.apify, suche)
        except Exception as e:  # noqa: BLE001
            self.warne(f"Sammler: Suche „{suche.name}“ zweimal fehlgeschlagen.\n{type(e).__name__}: {e}")
            return None

    def laufe(self) -> dict[str, Ergebnis | None]:
        """Wird vom Scheduler aufgerufen. Gibt je Suche das Ergebnis zurück (None = fehlgeschlagen)."""
        s = self.cfg.sammler
        stunde = self._jetzt().hour
        if in_ruhezeit(stunde, s["ruhezeit_von"], s["ruhezeit_bis"]):
            log.info("Ruhezeit (%d Uhr), kein Durchgang.", stunde)
            return {}
        con = verbinde(self.cfg.datenbank)
        try:
            sync_suchen(con, self.cfg.suchen)
            ergebnisse = {}
            for suche in self.cfg.suchen:
                if suche.aktiv:
                    ergebnisse[suche.name] = self._sammle_mit_wiederholung(con, suche)
            if self.nach_durchgang:
                try:
                    self.nach_durchgang(con, ergebnisse)
                except Exception as e:  # noqa: BLE001
                    self.warne(f"Analyst fehlgeschlagen: {type(e).__name__}: {e}")
            return ergebnisse
        finally:
            con.close()


def starte_dauerbetrieb(durchgang: Durchgang) -> None:
    """Blockierender Dauerprozess: sofort ein Durchgang, danach alle intervall_minuten."""
    minuten = durchgang.cfg.sammler["intervall_minuten"]
    sched = BlockingScheduler(timezone=datetime.now().astimezone().tzinfo)
    sched.add_job(durchgang.laufe, "interval", minutes=minuten, next_run_time=datetime.now(),
                  max_instances=1, coalesce=True, misfire_grace_time=600)
    log.info("Dauerbetrieb: alle %d Min., Ruhezeit %d–%d Uhr", minuten,
             durchgang.cfg.sammler["ruhezeit_von"], durchgang.cfg.sammler["ruhezeit_bis"])
    sched.start()
