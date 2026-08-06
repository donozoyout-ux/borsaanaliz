import csv
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

BASE_DIR = Path(__file__).resolve().parent
SYMBOLS_FILE = BASE_DIR / "data" / "bist_symbols.csv"

_session = requests.Session()
_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
})
_YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]

_mem_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_MEM_TTL = 20

_intraday_cache: dict[str, tuple[float, list[dict]]] = {}
_INTRADAY_TTL = 10

_history_cache: dict[str, tuple[float, list[dict]]] = {}
_HISTORY_TTL = 300


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    s = s.replace(".IS", "")
    s = s.replace(".E", "").replace(".IS", "")
    s = s.split(".")[0]
    return s.strip()


def yahoo_symbol(symbol: str) -> str:
    return f"{normalize_symbol(symbol)}.IS"


def load_symbols() -> list[dict]:
    if not SYMBOLS_FILE.exists():
        return []
    rows = []
    with SYMBOLS_FILE.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({"symbol": r["symbol"].strip().upper(), "name": r.get("name", "").strip()})
    return rows


def search_symbols(query: str, limit: int = 12) -> list[dict]:
    q = query.strip().upper()
    if not q:
        return []
    all_syms = load_symbols()
    exact = [s for s in all_syms if s["symbol"] == q]
    starts = [s for s in all_syms if s["symbol"].startswith(q)]
    contains = [s for s in all_syms if q in s["symbol"] or q in s["name"].upper()]
    merged = []
    seen = set()
    for s in exact + starts + contains:
        if s["symbol"] in seen:
            continue
        seen.add(s["symbol"])
        merged.append(s)
    return merged[:limit]


def _fetch_chart(symbol: str, interval: str, range_str: str) -> Optional[dict]:
    clean = yahoo_symbol(symbol)
    last_err = None
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{clean}"
        params = {"interval": interval, "range": range_str, "includePrePost": "false"}
        try:
            resp = _session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("chart") and data["chart"].get("result"):
                    return data["chart"]["result"][0]
            elif resp.status_code == 429:
                time.sleep(1)
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_err = str(exc)
    raise RuntimeError(f"Veri alınamadı ({last_err or 'bilinmeyen hata'})")


def _result_to_bars(result: dict) -> list[dict]:
    ts = result.get("timestamp") or []
    q = result.get("indicators", {}).get("quote", [{}])[0]
    if not ts:
        return []
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    vols = q.get("volume") or []
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
        if o is None or c is None or h is None or l is None:
            continue
        bars.append({
            "t": int(t),
            "o": round(o, 4),
            "h": round(h, 4),
            "l": round(l, 4),
            "c": round(c, 4),
            "v": int(v or 0),
        })
    return bars


def get_history(symbol: str, range_str: str = "6mo", interval: str = "1d") -> list[dict]:
    clean = normalize_symbol(symbol)
    key = f"{clean}:{range_str}:{interval}"
    now = time.time()
    with _cache_lock:
        cached = _history_cache.get(key)
        if cached and now - cached[0] < _HISTORY_TTL:
            return cached[1]
    result = _fetch_chart(symbol, interval, range_str)
    bars = _result_to_bars(result)
    with _cache_lock:
        _history_cache[key] = (now, bars)
    return bars


def _fetch_intraday_once(symbol: str) -> list[dict]:
    try:
        result = _fetch_chart(symbol, "1m", "1d")
    except RuntimeError:
        result = _fetch_chart(symbol, "5m", "1d")
    return _result_to_bars(result)


def _bars_are_stale(bars: list[dict], max_age: int = 300) -> bool:
    if not bars:
        return True
    last_t = bars[-1]["t"]
    now = time.time()
    return now - last_t > max_age


def get_intraday(symbol: str) -> list[dict]:
    clean = normalize_symbol(symbol)
    now = time.time()
    with _cache_lock:
        cached = _intraday_cache.get(clean)
        if cached and now - cached[0] < _INTRADAY_TTL:
            return cached[1]
    bars = _fetch_intraday_once(clean)
    if market_is_open() and _bars_are_stale(bars):
        time.sleep(2)
        bars = _fetch_intraday_once(clean)
    with _cache_lock:
        _intraday_cache[clean] = (now, bars)
    return bars


def get_price(symbol: str) -> Optional[float]:
    now = time.time()
    clean = normalize_symbol(symbol)
    with _cache_lock:
        cached = _mem_cache.get(clean)
        if cached and now - cached[0] < _MEM_TTL:
            return cached[1].get("price")
    quote = get_quote(clean)
    if not quote:
        return None
    return quote.get("price")


def get_quote(symbol: str) -> Optional[dict]:
    clean = normalize_symbol(symbol)
    now = time.time()
    with _cache_lock:
        cached = _mem_cache.get(clean)
        if cached and now - cached[0] < _MEM_TTL:
            return cached[1]

    try:
        from live import tv_quote
        tvq = tv_quote(clean)
    except Exception:
        tvq = None
    if tvq:
        with _cache_lock:
            _mem_cache[clean] = (now, tvq)
        return tvq

    try:
        result = _fetch_chart(clean, "1m", "1d")
    except RuntimeError:
        return None

    bars = _result_to_bars(result)
    meta = result.get("meta", {})
    if not bars:
        return None

    last = bars[-1]
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not prev_close and len(bars) > 1:
        prev_close = bars[-2]["c"]
    if not prev_close:
        prev_close = last["o"]

    price = last["c"]
    if price is None or price <= 0:
        price = meta.get("regularMarketPrice")

    change = price - prev_close if prev_close else 0
    change_pct = (change / prev_close * 100) if prev_close else 0

    day_high = meta.get("regularMarketDayHigh") or max(b["h"] for b in bars)
    day_low = meta.get("regularMarketDayLow") or min(b["l"] for b in bars)
    volume = sum(b["v"] for b in bars)
    market_state = meta.get("marketState", "")
    market_time = meta.get("regularMarketTime")

    quote = {
        "symbol": clean,
        "name": meta.get("shortName") or meta.get("longName") or clean,
        "price": round(float(price), 4),
        "prev_close": round(float(prev_close), 4) if prev_close else None,
        "change": round(float(change), 4),
        "change_pct": round(float(change_pct), 2),
        "day_high": round(float(day_high), 4),
        "day_low": round(float(day_low), 4),
        "volume": int(volume),
        "market_state": market_state,
        "market_time": market_time,
        "currency": meta.get("currency", "TRY"),
        "exchange": meta.get("exchangeName", ""),
        "source": "yahoo",
        "time": market_time or now,
    }
    with _cache_lock:
        _mem_cache[clean] = (now, quote)
    return quote


from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=3))


def market_is_open(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(IST)
    weekday = now.weekday()
    if weekday >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 + 5 <= t < 18 * 60


def market_label(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return "PİYASA AÇIK" if market_is_open(now) else "PİYASA KAPALI"
