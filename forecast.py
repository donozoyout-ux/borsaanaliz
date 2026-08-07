from __future__ import annotations

import time
from typing import Optional


def _range_swings(highs: list[float], lows: list[float], bars: int) -> tuple[float, float]:
    h = highs[-bars:] if len(highs) >= bars else highs
    l = lows[-bars:] if len(lows) >= bars else lows
    return max(h), min(l)


def fib_retracements(high: float, low: float, price: float) -> list[dict]:
    diff = high - low
    out = []
    for ratio, name in [(0.618, "Fib %61.8"), (0.5, "Fib %50.0"), (0.382, "Fib %38.2")]:
        level = high - diff * ratio
        if level < price:
            out.append({"label": name, "price": level})
    return sorted(out, key=lambda x: x["price"], reverse=True)


def fib_extensions(high: float, low: float, price: float) -> list[dict]:
    diff = high - low
    out = []
    for ratio, name in [(0.618, "Fib %61.8 uzantı"), (1.0, "Fib %100 uzantı"),
                        (1.272, "Fib %127.2 uzantı"), (1.618, "Fib %161.8 uzantı")]:
        level = high + diff * ratio
        if level > price:
            out.append({"label": name, "price": level})
    return out


def measured_move(high: float, low: float, price: float) -> Optional[dict]:
    diff = high - low
    target = high + diff
    if target > price:
        return {"label": "Ölçülen hareket", "price": target}
    return None


def technical_score(ind: dict, quote: dict) -> dict:
    price = float(quote["price"])
    breakdown = []

    ema20 = ind.get("ema20")
    ema50 = ind.get("ema50")
    if ema20 and ema50:
        if ema20 > ema50 and price > ema20:
            breakdown.append({"label": "Trend (EMA20>EMA50)", "dir": "bull", "points": 3})
        elif ema20 < ema50 and price < ema20:
            breakdown.append({"label": "Trend (EMA20<EMA50)", "dir": "bear", "points": -3})
        else:
            breakdown.append({"label": "Trend (karışık)", "dir": "neutral", "points": 0})
    else:
        breakdown.append({"label": "Trend (veri yetersiz)", "dir": "neutral", "points": 0})

    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi > 70:
            breakdown.append({"label": f"RSI {rsi:.1f} (aşırı alım)", "dir": "bear", "points": -2})
        elif rsi >= 50:
            breakdown.append({"label": f"RSI {rsi:.1f} (alım momentumu)", "dir": "bull", "points": 1})
        elif rsi >= 30:
            breakdown.append({"label": f"RSI {rsi:.1f} (satış momentumu)", "dir": "bear", "points": -1})
        else:
            breakdown.append({"label": f"RSI {rsi:.1f} (aşırı satım)", "dir": "bull", "points": 2})
    else:
        breakdown.append({"label": "RSI (veri yetersiz)", "dir": "neutral", "points": 0})

    hist = ind.get("macd_hist")
    if hist is not None:
        if hist > 0:
            breakdown.append({"label": "MACD pozitif (alım baskısı)", "dir": "bull", "points": 2})
        else:
            breakdown.append({"label": "MACD negatif (satış baskısı)", "dir": "bear", "points": -2})
    else:
        breakdown.append({"label": "MACD (veri yetersiz)", "dir": "neutral", "points": 0})

    vol_ratio = ind.get("volume_ratio") or 1.0
    chg = float(quote.get("change_pct") or 0)
    if vol_ratio > 1.5:
        pts = 2 if chg > 0 else -2
        breakdown.append({"label": f"Hacim {vol_ratio:.1f}x ({'artışla' if chg > 0 else 'düşüşle'})",
                          "dir": "bull" if pts > 0 else "bear", "points": pts})
    else:
        breakdown.append({"label": f"Hacim {vol_ratio:.1f}x (normal)", "dir": "neutral", "points": 0})

    bb = ind.get("bb") or {}
    if bb.get("upper") and price >= bb["upper"]:
        breakdown.append({"label": "Fiyat Bollinger üst bantta (aşırı yükseliş)", "dir": "bear", "points": -1})
    elif bb.get("lower") and price <= bb["lower"]:
        breakdown.append({"label": "Fiyat Bollinger alt bantta (dip bölge)", "dir": "bull", "points": 1})
    else:
        breakdown.append({"label": "Bollinger orta bölgede", "dir": "neutral", "points": 0})

    sr = ind.get("sr") or {}
    near_res = sr.get("nearest_resistance")
    near_sup = sr.get("nearest_support")
    if near_sup and 0 < (price - near_sup) / price <= 0.015:
        breakdown.append({"label": "Desteğe yakın (alım bölgesi)", "dir": "bull", "points": 1})
    elif near_res and 0 < (near_res - price) / price <= 0.015:
        breakdown.append({"label": "Direncin altında (baskı bölgesi)", "dir": "bear", "points": -1})
    else:
        breakdown.append({"label": "Seviyelerden uzakta", "dir": "neutral", "points": 0})

    score = sum(b["points"] for b in breakdown)
    score = max(-10, min(10, score))

    if score >= 6:
        stance = "GÜÇLÜ ALIM"
    elif score >= 3:
        stance = "ALIM"
    elif score > -3:
        stance = "NÖTR"
    elif score > -6:
        stance = "SATIM"
    else:
        stance = "GÜÇLÜ SATIM"

    return {"score": score, "stance": stance, "breakdown": breakdown}


