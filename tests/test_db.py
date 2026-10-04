from deal_finder.config import PROJEKT_ORDNER, lade_config
from deal_finder.db import sync_suchen, verbinde


def test_schema_und_suchen():
    cfg = lade_config(PROJEKT_ORDNER / "config.yaml")
    con = verbinde(":memory:")
    sync_suchen(con, cfg.suchen)
    tabellen = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"searches", "listings", "price_history", "evaluations"} <= tabellen
    assert con.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 3
    # zweiter Sync darf nichts verdoppeln
    sync_suchen(con, cfg.suchen)
    assert con.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 3
