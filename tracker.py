import json
import logging
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

socket.setdefaulttimeout(25)

import db
from bist_data import (get_history, get_intraday, get_quote, market_is_open,
                       market_label, normalize_symbol, seconds_until_open)
from forecast import build_forecast, format_forecast_message
from fundamentals import get_fundamentals
from indicators import build_snapshot, evaluate_signals
from news import format_news_message, get_news
from strategy import build_strategy, format_strategy_message
from telegram_notifier import format_signal_message, is_configured, send_telegram_message

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "data" / "tracker_state.json"
SIGNALS_FILE = BASE_DIR / "data" / "signals.json"
SNAPSHOT_FILE = BASE_DIR / "data" / "snapshot.json"
LOG_FILE = BASE_DIR / "data" / "tracker_logs.json"

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 60
MAX_SIGNALS = 100
MAX_LOGS = 200

_state_lock = threading.Lock()
_tracked: dict[str, dict] = {}
_worker_thread: threading.Thread | None = None
_worker_stop_event: threading.Event | None = None
_cycle_locks: dict[str, threading.Lock] = {}
_cycle_locks_lock = threading.Lock()
_last_activity = time.time()
_last_activity_lock = threading.Lock()


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_state() -> None:
    global _tracked
    with _state_lock:
        data = _load_json(STATE_FILE, {})
        _tracked = data.get("tracked", {})
        for sym in list(_tracked.keys()):
            if _tracked[sym].get("active", True) is False:
                del _tracked[sym]


def _save_state() -> None:
    with _state_lock:
        _save_json(STATE_FILE, {"tracked": _tracked})


def get_signals(symbol: str = None) -> list[dict]:
    if symbol:
        clean = normalize_symbol(symbol)
        return db.get_stored_signals(clean)
    all_sigs: list[dict] = []
    for sym in tracked_symbols():
        all_sigs += db.get_stored_signals(sym)
    return all_sigs


def _add_signal(sig: dict) -> None:
    db.store_signal(sig)
    data = _load_json(SIGNALS_FILE, {"signals": []})
    sigs = data.setdefault("signals", [])
    sigs.append(sig)
    sigs = sigs[-MAX_SIGNALS:]
    data["signals"] = sigs
    _save_json(SIGNALS_FILE, data)


def get_logs() -> list[str]:
    return _load_json(LOG_FILE, {"logs": []}).get("logs", [])


def _add_log(msg: str) -> None:
    data = _load_json(LOG_FILE, {"logs": []})
    logs = data.setdefault("logs", [])
    logs.append(f"[{_now_str()}] {msg}")
    data["logs"] = logs[-MAX_LOGS:]
    _save_json(LOG_FILE, data)


def get_snapshot(symbol: str = None) -> dict:
    data = _load_json(SNAPSHOT_FILE, {"snapshots": {}})
    snaps = data.get("snapshots", {})
    if symbol:
        return snaps.get(normalize_symbol(symbol), {})
    return snaps


def _set_snapshot(symbol: str, snap: dict) -> None:
    data = _load_json(SNAPSHOT_FILE, {"snapshots": {}})
    snaps = data.setdefault("snapshots", {})
    snaps[normalize_symbol(symbol)] = snap
    _save_json(SNAPSHOT_FILE, data)


def set_snapshot_now(symbol: str, snapshot: dict) -> None:
    clean = normalize_symbol(symbol)
    _set_snapshot(clean, snapshot)
    db.store_snapshot(clean, snapshot)


def tracked_symbols() -> list[str]:
    with _state_lock:
        return list(_tracked.keys())


def is_tracking(symbol: str) -> bool:
    clean = normalize_symbol(symbol)
    with _state_lock:
        return clean in _tracked


def set_focus(symbol: str) -> str:
    clean = normalize_symbol(symbol)
    with _state_lock:
        for sym in list(_tracked.keys()):
            if sym != clean:
                _tracked.pop(sym, None)
        state = _tracked.get(clean) or {"started_at": _now_str()}
        state["active"] = True
        state["last_cycle"] = None
        _tracked[clean] = state
    _save_state()
    _ensure_worker()
    return clean


