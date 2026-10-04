"""Gemini-Modul mit Fake-Client, kein Netz nötig."""
from types import SimpleNamespace

import pytest

from deal_finder.gemini import BildBefund, Gemini, TextBefund, gesamt_score, parse_json


class FakeModels:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def generate_content(self, model, contents, config):
        self.aufrufe.append(contents)
        return SimpleNamespace(text=self.antworten.pop(0))


def fake_gemini(*antworten):
    client = SimpleNamespace(models=FakeModels(antworten))
    return Gemini("KEY", client=client), client.models


def test_parse_json():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Hier: {"passt": true, "score": 8}') == {"passt": True, "score": 8}
    with pytest.raises(ValueError):
        parse_json("kein json")


def test_pruefe_text():
    g, m = fake_gemini('{"passt": true, "score": 8, "risiken": ["ungetestet"], "frage_an_verkaeufer": "Läuft sie?"}')
    b = g.pruefe_text("i7-13700k", "Intel i7 13700K", 195, "Sehr Gut", "Top Zustand")
    assert b == TextBefund(True, 8, ["ungetestet"], "Läuft sie?")
    assert "Intel i7 13700K" in m.aufrufe[0] and "195 €" in m.aufrufe[0]


def test_pruefe_text_robust():
    g, _ = fake_gemini('{"passt": false, "score": 99}')
    b = g.pruefe_text("x", "t", 1, None, None)
    assert b.passt is False and b.score == 10 and b.risiken == [] and b.frage == ""


def test_pruefe_bilder_ohne_ladbare_bilder(monkeypatch):
    monkeypatch.setattr("deal_finder.gemini.lade_bild", lambda url, timeout=20: None)
    g, m = fake_gemini()
    b = g.pruefe_bilder("i7", "t", ["http://x/1.jpg"])
    assert b.echtes_foto is False and b.anzahl_bilder == 0 and m.aufrufe == []


def test_pruefe_bilder(monkeypatch):
    monkeypatch.setattr("deal_finder.gemini.lade_bild", lambda url, timeout=20: b"\xff\xd8bild")
    g, m = fake_gemini('{"echtes_foto": true, "modell_sichtbar": true, "ovp": true, "schaeden": false, "befund": "ok"}')
    b = g.pruefe_bilder("i7", "t", [f"http://x/{i}.jpg" for i in range(6)], max_bilder=4)
    assert b.anzahl_bilder == 4 and b.modell_sichtbar and b.ovp and not b.schaeden
    assert len(m.aufrufe[0]) == 5  # Prompt + 4 Bilder


def test_gesamt_score():
    t = TextBefund(True, 8)
    assert gesamt_score(t, BildBefund()) == 8
    assert gesamt_score(t, BildBefund(echtes_foto=False)) == 5
    assert gesamt_score(t, BildBefund(schaeden=True, modell_sichtbar=True)) == 6
    assert gesamt_score(t, BildBefund(modell_sichtbar=True, ovp=True)) == 10
    assert gesamt_score(TextBefund(True, 2), BildBefund(echtes_foto=False)) == 0


def test_rate_limit_wiederholung():
    schlaf = []
    client = SimpleNamespace(models=FakeModels(['{"passt": true, "score": 7}']))
    aufrufe = []
    orig = client.models.generate_content

    def flaky(model, contents, config):
        aufrufe.append(1)
        if len(aufrufe) == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED ... Please retry in 3.5s.")
        return orig(model, contents, config)

    client.models.generate_content = flaky
    g = Gemini("KEY", client=client, pause_sekunden=0, schlafen=schlaf.append)
    assert g.pruefe_text("x", "t", 1, None, "").score == 7
    assert len(aufrufe) == 2 and schlaf == [4.5]


def test_pause_zwischen_aufrufen():
    schlaf = []
    g, _ = fake_gemini('{"passt": true, "score": 7}', '{"passt": true, "score": 7}')
    g.pause, g._schlafen = 13, schlaf.append
    g.pruefe_text("x", "t", 1, None, "")
    g.pruefe_text("x", "t", 1, None, "")
    assert len(schlaf) == 1 and 12 < schlaf[0] <= 13
