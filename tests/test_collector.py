import json
from pathlib import Path

import pytest

from deal_finder.collector import erkenne_status, parse_preis, speichere_items
from deal_finder.config import PROJEKT_ORDNER, lade_config
from deal_finder.db import sync_suchen, verbinde

ITEMS = json.loads((Path(__file__).parent / "beispiel_items.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("wert,erwartet", [
    ("280 € VB", 280.0), ("1.250 €", 1250.0), ("5,99 €", 5.99), ("+ 5,99 € Versand", 5.99),
    ("VB", None), ("Zu verschenken", None), (None, None), (120, 120.0), ("12.345,50 €", 12345.5),
])
def test_parse_preis(wert, erwartet):
    assert parse_preis(wert) == erwartet


def test_erkenne_status():
    assert erkenne_status({"id": "1"}) == "aktiv"
    assert erkenne_status({"monitoringStatus": "delisted"}) == "verschwunden"
    assert erkenne_status({"changeType": "NEW"}) == "aktiv"
    assert erkenne_status({"isDelisted": True}) == "verschwunden"


@pytest.fixture
def con():
    cfg = lade_config(PROJEKT_ORDNER / "config.yaml")
    c = verbinde(":memory:")
    sync_suchen(c, cfg.suchen)
    return c


def test_speichern_neu(con):
    erg = speichere_items(con, 2, ITEMS, "2026-10-04T10:00:00+00:00")
    assert erg.neu == 3 and erg.geaendert == 0 and erg.verschwunden == 0
    z = con.execute("SELECT * FROM listings WHERE id = '2001'").fetchone()
    assert z["price"] == 280.0 and z["shipping_price"] == 5.99 and z["status"] == "aktiv"
    assert json.loads(z["image_urls"]) == ITEMS[0]["imageURLs"]
    assert json.loads(z["raw_json"])["sellerName"] == "Max"
    assert con.execute("SELECT price FROM listings WHERE id = '2003'").fetchone()[0] is None
    assert con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 3


def test_speichern_preisaenderung_und_verschwunden(con):
    speichere_items(con, 2, ITEMS, "2026-10-04T10:00:00+00:00")
    # zweiter Lauf: 2001 billiger, 2002 unverändert, 2003 gelöscht
    lauf2 = [
        {**ITEMS[0], "price": "250 €"},
        ITEMS[1],
        {**ITEMS[2], "monitoringStatus": "delisted"},
    ]
    erg = speichere_items(con, 2, lauf2, "2026-10-04T11:00:00+00:00")
    assert erg.neu == 0 and erg.geaendert == 1 and erg.gesehen == 1 and erg.verschwunden == 1
    assert erg.gesenkte_ids == ["2001"]
    assert con.execute("SELECT price FROM listings WHERE id='2001'").fetchone()[0] == 250.0
    assert con.execute("SELECT COUNT(*) FROM price_history WHERE listing_id='2001'").fetchone()[0] == 2
    assert con.execute("SELECT status FROM listings WHERE id='2003'").fetchone()[0] == "verschwunden"
    assert con.execute("SELECT first_seen FROM listings WHERE id='2001'").fetchone()[0] == "2026-10-04T10:00:00+00:00"
    # dritter Lauf identisch: nichts Neues
    erg = speichere_items(con, 2, lauf2, "2026-10-04T12:00:00+00:00")
    assert erg.neu == 0 and erg.geaendert == 0 and erg.verschwunden == 0


def test_task_wird_automatisch_angelegt(con):
    from deal_finder.collector import sammle_suche
    from deal_finder.config import Suche

    class FakeApify:
        def __init__(self):
            self.angelegt = []

        def finde_oder_erstelle_task(self, query, max_items=50):
            self.angelegt.append(query)
            return "NEU123"

        def lauf(self, task_id, eingabe, wait):
            assert task_id == "NEU123"
            self.max_items = eingabe["maxItems"]
            return []

    cfg = lade_config(PROJEKT_ORDNER / "config.yaml")
    neu = Suche(name="RTX 4070", produkt_key="rtx-4070")
    cfg.suchen.append(neu)
    sync_suchen(con, cfg.suchen)
    apify = FakeApify()
    sammle_suche(con, cfg, apify, neu)
    assert apify.angelegt == ["RTX 4070"] and apify.max_items == 200  # Tag Null
    assert con.execute("SELECT apify_task_id, tag_null_am FROM searches WHERE query='RTX 4070'").fetchone()[0] == "NEU123"
    # zweiter Lauf: Task-ID bleibt, auch wenn Config sie nicht kennt
    sync_suchen(con, cfg.suchen)
    assert con.execute("SELECT apify_task_id FROM searches WHERE query='RTX 4070'").fetchone()[0] == "NEU123"
    sammle_suche(con, cfg, apify, neu)
    assert apify.angelegt == ["RTX 4070"] and apify.max_items == 50  # nicht nochmal angelegt, Monitoring