def stop(symbol: str) -> None:
    clean = normalize_symbol(symbol)
    with _state_lock:
        _tracked.pop(clean, None)
    _save_state()


def stop_all() -> None:
    with _state_lock:
        _tracked.clear()
    _save_state()


def _load_symbol_state(symbol: str) -> dict:
    with _state_lock:
        return dict(_tracked.get(symbol, {}))


def _save_symbol_state(symbol: str, state: dict) -> None:
    with _state_lock:
        if symbol in _tracked:
            _tracked[symbol].update(state)
    _save_state()


def collect_snapshot(symbol: str) -> dict | None:
    """Anlık tam snapshot üretir (signal göndermez, DB yazmaz). Sayfa açılışında kullanılır."""
    clean = normalize_symbol(symbol)
    quote = get_quote(clean)
    if not quote:
        return None

    history = get_history(clean, "6mo", "1d")
    if len(history) < 30:
        history = get_history(clean, "3mo", "1d")
    if len(history) < 15:
        return None

    intraday = get_intraday(clean)
    closes = [b["c"] for b in history]
    highs = [b["h"] for b in history]
    lows = [b["l"] for b in history]
    volumes = [b["v"] for b in history]

    symbol_state = _load_symbol_state(clean)
    ind, _signals = evaluate_signals(clean, quote, closes, highs, lows, volumes, intraday, symbol_state)

    fundamentals = get_fundamentals(clean)
    news_items = get_news(clean)
    forecast = build_forecast(clean, quote, ind, history, fundamentals)

    snapshot = build_snapshot(clean, quote, ind, intraday, trend=_derive_trend(ind, quote["price"]))
    snapshot["market_label"] = market_label()
    snapshot["fundamentals"] = fundamentals
    snapshot["news"] = news_items
    snapshot["forecast"] = forecast
    snapshot["strategy"] = build_strategy(clean, quote, ind, forecast)
    return snapshot


def _cycle(symbol: str) -> None:
    if not is_tracking(symbol):
        return
    try:
        quote = get_quote(symbol)
        if not quote:
            _add_log(f"{symbol}: fiyat alınamadı, tekrar deneniyor.")
            return

        history = get_history(symbol, "6mo", "1d")
        if len(history) < 30:
            history = get_history(symbol, "3mo", "1d")
        if len(history) < 15:
            _add_log(f"{symbol}: yetersiz geçmiş veri ({len(history)} bar).")
            return

        intraday = get_intraday(symbol)
        if not intraday:
            _add_log(f"{symbol}: gün içi veri alınamadı.")

        db.store_bars(symbol, "1d", history)
        if intraday:
            db.store_bars(symbol, "1m", intraday)

        closes = [b["c"] for b in history]
        highs = [b["h"] for b in history]
        lows = [b["l"] for b in history]
        volumes = [b["v"] for b in history]

        symbol_state = _load_symbol_state(symbol)
        ind, signals = evaluate_signals(symbol, quote, closes, highs, lows, volumes, intraday, symbol_state)
        symbol_state["last_cycle"] = _now_str()
        _save_symbol_state(symbol, symbol_state)

        fundamentals = get_fundamentals(symbol)
        news_items = get_news(symbol)
        forecast = build_forecast(symbol, quote, ind, history, fundamentals)

        snapshot = build_snapshot(symbol, quote, ind, intraday, trend=_derive_trend(ind, quote["price"]))
        snapshot["market_label"] = market_label()
        snapshot["fundamentals"] = fundamentals
        snapshot["news"] = news_items
        snapshot["forecast"] = forecast
        snapshot["strategy"] = build_strategy(symbol, quote, ind, forecast)
        _set_snapshot(symbol, snapshot)
        db.store_snapshot(symbol, snapshot)

        if not market_is_open():
            _add_log(f"{symbol}: piyasa kapalı, sinyal/bildirim gönderilmedi.")
        elif _quote_is_stale(quote):
            _add_log(f"{symbol}: veri bayat (kaynak gecikmeli), sinyal/bildirim gönderilmedi.")
        else:
            _handle_stance_change(symbol, quote, forecast, symbol_state)
            _handle_strategy_change(symbol, quote, snapshot["strategy"], symbol_state)

            for sig in signals:
                msg = format_signal_message(symbol, quote.get("name", ""), quote["price"], sig, market_label())
                _add_log(f"{symbol}: SİNYAL -> {sig['title']} ({sig['detail'][:80]})")
                sent = send_telegram_message(msg)
                _add_signal({
                    "symbol": symbol,
                    "title": sig["title"],
                    "emoji": sig["emoji"],
                    "direction": sig.get("direction"),
                    "detail": sig["detail"],
                    "price": quote["price"],
                    "sent_telegram": sent,
                    "time": _now_str(),
                })
            if signals:
                _add_log(f"{symbol}: {len(signals)} yeni sinyal.")

            _process_important_news(symbol, quote)
    except Exception as exc:
        logger.exception("İzleme hatası %s: %s", symbol, exc)
        _add_log(f"{symbol}: hata -> {exc}")


