from datetime import datetime, timezone

from deal_finder.analyst import Bewertung, bewerte_alle
from deal_finder.collector import speichere_items
from deal_finder.config import PROJEKT_ORDNER, lade_config
from deal_finder.db import kv_get, sync_suchen, verbinde
from deal_finder.melder import (REAKTIONEN, angebot, baue_meldung, melde_deals, produkt_name,
                                verarbeite_reaktionen, verhandlungstext)

JETZT = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
CFG = lade_config(PROJEKT_ORDNER / "config.yaml")
A = CFG.analyst


class FakeTelegram:
    def __init__(self, updates=None, foto_kaputt=False):
        self.gesendet, self.callbacks, self.knoepfe = [], [], []
        self.updates, self.foto_kaputt = updates or [], foto_kaputt

    def sende_foto(self, url, text, **extra):
        if self.foto_kaputt:
            raise RuntimeError("wrong file identifier")
        self.gesendet.append(("foto", url, text, extra))
        return 100 + len(self.gesendet)

    def sende_text(self, text, **extra):
        self.gesendet.append(("text", None, text, extra))
        return 100 + len(self.gesendet)

    def hole_updates(self, offset=None):
        return [u for u in self.updates if offset is None or u["update_id"] >= offset]

    def antworte_callback(self, cid, text):
        self.callbacks.append((cid, text))

    def setze_knoepfe(self, msg_id, knoepfe):
        self.knoepfe.append((msg_id, knoepfe))


def test_produkt_name_und_angebot():
    assert produkt_name("i7-13700k") == "i7-13700K"
    assert produkt_name("rtx-3070") == "RTX 3070"
    assert angebot(195, 0.8) == 155   # 156 → 155
    assert angebot(200, 0.8) == 160
    assert angebot(123, 0.8) == 100   # 98.4 → 100


def test_verhandlungstext():
    t = verhandlungstext("i7-13700K", 195, 5.99, "Läuft sie stabil?", 0.8)
    assert t == "Hallo, ich hätte Interesse am i7-13700K. Würdest du ihn für 155 Euro inklusive Versand abgeben? Läuft sie stabil? Viele Grüße"
    assert "bei Abholung" in verhandlungstext("x", 100, 0, "", 0.8)
    assert "getestet" in verhandlungstext("x", 100, None, "", 0.8)


def deal_in_db(con):
    speichere_items(con, 2, [{"id": str(100 + i), "title": "Intel i7 13700K", "price": f"{280 + 4 * i} €"} for i in range(10)],
                    JETZT.isoformat())
    speichere_items(con, 2, [{"id": "1", "title": "Intel Core i7-13700K <top>", "price": "195 €", "shippingPrice": "5,99 €",
                              "address": "10115 Berlin", "url": "https://kleinanzeigen.de/1",
                              "imageURLs": ["https://img/1.jpg", "https://img/2.jpg"]}], JETZT.isoformat())
    b = bewerte_alle(con, ["1"], A, JETZT)[0]
    b.ergebnis, b.ki_score, b.ki_risiken, b.ki_bildbefund, b.ki_frage = "deal", 8, ["nur Abholung"], "echtes Foto", "BIOS-Stand?"
    con.execute("UPDATE evaluations SET ergebnis='deal', ki_score=8 WHERE listing_id='1'")
    con.commit()
    return b


def test_baue_meldung():
    con = verbinde(":memory:"); sync_suchen(con, CFG.suchen)
    b = deal_in_db(con)
    z = con.execute("SELECT l.*, s.produkt_key FROM listings l JOIN searches s ON s.id=l.search_id WHERE l.id='1'").fetchone()
    text, bild = baue_meldung(z, b, A)
    assert bild == "https://img/1.jpg"
    assert "&lt;top&gt;" in text and "195 €" in text and "+ 5.99 €" in text
    assert "Markt 298 €" in text and "nur Abholung" in text and "KI-Score 8/10" in text
    assert "155 Euro inklusive Versand" in text and "BIOS-Stand?" in text
    assert len(text) < 1024


def test_melde_deals_und_reaktion():
    con = verbinde(":memory:"); sync_suchen(con, CFG.suchen)
    b = deal_in_db(con)
    tg = FakeTelegram()
    assert melde_deals(con, [b], tg, A) == 1
    art, url, text, extra = tg.gesendet[0]
    assert art == "foto" and url == "https://img/1.jpg"
    eval_id = con.execute("SELECT id FROM evaluations WHERE listing_id='1'").fetchone()[0]
    assert extra["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == f"gekauft:{eval_id}"
    e = con.execute("SELECT gemeldet_am, telegram_msg_id FROM evaluations WHERE id=?", (eval_id,)).fetchone()
    assert e["gemeldet_am"] and e["telegram_msg_id"] == 101
    # nicht doppelt melden
    assert melde_deals(con, [b], tg, A) == 0

    # Carl drückt „Gekauft“
    tg.updates = [{"update_id": 500, "callback_query": {"id": "cq1", "data": f"gekauft:{eval_id}", "message": {"message_id": 101}}},
                  {"update_id": 501, "callback_query": {"id": "cq2", "data": "quatsch"}}]
    assert verarbeite_reaktionen(con, tg) == 1
    assert con.execute("SELECT carl_reaktion FROM evaluations WHERE id=?", (eval_id,)).fetchone()[0] == "gekauft"
    assert kv_get(con, "telegram_offset") == "502"
    assert tg.callbacks == [("cq1", "Gespeichert: ✅ Gekauft")]
    assert tg.knoepfe[0][0] == 101 and tg.knoepfe[0][1][0][0]["text"] == REAKTIONEN["gekauft"]
    # zweiter Aufruf: nichts Neues (Offset)
    assert verarbeite_reaktionen(con, tg) == 0


def test_melde_deal_foto_kaputt_faellt_auf_text_zurueck():
    con = verbinde(":memory:"); sync_suchen(con, CFG.suchen)
    b = deal_in_db(con)
    tg = FakeTelegram(foto_kaputt=True)
    assert melde_deals(con, [b], tg, A) == 1
    assert tg.gesendet[0][0] == "text"
