import json
import sqlite3
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "borsa.db"

_lock = threading.Lock()
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_bars(
  symbol TEXT NOT NULL,
  ts INTEGER NOT NULL,
  interval TEXT NOT NULL,
  o REAL, h REAL, l REAL, c REAL, v INTEGER,
  PRIMARY KEY(symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS fundamentals(
  symbol TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS news(
  symbol TEXT NOT NULL,
  title TEXT NOT NULL,
  source TEXT,
  url TEXT,
  time INTEGER,
  important INTEGER DEFAULT 0,
  fetched_at REAL,
  PRIMARY KEY(symbol, title)
);
CREATE TABLE IF NOT EXISTS snapshots(
  symbol TEXT NOT NULL,
  ts INTEGER NOT NULL,
  data TEXT NOT NULL,
  PRIMARY KEY(symbol, ts)
);
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, title TEXT, emoji TEXT, direction TEXT,
  detail TEXT, source TEXT, url TEXT, price REAL,
  sent INTEGER DEFAULT 0, time TEXT
);
CREATE TABLE IF NOT EXISTS positions(
  symbol TEXT PRIMARY KEY,
  buy_price REAL NOT NULL,
  quantity REAL NOT NULL,
  buy_date TEXT,
  stop REAL,
  target REAL,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_bars_symbol ON price_bars(symbol, interval, ts);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol, fetched_at);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, id);
CREATE INDEX IF NOT EXISTS idx_snap_symbol ON snapshots(symbol, ts);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init() -> None:
    with _lock:
        conn = _conn()
        conn.executescript(_SCHEMA)
        conn.commit()


# ---------- price bars ----------

def store_bars(symbol: str, interval: str, bars: list[dict]) -> int:
    if not bars:
        return 0
    rows = [
        (symbol, int(b["t"]), interval, b["o"], b["h"], b["l"], b["c"], int(b.get("v") or 0))
        for b in bars
    ]
    with _lock:
        conn = _conn()
        conn.executemany(
            "INSERT OR REPLACE INTO price_bars(symbol, ts, interval, o, h, l, c, v) "
            "VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    return len(rows)


def get_bars(symbol: str, interval: str = "1d", limit: int = 0) -> list[dict]:
    sql = "SELECT ts, o, h, l, c, v FROM price_bars WHERE symbol=? AND interval=? ORDER BY ts"
    params = [symbol, interval]
    if limit:
        sql += " DESC LIMIT ?"
        params.append(limit)
    with _lock:
        conn = _conn()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    out = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows]
    if limit:
        out.reverse()
    return out


# ---------- fundamentals ----------

def store_fundamentals(symbol: str, data: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals(symbol, data, updated_at) VALUES(?,?,?)",
            (symbol, json.dumps(data, ensure_ascii=False), time.time()),
        )
        conn.commit()


def get_stored_fundamentals(symbol: str) -> dict | None:
    with _lock:
        conn = _conn()
        cur = conn.execute("SELECT data, updated_at FROM fundamentals WHERE symbol=?", (symbol,))
        row = cur.fetchone()
    if not row:
        return None
    data = json.loads(row[0])
    data["stored_at"] = row[1]
    return data


# ---------- news ----------

def store_news(symbol: str, items: list[dict]) -> int:
    if not items:
        return 0
    rows = [
        (symbol, n["title"], n.get("source", ""), n.get("url", ""),
         int(n.get("time") or 0), 1 if n.get("important") else 0, time.time())
        for n in items
    ]
    with _lock:
        conn = _conn()
        conn.executemany(
            "INSERT OR IGNORE INTO news(symbol, title, source, url, time, important, fetched_at) "
            "VALUES(?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    return len(rows)


def get_stored_news(symbol: str, limit: int = 10, min_age: int = 0) -> list[dict]:
    sql = ("SELECT title, source, url, time, important FROM news "
           "WHERE symbol=? AND (time>=? OR ?=0) ORDER BY time DESC, fetched_at DESC LIMIT ?")
    params = [symbol, min_age, min_age, limit]
    with _lock:
        conn = _conn()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    return [
        {"title": r[0], "source": r[1], "url": r[2], "time": r[3], "important": bool(r[4])}
        for r in rows
    ]


# ---------- snapshots ----------

def store_snapshot(symbol: str, data: dict, max_per_symbol: int = 5000) -> None:
    now = int(time.time())
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO snapshots(symbol, ts, data) VALUES(?,?,?)",
            (symbol, now, json.dumps(data, ensure_ascii=False)),
        )
        conn.execute(
            "DELETE FROM snapshots WHERE symbol=? AND ts NOT IN "
            "(SELECT ts FROM snapshots WHERE symbol=? ORDER BY ts DESC LIMIT ?)",
            (symbol, symbol, max_per_symbol),
        )
        conn.commit()


def get_recent_snapshots(symbol: str, limit: int = 10) -> list[dict]:
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "SELECT ts, data FROM snapshots WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        )
        rows = cur.fetchall()
    return [{"ts": r[0], **json.loads(r[1])} for r in rows]


def get_last_snapshot(symbol: str) -> dict | None:
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "SELECT ts, data FROM snapshots WHERE symbol=? ORDER BY ts DESC LIMIT 1", (symbol,)
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"ts": row[0], **json.loads(row[1])}


# ---------- positions ----------

def save_position(pos: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO positions(symbol, buy_price, quantity, buy_date, stop, target, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                pos["symbol"],
                float(pos.get("buy_price") or 0),
                float(pos.get("quantity") or 0),
                pos.get("buy_date", ""),
                pos.get("stop"),
                pos.get("target"),
                time.time(),
            ),
        )
        conn.commit()


def get_position(symbol: str) -> dict | None:
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "SELECT symbol, buy_price, quantity, buy_date, stop, target, created_at "
            "FROM positions WHERE symbol=?",
            (symbol,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "symbol": row[0],
        "buy_price": row[1],
        "quantity": row[2],
        "buy_date": row[3],
        "stop": row[4],
        "target": row[5],
        "created_at": row[6],
    }


def delete_position(symbol: str) -> None:
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        conn.commit()


# ---------- signals ----------

def store_signal(sig: dict) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO signals(symbol, title, emoji, direction, detail, source, url, price, sent, time) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                sig.get("symbol", ""),
                sig.get("title", ""),
                sig.get("emoji", ""),
                sig.get("direction"),
                sig.get("detail", ""),
                sig.get("source"),
                sig.get("url"),
                sig.get("price"),
                1 if sig.get("sent_telegram") else 0,
                sig.get("time", ""),
            ),
        )
        conn.commit()


def get_stored_signals(symbol: str, limit: int = 100) -> list[dict]:
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "SELECT symbol, title, emoji, direction, detail, source, url, price, sent, time "
            "FROM signals WHERE symbol=? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        )
        rows = cur.fetchall()
    out = []
    for r in reversed(rows):
        out.append({
            "symbol": r[0], "title": r[1], "emoji": r[2], "direction": r[3],
            "detail": r[4], "source": r[5], "url": r[6], "price": r[7],
            "sent_telegram": bool(r[8]), "time": r[9],
        })
    return out


def stats() -> dict:
    out = {}
    with _lock:
        conn = _conn()
        for table in ("price_bars", "news", "snapshots", "signals", "fundamentals", "positions"):
            try:
                cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
                out[table] = cur.fetchone()[0]
            except sqlite3.Error:
                out[table] = 0
    return out
