from __future__ import annotations

import time
from typing import Optional


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _round(v: Optional[float], digits: int = 2) -> Optional[float]:
    return round(v, digits) if v is not None else None


def build_strategy(symbol: str, quote: dict, ind: dict, forecast: dict) -> dict:
    """Kural bazlı sinyal stratejisi: AL / BEKLE + giriş/hedef/stop + risk:kazanç."""
    price = _num(quote.get("price"))
    if price is None:
        price = 0.0
    stance = forecast.get("stance", "NÖTR")
    score = forecast.get("score", 0)
    reasons: list[str] = []

    rsi = _num(ind.get("rsi"))
    trend = (ind.get("ema20") or 0) > (ind.get("ema50") or 0) if ind.get("ema20") and ind.get("ema50") else None
    sr = ind.get("sr") or {}
    near_sup = _num(sr.get("nearest_support"))
    near_res = _num(sr.get("nearest_resistance"))
    vol_ratio = _num(ind.get("volume_ratio")) or 1.0

    # --- Hedef ve stop (forecast'ten) ---
    target = None
    if forecast.get("top"):
        target = _num(forecast["top"].get("price"))
    if not target and forecast.get("bull"):
        first = forecast["bull"][0]
        target = _num(first.get("price"))
    stop = _num(forecast.get("stop"))

    # --- Direnç/desteğe yakınlık değerlendirmesi ---
    at_support = near_sup is not None and 0 < (price - near_sup) / price <= 0.015
    at_resistance = near_res is not None and 0 < (near_res - price) / price <= 0.015
    above_res = near_res is not None and price > near_res

    # --- Alım sinyali ---
    buy_zone = trend is True and at_support
    breakout = above_res and vol_ratio >= 1.3
    momentum = stance in ("GÜÇLÜ ALIM", "ALIM") and (rsi is None or 35 <= rsi <= 70)

    action = "BEKLE"
    if buy_zone and momentum:
        action = "AL"
        reasons.append(f"Yükseliş trendinde desteğe yakın ({near_sup} TL) — alım bölgesi.")
    elif breakout and momentum:
        action = "AL"
        reasons.append(f"Direnç kırılımı ({near_res} TL) yüksek hacimle ({vol_ratio:.1f}x) — alım.")
    elif stance in ("GÜÇLÜ SATIM", "SATIM"):
        action = "BEKLE"
        reasons.append(f"Görünüm {stance} — yeni alım önerilmez.")
    elif at_resistance:
        action = "BEKLE"
        reasons.append(f"Direncin altında baskı var ({near_res} TL) — kırılım bekleniyor.")
    else:
        action = "BEKLE"
        reasons.append(f"Görünüm {stance} ({score:+d}) — net giriş sinyali yok.")

    # --- Risk:Kazanç ---
    risk = reward = rr = None
    if stop and stop < price:
        risk = price - stop
        if target and target > price:
            reward = target - price
            rr = reward / risk if risk else None

    action_label = {"AL": "ALIM", "BEKLE": "BEKLE"}.get(action, action)

    return {
        "symbol": symbol,
        "action": action,
        "action_label": action_label,
        "reasons": reasons,
        "entry": _round(price),
        "target": _round(target),
        "stop": _round(stop),
        "risk": _round(risk),
        "reward": _round(reward),
        "rr": _round(rr, 2),
        "stance": stance,
        "score": score,
        "generated_at": time.time(),
    }


def format_strategy_message(symbol: str, strategy: dict) -> str:
    emoji = {"AL": "🟢", "BEKLE": "⚪"}.get(strategy["action"], "⚪")
    lines = [
        f"{emoji} <b>{symbol} — STRATEJİ: {strategy['action_label']}</b>",
        f"Fiyat: <b>{strategy['entry']}</b> TL · Görünüm: {strategy['stance']} ({strategy['score']:+d})",
    ]
    if strategy.get("target"):
        lines.append(f"🎯 Hedef: <b>{strategy['target']}</b> TL")
    if strategy.get("stop"):
        lines.append(f"🛑 Stop: <b>{strategy['stop']}</b> TL")
    if strategy.get("rr"):
        lines.append(f"⚖️ Risk:Kazanç = <b>1:{strategy['rr']}</b>")
    if strategy.get("reasons"):
        lines.append("")
        lines.extend(f"• {r}" for r in strategy["reasons"][:3])
    return "\n".join(lines)