def _quote_is_stale(quote: dict) -> bool:
    if not quote:
        return True
    if quote.get("source") == "tradingview":
        return False
    mt = quote.get("market_time")
    return bool(mt and market_is_open() and time.time() - mt > 300)


def _handle_stance_change(symbol: str, quote: dict, forecast: dict, symbol_state: dict) -> None:
    new_stance = forecast.get("stance", "NÖTR")
    prev_stance = symbol_state.get("prev_stance")
    if prev_stance == new_stance:
        return
    symbol_state["prev_stance"] = new_stance
    _save_symbol_state(symbol, symbol_state)
    if prev_stance is None:
        return
    last_msg = symbol_state.get("stance_msg_at", 0)
    if time.time() - last_msg < 4 * 3600:
        _add_log(f"{symbol}: görünüm değişti ama 4 saat içinde tekrar mesaj gönderilmedi ({prev_stance} → {new_stance}).")
        return
    symbol_state["stance_msg_at"] = time.time()
    _save_symbol_state(symbol, symbol_state)
    msg = format_forecast_message(symbol, forecast)
    sent = send_telegram_message(msg)
    _add_signal({
        "symbol": symbol,
        "title": f"GÖRÜNÜM: {prev_stance} → {new_stance}",
        "emoji": "🎯",
        "direction": forecast.get("stance"),
        "detail": f"Teknik skor {forecast['score']:+d}. " + "; ".join(
            f"{t['label']} {t['price']} (%{t['pct']:+})" for t in (forecast.get("bull") or [])[:2]
        ),
        "price": quote["price"],
        "sent_telegram": sent,
        "time": _now_str(),
    })
    _add_log(f"{symbol}: GÖRÜNÜM DEĞİŞTİ -> {prev_stance} → {new_stance}")


def _handle_strategy_change(symbol: str, quote: dict, strategy: dict, symbol_state: dict) -> None:
    new_action = strategy.get("action", "BEKLE")
    prev_action = symbol_state.get("prev_strategy_action")
    if new_action in ("BEKLE", "TUT"):
        symbol_state["prev_strategy_action"] = new_action
        return
    if prev_action == new_action:
        return
    last_msg = symbol_state.get("strategy_msg_at", 0)
    if time.time() - last_msg < 4 * 3600:
        return
    symbol_state["prev_strategy_action"] = new_action
    symbol_state["strategy_msg_at"] = time.time()
    _save_symbol_state(symbol, symbol_state)
    msg = format_strategy_message(symbol, strategy)
    sent = send_telegram_message(msg)
    _add_signal({
        "symbol": symbol,
        "title": f"STRATEJİ: {new_action}",
        "emoji": "🟢" if new_action == "AL" else "🔴",
        "direction": new_action,
        "detail": "; ".join(strategy.get("reasons") or [])[:160],
        "price": quote["price"],
        "sent_telegram": sent,
        "time": _now_str(),
    })
    _add_log(f"{symbol}: STRATEJİ DEĞİŞTİ -> {new_action}")


