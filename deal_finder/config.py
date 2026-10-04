"""Lädt config.yaml und die Geheimnisse aus Umgebungsvariablen bzw. .env."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJEKT_ORDNER = Path(__file__).resolve().parent.parent
GEHEIMNISSE = ("APIFY_TOKEN", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def lade_env(pfad: Path = PROJEKT_ORDNER / ".env") -> None:
    """Einfache .env-Datei einlesen. Vorhandene Umgebungsvariablen haben Vorrang."""
    if not pfad.exists():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        os.environ.setdefault(name.strip(), wert.strip().strip('"').strip("'"))


@dataclass
class Suche:
    name: str
    produkt_key: str
    apify_task_id: str = ""   # leer = wird beim ersten Lauf automatisch angelegt
    aktiv: bool = True
    ausschluss: list[str] = field(default_factory=list)  # Titel mit diesen Wörtern zählen nicht (z. B. "3070 ti")


@dataclass
class Config:
    datenbank: Path
    suchen: list[Suche]
    sammler: dict
    analyst: dict

    def geheimnis(self, name: str) -> str:
        wert = os.environ.get(name, "")
        if not wert:
            raise RuntimeError(f"Umgebungsvariable {name} fehlt (siehe .env.example)")
        return wert

    def fehlende_geheimnisse(self) -> list[str]:
        return [n for n in GEHEIMNISSE if not os.environ.get(n)]


def lade_config(pfad: Path = PROJEKT_ORDNER / "config.yaml") -> Config:
    lade_env()
    daten = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    db = Path(daten["datenbank"])
    if not db.is_absolute():
        db = PROJEKT_ORDNER / db
    suchen = [Suche(**s) for s in daten["suchen"]]
    analyst = dict(daten["analyst"])
    analyst["produkt_ausschluss"] = {s.produkt_key: s.ausschluss for s in suchen}
    return Config(datenbank=db, suchen=suchen, sammler=daten["sammler"], analyst=analyst)