def direction_probability(sc: dict) -> dict:
    """Bileşik teknik skordan yön olasılığı (merkez 50, skor ±10 arası)."""
    score = sc["score"]
    pct = 50 + score * 3
    pct = max(15, min(85, pct))
    direction = "up" if pct >= 55 else ("down" if pct <= 45 else "neutral")
    label = {"up": "YUKARI", "down": "AŞAĞI", "neutral": "NÖTR"}[direction]
    return {"pct": round(pct), "direction": direction, "label": label}


def build_forecast(symbol: str, quote: dict, ind: dict, history: list[dict],
                   fundamentals: Optional[dict] = None) -> dict:
    price = float(quote["price"])
    highs = [b["h"] for b in history]
    lows = [b["l"] for b in history]

    sc = technical_score(ind, quote)

    sr = ind.get("sr") or {}
    resistance = [r for r in (sr.get("resistance") or [])]
    support = [s for s in (sr.get("support") or [])]

    swing_high, swing_low = _range_swings(highs, lows, 120)
    loc_high, loc_low = _range_swings(highs, lows, 30)

    bull_candidates: list[dict] = []
    for i, r in enumerate(resistance):
        if r > price:
            bull_candidates.append({"label": f"Direnç {i + 1}", "price": r})
    bull_candidates += fib_extensions(swing_high, swing_low, price)
    mm = measured_move(loc_high, loc_low, price)
    if mm:
        bull_candidates.append(mm)

    seen = set()
    dedup_bull = []
    for c in sorted(bull_candidates, key=lambda x: x["price"]):
        key = round(c["price"], 2)
        if key in seen:
            continue
        seen.add(key)
        dedup_bull.append(c)

    bear_candidates: list[dict] = []
    for i, s in enumerate(support):
        if s < price:
            bear_candidates.append({"label": f"Destek {i + 1}", "price": s})
    bear_candidates += fib_retracements(swing_high, swing_low, price)

    seen = set()
    dedup_bear = []
    for c in sorted(bear_candidates, key=lambda x: x["price"], reverse=True):
        key = round(c["price"], 2)
        if key in seen:
            continue
        seen.add(key)
        dedup_bear.append(c)

    bull = []
    for c in dedup_bull[:3]:
        bull.append({**c, "pct": round((c["price"] - price) / price * 100, 2)})
    bear = []
    for c in dedup_bear[:3]:
        bear.append({**c, "pct": round((c["price"] - price) / price * 100, 2)})

    atr = ind.get("atr")
    expected_1d = None
    expected_1w = None
    if atr:
        expected_1d = {
            "low": round(price - atr * 0.6, 2),
            "high": round(price + atr * 0.6, 2),
            "pct": round(atr * 0.6 / price * 100, 2),
        }
        expected_1w = {
            "low": round(price - atr * (5 ** 0.5), 2),
            "high": round(price + atr * (5 ** 0.5), 2),
        }

    stop = None
    if atr:
        stop = round(max(loc_low, price - atr * 1.5), 2)
    else:
        stop = round(loc_low, 2)

    analyst_target = fundamentals.get("target_price") if fundamentals else None
    if analyst_target and analyst_target > 0:
        bull.append({
            "label": "Analist hedefi",
            "price": round(analyst_target, 2),
            "pct": round((analyst_target - price) / price * 100, 2),
        })
    bull = bull[:4]

    # Tavan/zirve hedefi: en yüksek makul yukarı hedef (analist hedefi çok uzaksa
    # fiilen ulaşılabilir tavan olarak en yüksek teknik hedef seçilir).
    top = None
    if bull:
        practical = [t for t in bull if t["label"] != "Analist hedefi"]
        candidates = practical or bull
        best = max(candidates, key=lambda t: t["price"])
        if best["price"] > price:
            top = {"price": round(best["price"], 2), "pct": round((best["price"] - price) / price * 100, 2)}

    # Dip/taban: en düşük makul aşağı hedef.
    bottom = None
    if bear:
        worst = min(bear, key=lambda t: t["price"])
        if worst["price"] < price:
            bottom = {"price": round(worst["price"], 2), "pct": round((worst["price"] - price) / price * 100, 2)}

    summary = _build_summary(sc["stance"], sc["score"], bull, bear, stop)

    return {
        "symbol": symbol,
        "score": sc["score"],
        "stance": sc["stance"],
        "breakdown": sc["breakdown"],
        "bull": bull,
        "bear": bear,
        "top": top,
        "bottom": bottom,
        "probability": direction_probability(sc),
        "expected_1d": expected_1d,
        "expected_1w": expected_1w,
        "stop": stop,
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "summary": summary,
        "generated_at": time.time(),
    }


