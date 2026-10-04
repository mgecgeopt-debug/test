"""Analyst Stufe 1: Vorfilter, Marktpreis, Marge. Ohne KI, kostet nichts.

Prüft jede neue oder im Preis gesenkte Anzeige und schreibt das Ergebnis nach
`evaluations`. Nur Anzeigen mit ergebnis = 'kandidat' gehen weiter zu Gemini.
"""
import json
import logging
import re
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

BUNDLE_WOERTER = ("bundle", "komplett", "+ ", " set ", "kit ", " pc", "pc ", "rechner", "tower", "system")
KEIN_EINZELTEIL = ("laptop", "notebook", "zoll", "2-in-1", "legion", "rog strix", "tuf gaming", "erazer", "omen")
# Komponenten-Klassen: zwei verschiedene im Titel = Bundle (z. B. "Ryzen 7 5800 | RTX 3070 | 1 TB SSD")
KOMPONENTEN = {
    "cpu": re.compile(r"\b(i[3579][\s-]?\d{4,5}|ryzen|r[3579][\s-]?\d{4}|core i[3579])\b"),
    "gpu": re.compile(r"\b(rtx|gtx|rx|geforce|radeon|arc)\s?[a-z]?\d{3,4}\b|\b\d{4}\s?(ti|super|xt|fe)\b|\b(grafikkarte|gpu)\b"),
    "ram": re.compile(r"\b\d{1,3}\s?gb\b.*\b(ram|ddr[45])\b|\bddr[45]\b"),
    "speicher": re.compile(r"\b(ssd|nvme|hdd|m\.2)\b|\b\d(,\d)?\s?tb\b"),
    "mainboard": re.compile(r"\b(mainboard|motherboard|[zbh]\d{3}[a-z]?(-[a-z]+)?)\b"),
    "netzteil": re.compile(r"\b(netzteil|psu|\d{3,4}\s?w)\b"),
}


@dataclass
class Marktpreis:
    median: float | None
    anzahl: int          # Vergleichsanzeigen nach Ausreißer-Entfernung


@dataclass
class Bewertung:
    listing_id: str
    ergebnis: str        # kandidat | aussortiert | zu_wenig_daten | zu_teuer
    grund: str | None = None
    preis: float | None = None
    versand: float | None = None
    marktpreis: float | None = None
    vergleichsanzahl: int = 0
    abstand_prozent: float | None = None
    marge_euro: float | None = None
    ki_score: int | None = None
    ki_risiken: list[str] = field(default_factory=list)
    ki_bildbefund: str | None = None
    ki_frage: str | None = None


def ist_ausgeschlossen(title: str, preis: float | None, ausschluss: list[str]) -> str | None:
    """Gibt einen Grund zurück, wenn die Anzeige vorab aussortiert wird, sonst None."""
    if preis is None:
        return "kein Preis"
    if preis <= 1:
        return "Preis 0/1 € (Platzhalter)"
    if preis >= 9999:
        return "Preis 9999 € (Platzhalter)"
    t = title.lower()
    for w in ausschluss:
        if re.search(rf"\b{re.escape(w.lower())}\b", t):
            return f"Titel enthält „{w}“"
    return None


def ist_bundle(title: str) -> bool:
    """Bundle, Komplett-PC oder Laptop: kein Einzelteil, zählt nicht zum Marktpreis."""
    t = f" {title.lower()} "
    if any(w in t for w in BUNDLE_WOERTER) or any(w in t for w in KEIN_EINZELTEIL):
        return True
    klassen = sum(1 for rx in KOMPONENTEN.values() if rx.search(t))
    return klassen >= 2


def ist_anderes_produkt(title: str, produkt_key: str, a: dict) -> bool:
    """Suchspezifische Ausschlusswörter aus config.yaml, z. B. '3070 ti' bei rtx-3070."""
    t = re.sub(r"[\s-]+", " ", title.lower())
    for w in a.get("produkt_ausschluss", {}).get(produkt_key, []):
        w = re.sub(r"[\s-]+", " ", w.lower())
        if w in t or w.replace(" ", "") in t.replace(" ", ""):
            return True
    return False


