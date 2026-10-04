"""Analyst Stufe 2 und 3: Text- und Bildprüfung mit Gemini. Antworten als JSON."""
import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests
from google import genai
from google.genai import types

log = logging.getLogger(__name__)

TEXT_PROMPT = """Du prüfst eine Kleinanzeige für gebrauchte PC-Hardware für einen Wiederverkäufer.
Gesucht wird genau dieses Produkt: {produkt}

Titel: {titel}
Preis: {preis} €
Zustand laut Anzeige: {zustand}
Beschreibung:
\"\"\"{beschreibung}\"\"\"

Prüfe:
1. Ist es wirklich das gesuchte Produkt als Einzelteil? (z. B. 13700 statt 13700K, Laptop statt Einzel-CPU, nur Teil eines Bundles → passt: false)
2. Haken: defekt, ungetestet, Pins verbogen, Abholung only, „ohne Gewähr“, fehlendes Zubehör.
3. Betrugssignale: Vorkasse, WhatsApp-Nummer, Preis unrealistisch niedrig, Druck zur Eile.
4. Bei Intel i7/i9 der 13. und 14. Generation: Hinweis auf bekanntes Stabilitätsproblem, Frage nach BIOS-Stand aufnehmen.

Antworte NUR mit JSON, ohne Erklärung, genau in dieser Form:
{{"passt": true, "score": 0, "risiken": ["..."], "frage_an_verkaeufer": "..."}}
score: 0–10, 10 = unbedenklich. risiken: kurze Stichpunkte, leer wenn nichts auffällt.
frage_an_verkaeufer: eine kurze, freundliche Frage auf Deutsch, die das größte Risiko klärt."""

BILD_PROMPT = """Das sind Fotos aus einer Kleinanzeige für: {produkt} (Titel: {titel}).
Beurteile die Bilder zusammen und antworte NUR mit JSON in dieser Form:
{{"echtes_foto": true, "modell_sichtbar": false, "ovp": false, "schaeden": false, "befund": "..."}}
echtes_foto: false, wenn es Stock-/Herstellerbilder oder Screenshots sind.
modell_sichtbar: true, wenn die Modellbezeichnung auf dem Produkt lesbar ist.
schaeden: true bei sichtbaren Schäden (verbogene Pins, Kratzer, Brandspuren).
befund: ein Satz auf Deutsch."""


@dataclass
class TextBefund:
    passt: bool
    score: int
    risiken: list[str] = field(default_factory=list)
    frage: str = ""


@dataclass
class BildBefund:
    echtes_foto: bool = True
    modell_sichtbar: bool = False
    ovp: bool = False
    schaeden: bool = False
    befund: str = ""
    anzahl_bilder: int = 0


def parse_json(text: str) -> dict:
    """Holt das erste JSON-Objekt aus einer Antwort, auch wenn ```json-Zäune drum sind."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError(f"Keine JSON-Antwort: {text[:200]}")
    return json.loads(m.group(0))


def lade_bild(url: str, timeout: int = 20) -> bytes | None:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and r.headers.get("Content-Type", "").startswith("image/"):
            return r.content
    except requests.RequestException as e:
        log.warning("Bild %s nicht ladbar: %s", url, e)
    return None


class Gemini:
    def __init__(self, api_key: str, modell: str = "gemini-2.5-flash", client=None,
                 pause_sekunden: float = 0, schlafen=time.sleep):
        self.modell = modell
        self._client = client or genai.Client(api_key=api_key)
        self._cfg = types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json")
        self.pause = pause_sekunden
        self._schlafen = schlafen
        self._letzter_aufruf = 0.0

    def _frage(self, inhalte, versuche: int = 3) -> dict:
        """Ein Gemini-Aufruf mit Mindestabstand und Wiederholung bei Rate-Limit (429)."""
        for versuch in range(1, versuche + 1):
            wartezeit = self.pause - (time.monotonic() - self._letzter_aufruf)
            if wartezeit > 0:
                self._schlafen(wartezeit)
            self._letzter_aufruf = time.monotonic()
            try:
                antwort = self._client.models.generate_content(model=self.modell, contents=inhalte, config=self._cfg)
                return parse_json(antwort.text or "")
            except Exception as e:  # noqa: BLE001
                text = str(e)
                if "429" not in text and "RESOURCE_EXHAUSTED" not in text:
                    raise
                if versuch == versuche:
                    raise
                m = re.search(r"retry in (\d+(?:\.\d+)?)", text, flags=re.I)
                pause = float(m.group(1)) + 1 if m else 60.0
                log.warning("Gemini Rate-Limit, warte %.0f s (Versuch %d/%d)", pause, versuch, versuche)
                self._schlafen(pause)
        raise RuntimeError("unreachable")

    def pruefe_text(self, produkt: str, titel: str, preis: float, zustand: str | None, beschreibung: str) -> TextBefund:
        prompt = TEXT_PROMPT.format(produkt=produkt, titel=titel, preis=f"{preis:.0f}",
                                    zustand=zustand or "keine Angabe", beschreibung=(beschreibung or "")[:4000])
        d = self._frage(prompt)
        return TextBefund(
            passt=bool(d.get("passt", False)),
            score=max(0, min(10, int(d.get("score", 0)))),
            risiken=[str(r) for r in d.get("risiken", []) or []],
            frage=str(d.get("frage_an_verkaeufer", "") or ""),
        )

    def pruefe_bilder(self, produkt: str, titel: str, bild_urls: list[str], max_bilder: int = 4) -> BildBefund:
        bilder = [b for b in (lade_bild(u) for u in bild_urls[:max_bilder]) if b]
        if not bilder:
            return BildBefund(echtes_foto=False, befund="keine Bilder ladbar", anzahl_bilder=0)
        teile = [BILD_PROMPT.format(produkt=produkt, titel=titel)]
        teile += [types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in bilder]
        d = self._frage(teile)
        return BildBefund(
            echtes_foto=bool(d.get("echtes_foto", True)),
            modell_sichtbar=bool(d.get("modell_sichtbar", False)),
            ovp=bool(d.get("ovp", False)),
            schaeden=bool(d.get("schaeden", False)),
            befund=str(d.get("befund", "") or ""),
            anzahl_bilder=len(bilder),
        )


def gesamt_score(text: TextBefund, bild: BildBefund) -> int:
    """Textscore als Basis; Stockbilder und Schäden senken deutlich, OVP/Modell sichtbar heben leicht."""
    s = text.score
    if not bild.echtes_foto:
        s -= 3
    if bild.schaeden:
        s -= 3
    if bild.modell_sichtbar:
        s += 1
    if bild.ovp:
        s += 1
    return max(0, min(10, s))
