"""SQLite 持久化：每日交易記錄 + 累積持倉"""
import sqlite3
from datetime import date
from pathlib import Path


import os as _os
DB_PATH = Path(_os.getenv("DATABASE_PATH", str(Path(__file__).parent / "tracker.db")))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date  TEXT    NOT NULL,
                stock_code  TEXT    NOT NULL,
                stock_name  TEXT    NOT NULL,
                broker_code TEXT    NOT NULL,
                broker_name TEXT    NOT NULL,
                watch_side  TEXT    NOT NULL DEFAULT 'net',
                buy_lots    INTEGER NOT NULL DEFAULT 0,
                sell_lots   INTEGER NOT NULL DEFAULT 0,
                net_lots    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(trade_date, stock_code, broker_code)
            );

            CREATE TABLE IF NOT EXISTS positions (
                stock_code  TEXT    NOT NULL,
                stock_name  TEXT    NOT NULL,
                broker_code TEXT    NOT NULL,
                broker_name TEXT    NOT NULL,
                watch_side  TEXT    NOT NULL DEFAULT 'net',
                total_buy   INTEGER NOT NULL DEFAULT 0,
                total_sell  INTEGER NOT NULL DEFAULT 0,
                total_net   INTEGER NOT NULL DEFAULT 0,
                last_date   TEXT    NOT NULL,
                PRIMARY KEY (stock_code, broker_code)
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                trade_date  TEXT    NOT NULL,
                stock_code  TEXT    NOT NULL,
                fetched_at  TEXT    NOT NULL,
                PRIMARY KEY (trade_date, stock_code)
            );
        """)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection):
    """舊版 schema 升級：補上 watch_side / total_buy / total_sell 欄位。"""
    trades_cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if trades_cols and "watch_side" not in trades_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN watch_side TEXT NOT NULL DEFAULT 'net'")

    pos_cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if pos_cols and "total_buy" not in pos_cols:
        conn.execute("ALTER TABLE positions ADD COLUMN total_buy  INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE positions ADD COLUMN total_sell INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE positions ADD COLUMN watch_side TEXT    NOT NULL DEFAULT 'net'")
        if "total_net" not in pos_cols:
            conn.execute("ALTER TABLE positions ADD COLUMN total_net INTEGER NOT NULL DEFAULT 0")
            if "total_lots" in pos_cols:
                conn.execute("UPDATE positions SET total_net = total_lots")


def is_synced(trade_date: date, stock_code: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sync_log WHERE trade_date=? AND stock_code=?",
            (trade_date.isoformat(), stock_code),
        ).fetchone()
        return row is not None


def save_trades(trade_date: date, stock_code: str, stock_name: str,
                records: list[dict], mark_synced: bool = True):
    """儲存當日交易記錄並累積持倉（buy / sell / net 三欄各自累加）。"""
    from datetime import datetime

    date_str = trade_date.isoformat()
    with get_conn() as conn:
        for r in records:
            bid = r.get("broker_id") or r.get("broker_code", "")
            ws  = r.get("watch_side", "net")
            conn.execute(
                """
                INSERT OR REPLACE INTO trades
                    (trade_date, stock_code, stock_name, broker_code, broker_name,
                     watch_side, buy_lots, sell_lots, net_lots)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (date_str, stock_code, stock_name,
                 bid, r["broker_name"], ws,
                 r["buy_lots"], r["sell_lots"], r["net_lots"]),
            )
            conn.execute(
                """
                INSERT INTO positions
                    (stock_code, stock_name, broker_code, broker_name,
                     watch_side, total_buy, total_sell, total_net, last_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_code, broker_code) DO UPDATE SET
                    total_buy  = total_buy  + excluded.total_buy,
                    total_sell = total_sell + excluded.total_sell,
                    total_net  = total_net  + excluded.total_net,
                    last_date  = excluded.last_date,
                    stock_name  = excluded.stock_name,
                    broker_name = excluded.broker_name,
                    watch_side  = excluded.watch_side
                """,
                (stock_code, stock_name,
                 bid, r["broker_name"], ws,
                 r["buy_lots"], r["sell_lots"], r["net_lots"], date_str),
            )

        if mark_synced:
            conn.execute(
                "INSERT OR REPLACE INTO sync_log VALUES (?, ?, ?)",
                (date_str, stock_code, datetime.now().isoformat()),
            )


def get_today_trades(trade_date: date, target_brokers: list[str]) -> list[dict]:
    """取得指定日期、目標分點的所有交易記錄（含 watch_side）。"""
    placeholders = ",".join("?" * len(target_brokers))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT trade_date, stock_code, stock_name,
                   broker_code, broker_name, watch_side,
                   buy_lots, sell_lots, net_lots
            FROM trades
            WHERE trade_date=? AND broker_code IN ({placeholders})
            ORDER BY ABS(net_lots) DESC
            """,
            [trade_date.isoformat(), *target_brokers],
        ).fetchall()
    return [dict(r) for r in rows]


def get_positions(target_brokers: list[str]) -> list[dict]:
    """取得目標分點的累積持倉（total_buy / total_sell / total_net）。"""
    placeholders = ",".join("?" * len(target_brokers))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT stock_code, stock_name, broker_code, broker_name,
                   watch_side, total_buy, total_sell, total_net, last_date
            FROM positions
            WHERE broker_code IN ({placeholders})
              AND (total_buy != 0 OR total_sell != 0 OR total_net != 0)
            ORDER BY broker_code, ABS(total_net) DESC
            """,
            target_brokers,
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_synced_date() -> date | None:
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(trade_date) as d FROM sync_log").fetchone()
    if row and row["d"]:
        return date.fromisoformat(row["d"])
    return None
