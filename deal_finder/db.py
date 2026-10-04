"""SQLite-Datenbank: Tabellen anlegen und Verbindung liefern."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT NOT NULL UNIQUE,
    apify_task_id TEXT NOT NULL,
    produkt_key   TEXT NOT NULL,
    aktiv         INTEGER NOT NULL DEFAULT 1,
    tag_null_am   TEXT               -- Zeitpunkt der Komplettaufnahme, NULL = noch nicht gelaufen
);

CREATE TABLE IF NOT EXISTS listings (
    id             TEXT PRIMARY KEY,  -- Kleinanzeigen-ID
    search_id      INTEGER NOT NULL REFERENCES searches(id),
    title          TEXT NOT NULL,
    price          REAL,              -- NULL = kein Preis / VB ohne Zahl
    shipping_price REAL,
    condition      TEXT,
    address        TEXT,
    description    TEXT,
    seller_name    TEXT,
    seller_url     TEXT,
    image_urls     TEXT NOT NULL DEFAULT '[]',  -- JSON-Liste
    url            TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'aktiv',  -- aktiv | verschwunden
    raw_json       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listings_search ON listings(search_id, status);

CREATE TABLE IF NOT EXISTS price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL REFERENCES listings(id),
    price      REAL,
    seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);

CREATE TABLE IF NOT EXISTS evaluations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id         TEXT NOT NULL REFERENCES listings(id),
    bewertet_am        TEXT NOT NULL,
    preis_bewertet     REAL,          -- Anzeigenpreis zum Zeitpunkt der Bewertung
    marktpreis         REAL,
    vergleichsanzahl   INTEGER,
    abstand_prozent    REAL,
    ergebnis           TEXT NOT NULL, -- deal | zu_wenig_daten | aussortiert | zu_teuer | ki_abgelehnt
    grund              TEXT,
    ki_score           INTEGER,
    ki_risiken         TEXT,          -- JSON-Liste
    ki_bildbefund      TEXT,
    ki_frage           TEXT,
    marge_euro         REAL,
    gemeldet_am        TEXT,
    telegram_msg_id    INTEGER,
    carl_reaktion      TEXT           -- angeschrieben | gekauft | uninteressant
);
CREATE INDEX IF NOT EXISTS idx_evaluations_listing ON evaluations(listing_id);
"""


def verbinde(pfad: Path | str) -> sqlite3.Connection:
    """Öffnet die Datenbank, legt Ordner und Tabellen bei Bedarf an."""
    if str(pfad) != ":memory:":
        Path(pfad).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(pfad))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    return con


def sync_suchen(con: sqlite3.Connection, suchen) -> None:
    """Spiegelt die Suchen aus config.yaml in die Tabelle searches."""
    for s in suchen:
        con.execute(
            """INSERT INTO searches (query, apify_task_id, produkt_key, aktiv)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(query) DO UPDATE SET
                 apify_task_id = excluded.apify_task_id,
                 produkt_key   = excluded.produkt_key,
                 aktiv         = excluded.aktiv""",
            (s.name, s.apify_task_id, s.produkt_key, int(s.aktiv)),
        )
    con.commit()