def berechne_marktpreis(preise: list[float], ausreisser_prozent: int) -> Marktpreis:
    """Median nach Entfernen des untersten und obersten `ausreisser_prozent` %."""
    werte = sorted(p for p in preise if p is not None and p > 1)
    if not werte:
        return Marktpreis(None, 0)
    abschnitt = int(len(werte) * ausreisser_prozent / 100)
    kern = werte[abschnitt:len(werte) - abschnitt] if abschnitt else werte
    if not kern:
        kern = werte
    return Marktpreis(float(statistics.median(kern)), len(kern))


def marktpreis_aus_db(con: sqlite3.Connection, produkt_key: str, a: dict,
                      jetzt: datetime | None = None, ohne_listing: str | None = None) -> Marktpreis:
    """Alle Preise desselben produkt_key der letzten `marktpreis_tage` (aktiv oder verschwunden).

    Bundles und Platzhalter-Preise zählen nicht. Die zu bewertende Anzeige selbst wird ausgelassen.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    seit = (jetzt - timedelta(days=a["marktpreis_tage"])).isoformat(timespec="seconds")
    zeilen = con.execute(
        """SELECT l.id, l.title, l.price FROM listings l
           JOIN searches s ON s.id = l.search_id
           WHERE s.produkt_key = ? AND l.last_seen >= ? AND l.price IS NOT NULL""",
        (produkt_key, seit),
    ).fetchall()
    preise = [z["price"] for z in zeilen
              if z["id"] != ohne_listing and not ist_bundle(z["title"])
              and not ist_anderes_produkt(z["title"], produkt_key, a)
              and ist_ausgeschlossen(z["title"], z["price"], a["ausschluss_woerter"]) is None]
    return berechne_marktpreis(preise, a["ausreisser_prozent"])


def berechne_marge(marktpreis: float, preis: float, versand: float | None, a: dict) -> float:
    """Verkaufspreis = Marktpreis × Faktor. Marge = Verkauf − Einkauf − Einkaufsversand − eigener Versand."""
    verkauf = marktpreis * a["verkaufsfaktor"]
    return round(verkauf - preis - (versand or 0) - a["eigener_versand_euro"], 2)


def bewerte(con: sqlite3.Connection, listing_id: str, a: dict, jetzt: datetime | None = None) -> Bewertung:
    """Stufe 1 für eine Anzeige. Schreibt nichts in die Datenbank."""
    z = con.execute(
        """SELECT l.id, l.title, l.price, l.shipping_price, s.produkt_key
           FROM listings l JOIN searches s ON s.id = l.search_id WHERE l.id = ?""",
        (listing_id,),
    ).fetchone()
    if z is None:
        raise KeyError(listing_id)
    b = Bewertung(listing_id, "kandidat", preis=z["price"], versand=z["shipping_price"])

    grund = ist_ausgeschlossen(z["title"], z["price"], a["ausschluss_woerter"])
    if grund:
        return Bewertung(listing_id, "aussortiert", grund, preis=z["price"], versand=z["shipping_price"])
    if not a["bundles_melden"] and ist_bundle(z["title"]):
        return Bewertung(listing_id, "aussortiert", "Bundle", preis=z["price"], versand=z["shipping_price"])
    if ist_anderes_produkt(z["title"], z["produkt_key"], a):
        return Bewertung(listing_id, "aussortiert", "anderes Produkt (Ausschlusswort)", preis=z["price"], versand=z["shipping_price"])

    mp = marktpreis_aus_db(con, z["produkt_key"], a, jetzt, ohne_listing=listing_id)
    b.marktpreis, b.vergleichsanzahl = mp.median, mp.anzahl
    if mp.median is None or mp.anzahl < a["mindest_vergleichsanzeigen"]:
        b.ergebnis, b.grund = "zu_wenig_daten", f"nur {mp.anzahl} Vergleichsanzeigen"
        return b

    gesamt = z["price"] + (z["shipping_price"] or 0)
    b.abstand_prozent = round((1 - gesamt / mp.median) * 100, 1)
    b.marge_euro = berechne_marge(mp.median, z["price"], z["shipping_price"], a)
    if b.abstand_prozent < a["mindestabstand_prozent"]:
        b.ergebnis, b.grund = "zu_teuer", f"nur {b.abstand_prozent} % unter Marktpreis"
    elif b.marge_euro < a["mindestmarge_euro"]:
        b.ergebnis, b.grund = "zu_teuer", f"Marge nur {b.marge_euro} €"
    return b


def speichere_bewertung(con: sqlite3.Connection, b: Bewertung, jetzt: datetime | None = None) -> int:
    jetzt = jetzt or datetime.now(timezone.utc)
    cur = con.execute(
        """INSERT INTO evaluations (listing_id, bewertet_am, preis_bewertet, marktpreis, vergleichsanzahl,
           abstand_prozent, ergebnis, grund, marge_euro) VALUES (?,?,?,?,?,?,?,?,?)""",
        (b.listing_id, jetzt.isoformat(timespec="seconds"), b.preis, b.marktpreis, b.vergleichsanzahl,
         b.abstand_prozent, b.ergebnis, b.grund, b.marge_euro),
    )
    con.commit()
    return cur.lastrowid


def bewerte_alle(con: sqlite3.Connection, listing_ids: list[str], a: dict,
                 jetzt: datetime | None = None) -> list[Bewertung]:
    """Stufe 1 für mehrere Anzeigen, Ergebnisse werden gespeichert. Gibt alle Bewertungen zurück."""
    ergebnisse = []
    for lid in listing_ids:
        try:
            b = bewerte(con, lid, a, jetzt)
        except KeyError:
            log.warning("Anzeige %s nicht gefunden", lid)
            continue
        speichere_bewertung(con, b, jetzt)
        ergebnisse.append(b)
    kandidaten = sum(1 for b in ergebnisse if b.ergebnis == "kandidat")
    log.info("Analyst Stufe 1: %d bewertet, %d Kandidaten", len(ergebnisse), kandidaten)
    return ergebnisse


def unbewertete_ids(con: sqlite3.Connection) -> list[str]:
    """Aktive Anzeigen, die noch nie bewertet wurden oder seit der letzten Bewertung billiger sind."""
    zeilen = con.execute(
        """SELECT l.id FROM listings l
           LEFT JOIN evaluations e ON e.id = (
               SELECT id FROM evaluations WHERE listing_id = l.id ORDER BY bewertet_am DESC, id DESC LIMIT 1)
           WHERE l.status = 'aktiv' AND l.price IS NOT NULL
             AND (e.id IS NULL OR (e.preis_bewertet IS NOT NULL AND l.price < e.preis_bewertet))"""
    ).fetchall()
    return [z["id"] for z in zeilen]


def offene_kandidaten(con: sqlite3.Connection) -> list[Bewertung]:
    """Kandidaten aus früheren Durchgängen, die Gemini noch nicht geprüft hat (z. B. wegen Rate-Limit)."""
    zeilen = con.execute(
        """SELECT e.listing_id, e.preis_bewertet, e.marktpreis, e.vergleichsanzahl, e.abstand_prozent, e.marge_euro
           FROM evaluations e JOIN listings l ON l.id = e.listing_id
           WHERE e.ergebnis = 'kandidat' AND e.ki_score IS NULL AND l.status = 'aktiv'
             AND e.id = (SELECT id FROM evaluations WHERE listing_id = e.listing_id ORDER BY bewertet_am DESC, id DESC LIMIT 1)
           ORDER BY e.abstand_prozent DESC"""
    ).fetchall()
    return [Bewertung(z["listing_id"], "kandidat", preis=z["preis_bewertet"], marktpreis=z["marktpreis"],
                      vergleichsanzahl=z["vergleichsanzahl"], abstand_prozent=z["abstand_prozent"],
                      marge_euro=z["marge_euro"]) for z in zeilen]


def deal_text(b: Bewertung) -> str:
    """Kurze Zusammenfassung fürs Log."""
    return json.dumps(b.__dict__, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Stufe 2 und 3: Kandidaten mit Gemini prüfen
# ---------------------------------------------------------------------------

def pruefe_kandidaten(con: sqlite3.Connection, bewertungen: list[Bewertung], gemini, a: dict,
                      jetzt: datetime | None = None) -> list[Bewertung]:
    """Schickt alle Kandidaten aus Stufe 1 durch Text- und Bildprüfung.

    Aktualisiert die zuletzt gespeicherte Bewertung je Anzeige: ergebnis wird 'deal'
    oder 'ki_abgelehnt'. Gibt die Deals zurück. Fehler bei Gemini werden geloggt,
    die Anzeige bleibt dann 'kandidat' und wird beim nächsten Durchgang nicht erneut
    geprüft (bewusst: lieber ein verpasster Deal als Endlosschleife bei API-Störung).
    """
    from deal_finder.gemini import gesamt_score  # lokal, damit analyst.py ohne google-genai testbar bleibt

    deals = []
    geprueft = 0
    for b in bewertungen:
        if b.ergebnis != "kandidat":
            continue
        if geprueft >= a.get("max_gemini_pro_durchgang", 20):
            log.info("Gemini-Limit je Durchgang erreicht, Rest beim nächsten Mal")
            break
        geprueft += 1
        z = con.execute(
            """SELECT l.title, l.price, l.condition, l.description, l.image_urls, s.produkt_key
               FROM listings l JOIN searches s ON s.id = l.search_id WHERE l.id = ?""",
            (b.listing_id,),
        ).fetchone()
        try:
            text = gemini.pruefe_text(z["produkt_key"], z["title"], z["price"], z["condition"], z["description"])
            bild = gemini.pruefe_bilder(z["produkt_key"], z["title"], json.loads(z["image_urls"]), a["max_bilder"])
        except Exception as e:  # noqa: BLE001
            log.error("Gemini-Prüfung für %s fehlgeschlagen: %s", b.listing_id, e)
            continue
        score = gesamt_score(text, bild)
        if not text.passt:
            ergebnis, grund = "ki_abgelehnt", "nicht das gesuchte Produkt"
        elif score < a["mindest_ki_score"]:
            ergebnis, grund = "ki_abgelehnt", f"KI-Score {score} < {a['mindest_ki_score']}"
        else:
            ergebnis, grund = "deal", None
        risiken = list(text.risiken)
        if not bild.echtes_foto:
            risiken.append("Stock-/Herstellerbild")
        if bild.schaeden:
            risiken.append("sichtbare Schäden")
        con.execute(
            """UPDATE evaluations SET ergebnis = ?, grund = ?, ki_score = ?, ki_risiken = ?, ki_bildbefund = ?, ki_frage = ?
               WHERE id = (SELECT id FROM evaluations WHERE listing_id = ? ORDER BY bewertet_am DESC, id DESC LIMIT 1)""",
            (ergebnis, grund, score, json.dumps(risiken, ensure_ascii=False), bild.befund, text.frage, b.listing_id),
        )
        con.commit()
        b.ergebnis, b.grund = ergebnis, grund
        b.ki_score, b.ki_risiken, b.ki_bildbefund, b.ki_frage = score, risiken, bild.befund, text.frage
        log.info("Gemini %s: %s (Score %d) %s", b.listing_id, ergebnis, score, "; ".join(risiken))
        if ergebnis == "deal":
            deals.append(b)
    log.info("Analyst Stufe 2+3: %d Deals", len(deals))
    return deals
