from datetime import datetime, timezone

import pytest

from deal_finder.analyst import (berechne_marge, berechne_marktpreis, bewerte, bewerte_alle,
                                 ist_ausgeschlossen, ist_bundle, unbewertete_ids)
from deal_finder.collector import speichere_items
from deal_finder.config import PROJEKT_ORDNER, lade_config
from deal_finder.db import sync_suchen, verbinde

JETZT = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
CFG = lade_config(PROJEKT_ORDNER / "config.yaml")
A = CFG.analyst


def item(i, preis, title="Intel Core i7 13700K", versand=None):
    return {"id": str(i), "title": title, "price": preis, "shippingPrice": versand,
            "descriptionText": "", "url": f"https://kleinanzeigen.de/{i}"}


@pytest.fixture
def con():
    c = verbinde(":memory:")
    sync_suchen(c, CFG.suchen)
    return c


def test_ausschluss():
    assert ist_ausgeschlossen("Suche i7 13700K", 100, A["ausschluss_woerter"]) == "Titel enthält „suche“"
    assert ist_ausgeschlossen("i7 13700K Ankauf", 100, A["ausschluss_woerter"]).startswith("Titel")
    assert ist_ausgeschlossen("i7 13700K", None, A["ausschluss_woerter"]) == "kein Preis"
    assert ist_ausgeschlossen("i7 13700K", 9999, A["ausschluss_woerter"]).startswith("Preis 9999")
    assert ist_ausgeschlossen("i7 13700K", 1, A["ausschluss_woerter"]).startswith("Preis 0/1")
    assert ist_ausgeschlossen("i7 13700K Versuche", 100, A["ausschluss_woerter"]) is None  # kein Wortteil
    assert ist_ausgeschlossen("i7 13700K", 250, A["ausschluss_woerter"]) is None


def test_bundle():
    assert ist_bundle("i7 13700K Bundle mit Mainboard")
    assert ist_bundle("Gaming PC i7 13700K")
    assert not ist_bundle("Intel Core i7 13700K")


def test_marktpreis():
    # 10 Werte, 10 % Ausreißer: 1 unten, 1 oben weg → Median der mittleren 8
    preise = [50, 200, 210, 220, 230, 240, 250, 260, 270, 2000]
    mp = berechne_marktpreis(preise, 10)
    assert mp.anzahl == 8 and mp.median == 235.0
    assert berechne_marktpreis([], 10).median is None
    assert berechne_marktpreis([100, 1, None], 10).anzahl == 1   # 1 € und None zählen nicht
    assert berechne_marktpreis([100, 300], 10).median == 200.0   # zu wenig für Abschnitt: alles bleibt


def test_marge():
    # Markt 300 → Verkauf 285, Einkauf 200 + 5.99 Versand + 6 eigener = 73.01
    assert berechne_marge(300, 200, 5.99, A) == 73.01
    assert berechne_marge(300, 280, None, A) == -1.0


def fuelle_markt(con, n=10, basis=300):
    """n Vergleichsanzeigen i7 13700K um `basis` € (Median = basis)."""
    items = [item(100 + i, f"{basis - 20 + 4 * i} €") for i in range(n)]
    speichere_items(con, 2, items, JETZT.isoformat())


def test_bewerte_deal(con):
    fuelle_markt(con)
    speichere_items(con, 2, [item(1, "200 €", versand="+ 5,99 € Versand")], JETZT.isoformat())
    b = bewerte(con, "1", A, JETZT)
    assert b.ergebnis == "kandidat"
    assert b.marktpreis == 298.0 and b.vergleichsanzahl == 8
    assert b.abstand_prozent == 30.9 and b.marge_euro == 71.11


def test_bewerte_zu_teuer_und_marge(con):
    fuelle_markt(con)
    speichere_items(con, 2, [item(1, "250 €"), item(2, "232 €")], JETZT.isoformat())
    b = bewerte(con, "1", A, JETZT)
    assert b.ergebnis == "zu_teuer" and "unter Marktpreis" in b.grund
    # 232: Abstand 22,1 % < 25 → zu_teuer wegen Abstand
    assert bewerte(con, "2", A, JETZT).ergebnis == "zu_teuer"


def test_bewerte_zu_wenig_daten(con):
    fuelle_markt(con, n=5)
    speichere_items(con, 2, [item(1, "100 €")], JETZT.isoformat())
    b = bewerte(con, "1", A, JETZT)
    assert b.ergebnis == "zu_wenig_daten" and b.vergleichsanzahl == 5


