"""本地行情仓库（SQLite）。

设计目标：
- 单文件，无外部服务，零依赖（sqlite3 是标准库）。
- 主键 (code, trade_date)，重复写入用 UPSERT，重跑同一天不会产生脏数据。
- sync_state 表记录每个 .day 文件上次同步到的字节偏移，实现增量读取。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bar (
    code       TEXT    NOT NULL,
    trade_date INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    amount     REAL    NOT NULL,
    vol        REAL    NOT NULL,
    PRIMARY KEY (code, trade_date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_daily_bar_date ON daily_bar(trade_date);

CREATE TABLE IF NOT EXISTS sync_state (
    code         TEXT PRIMARY KEY,
    market       TEXT NOT NULL,
    file_size    INTEGER NOT NULL,
    file_mtime   REAL    NOT NULL,
    record_count INTEGER NOT NULL,
    last_date    INTEGER NOT NULL,
    synced_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS security (
    code   TEXT PRIMARY KEY,
    name   TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL DEFAULT '',
    board  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS xdxr (
    code        TEXT NOT NULL,
    ex_date     INTEGER NOT NULL,
    bonus       REAL DEFAULT 0,   -- 每 10 股送股
    rights      REAL DEFAULT 0,   -- 每 10 股配股
    rights_px   REAL DEFAULT 0,   -- 配股价
    dividend    REAL DEFAULT 0,   -- 每 10 股派息（税前，元）
    PRIMARY KEY (code, ex_date)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class BarStore:
    """行情仓库。用作上下文管理器或长驻对象都可以。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # -- 连接管理 ---------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # Streamlit cache_resource may reuse a store from another script thread.
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "BarStore":
        self.conn  # noqa: B018 - 触发建表
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            conn = self.conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # -- 写入 -------------------------------------------------------------

    def upsert_bars(self, code: str, df: pd.DataFrame) -> int:
        """写入某只股票的日线。重复日期覆盖，返回写入条数。"""
        if df is None or df.empty:
            return 0
        cols = ["trade_date", "open", "high", "low", "close", "amount", "vol"]
        rows = [(code, *r) for r in df[cols].itertuples(index=False, name=None)]
        with self.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO daily_bar (code, trade_date, open, high, low, close, amount, vol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, trade_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, amount=excluded.amount, vol=excluded.vol
                """,
                rows,
            )
        return len(rows)

    def upsert_bars_bulk(self, batches: Iterable[tuple]) -> int:
        """批量写入多只股票，(code, df) 的可迭代对象。一次事务，快很多。"""
        cols = ["trade_date", "open", "high", "low", "close", "amount", "vol"]
        total = 0
        with self.transaction() as conn:
            for code, df in batches:
                if df is None or df.empty:
                    continue
                rows = [(code, *r) for r in df[cols].itertuples(index=False, name=None)]
                conn.executemany(
                    """
                    INSERT INTO daily_bar (code, trade_date, open, high, low, close, amount, vol)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code, trade_date) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, amount=excluded.amount, vol=excluded.vol
                    """,
                    rows,
                )
                total += len(rows)
        return total

    def set_sync_state(
        self,
        code: str,
        market: str,
        file_size: int,
        file_mtime: float,
        record_count: int,
        last_date: int,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sync_state
                    (code, market, file_size, file_mtime, record_count, last_date, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(code) DO UPDATE SET
                    market=excluded.market, file_size=excluded.file_size,
                    file_mtime=excluded.file_mtime, record_count=excluded.record_count,
                    last_date=excluded.last_date, synced_at=excluded.synced_at
                """,
                (code, market, file_size, file_mtime, record_count, last_date),
            )

    def set_sync_states_bulk(self, states: Sequence[tuple]) -> None:
        """states: [(code, market, size, mtime, record_count, last_date), ...]"""
        if not states:
            return
        with self.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO sync_state
                    (code, market, file_size, file_mtime, record_count, last_date, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(code) DO UPDATE SET
                    market=excluded.market, file_size=excluded.file_size,
                    file_mtime=excluded.file_mtime, record_count=excluded.record_count,
                    last_date=excluded.last_date, synced_at=excluded.synced_at
                """,
                states,
            )

    def get_sync_state(self, code: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM sync_state WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_sync_states(self) -> dict:
        rows = self.conn.execute("SELECT * FROM sync_state").fetchall()
        return {r["code"]: dict(r) for r in rows}

    def upsert_securities(self, records: Sequence[tuple]) -> None:
        """records: [(code, name, market, board), ...]"""
        if not records:
            return
        with self.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO security (code, name, market, board) VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=CASE WHEN excluded.name != '' THEN excluded.name ELSE security.name END,
                    market=excluded.market, board=excluded.board
                """,
                records,
            )

    def upsert_xdxr(self, records: Sequence[tuple]) -> None:
        """records: [(code, ex_date, bonus, rights, rights_px, dividend), ...]"""
        if not records:
            return
        with self.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO xdxr (code, ex_date, bonus, rights, rights_px, dividend)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, ex_date) DO UPDATE SET
                    bonus=excluded.bonus, rights=excluded.rights,
                    rights_px=excluded.rights_px, dividend=excluded.dividend
                """,
                records,
            )

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # -- 读取 -------------------------------------------------------------

    def load_bars(
        self,
        code: str,
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """读取单只股票日线，按日期升序，索引为 DatetimeIndex。"""
        sql = "SELECT trade_date, open, high, low, close, amount, vol FROM daily_bar WHERE code = ?"
        params: List = [code]
        if start_date is not None:
            sql += " AND trade_date >= ?"
            params.append(int(start_date))
        if end_date is not None:
            sql += " AND trade_date <= ?"
            params.append(int(end_date))
        if limit is not None:
            # 取最近 limit 条，再翻回升序
            sql += " ORDER BY trade_date DESC LIMIT ?"
            params.append(int(limit))
        else:
            sql += " ORDER BY trade_date ASC"

        df = pd.read_sql_query(sql, self.conn, params=params)
        if df.empty:
            return _empty_bar_frame()
        if limit is not None:
            df = df.iloc[::-1].reset_index(drop=True)
        return _finalize_bar_frame(df)

    def load_bars_many(
        self,
        codes: Sequence[str],
        start_date: Optional[int] = None,
        end_date: Optional[int] = None,
    ) -> dict:
        """一次查询多只股票，返回 {code: DataFrame}。扫描全市场时比逐只查快很多。"""
        if not codes:
            return {}
        placeholders = ",".join("?" * len(codes))
        sql = (
            "SELECT code, trade_date, open, high, low, close, amount, vol "
            f"FROM daily_bar WHERE code IN ({placeholders})"
        )
        params: List = list(codes)
        if start_date is not None:
            sql += " AND trade_date >= ?"
            params.append(int(start_date))
        if end_date is not None:
            sql += " AND trade_date <= ?"
            params.append(int(end_date))
        sql += " ORDER BY code, trade_date ASC"

        raw = pd.read_sql_query(sql, self.conn, params=params)
        if raw.empty:
            return {}
        out = {}
        for code, group in raw.groupby("code", sort=False):
            out[str(code)] = _finalize_bar_frame(group.drop(columns=["code"]))
        return out

    def latest_snapshot(self, trade_date: Optional[int] = None) -> pd.DataFrame:
        """构造某个交易日的"全市场快照"，取代原来的 tdx_screener 条件选股。

        Returns:
            DataFrame: code, name, open, high, low, close, amount, vol, pct_chg, date
        """
        requested = int(trade_date) if trade_date is not None else None
        params = (requested,) if requested is not None else ()
        row = self.conn.execute(
            "SELECT MAX(trade_date) AS d FROM daily_bar "
            "WHERE code = 'sh000001' "
            + ("AND trade_date <= ?" if requested is not None else ""),
            params,
        ).fetchone()
        if row is None or row["d"] is None:
            row = self.conn.execute(
                "SELECT MAX(trade_date) AS d FROM daily_bar"
                + (" WHERE trade_date <= ?" if requested is not None else ""),
                params,
            ).fetchone()
        if row is None or row["d"] is None:
            return pd.DataFrame(
                columns=["code", "name", "close", "amount", "pct_chg", "date"]
            )
        trade_date = int(row["d"])

        sql = """
        WITH ranked AS (
            SELECT code, trade_date, open, high, low, close, amount, vol,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
            FROM daily_bar
            WHERE trade_date <= ?
        )
        SELECT t.code,
               COALESCE(s.name, '') AS name,
               t.trade_date, t.open, t.high, t.low, t.close, t.amount, t.vol,
               p.close AS pre_close
        FROM ranked t
        LEFT JOIN ranked p ON p.code = t.code AND p.rn = 2
        LEFT JOIN security s ON s.code = t.code
        WHERE t.rn = 1 AND t.trade_date = ?
        """
        df = pd.read_sql_query(sql, self.conn, params=[int(trade_date), int(trade_date)])
        if df.empty:
            return pd.DataFrame(columns=["code", "name", "close", "amount", "pct_chg", "date"])
        df["pct_chg"] = (df["close"] / df["pre_close"] - 1.0) * 100.0
        df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df.drop(columns=["pre_close"])

    def bar_count(self, code: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM daily_bar WHERE code = ?", (code,)
        ).fetchone()
        return int(row["n"]) if row else 0

    def trading_dates(
        self, start_date: int, end_date: int, ref_code: str = "sh000001"
    ) -> List[int]:
        """用基准指数的日线推交易日历。默认上证指数，退化时用全库并集。"""
        for candidate in (ref_code, "000001", "600000"):
            rows = self.conn.execute(
                "SELECT DISTINCT trade_date FROM daily_bar "
                "WHERE code = ? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                (candidate, int(start_date), int(end_date)),
            ).fetchall()
            if rows:
                return [int(r["trade_date"]) for r in rows]
        # 退化：用全库所有出现过的日期
        rows = self.conn.execute(
            "SELECT DISTINCT trade_date FROM daily_bar "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (int(start_date), int(end_date)),
        ).fetchall()
        return [int(r["trade_date"]) for r in rows]

    def stats(self) -> dict:
        c = self.conn
        bars = c.execute("SELECT COUNT(*) AS n FROM daily_bar").fetchone()["n"]
        codes = c.execute("SELECT COUNT(DISTINCT code) AS n FROM daily_bar").fetchone()["n"]
        rng = c.execute(
            "SELECT MIN(trade_date) AS lo, MAX(trade_date) AS hi FROM daily_bar"
        ).fetchone()
        market_date = c.execute(
            "SELECT MAX(trade_date) AS d FROM daily_bar WHERE code = 'sh000001'"
        ).fetchone()["d"]
        named = c.execute("SELECT COUNT(*) AS n FROM security WHERE name != ''").fetchone()["n"]
        xdxr_n = c.execute("SELECT COUNT(DISTINCT code) AS n FROM xdxr").fetchone()["n"]
        return {
            "bars": int(bars or 0),
            "codes": int(codes or 0),
            "date_min": rng["lo"],
            "date_max": market_date if market_date is not None else rng["hi"],
            "named": int(named or 0),
            "xdxr_codes": int(xdxr_n or 0),
            "db_size_mb": round(self.db_path.stat().st_size / 1024 / 1024, 1)
            if self.db_path.exists()
            else 0.0,
            "last_sync": self.get_meta("last_sync", "never"),
        }

    def load_xdxr(self, code: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ex_date, bonus, rights, rights_px, dividend FROM xdxr "
            "WHERE code = ? ORDER BY ex_date",
            self.conn,
            params=[code],
        )

    def load_securities(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM security", self.conn)


def _empty_bar_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=["open", "high", "low", "close", "amount", "vol"])
    df.index = pd.DatetimeIndex([], name="date")
    return df


def _finalize_bar_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.to_datetime(df["trade_date"].astype(int).astype(str), format="%Y%m%d")
    df = df.drop(columns=["trade_date"])
    df.index = pd.DatetimeIndex(idx, name="date")
    return df[["open", "high", "low", "close", "amount", "vol"]]
