import logging
import os
import time

from flask import Flask, jsonify, render_template, request

import db
import tracker
from bist_data import get_history, get_intraday, get_quote, market_is_open, normalize_symbol, search_symbols

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

_interval_minutes = 60


@app.before_request
def _ensure_running():
    tracker.init()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stock/<symbol>")
def stock_page(symbol):
    clean = normalize_symbol(symbol)
    return render_template("stock.html", symbol=clean)


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    return jsonify({"results": search_symbols(q)})


@app.route("/api/validate/<symbol>")
def api_validate(symbol):
    clean = normalize_symbol(symbol)
    quote = get_quote(clean)
    if not quote:
        return jsonify({"ok": False, "symbol": clean, "error": "Hisse bulunamadı. Sembolü kontrol edin (örn. THYAO, AKBNK)."}), 404
    return jsonify({
        "ok": True,
        "symbol": clean,
        "name": quote.get("name"),
        "price": quote.get("price"),
    })


@app.route("/api/stock/<symbol>")
def api_stock(symbol):
    clean = normalize_symbol(symbol)
    snapshot = tracker.get_snapshot(clean)
    tracking = tracker.is_tracking(clean)
    if not snapshot:
        try:
            snapshot = tracker.collect_snapshot(clean)
        except Exception:
            snapshot = None
    if not snapshot:
        quote = get_quote(clean)
        if quote:
            snapshot = {
                "symbol": clean,
                "quote": quote,
                "indicators": {},
                "levels": {},
                "trend": "neutral",
                "timestamp": None,
                "market_label": None,
            }
    quote = (snapshot or {}).get("quote") or get_quote(clean)
    data_stale, data_time = _data_freshness(quote)
    return jsonify({
        "symbol": clean,
        "tracking": tracking,
        "snapshot": snapshot,
        "telegram": tracker.telegram_status(),
        "db": db_stats(),
        "data_stale": data_stale,
        "data_time": data_time,
    })


def _data_freshness(quote):
    now = time.time()
    data_time = None
    if quote:
        data_time = quote.get("market_time")
    if not data_time:
        return False, None
    if market_is_open() and now - data_time > 300:
        return True, data_time
    return False, data_time


@app.route("/api/stock/<symbol>/refresh", methods=["POST"])
def api_refresh(symbol):
    clean = normalize_symbol(symbol)
    try:
        from fundamentals import clear_cache
        clear_cache()
    except Exception:
        pass
    try:
        from news import clear_cache as clear_news_cache
        clear_news_cache()
    except Exception:
        pass
    snapshot = tracker.collect_snapshot(clean)
    if snapshot:
        tracker.set_snapshot_now(clean, snapshot)
        return jsonify({"ok": True, "snapshot": snapshot})
    return jsonify({"ok": False, "error": "Veri alınamadı"}), 502


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    clean = normalize_symbol(symbol)
    tf = request.args.get("range", "6mo")
    tf = tf if tf in ("1h", "1d", "1mo", "6mo", "1y") else "6mo"
    if tf in ("1h", "1d"):
        bars = get_intraday(clean)
        if tf == "1h":
            from_t = int(time.time()) - 3600
            bars = [b for b in bars if b["t"] >= from_t]
        interval = "1m"
    else:
        bars = get_history(clean, tf, "1d")
        interval = "1d"
    price = None
    quote = get_quote(clean)
    if quote:
        price = quote.get("price")
    snap = tracker.get_snapshot(clean)
    levels = (snap or {}).get("levels") or {}
    if not levels and bars:
        try:
            from indicators import support_resistance
            levels = support_resistance(
                [b["c"] for b in bars],
                [b["h"] for b in bars],
                [b["l"] for b in bars],
                price or bars[-1]["c"],
            )
        except Exception:
            levels = {}
    data_stale, data_time = _data_freshness(quote)
    return jsonify({
        "symbol": clean,
        "price": price,
        "last": bars[-1] if bars else None,
        "bars": bars,
        "interval": interval,
        "levels": levels,
        "timeframe": tf,
        "data_stale": data_stale,
        "data_time": data_time,
    })


@app.route("/api/db/stats")
def api_db_stats():
    return jsonify(db.stats())


def db_stats() -> dict:
    try:
        return db.stats()
    except Exception:
        return {}


@app.route("/api/stock/<symbol>/start", methods=["POST"])
def api_start(symbol):
    clean = normalize_symbol(symbol)
    quote = get_quote(clean)
    if not quote:
        return jsonify({"ok": False, "error": "Hisse bulunamadı"}), 404
    tracker.set_focus(clean)
    return jsonify({"ok": True, "symbol": clean, "tracking": True})


@app.route("/api/stock/<symbol>/stop", methods=["POST"])
def api_stop(symbol):
    clean = normalize_symbol(symbol)
    tracker.stop(clean)
    return jsonify({"ok": True, "tracking": False})


@app.route("/api/signals/<symbol>")
def api_signals(symbol):
    clean = normalize_symbol(symbol)
    return jsonify({"signals": tracker.get_signals(clean)})


@app.route("/api/logs")
def api_logs():
    return jsonify({"logs": tracker.get_logs()})


@app.route("/api/telegram/status")
def api_telegram_status():
    return jsonify(tracker.telegram_status())


@app.route("/api/telegram/test", methods=["POST"])
def api_telegram_test():
    from telegram_notifier import send_telegram_message
    ok = send_telegram_message("🧪 <b>Bağlantı testi</b>\nBIST Canlı Analiz Telegram bağlantısı çalışıyor!")
    if ok:
        return jsonify({"ok": True, "message": "Test mesajı gönderildi."})
    return jsonify({"ok": False, "error": "Telegram ayarları eksik ya da hatalı. TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ortam değişkenlerini ayarlayın."}), 502


if __name__ == "__main__":
    tracker.init()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
