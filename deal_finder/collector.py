"""Sammler: Apify-Ergebnisse in listings und price_history schreiben.

Feldnamen des Actors lexis-solutions/ebay-kleinanzeigen (Stand Okt. 2026):
id, title, price ("120 € VB"), shippingPrice, condition, address, descriptionText,
sellerName, sellerURL, imageURLs, primaryImageURL, url, date.
Im Monitoring-Modus kommt zusätzlich ein Status-Feld (neu/geändert/gelöscht);
dessen genauer Name ist nicht dokumentiert, darum wird er tolerant gesucht.
"""
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from deal_finder.apify import Apify
from deal_finder.config import Config, Suche

log = logging.getLogger(__name__)

_ZAHL = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?")
_STATUS_FELDER = ("monitoringStatus", "changeType", "changeStatus", "status", "change", "monitoring")
_GELOESCHT = ("delist", "removed", "deleted", "gone", "geloescht", "gelöscht")


def parse_preis(wert) -> float | None:
    """'1.200 € VB' → 1200.0, '5,99 €' → 5.99, 'VB'/'Zu verschenken'/None → None."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    m = _ZAHL.search(str(wert))
    if not m:
        return None
    ganz = m.group(1).replace(".", "")
    return float(f"{ganz}.{m.group(2) or 0}")


def parse_versand(wert) -> float | None:
    """shippingPrice: '+ 5,99 € Versand' → 5.99, 'Nur Abholung'/None → None."""
    return parse_preis(wert)


def erkenne_status(item: dict) -> str:
    """Liefert 'verschwunden', wenn der Monitoring-Modus die Anzeige als gelöscht meldet, sonst 'aktiv'."""
    for feld in _STATUS_FELDER:
        wert = item.get(feld)
        if isinstance(wert, str) and any(w in wert.lower() for w in _GELOESCHT):
            return "verschwunden"
    for feld in ("isDelisted", "delisted", "isRemoved"):
        if item.get(feld) is True:
            return "verschwunden"
    return "aktiv"


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Ergebnis:
    neu: int = 0
    geaendert: int = 0     # Preis geändert
    gesehen: int = 0       # bekannt, unverändert
    verschwunden: int = 0
    neue_ids: list[str] = field(default_factory=list)
    gesenkte_ids: list[str] = field(default_factory=list)


def speichere_items(con: sqlite3.Connection, search_id: int, items: list[dict], jetzt: str | None = None) -> Ergebnis:
    """Schreibt Apify-Items in listings/price_history. Idempotent, Kleinanzeigen-ID ist Schlüssel."""
    jetzt = jetzt or _jetzt()
    erg = Ergebnis()
    for item in items:
        kid = str(item.get("id") or "").strip()
        if not kid:
            log.warning("Item ohne id übersprungen: %s", str(item)[:120])
            continue
        preis = parse_preis(item.get("price"))
        status = erkenne_status(item)
        bilder = item.get("imageURLs") or ([item["primaryImageURL"]] if item.get("primaryImageURL") else [])
        alt = con.execute("SELECT price, status FROM listings WHERE id = ?", (kid,)).fetchone()

        if alt is None:
            con.execute(
                """INSERT INTO listings (id, search_id, title, price, shipping_price, condition, address,
                   description, seller_name, seller_url, image_urls, url, first_seen, last_seen, status, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (kid, search_id, item.get("title") or "", preis, parse_versand(item.get("shippingPrice")),
                 item.get("condition"), item.get("address"), item.get("descriptionText"),
                 item.get("sellerName"), item.get("sellerURL"), json.dumps(bilder), item.get("url"),
                 jetzt, jetzt, status, json.dumps(item, ensure_ascii=False)),
            )
            con.execute("INSERT INTO price_history (listing_id, price, seen_at) VALUES (?,?,?)", (kid, preis, jetzt))
            if status == "verschwunden":
                erg.verschwunden += 1
            else:
                erg.neu += 1
                erg.neue_ids.append(kid)
            continue

        if status == "verschwunden":
            con.execute("UPDATE listings SET status = 'verschwunden', last_seen = ?, raw_json = ? WHERE id = ?",
                        (jetzt, json.dumps(item, ensure_ascii=False), kid))
            if alt["status"] != "verschwunden":
                erg.verschwunden += 1
            continue

        con.execute(
            """UPDATE listings SET title = ?, price = ?, shipping_price = ?, description = ?, image_urls = ?,
               last_seen = ?, status = 'aktiv', raw_json = ? WHERE id = ?""",
            (item.get("title") or "", preis, parse_versand(item.get("shippingPrice")), item.get("descriptionText"),
             json.dumps(bilder), jetzt, json.dumps(item, ensure_ascii=False), kid),
        )
        if preis != alt["price"]:
            con.execute("INSERT INTO price_history (listing_id, price, seen_at) VALUES (?,?,?)", (kid, preis, jetzt))
            erg.geaendert += 1
            if preis is not None and (alt["price"] is None or preis < alt["price"]):
                erg.gesenkte_ids.append(kid)
        else:
            erg.gesehen += 1
    con.commit()
    return erg


def sammle_suche(con: sqlite3.Connection, cfg: Config, apify: Apify, suche: Suche) -> Ergebnis:
    """Ein Apify-Lauf für eine Suche. Erster Lauf = Tag Null mit max_items_tag_null."""
    zeile = con.execute("SELECT id, tag_null_am, apify_task_id FROM searches WHERE query = ?", (suche.name,)).fetchone()
    if zeile is None:
        raise RuntimeError(f"Suche '{suche.name}' nicht in der Datenbank, erst sync_suchen aufrufen")
    tag_null = zeile["tag_null_am"] is None
    s = cfg.sammler
    task_id = zeile["apify_task_id"]
    if not task_id:
        task_id = apify.finde_oder_erstelle_task(suche.name, s["max_items"])
        con.execute("UPDATE searches SET apify_task_id = ? WHERE id = ?", (task_id, zeile["id"]))
        con.commit()
    eingabe = {
        "query": suche.name,
        "maxItems": s["max_items_tag_null"] if tag_null else s["max_items"],
        "monitoringMode": True,
        "monitoringFields": ["price", "title"],
        "fetchViewsCount": False,
        "proxy": {"useApifyProxy": True},
    }
    log.info("Suche '%s': %s", suche.name, "Tag-Null-Lauf" if tag_null else "Monitoring-Lauf")
    items = apify.lauf(task_id, eingabe, s["wait_for_finish_sekunden"])
    erg = speichere_items(con, zeile["id"], items)
    if tag_null:
        con.execute("UPDATE searches SET tag_null_am = ? WHERE id = ?", (_jetzt(), zeile["id"]))
        con.commit()
    log.info("Suche '%s': neu=%d geändert=%d gesehen=%d verschwunden=%d",
             suche.name, erg.neu, erg.geaendert, erg.gesehen, erg.verschwunden)
    return erg