def test_bewerte_aussortiert(con):
    fuelle_markt(con)
    speichere_items(con, 2, [item(1, "100 €", title="Suche i7 13700K"),
                             item(2, "100 €", title="i7 13700K Bundle mit Z790"),
                             item(3, "VB")], JETZT.isoformat())
    assert bewerte(con, "1", A, JETZT).grund.startswith("Titel")
    assert bewerte(con, "2", A, JETZT).grund == "Bundle"
    assert bewerte(con, "3", A, JETZT).grund == "kein Preis"


def test_alte_anzeigen_zaehlen_nicht(con):
    alt = datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()
    speichere_items(con, 2, [item(100 + i, "300 €") for i in range(10)], alt)
    speichere_items(con, 2, [item(1, "100 €")], JETZT.isoformat())
    assert bewerte(con, "1", A, JETZT).ergebnis == "zu_wenig_daten"


def test_andere_suche_zaehlt_nicht(con):
    fuelle_markt(con)  # i7 unter search_id 2
    speichere_items(con, 3, [item(1, "100 €", title="RTX 3070")], JETZT.isoformat())
    assert bewerte(con, "1", A, JETZT).ergebnis == "zu_wenig_daten"


def test_bewerte_alle_und_unbewertete(con):
    fuelle_markt(con)
    speichere_items(con, 2, [item(1, "200 €")], JETZT.isoformat())
    assert "1" in unbewertete_ids(con) and len(unbewertete_ids(con)) == 11
    ergebnisse = bewerte_alle(con, unbewertete_ids(con), A, JETZT)
    assert len(ergebnisse) == 11
    assert con.execute("SELECT ergebnis FROM evaluations WHERE listing_id='1'").fetchone()[0] == "kandidat"
    assert unbewertete_ids(con) == []
    # Preis sinkt → wieder bewerten; gleicher Preis → nicht
    speichere_items(con, 2, [item(1, "180 €")], JETZT.isoformat())
    assert unbewertete_ids(con) == ["1"]
    bewerte_alle(con, ["1"], A, JETZT)
    speichere_items(con, 2, [item(1, "180 €")], JETZT.isoformat())
    assert unbewertete_ids(con) == []


class FakeGemini:
    def __init__(self, passt=True, score=8, echtes_foto=True):
        from deal_finder.gemini import BildBefund, TextBefund
        self.text = TextBefund(passt, score, ["ungetestet"], "Läuft sie stabil?")
        self.bild = BildBefund(echtes_foto=echtes_foto, befund="echtes Foto")
        self.aufrufe = 0

    def pruefe_text(self, *a):
        self.aufrufe += 1
        return self.text

    def pruefe_bilder(self, *a):
        return self.bild


def test_pruefe_kandidaten_deal(con):
    from deal_finder.analyst import pruefe_kandidaten
    fuelle_markt(con)
    speichere_items(con, 2, [item(1, "200 €"), item(2, "250 €")], JETZT.isoformat())
    bew = bewerte_alle(con, ["1", "2"], A, JETZT)
    g = FakeGemini()
    deals = pruefe_kandidaten(con, bew, g, A, JETZT)
    assert [d.listing_id for d in deals] == ["1"] and g.aufrufe == 1  # nur der Kandidat geht zu Gemini
    e = con.execute("SELECT * FROM evaluations WHERE listing_id='1'").fetchone()
    assert e["ergebnis"] == "deal" and e["ki_score"] == 8 and "ungetestet" in e["ki_risiken"]
    assert e["ki_frage"] == "Läuft sie stabil?" and e["ki_bildbefund"] == "echtes Foto"


def test_pruefe_kandidaten_abgelehnt(con):
    from deal_finder.analyst import pruefe_kandidaten
    fuelle_markt(con)
    speichere_items(con, 2, [item(1, "200 €"), item(2, "200 €")], JETZT.isoformat())
    bew = bewerte_alle(con, ["1"], A, JETZT)
    assert pruefe_kandidaten(con, bew, FakeGemini(echtes_foto=False), A, JETZT) == []   # 8 − 3 = 5 < 7
    e = con.execute("SELECT ergebnis, grund, ki_risiken FROM evaluations WHERE listing_id='1'").fetchone()
    assert e["ergebnis"] == "ki_abgelehnt" and "Score 5" in e["grund"] and "Stock" in e["ki_risiken"]
    bew = bewerte_alle(con, ["2"], A, JETZT)
    assert pruefe_kandidaten(con, bew, FakeGemini(passt=False), A, JETZT) == []
    assert con.execute("SELECT grund FROM evaluations WHERE listing_id='2'").fetchone()[0] == "nicht das gesuchte Produkt"