def _process_important_news(symbol: str, quote: dict) -> None:
    try:
        items = get_news(symbol)
    except Exception:
        return
    important = [n for n in items if n.get("important")]
    if not important:
        return

    state = _load_symbol_state(symbol)
    seen = state.setdefault("seen_news", [])
    fresh = [n for n in important if n["title"] not in seen]
    if not fresh:
        return

    added = 0
    for n in fresh:
        seen.append(n["title"])
        added += 1
    seen[:] = seen[-40:]
    _save_symbol_state(symbol, state)

    msg = format_news_message(symbol, fresh)
    sent = send_telegram_message(msg)
    for n in fresh:
        _add_signal({
            "symbol": symbol,
            "title": "ÖNEMLİ HABER",
            "emoji": "📰",
            "direction": "info",
            "detail": n["title"],
            "source": n.get("source", ""),
            "url": n.get("url", ""),
            "price": quote["price"],
            "sent_telegram": sent,
            "time": _now_str(),
        })
    _add_log(f"{symbol}: {added} önemli haber iletildi.")


def _derive_trend(ind: dict, price: float) -> str:
    ema20 = ind.get("ema20")
    ema50 = ind.get("ema50")
    if ema20 and ema50:
        if ema20 > ema50 and price > ema20:
            return "up"
        if ema20 < ema50 and price < ema20:
            return "down"
    return "neutral"


def _cycle_lock(symbol: str) -> threading.Lock:
    with _cycle_locks_lock:
        return _cycle_locks.setdefault(symbol, threading.Lock())


def _worker() -> None:
    _add_log("İzleme motoru başlatıldı.")
    was_open = None
    while not _worker_stop_event.is_set():
        open_now = market_is_open()
        if was_open is not None and was_open != open_now:
            _add_log("Piyasa açıldı, izleme devam ediyor." if open_now else "Piyasa kapandı, uyku moduna geçildi.")
        was_open = open_now

        if not open_now:
            wait = seconds_until_open()
            if wait is not None and wait > 0:
                _worker_stop_event.wait(timeout=min(wait, 600))
                continue
            _worker_stop_event.wait(timeout=60)
            continue

        syms = tracked_symbols()
        if not syms:
            _worker_stop_event.wait(timeout=5)
            continue
        started = time.time()
        for symbol in syms:
            if _worker_stop_event.is_set():
                break
            if not _cycle_lock(symbol).acquire(blocking=False):
                _add_log(f"{symbol}: önceki çevrim hâlâ sürüyor, bu tur atlandı.")
                continue
            try:
                _cycle(symbol)
            finally:
                _cycle_lock(symbol).release()
        with _last_activity_lock:
            _last_activity = time.time()
        elapsed = time.time() - started
        wait = max(5, DEFAULT_INTERVAL - elapsed)
        _worker_stop_event.wait(timeout=wait)
    _add_log("İzleme motoru durduruldu.")


def _ensure_worker() -> None:
    global _worker_thread, _worker_stop_event
    now = time.time()
    with _last_activity_lock:
        idle = now - _last_activity
    if _worker_thread and _worker_thread.is_alive():
        # Piyasa kapalıyken worker bilinçli olarak uyur; "sessiz" sayılmaz.
        if market_is_open() and idle > 300:
            _add_log(f"İzleme motoru {idle:.0f} sn'dir sessiz — yeniden başlatılıyor.")
            if _worker_stop_event:
                _worker_stop_event.set()
        else:
            return
    _worker_stop_event = threading.Event()
    _worker_thread = threading.Thread(target=_worker, daemon=True)
    _worker_thread.start()


def worker_status() -> dict:
    with _last_activity_lock:
        idle = round(time.time() - _last_activity, 1)
    return {
        "alive": bool(_worker_thread and _worker_thread.is_alive()),
        "idle_seconds": idle,
        "tracked": tracked_symbols(),
    }


def init() -> None:
    db.init()
    _load_state()
    _ensure_worker()


def telegram_status() -> dict:
    return {
        "configured": is_configured(),
        "token_set": bool(__import__("os").getenv("TELEGRAM_BOT_TOKEN")),
        "chat_id_set": bool(__import__("os").getenv("TELEGRAM_CHAT_ID")),
    }
