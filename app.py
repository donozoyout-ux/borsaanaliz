import logging
import os

from flask import Flask, jsonify, render_template, request

import tracker
from bist_data import get_quote, normalize_symbol, search_symbols

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
    return jsonify({
        "symbol": clean,
        "tracking": tracking,
        "snapshot": snapshot,
        "telegram": tracker.telegram_status(),
    })


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
