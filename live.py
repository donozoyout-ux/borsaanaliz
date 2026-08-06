import json
import threading
import time

import requests

_session = requests.Session()
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_TTL = 5
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}

_COLUMNS = ["name", "description", "close", "change", "change_abs", "volume", "high", "low", "update_mode"]


def tv_quote(symbol: str) -> dict | None:
    now = time.time()
    with _cache_lock:
        cached = _cache.get(symbol)
        if cached and now - cached[0] < _TTL:
            return cached[1]
    payload = {
        "symbols": {"tickers": [f"BIST:{symbol}"], "query": {"types": []}},
        "columns": _COLUMNS,
    }
    try:
        resp = _session.post(
            "https://scanner.tradingview.com/turkey/scan",
            data=json.dumps(payload),
            timeout=10,
            headers=_HEADERS,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data") or []
        if not data:
            return None
        row = data[0]["d"]
        name, desc, close, chg_pct, chg_abs, volume, high, low = row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
        if close is None:
            return None
        price = float(close)
        change_abs = float(chg_abs) if chg_abs is not None else 0.0
        quote = {
            "symbol": symbol,
            "name": (desc or name or symbol),
            "price": round(price, 4),
            "prev_close": round(price - change_abs, 4),
            "change": round(change_abs, 4),
            "change_pct": round(float(chg_pct), 2) if chg_pct is not None else 0.0,
            "day_high": round(float(high), 4) if high is not None else None,
            "day_low": round(float(low), 4) if low is not None else None,
            "volume": int(volume or 0),
            "market_state": "",
            "market_time": now,
            "currency": "TRY",
            "exchange": "IST",
            "source": "tradingview",
            "time": now,
        }
        with _cache_lock:
            _cache[symbol] = (now, quote)
        return quote
    except Exception:
        return None


def live_price(symbol: str) -> float | None:
    q = tv_quote(symbol)
    return q["price"] if q else None
