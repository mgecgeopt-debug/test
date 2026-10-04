from datetime import datetime

import pytest

from deal_finder.apify import ApifyFehler
from deal_finder.collector import Ergebnis
from deal_finder.config import PROJEKT_ORDNER, lade_config
from deal_finder.scheduler import Durchgang, in_ruhezeit


@pytest.mark.parametrize("stunde,von,bis,erwartet", [
    (0, 0, 7, True), (6, 0, 7, True), (7, 0, 7, False), (12, 0, 7, False),
    (23, 23, 7, True), (3, 23, 7, True), (8, 23, 7, False), (5, 5, 5, False),
])
def test_in_ruhezeit(stunde, von, bis, erwartet):
    assert in_ruhezeit(stunde, von, bis) is erwartet


class FakeApify:
    """Scheitert für Tasks in `kaputt` so oft, wie dort angegeben."""
    def __init__(self, kaputt=None):
        self.kaputt = dict(kaputt or {})
        self.aufrufe = []

    def lauf(self, task_id, eingabe=None, wait_for_finish=120):
        self.aufrufe.append(task_id)
        if self.kaputt.get(task_id, 0) > 0:
            self.kaputt[task_id] -= 1
            raise ApifyFehler("kaputt")
        return []


class FakeTelegram:
    def __init__(self):
        self.warnungen = []

    def warnung(self, text):
        self.warnungen.append(text)


@pytest.fixture
def cfg(tmp_path):
    c = lade_config(PROJEKT_ORDNER / "config.yaml")
    c.datenbank = tmp_path / "test.sqlite"
    return c


def durchgang(cfg, apify, stunde=12):
    schlaf = []
    d = Durchgang(cfg, apify, FakeTelegram(), schlafen=schlaf.append,
                  jetzt=lambda: datetime(2026, 10, 4, stunde, 0))
    return d, schlaf


def test_ruhezeit_kein_lauf(cfg):
    apify = FakeApify()
    d, _ = durchgang(cfg, apify, stunde=3)
    assert d.laufe() == {} and apify.aufrufe == []


def test_normaler_durchgang(cfg):
    apify = FakeApify()
    d, schlaf = durchgang(cfg, apify)
    erg = d.laufe()
    assert len(erg) == 3 and all(isinstance(e, Ergebnis) for e in erg.values())
    assert len(apify.aufrufe) == 3 and schlaf == [] and d.telegram.warnungen == []


def test_einmal_fehler_dann_ok(cfg):
    apify = FakeApify({"TASK_ID_I7_13700K": 1})
    d, schlaf = durchgang(cfg, apify)
    erg = d.laufe()
    assert isinstance(erg["i7 13700K"], Ergebnis)
    assert schlaf == [5 * 60] and len(apify.aufrufe) == 4
    assert d.telegram.warnungen == []


def test_zweimal_fehler_warnung(cfg):
    apify = FakeApify({"TASK_ID_RTX_3070": 2})
    d, schlaf = durchgang(cfg, apify)
    erg = d.laufe()
    assert erg["RTX 3070"] is None
    assert isinstance(erg["i5 12600K"], Ergebnis)  # andere Suchen laufen trotzdem
    assert len(d.telegram.warnungen) == 1 and "RTX 3070" in d.telegram.warnungen[0]


def test_nach_durchgang_hook_fehler_warnt(cfg):
    d, _ = durchgang(cfg, FakeApify())
    def kaputt(con, ergebnisse):
        raise ValueError("Analyst kaputt")
    d.nach_durchgang = kaputt
    d.laufe()
    assert "Analyst" in d.telegram.warnungen[0]
