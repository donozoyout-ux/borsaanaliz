import email.utils
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import requests

_CACHE_TTL = 10 * 60
_MAX_AGE = 30 * 24 * 3600  # 30 günden eski haberleri gösterme
_GOOGLE_MIN_GAP = 5  # Google'a iki istek arası minimum saniye (burst koruması)
_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()
_last_google_at: float = 0

_session = requests.Session()
_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "*/*",
})

# Fiyatı hareket ettirebilecek önemli başlık anahtar kelimeleri
IMPORTANT_KEYWORDS = [
    "temettü", "bedelli", "bedelsiz", "sermaye artırımı", "kâr açıkladı", "kar açıkladı",
    "kârını açıkladı", "zarar açıkladı", "zarar etti", "sözleşme", "anlaşma", "satın alma",
    "birleşme", "ihale", "iştirak", "geri alım", "hisse geri alımı", "halka arz", "kotasyon",
    "istifa", "görevden", "soruşturma", "ceza", "denetim", "rekor", "tavan", "taban", "uyarı",
    "kredi", "borç yapılandırma", "yeni rekor", "yüzde", "%", "spk", "borsa istanbul onay",
]

_GOOGLE_ATOM = "http://www.w3.org/2005/Atom"


def _clean_title(title: str) -> str:
    t = title.strip()
    for sep in [" - ", " | ", " – "]:
        parts = t.split(sep)
        if len(parts) > 1:
            t = parts[-1].strip()
            break
    return t


def _is_important(title: str) -> bool:
    low = title.lower()
    return any(k in low for k in IMPORTANT_KEYWORDS)


def _google_news(symbol: str, limit: int = 5) -> list[dict]:
    q = f"{symbol} borsa"
    url = "https://news.google.com/rss/search?" + quote(q) + "&hl=tr&gl=TR&ceid=TR:tr"
    try:
        resp = _session.get(url, timeout=12)
        if resp.status_code != 200 or "<?xml" not in resp.text[:200]:
            return []
        root = ET.fromstring(resp.text)
        out = []
        for item in root.findall(".//item")[:limit * 2]:
            title = item.findtext("title") or ""
            pub = item.findtext("pubDate") or ""
            link = item.findtext("link") or ""
            source_el = item.find("source")
            source = source_el.text if source_el is not None and source_el.text else "Google Haberler"
            ts = None
            try:
                if pub:
                    ts = int(email.utils.parsedate_to_datetime(pub).timestamp())
            except Exception:
                pass
            out.append({
                "title": _clean_title(title),
                "source": source,
                "url": link,
                "time": ts,
                "important": _is_important(title),
            })
        return out[:limit]
    except (requests.RequestException, ET.ParseError):
        return []


def _bing_news(symbol: str, limit: int = 5) -> list[dict]:
    url = "https://www.bing.com/news/search"
    params = {"q": f"{symbol} borsa hisse", "format": "rss", "setlang": "tr"}
    try:
        resp = _session.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        out = []
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            source_el = item.find(f"source/{{http://www.w3.org/2005/Atom}}name")
            if source_el is None:
                source_el = item.findtext("n:name", namespaces={"n": _GOOGLE_ATOM})
            source = source_el if isinstance(source_el, str) else (source_el.text if source_el is not None else "")
            pub = item.findtext("pubDate") or item.findtext(f"{_GOOGLE_ATOM}publishDate") or ""
            ts = None
            try:
                if pub:
                    ts = int(email.utils.parsedate_to_datetime(pub).timestamp())
            except Exception:
                pass
            out.append({
                "title": _clean_title(title),
                "source": source or "Bing Haberler",
                "url": link,
                "time": ts,
                "important": _is_important(title),
            })
        return out
    except (requests.RequestException, ET.ParseError):
        return []


def get_news(symbol: str, limit: int = 8, force: bool = False) -> list[dict]:
    clean = symbol.upper().replace(".IS", "")
    now = time.time()
    with _cache_lock:
        cached = _cache.get(clean)
        if cached and not force and now - cached[0] < _CACHE_TTL:
            return cached[1]

    global _last_google_at
    items = []
    with _cache_lock:
        gap_ok = now - _last_google_at >= _GOOGLE_MIN_GAP
    if gap_ok:
        with _cache_lock:
            _last_google_at = now
        items += _google_news(clean)
    time.sleep(0.6)
    items += _bing_news(clean)

    seen = set()
    deduped = []
    for it in items:
        key = it["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    if _MAX_AGE:
        cutoff = now - _MAX_AGE
        fresh = [it for it in deduped if (it.get("time") or 0) >= cutoff]
        deduped = fresh if fresh else []

    result = deduped[:limit]
    with _cache_lock:
        _cache[clean] = (now, result)
    return result


def format_news_message(symbol: str, news_items: list[dict]) -> str:
    lines = [f"📰 <b>{symbol} — ÖNEMLİ HABER</b>", ""]
    for n in news_items[:3]:
        title = n["title"]
        src = n.get("source") or ""
        lines.append(f"• {title}")
        if src:
            lines.append(f"   <i>{src}</i>")
        lines.append("")
    return "\n".join(lines)
