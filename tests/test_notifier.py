"""Testet den Telegram-Client gegen einen lokalen Fake-Server (kein Netz nötig)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from deal_finder.notifier import Telegram


class FakeTelegram(BaseHTTPRequestHandler):
    aufrufe: list = []

    def do_POST(self):
        laenge = int(self.headers.get("Content-Length", 0))
        daten = json.loads(self.rfile.read(laenge) or b"{}")
        methode = self.path.rsplit("/", 1)[-1]
        FakeTelegram.aufrufe.append((self.path, daten))
        if "FEHLER" in self.path:
            body = {"ok": False, "description": "Unauthorized"}
        elif methode == "getMe":
            body = {"ok": True, "result": {"first_name": "Test", "username": "testbot"}}
        else:
            body = {"ok": True, "result": {"message_id": 42}}
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # Server leise halten
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeTelegram)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_sende_text(server):
    FakeTelegram.aufrufe.clear()
    tg = Telegram("TOKEN123", "999", api_basis=server)
    assert tg.sende_text("Hallo") == 42
    pfad, daten = FakeTelegram.aufrufe[0]
    assert pfad == "/botTOKEN123/sendMessage"
    assert daten["chat_id"] == "999" and daten["text"] == "Hallo"


def test_bot_info_und_foto(server):
    tg = Telegram("TOKEN123", "999", api_basis=server)
    assert tg.bot_info()["username"] == "testbot"
    assert tg.sende_foto("http://bild", "x" * 2000) == 42
    assert len(FakeTelegram.aufrufe[-1][1]["caption"]) == 1024


def test_fehler_wirft_ausnahme(server):
    tg = Telegram("FEHLER", "999", api_basis=server)
    with pytest.raises(RuntimeError, match="Unauthorized"):
        tg.sende_text("x")


def test_warnung_wirft_nie(server):
    Telegram("FEHLER", "999", api_basis=server).warnung("kaputt")  # darf nicht crashen
