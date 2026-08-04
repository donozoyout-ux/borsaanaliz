from __future__ import annotations

import time
from typing import Optional


def sma(values: list[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if not values:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(values: list[float], n: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (n + 1)
    prev = values[0]
    out[0] = prev
    for i in range(1, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], n: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) <= n:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        diff = values[i] - values[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_g = gains / n
    avg_l = losses / n
    if avg_l == 0:
        out[n] = 100.0
    else:
        rs = avg_g / avg_l
        out[n] = 100 - (100 / (1 + rs))
    for i in range(n + 1, len(values)):
        diff = values[i] - values[i - 1]
        g = diff if diff > 0 else 0.0
        l = -diff if diff < 0 else 0.0
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
        if avg_l == 0:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = 100 - (100 / (1 + rs))
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    line: list[Optional[float]] = []
    for i in range(len(values)):
        if ema_fast[i] is None or ema_slow[i] is None:
            line.append(None)
        else:
            line.append(ema_fast[i] - ema_slow[i])
    valid_start = None
    for i, v in enumerate(line):
        if v is not None:
            valid_start = i
            break
    if valid_start is None:
        return {"line": 0, "signal": 0, "hist": 0, "series": [], "series_signal": []}
    macd_line = [v for v in line if v is not None]
    sig = ema(macd_line, signal)
    macd_series = line
    sig_series: list[Optional[float]] = [None] * valid_start + sig
    hist_series: list[Optional[float]] = []
    for i in range(len(macd_series)):
        if macd_series[i] is not None and sig_series[i] is not None:
            hist_series.append(macd_series[i] - sig_series[i])
        else:
            hist_series.append(None)
    last_line = macd_series[-1] or 0
    last_sig = sig_series[-1] or 0
    return {
        "line": last_line,
        "signal": last_sig,
        "hist": last_line - last_sig,
        "series": macd_series,
        "series_signal": sig_series,
        "series_hist": hist_series,
    }


def bollinger(values: list[float], n: int = 20, k: float = 2.0) -> dict:
    if len(values) < n:
        return {"upper": None, "middle": None, "lower": None}
    window = values[-n:]
    mid = sum(window) / n
    var = sum((v - mid) ** 2 for v in window) / n
    sd = var ** 0.5
    return {"upper": mid + k * sd, "middle": mid, "lower": mid - k * sd}


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs[-n:]) / n


def _swings(highs: list[float], lows: list[float], lookback: int = 3) -> tuple[list[float], list[float]]:
    res, sup = [], []
    for i in range(lookback, len(highs) - lookback):
        h = highs[i]
        l = lows[i]
        if all(h >= highs[j] for j in range(i - lookback, i + lookback + 1)):
            res.append(h)
        if all(l <= lows[j] for j in range(i - lookback, i + lookback + 1)):
            sup.append(l)
    return res, sup


def support_resistance(closes: list[float], highs: list[float], lows: list[float], price: float) -> dict:
    res, sup = _swings(highs, lows, lookback=3)
    if len(closes) >= 2:
        h, l, c = closes[-2], lows[-2], highs[-2]
        pivot = (h + l + c) / 3
        res.append(pivot + (h - l))
        res.append(2 * pivot - l)
        sup.append(2 * pivot - h)
        sup.append(pivot - (h - l))
    res = sorted(set(round(r, 4) for r in res if r > 0))
    sup = sorted(set(round(s, 4) for s in sup if s > 0))

    resistance = [r for r in res if r > price]
    support = [s for s in sup if s < price]

    return {
        "resistance": sorted(resistance)[:4] if resistance else [],
        "support": sorted(support, reverse=True)[:4] if support else [],
        "nearest_resistance": min(resistance) if resistance else None,
        "nearest_support": max(support) if support else None,
    }


def compute_indicator_summary(closes: list[float], highs: list[float], lows: list[float], volumes: list[float], price: float) -> dict:
    summary: dict = {"price": price}
    rsi_series = rsi(closes)
    summary["rsi"] = rsi_series[-1] if rsi_series else None
    macd_res = macd(closes)
    summary["macd"] = macd_res["line"]
    summary["macd_signal"] = macd_res["signal"]
    summary["macd_hist"] = macd_res["hist"]
    bb = bollinger(closes)
    summary["bb"] = bb
    summary["sma20"] = sma(closes, 20)[-1] if len(closes) >= 20 else None
    summary["ema20"] = ema(closes, 20)[-1] if len(closes) >= 20 else None
    summary["ema50"] = ema(closes, 50)[-1] if len(closes) >= 50 else None
    summary["atr"] = atr(highs, lows, closes)
    avg_vol = sum(volumes[-20:]) / len(volumes[-20:]) if volumes else 0
    summary["avg_volume"] = avg_vol
    if volumes:
        summary["volume"] = volumes[-1]
        summary["volume_ratio"] = volumes[-1] / avg_vol if avg_vol else 1.0
    sr = support_resistance(closes, highs, lows, price)
    summary["sr"] = sr
    return summary


def _zone(rsi_val: Optional[float]) -> str:
    if rsi_val is None:
        return "normal"
    if rsi_val >= 70:
        return "overbought"
    if rsi_val <= 30:
        return "oversold"
    return "normal"


def _detect_dip(closes: list[float], low: float, ema20: Optional[float], trend_up: bool, rsi_val: Optional[float]) -> bool:
    if not trend_up or ema20 is None or len(closes) < 5:
        return False
    pullback = low <= ema20 * 1.01
    rsi_ok = (rsi_val or 50) < 50
    return pullback and rsi_ok


def evaluate_signals(symbol: str, quote: dict, closes: list[float], highs: list[float],
                     lows: list[float], volumes: list[float], intraday: list[dict], state: dict) -> tuple[dict, list[dict]]:
    price = float(quote["price"])
    prev_close = float(quote.get("prev_close") or 0) or price
    change_pct = float(quote.get("change_pct") or 0)

    ind = compute_indicator_summary(closes, highs, lows, volumes, price)
    rsi_val = ind["rsi"]
    zone = _zone(rsi_val)
    sr = ind["sr"]
    nearest_res = sr["nearest_resistance"]
    nearest_sup = sr["nearest_support"]

    trend_up = ind["ema20"] is not None and ind["ema50"] is not None and ind["ema20"] > ind["ema50"] and price > ind["ema20"]
    trend_down = ind["ema20"] is not None and ind["ema50"] is not None and ind["ema20"] < ind["ema50"] and price < ind["ema20"]
    trend = "up" if trend_up else ("down" if trend_down else "neutral")

    low_val = lows[-1] if lows else price
    day_low = min(b["l"] for b in intraday) if intraday else low_val
    signals: list[dict] = []
    now = time.time()

    prev_state = state or {}

    # --- 1. Direnç kırıldı (yukarı çıkış) ---
    if nearest_res and nearest_res > 0:
        prev_r = prev_state.get("resistance_tracked")
        prev_broken = prev_state.get("resistance_broken")
        if prev_r is None:
            prev_r = nearest_res
        if prev_r != nearest_res:
            prev_broken = False
        crossed = price >= prev_r and prev_close < prev_r
        if crossed and not prev_broken:
            signals.append({
                "type": "resistance_break", "direction": "bull", "emoji": "🚀",
                "title": "DİRENÇ KIRILDI",
                "detail": f"Fiyat <b>{prev_r}</b> direncini yukarı yönlü kırdı → yeni hedef: <b>{next((r for r in sr['resistance'] if r > price), None)}</b>",
            })
            prev_broken = True
        elif price < prev_r * 0.98:
            prev_broken = False
        state["resistance_tracked"] = prev_r
        state["resistance_broken"] = prev_broken

    # --- 2. Destek kırıldı (aşağı kırılım) ---
    if nearest_sup and nearest_sup > 0:
        prev_s = prev_state.get("support_tracked")
        prev_sbroken = prev_state.get("support_broken")
        if prev_s is None:
            prev_s = nearest_sup
        if prev_s != nearest_sup:
            prev_sbroken = False
        crossed = price <= prev_s and prev_close > prev_s
        if crossed and not prev_sbroken:
            signals.append({
                "type": "support_break", "direction": "bear", "emoji": "⚠️",
                "title": "DESTEK KIRILDI",
                "detail": f"Fiyat <b>{prev_s}</b> desteğinin altına indi → bir sonraki destek: <b>{next((s for s in sr['support'] if s < price), None)}</b>",
            })
            prev_sbroken = True
        elif price > prev_s * 1.02:
            prev_sbroken = False
        state["support_tracked"] = prev_s
        state["support_broken"] = prev_sbroken

    # --- 3. Direnç/desteğe yaklaşma ---
    near_pct = 1.5
    near_res_fire = prev_state.get("near_res_fire", False)
    if nearest_res and not near_res_fire:
        dist_pct = (nearest_res - price) / price * 100
        if 0 <= dist_pct <= near_pct:
            signals.append({
                "type": "near_resistance", "direction": "info", "emoji": "📈",
                "title": "DİRENCE YAKLAŞIYOR",
                "detail": f"Fiyat <b>{nearest_res}</b> direncine %{dist_pct:.2f} mesafede",
            })
            near_res_fire = True
    if nearest_res and (price - nearest_res) / price * 100 > near_pct * 2:
        near_res_fire = False
    state["near_res_fire"] = near_res_fire

    near_sup_fire = prev_state.get("near_sup_fire", False)
    if nearest_sup and not near_sup_fire:
        dist_pct = (price - nearest_sup) / price * 100
        if 0 <= dist_pct <= near_pct:
            signals.append({
                "type": "near_support", "direction": "info", "emoji": "📉",
                "title": "DESTEGE YAKLAŞIYOR",
                "detail": f"Fiyat <b>{nearest_sup}</b> desteğine %{dist_pct:.2f} mesafede",
            })
            near_sup_fire = True
    if nearest_sup and (nearest_sup - price) / price * 100 > near_pct * 2:
        near_sup_fire = False
    state["near_sup_fire"] = near_sup_fire

    # --- 4. Hacim patlaması ---
    vol_ratio = ind.get("volume_ratio") or 1.0
    vol_last = prev_state.get("volume_last_fire", 0)
    if vol_ratio >= 2.0 and now - vol_last > 3600:
        signals.append({
            "type": "volume_spike", "direction": "info", "emoji": "📊",
            "title": "HACİM PATLAMASI",
            "detail": f"İşlem hacmi ortalamasının <b>{vol_ratio:.1f}x</b> üzerinde ({ind['volume']:,} lot / ort. {ind['avg_volume']:,.0f} lot)",
        })
        state["volume_last_fire"] = now

    # --- 5. RSI aşırı alım / satım bölgeleri ---
    prev_zone = prev_state.get("rsi_zone", "normal")
    if zone != prev_zone:
        if zone == "oversold":
            signals.append({
                "type": "rsi_oversold", "direction": "bull", "emoji": "🟢",
                "title": "RSI AŞIRI SATIM",
                "detail": f"RSI <b>{rsi_val:.1f}</b> seviyesine indi (30 altı) — dip potansiyeli, tepki alımı izlenebilir",
            })
        elif zone == "overbought":
            signals.append({
                "type": "rsi_overbought", "direction": "bear", "emoji": "🔴",
                "title": "RSI AŞIRI ALIM",
                "detail": f"RSI <b>{rsi_val:.1f}</b> seviyesine çıktı (70 üstü) — aşırı yükseliş, kar satışı riski",
            })
        state["rsi_zone"] = zone

    # --- 6. MACD kesişimleri ---
    macd_line = ind["macd"]
    macd_sig = ind["macd_signal"]
    prev_line = prev_state.get("macd_line")
    prev_sig = prev_state.get("macd_signal")
    if prev_line is not None and prev_sig is not None:
        if prev_line <= prev_sig and macd_line > macd_sig:
            signals.append({
                "type": "macd_bull", "direction": "bull", "emoji": "🟢",
                "title": "MACD ALIM KESİŞİMİ",
                "detail": f"MACD ({macd_line:.3f}) sinyal çizgisini ({macd_sig:.3f}) yukarı kesti",
            })
        elif prev_line >= prev_sig and macd_line < macd_sig:
            signals.append({
                "type": "macd_bear", "direction": "bear", "emoji": "🔴",
                "title": "MACD SATIM KESİŞİMİ",
                "detail": f"MACD ({macd_line:.3f}) sinyal çizgisini ({macd_sig:.3f}) aşağı kesti",
            })
    state["macd_line"] = macd_line
    state["macd_signal"] = macd_sig

    # --- 7. Yükseliş trendinde dip (pullback) fırsatı ---
    dip_last = prev_state.get("dip_last_fire", 0)
    is_dip = _detect_dip(closes, day_low, ind["ema20"], trend_up, rsi_val)
    if is_dip and now - dip_last > 7200:
        signals.append({
            "type": "dip_buy", "direction": "bull", "emoji": "💎",
            "title": "DİP FIRSATI (PULLBACK)",
            "detail": f"Yükseliş trendi içinde fiyat EMA20'ye ({ind['ema20']:.2f}) çekildi — uygun alım bölgesi olabilir (RSI {rsi_val:.1f})",
        })
        state["dip_last_fire"] = now

    # --- 8. Önemli fiyat hareketi ---
    big_last = prev_state.get("bigmove_last_fire", 0)
    if abs(change_pct) >= 3.0 and now - big_last > 1800:
        up = change_pct > 0
        signals.append({
            "type": "big_move", "direction": "bull" if up else "bear",
            "emoji": "📈" if up else "📉",
            "title": "BÜYÜK HAREKET",
            "detail": f"Günlük değişim <b>%{change_pct:+.2f}</b> (gün içi) — {'güçlü alım' if up else 'güçlü satış'} baskısı",
        })
        state["bigmove_last_fire"] = now

    # --- 9. RSI bölgeden çıkış (normalleşme) ---
    if zone == "normal" and prev_zone in ("overbought", "oversold"):
        signals.append({
            "type": "rsi_normalize", "direction": "info", "emoji": "⚪",
            "title": "RSI NORMALE DÖNDÜ",
            "detail": f"RSI {rsi_val:.1f} ile aşırı alım/satım bölgesinden çıktı ({prev_zone})",
        })

    state["prev_close"] = price
    state["rsi_zone"] = zone
    state["prev_close_val"] = price

    return ind, signals


def build_snapshot(symbol: str, quote: dict, ind: dict, intraday: list[dict], trend: str) -> dict:
    bb = ind.get("bb") or {}
    sr = ind.get("sr") or {}
    return {
        "symbol": symbol,
        "quote": quote,
        "indicators": {
            "rsi": round(ind["rsi"], 1) if ind.get("rsi") is not None else None,
            "macd": round(ind["macd"], 3) if ind.get("macd") is not None else None,
            "macd_signal": round(ind["macd_signal"], 3) if ind.get("macd_signal") is not None else None,
            "sma20": round(ind["sma20"], 3) if ind.get("sma20") else None,
            "ema20": round(ind["ema20"], 3) if ind.get("ema20") else None,
            "ema50": round(ind["ema50"], 3) if ind.get("ema50") else None,
            "bb_upper": round(bb["upper"], 3) if bb.get("upper") else None,
            "bb_lower": round(bb["lower"], 3) if bb.get("lower") else None,
            "bb_middle": round(bb["middle"], 3) if bb.get("middle") else None,
            "volume_ratio": round(ind.get("volume_ratio") or 1.0, 2),
            "avg_volume": round(ind.get("avg_volume") or 0),
            "volume": round(ind.get("volume") or 0),
        },
        "levels": {
            "resistance": sr.get("resistance") or [],
            "support": sr.get("support") or [],
            "nearest_resistance": sr.get("nearest_resistance"),
            "nearest_support": sr.get("nearest_support"),
        },
        "trend": trend,
        "day_low": min(b["l"] for b in intraday) if intraday else None,
        "day_high": max(b["h"] for b in intraday) if intraday else None,
        "timestamp": time.time(),
    }
