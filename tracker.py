import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from bist_data import get_history, get_intraday, get_quote, market_label, normalize_symbol
from indicators import build_snapshot, evaluate_signals
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
_stop_event = threading.Event()


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
    data = _load_json(SIGNALS_FILE, {"signals": []})
    sigs = data.get("signals", [])
    if symbol:
        clean = normalize_symbol(symbol)
        sigs = [s for s in sigs if s.get("symbol") == clean]
    return sigs


def _add_signal(sig: dict) -> None:
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

        closes = [b["c"] for b in history]
        highs = [b["h"] for b in history]
        lows = [b["l"] for b in history]
        volumes = [b["v"] for b in history]

        symbol_state = _load_symbol_state(symbol)
        ind, signals = evaluate_signals(symbol, quote, closes, highs, lows, volumes, intraday, symbol_state)
        symbol_state["last_cycle"] = _now_str()
        _save_symbol_state(symbol, symbol_state)

        snapshot = build_snapshot(symbol, quote, ind, intraday, trend=_derive_trend(ind, quote["price"]))
        snapshot["market_label"] = market_label()
        _set_snapshot(symbol, snapshot)

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
    except Exception as exc:
        logger.exception("İzleme hatası %s: %s", symbol, exc)
        _add_log(f"{symbol}: hata -> {exc}")


def _derive_trend(ind: dict, price: float) -> str:
    ema20 = ind.get("ema20")
    ema50 = ind.get("ema50")
    if ema20 and ema50:
        if ema20 > ema50 and price > ema20:
            return "up"
        if ema20 < ema50 and price < ema20:
            return "down"
    return "neutral"


def _worker() -> None:
    _add_log("İzleme motoru başlatıldı.")
    while not _stop_event.is_set():
        syms = tracked_symbols()
        if not syms:
            _stop_event.wait(timeout=5)
            continue
        started = time.time()
        for symbol in syms:
            if _stop_event.is_set():
                break
            _cycle(symbol)
        elapsed = time.time() - started
        wait = max(5, DEFAULT_INTERVAL - elapsed)
        _stop_event.wait(timeout=wait)
    _add_log("İzleme motoru durduruldu.")


def _ensure_worker() -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_worker, daemon=True)
    _worker_thread.start()


def init() -> None:
    _load_state()
    _ensure_worker()


def telegram_status() -> dict:
    return {
        "configured": is_configured(),
        "token_set": bool(__import__("os").getenv("TELEGRAM_BOT_TOKEN")),
        "chat_id_set": bool(__import__("os").getenv("TELEGRAM_CHAT_ID")),
    }
