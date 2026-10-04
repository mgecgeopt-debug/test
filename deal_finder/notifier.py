"""Telegram: Nachrichten und Warnungen an Carl schicken.

Nutzt die Bot-API direkt über requests. So lässt sich alles ohne laufende
Event-Loop testen; die Knöpfe und Reaktionen kommen in einem späteren Schritt.
"""
import logging

import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org"


class Telegram:
    def __init__(self, token: str, chat_id: str, api_basis: str = API, timeout: int = 20):
        self._basis = f"{api_basis}/bot{token}"
        self.chat_id = chat_id
        self.timeout = timeout

    def _post(self, methode: str, **daten) -> dict:
        antwort = requests.post(f"{self._basis}/{methode}", json=daten, timeout=self.timeout)
        body = antwort.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {methode}: {body.get('description', antwort.text)}")
        return body["result"]

    def sende_text(self, text: str, parse_mode: str | None = "HTML", **extra) -> int:
        """Schickt eine Textnachricht, gibt die message_id zurück."""
        result = self._post("sendMessage", chat_id=self.chat_id, text=text,
                            parse_mode=parse_mode, disable_web_page_preview=True, **extra)
        return result["message_id"]

    def sende_foto(self, foto_url: str, bildtext: str, parse_mode: str | None = "HTML", **extra) -> int:
        """Schickt ein Bild mit Bildtext (max. 1024 Zeichen), gibt die message_id zurück."""
        result = self._post("sendPhoto", chat_id=self.chat_id, photo=foto_url,
                            caption=bildtext[:1024], parse_mode=parse_mode, **extra)
        return result["message_id"]

    def warnung(self, text: str) -> None:
        """Fehler-Warnung. Darf selbst nie eine Ausnahme werfen, damit der Sammler weiterläuft."""
        try:
            self.sende_text(f"⚠️ <b>Deal-Finder Warnung</b>\n{text}")
        except Exception as e:  # noqa: BLE001
            log.error("Warnung konnte nicht gesendet werden: %s", e)

    def hole_updates(self, offset: int | None = None) -> list[dict]:
        """getUpdates ohne Warten: nur Knopfdrücke (callback_query)."""
        daten = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if offset is not None:
            daten["offset"] = offset
        return self._post("getUpdates", **daten)

    def antworte_callback(self, callback_id: str, text: str) -> None:
        self._post("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def setze_knoepfe(self, message_id: int, knoepfe: list[list[dict]] | None) -> None:
        """Ersetzt die Knöpfe unter einer Nachricht (None = entfernen)."""
        self._post("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                   reply_markup={"inline_keyboard": knoepfe or []})

    def bot_info(self) -> dict:
        """Prüft Token: gibt Name und Username des Bots zurück."""
        return self._post("getMe")
