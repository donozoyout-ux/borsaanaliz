import logging
import os

import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    if not is_configured():
        logger.warning("Telegram ayarları eksik (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=20)
        ok = resp.status_code == 200
        if not ok:
            logger.error("Telegram hata: %s %s", resp.status_code, resp.text[:300])
        return ok
    except requests.RequestException as exc:
        logger.error("Telegram isteği başarısız: %s", exc)
        return False


def format_signal_message(symbol: str, name: str, price: float, signal: dict, market_state: str = "") -> str:
    lines = [
        f"<b>{signal['emoji']} {symbol} — {signal['title']}</b>",
        f"💹 Fiyat: <b>{price}</b> TL  ({name})",
        signal["detail"],
        "",
        f"⏰ {market_state}",
    ]
    return "\n".join(lines)
