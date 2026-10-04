"""Analyst Stufe 1: Vorfilter, Marktpreis, Marge. Ohne KI, kostet nichts.

Prüft jede neue oder im Preis gesenkte Anzeige und schreibt das Ergebnis nach
`evaluations`. Nur Anzeigen mit ergebnis = 'kandidat' gehen weiter zu Gemini.
"""
import json
import logging
import re
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

BUNDLE_WOERTER = ("bundle", "set", "komplett", "mainboard", "+ ", " mit ", "kit", "pc ")


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
    t = f" {title.lower()} "
    return any(w in t for w in BUNDLE_WOERTER)


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


def deal_text(b: Bewertung) -> str:
    """Kurze Zusammenfassung fürs Log."""
    return json.dumps(b.__dict__, ensure_ascii=False)
