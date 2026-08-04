import threading
import time
from typing import Optional

import requests

_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
_CACHE_TTL = 6 * 3600  # bilanço 6 saatte bir tazelenir (gün içi değişmez)

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

_session = requests.Session()
_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "application/json,text/plain,*/*",
})
_session_lock = threading.Lock()
_crumb: str = ""
_crumb_fetched_at: float = 0
_CRUMB_TTL = 3600

_MODULES = (
    "summaryDetail,financialData,defaultKeyStatistics,"
    "balanceSheetHistoryQuarterly,incomeStatementHistory,cashflowStatementHistory"
)


def _refresh_crumb() -> bool:
    global _crumb, _crumb_fetched_at
    try:
        _session.get("https://fc.yahoo.com", timeout=15)
        resp = _session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15)
        if resp.status_code == 200 and resp.text.strip():
            _crumb = resp.text.strip()
            _crumb_fetched_at = time.time()
            return True
    except requests.RequestException:
        pass
    return False


def _ensure_crumb() -> str:
    if not _crumb or time.time() - _crumb_fetched_at > _CRUMB_TTL:
        _refresh_crumb()
    return _crumb


def _quote_summary(symbol: str) -> Optional[dict]:
    clean = symbol.upper().replace(".IS", "") + ".IS"
    for host in _HOSTS:
        try:
            with _session_lock:
                crumb = _ensure_crumb()
                url = f"https://{host}/v10/finance/quoteSummary/{clean}"
                resp = _session.get(url, params={"modules": _MODULES, "crumb": crumb}, timeout=20)
                if resp.status_code == 401:
                    _refresh_crumb()
                    crumb = _crumb
                    resp = _session.get(url, params={"modules": _MODULES, "crumb": crumb}, timeout=20)
                if resp.status_code == 200:
                    result = resp.json().get("quoteSummary", {}).get("result")
                    if result:
                        return result[0]
                elif resp.status_code == 404:
                    return None
        except requests.RequestException:
            continue
    return None


def _num(*values) -> Optional[float]:
    for v in values:
        if v is None:
            continue
        if isinstance(v, dict):
            v = v.get("raw")
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _pct(raw) -> Optional[float]:
    v = _num(raw)
    if v is None:
        return None
    return v * 100 if abs(v) < 5 else v


def get_fundamentals(symbol: str, force: bool = False) -> Optional[dict]:
    clean = symbol.upper().replace(".IS", "")
    now = time.time()
    with _cache_lock:
        cached = _cache.get(clean)
        if cached and not force and now - cached[0] < _CACHE_TTL:
            return cached[1]

    data = _quote_summary(clean)
    if not data:
        return None

    fd = data.get("financialData") or {}
    ks = data.get("defaultKeyStatistics") or {}
    sd = data.get("summaryDetail") or {}

    bs = None
    bs_q = data.get("balanceSheetHistoryQuarterly") or {}
    if bs_q.get("balanceSheetStatements"):
        bs = bs_q["balanceSheetStatements"][0]

    inc = None
    inc_h = data.get("incomeStatementHistory") or {}
    if inc_h.get("incomeStatementHistory"):
        inc = inc_h["incomeStatementHistory"][0]

    cf = None
    cf_h = data.get("cashflowStatementHistory") or {}
    if cf_h.get("cashflowStatements"):
        cf = cf_h["cashflowStatements"][0]

    price = _num(sd.get("regularMarketPrice")) or _num(fd.get("currentPrice"))
    market_cap = _num(sd.get("marketCap")) or _num(ks.get("marketCap"))
    total_debt = _num(fd.get("totalDebt"))
    total_cash = _num(fd.get("totalCash"))
    total_equity = _num(bs.get("totalStockholderEquity") if bs else None)
    total_assets = _num(bs.get("totalAssets") if bs else None)
    total_liab = _num(bs.get("totalLiab") if bs else None)
    ebitda = _num(fd.get("ebitda"))

    result = {
        "symbol": clean,
        "name": data.get("shortName") or data.get("longName"),
        "price": price,
        "market_cap": market_cap,
        "pe": _num(sd.get("trailingPE")) or _num(ks.get("trailingPE")),
        "forward_pe": _num(sd.get("forwardPE")) or _num(ks.get("forwardPE")),
        "pb": _num(sd.get("priceToBook")) or _num(ks.get("priceToBook")),
        "ps": _num(sd.get("priceToSalesTrailing12Months")) or _num(ks.get("priceToSalesTrailing12Months")),
        "ev_ebitda": _num(sd.get("enterpriseToEbitda")) or _num(ks.get("enterpriseToEbitda")),
        "eps": _num(ks.get("trailingEps")) or _num(ks.get("epsTrailingTwelveMonths")),
        "book_value": _num(ks.get("bookValue")),
        "beta": _num(ks.get("beta")),
        "dividend_yield": _pct(sd.get("dividendYield")) or _pct(ks.get("dividendYield")),
        "roe": _pct(fd.get("returnOnEquity")),
        "roa": _pct(fd.get("returnOnAssets")),
        "profit_margin": _pct(fd.get("profitMargins")),
        "gross_margin": _pct(fd.get("grossMargins")),
        "operating_margin": _pct(fd.get("operatingMargins")),
        "revenue": _num(fd.get("totalRevenue")) or _num(inc.get("totalRevenue") if inc else None),
        "revenue_growth": _pct(fd.get("revenueGrowth")),
        "net_income": _num(fd.get("netIncome")) or _num(inc.get("netIncome") if inc else None),
        "earnings_growth": _pct(fd.get("earningsGrowth")) or _pct(ks.get("earningsQuarterlyGrowth")),
        "ebitda": ebitda,
        "free_cash_flow": _num(fd.get("freeCashflow")) or _num(ks.get("freeCashflow")),
        "operating_cash_flow": _num(fd.get("operatingCashflow")) or _num(cf.get("totalCashFromOperatingActivities") if cf else None),
        "total_debt": total_debt,
        "total_cash": total_cash,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
        "debt_to_equity": _num(bs.get("debtToEquity")) or _num(ks.get("debtToEquity")),
        "target_price": _num(fd.get("targetMeanPrice")),
        "shares_outstanding": _num(ks.get("sharesOutstanding")),
    }

    with _cache_lock:
        _cache[clean] = (now, result)
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
