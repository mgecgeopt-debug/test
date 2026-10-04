"""Apify-Client gegen lokalen Fake-Server: Task starten, Status, Dataset."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from deal_finder.apify import Apify, ApifyFehler

ITEMS = json.loads((Path(__file__).parent / "beispiel_items.json").read_text(encoding="utf-8"))


class FakeApify(BaseHTTPRequestHandler):
    status_nach_start = "SUCCEEDED"
    anfragen: list = []

    def _antwort(self, body, code=200):
        out = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length", 0))
        FakeApify.anfragen.append(("POST", self.path, json.loads(self.rfile.read(laenge) or b"{}"),
                                   self.headers.get("Authorization")))
        if "/actor-tasks/KAPUTT/" in self.path:
            return self._antwort({"error": "nope"}, 404)
        self._antwort({"data": {"id": "run1", "status": FakeApify.status_nach_start, "defaultDatasetId": "ds1"}})

    def do_GET(self):
        FakeApify.anfragen.append(("GET", self.path, None, None))
        if self.path.startswith("/actor-runs/run1"):
            return self._antwort({"data": {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds1"}})
        if self.path.startswith("/datasets/ds1/items"):
            return self._antwort(ITEMS)
        self._antwort({}, 404)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeApify)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_lauf_komplett(server):
    FakeApify.anfragen.clear()
    FakeApify.status_nach_start = "SUCCEEDED"
    items = Apify("TOK", api_basis=server).lauf("task1", {"query": "i7 13700K", "maxItems": 50}, 120)
    assert len(items) == 3 and items[0]["id"] == "2001"
    methode, pfad, body, auth = FakeApify.anfragen[0]
    assert pfad == "/actor-tasks/task1/runs?waitForFinish=120"
    assert body["query"] == "i7 13700K" and auth == "Bearer TOK"


def test_lauf_wartet_wenn_noch_laeuft(server):
    FakeApify.anfragen.clear()
    FakeApify.status_nach_start = "RUNNING"
    items = Apify("TOK", api_basis=server).lauf("task1")
    assert len(items) == 3
    assert any(p.startswith("/actor-runs/run1") for _, p, _, _ in FakeApify.anfragen)


def test_fehler(server):
    FakeApify.status_nach_start = "SUCCEEDED"
    with pytest.raises(ApifyFehler, match="HTTP 404"):
        Apify("TOK", api_basis=server).lauf("KAPUTT")
    FakeApify.status_nach_start = "FAILED"
    with pytest.raises(ApifyFehler, match="FAILED"):
        Apify("TOK", api_basis=server).lauf("task1")