def _build_summary(stance: str, score: int, bull: list[dict], bear: list[dict], stop) -> str:
    parts = []
    if stance in ("GÜÇLÜ ALIM", "ALIM"):
        parts.append(f"Görünüm <b>{stance}</b> — indikatörler (skor {score:+d}) alım yönünde ağırlıklı.")
    elif stance in ("GÜÇLÜ SATIM", "SATIM"):
        parts.append(f"Görünüm <b>{stance}</b> — indikatörler (skor {score:+d}) satış yönünde ağırlıklı.")
    else:
        parts.append(f"Görünüm <b>NÖTR</b> (skor {score:+d}) — fiyat aralıkta, güçlü yön sinyali yok.")

    if bull:
        first = bull[0]
        parts.append(f"İlk yukarı hedef <b>{first['price']}</b> TL (%{first['pct']:+}).")
    if bear:
        parts.append(f"Risk altında destek <b>{bear[0]['price']}</b> TL.")
    if stop:
        parts.append(f"Önerilen stop <b>{stop}</b> TL.")
    return " ".join(parts)


def format_forecast_message(symbol: str, forecast: dict) -> str:
    emoji = {"GÜÇLÜ ALIM": "🟢🟢", "ALIM": "🟢", "NÖTR": "⚪", "SATIM": "🔴", "GÜÇLÜ SATIM": "🔴🔴"}
    lines = [
        f"{emoji.get(forecast['stance'], '⚪')} <b>{symbol} — GÖRÜNÜM: {forecast['stance']}</b>",
        f"Teknik skor: <b>{forecast['score']:+d}</b> / 10",
        "",
    ]
    if forecast["bull"]:
        lines.append("🔼 <b>Yukarı hedefler:</b>")
        for t in forecast["bull"][:3]:
            lines.append(f"   • {t['label']}: <b>{t['price']}</b> (%{t['pct']:+})")
    if forecast.get("top"):
        lines.append(f"🎯 Tavan/Zirve hedefi: <b>{forecast['top']['price']}</b> TL")
    if forecast.get("probability"):
        pr = forecast["probability"]
        arrow = "▲" if pr["direction"] == "up" else ("▼" if pr["direction"] == "down" else "—")
        lines.append(f"📊 Yön olasılığı: {arrow} <b>%{pr['pct']}</b> ({pr['label']})")
    if forecast["bear"]:
        lines.append("🔽 <b>Aşağı riskler:</b>")
        for t in forecast["bear"][:3]:
            lines.append(f"   • {t['label']}: <b>{t['price']}</b> (%{t['pct']:+})")
    if forecast.get("bottom"):
        lines.append(f"🕳️ Dip/Taban riski: <b>{forecast['bottom']['price']}</b> TL")
    if forecast["stop"]:
        lines.append(f"🛑 Stop seviyesi: <b>{forecast['stop']}</b> TL")
    lines.append("")
    lines.append(forecast["summary"].replace("<b>", "").replace("</b>", ""))
    return "\n".join(lines)
