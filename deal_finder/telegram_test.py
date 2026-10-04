"""Schritt 2: Testnachricht an Carl. Aufruf: python -m deal_finder.telegram_test"""
import sys
from datetime import datetime

from deal_finder.config import lade_config
from deal_finder.notifier import Telegram


def main() -> int:
    cfg = lade_config()
    fehlend = [n for n in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if n in cfg.fehlende_geheimnisse()]
    if fehlend:
        print("Fehlt:", ", ".join(fehlend))
        return 1
    tg = Telegram(cfg.geheimnis("TELEGRAM_BOT_TOKEN"), cfg.geheimnis("TELEGRAM_CHAT_ID"))
    info = tg.bot_info()
    print(f"Bot: {info.get('first_name')} (@{info.get('username')})")
    msg_id = tg.sende_text(
        f"✅ <b>Deal-Finder Testnachricht</b>\n"
        f"{datetime.now():%d.%m.%Y %H:%M}\n"
        f"Aktive Suchen: {', '.join(s.name for s in cfg.suchen if s.aktiv)}"
    )
    print(f"Gesendet, message_id={msg_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
