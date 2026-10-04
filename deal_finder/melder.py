"""Deal-Meldungen per Telegram: Nachricht mit Bild, Verhandlungstext, Knöpfe, Reaktionen speichern."""
import html
import json
import logging
import sqlite3
from datetime import datetime, timezone

from deal_finder.analyst import Bewertung
from deal_finder.db import kv_get, kv_set
from deal_finder.notifier import Telegram

log = logging.getLogger(__name__)

REAKTIONEN = {"angeschrieben": "✉️ Angeschrieben", "gekauft": "✅ Gekauft", "uninteressant": "✖️ Uninteressant"}


def produkt_name(produkt_key: str) -> str:
    """'i7-13700k' → 'i7-13700K', 'rtx-3070' → 'RTX 3070'."""
    teile = produkt_key.split("-")
    if teile[0] in ("rtx", "gtx", "rx"):
        return " ".join(t.upper() for t in teile)
    return "-".join(t.upper() if t[-1:].isalpha() and t[:-1].isdigit() else t for t in teile)


def angebot(preis: float, faktor: float) -> int:
    """Verhandlungsangebot, auf 5 € gerundet."""
    return int(round(preis * faktor / 5.0) * 5)


def verhandlungstext(produkt: str, preis: float, versand: float | None, frage: str, faktor: float) -> str:
    betrag = angebot(preis, faktor)
    versand_text = "inklusive Versand" if versand is None or versand > 0 else "bei Abholung"
    frage = (frage or "").strip() or "Ist alles getestet und läuft stabil?"
    return f"Hallo, ich hätte Interesse am {produkt}. Würdest du ihn für {betrag} Euro {versand_text} abgeben? {frage} Viele Grüße"


def baue_meldung(z: sqlite3.Row, b: Bewertung, a: dict) -> tuple[str, str | None]:
    """Gibt (Bildtext als HTML, erstes Bild oder None) zurück."""
    produkt = produkt_name(z["produkt_key"])
    versand = z["shipping_price"]
    versand_s = "kostenlos" if versand == 0 else (f"+ {versand:.2f} €" if versand else "Abholung/unklar")
    risiken = ", ".join(b.ki_risiken) if b.ki_risiken else "keine"
    bilder = json.loads(z["image_urls"] or "[]")
    text = verhandlungstext(produkt, z["price"], versand, b.ki_frage or "", a["angebotsfaktor"])
    zeilen = [
        f"🔥 <b>{html.escape(z['title'])}</b>",
        f"💶 <b>{z['price']:.0f} €</b> Versand {versand_s} · 📍 {html.escape(z['address'] or '?')}",
        f"📊 Markt {b.marktpreis:.0f} € (Median, {b.vergleichsanzahl} Anzeigen) · <b>{b.abstand_prozent:.0f} % drunter</b>",
        f"💰 Marge ca. <b>{b.marge_euro:.0f} €</b> · KI-Score {b.ki_score}/10",
        f"⚠️ {html.escape(risiken)}",
        f"🖼 {html.escape(b.ki_bildbefund or '')}" if b.ki_bildbefund else "",
        f"🔗 <a href=\"{html.escape(z['url'] or '')}\">Zur Anzeige</a>",
        "",
        f"<code>{html.escape(text)}</code>",
    ]
    return "\n".join(x for x in zeilen if x != ""), (bilder[0] if bilder else None)


def knoepfe(eval_id: int) -> list[list[dict]]:
    return [[{"text": t, "callback_data": f"{k}:{eval_id}"} for k, t in REAKTIONEN.items()]]


def melde_deals(con: sqlite3.Connection, deals: list[Bewertung], tg: Telegram, a: dict) -> int:
    """Schickt je Deal eine Nachricht, merkt message_id und gemeldet_am. Gibt Anzahl gesendeter Meldungen zurück."""
    gesendet = 0
    for b in deals:
        e = con.execute("SELECT id, gemeldet_am FROM evaluations WHERE listing_id = ? ORDER BY bewertet_am DESC, id DESC LIMIT 1",
                        (b.listing_id,)).fetchone()
        if e is None or e["gemeldet_am"]:
            continue
        z = con.execute("""SELECT l.*, s.produkt_key FROM listings l JOIN searches s ON s.id = l.search_id
                           WHERE l.id = ?""", (b.listing_id,)).fetchone()
        text, bild = baue_meldung(z, b, a)
        markup = {"inline_keyboard": knoepfe(e["id"])}
        try:
            if bild:
                msg_id = tg.sende_foto(bild, text, reply_markup=markup)
            else:
                msg_id = tg.sende_text(text, reply_markup=markup)
        except Exception as ex:  # noqa: BLE001
            log.error("Meldung für %s fehlgeschlagen: %s", b.listing_id, ex)
            if bild:  # Bild-URL kaputt → als Text nachschicken
                try:
                    msg_id = tg.sende_text(text, reply_markup=markup)
                except Exception as ex2:  # noqa: BLE001
                    log.error("Auch Textmeldung fehlgeschlagen: %s", ex2)
                    continue
            else:
                continue
        con.execute("UPDATE evaluations SET gemeldet_am = ?, telegram_msg_id = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), msg_id, e["id"]))
        con.commit()
        gesendet += 1
    log.info("Telegram: %d Deal(s) gemeldet", gesendet)
    return gesendet


def verarbeite_reaktionen(con: sqlite3.Connection, tg: Telegram) -> int:
    """Holt Knopfdrücke ab, speichert carl_reaktion, ersetzt die Knöpfe durch die gewählte Antwort."""
    offset = kv_get(con, "telegram_offset")
    updates = tg.hole_updates(int(offset) if offset else None)
    anzahl = 0
    for u in updates:
        kv_set(con, "telegram_offset", str(u["update_id"] + 1))
        cq = u.get("callback_query")
        if not cq:
            continue
        try:
            reaktion, eval_id = cq["data"].split(":", 1)
            eval_id = int(eval_id)
        except (KeyError, ValueError):
            continue
        if reaktion not in REAKTIONEN:
            continue
        con.execute("UPDATE evaluations SET carl_reaktion = ? WHERE id = ?", (reaktion, eval_id))
        con.commit()
        anzahl += 1
        try:
            tg.antworte_callback(cq["id"], f"Gespeichert: {REAKTIONEN[reaktion]}")
            msg = cq.get("message") or {}
            if msg.get("message_id"):
                tg.setze_knoepfe(msg["message_id"], [[{"text": REAKTIONEN[reaktion], "callback_data": "noop:0"}]])
        except Exception as ex:  # noqa: BLE001
            log.warning("Rückmeldung an Telegram fehlgeschlagen: %s", ex)
    return anzahl
