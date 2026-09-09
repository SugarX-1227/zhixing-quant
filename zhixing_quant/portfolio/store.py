"""持仓存储。

这是规格 07（防守）、08（仓位）能落地的前提。在此之前数据库只有行情表，
没有任何地方记录你实际持有什么，所以 DefenseEngine / HoldingRater / PositionSizer
永远只能跑测试用例。

四张表：
    position   —— 当前持仓
    trade_log  —— 已平仓的成交记录
    watchlist  —— 自选池（规格 01.3 的 WATCHING 状态）
    blacklist  —— 跌破黄线后移出的标的（规格 01.3 BLACKLISTED，[LOCKED]）

规格 01.6 要求超短体系与波段体系**分账户运行**，所以每条记录都带 `book` 字段
（"swing" 波段 / "scalp" 超短），查询默认按 book 隔离。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS position (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book        TEXT    NOT NULL DEFAULT 'swing',
    code        TEXT    NOT NULL,
    name        TEXT    NOT NULL DEFAULT '',
    strategy    TEXT    NOT NULL DEFAULT '',
    state       TEXT    NOT NULL DEFAULT 'HELD',   -- HELD / SCALED_OUT / REDUCED
    entry_date  TEXT    NOT NULL,
    entry_price REAL    NOT NULL,
    shares      INTEGER NOT NULL,
    stop_loss   REAL    NOT NULL,                  -- 规格硬要求：不允许为空
    take_profit REAL,
    note        TEXT    NOT NULL DEFAULT '',
    UNIQUE(book, code)
);

CREATE TABLE IF NOT EXISTS trade_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book        TEXT    NOT NULL DEFAULT 'swing',
    code        TEXT    NOT NULL,
    name        TEXT    NOT NULL DEFAULT '',
    strategy    TEXT    NOT NULL DEFAULT '',
    entry_date  TEXT    NOT NULL,
    exit_date   TEXT    NOT NULL,
    entry_price REAL    NOT NULL,
    exit_price  REAL    NOT NULL,
    shares      INTEGER NOT NULL,
    pnl         REAL    NOT NULL,
    return_pct  REAL    NOT NULL,
    exit_reason TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watchlist (
    book     TEXT NOT NULL DEFAULT 'swing',
    code     TEXT NOT NULL,
    name     TEXT NOT NULL DEFAULT '',
    added_on TEXT NOT NULL,
    source   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (book, code)
);

CREATE TABLE IF NOT EXISTS blacklist (
    code       TEXT PRIMARY KEY,
    reason     TEXT NOT NULL DEFAULT '',
    added_on   TEXT NOT NULL,
    cleared_on TEXT
);

CREATE TABLE IF NOT EXISTS account (
    book   TEXT PRIMARY KEY,
    cash   REAL NOT NULL DEFAULT 0
);
"""


class PortfolioStore:
    """持仓仓库。与行情库共用同一个 SQLite 文件。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "PortfolioStore":
        self.conn
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- 持仓 -------------------------------------------------------------

    def open_position(
        self,
        code: str,
        shares: int,
        entry_price: float,
        stop_loss: float,
        *,
        book: str = "swing",
        name: str = "",
        strategy: str = "",
        take_profit: Optional[float] = None,
        entry_date: Optional[str] = None,
        note: str = "",
    ) -> int:
        """建仓。

        规格 01.2 要求 build_trade_plan 不得返回没有止损位的计划，
        所以这里对缺失或不合法的止损直接抛错，而不是填默认值。
        """
        if stop_loss is None or stop_loss <= 0:
            raise ValueError(
                f"{code} 缺少止损位。规格要求下单前必须先写好止损，不允许留空。"
            )
        if stop_loss >= entry_price:
            raise ValueError(
                f"{code} 止损价 {stop_loss} 不低于买入价 {entry_price}，无法计算风险敞口。"
            )
        if shares <= 0 or shares % 100 != 0:
            raise ValueError(f"{code} 股数 {shares} 非法，A 股必须是 100 的整数倍。")

        entry_date = entry_date or datetime.now().strftime("%Y-%m-%d")
        cur = self.conn.execute(
            """
            INSERT INTO position
              (book, code, name, strategy, state, entry_date, entry_price,
               shares, stop_loss, take_profit, note)
            VALUES (?,?,?,?,'HELD',?,?,?,?,?,?)
            ON CONFLICT(book, code) DO UPDATE SET
              shares = position.shares + excluded.shares,
              entry_price = (position.entry_price * position.shares
                             + excluded.entry_price * excluded.shares)
                            / (position.shares + excluded.shares),
              stop_loss = excluded.stop_loss,
              take_profit = excluded.take_profit
            """,
            (book, str(code).zfill(6), name, strategy, entry_date,
             float(entry_price), int(shares), float(stop_loss),
             take_profit, note),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def close_position(
        self,
        code: str,
        exit_price: float,
        *,
        book: str = "swing",
        shares: Optional[int] = None,
        exit_date: Optional[str] = None,
        reason: str = "",
    ) -> Optional[dict]:
        """平仓或减仓。shares 为空表示全部卖出，写入 trade_log。"""
        row = self.conn.execute(
            "SELECT * FROM position WHERE book=? AND code=?",
            (book, str(code).zfill(6)),
        ).fetchone()
        if row is None:
            return None

        pos = dict(row)
        sell = int(shares) if shares else int(pos["shares"])
        sell = min(sell, int(pos["shares"]))
        exit_date = exit_date or datetime.now().strftime("%Y-%m-%d")

        pnl = (float(exit_price) - float(pos["entry_price"])) * sell
        ret = float(exit_price) / float(pos["entry_price"]) - 1.0

        self.conn.execute(
            """
            INSERT INTO trade_log
              (book, code, name, strategy, entry_date, exit_date,
               entry_price, exit_price, shares, pnl, return_pct, exit_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (book, pos["code"], pos["name"], pos["strategy"], pos["entry_date"],
             exit_date, pos["entry_price"], float(exit_price), sell,
             round(pnl, 2), round(ret, 4), reason),
        )
        remain = int(pos["shares"]) - sell
        if remain > 0:
            self.conn.execute(
                "UPDATE position SET shares=?, state='REDUCED' WHERE id=?",
                (remain, pos["id"]),
            )
        else:
            self.conn.execute("DELETE FROM position WHERE id=?", (pos["id"],))
        self.conn.commit()
        return {"code": pos["code"], "shares": sell, "pnl": round(pnl, 2),
                "return_pct": round(ret, 4), "remaining": remain}

    def update_stop(self, code: str, stop_loss: float, *, book: str = "swing") -> None:
        """上移止损（放飞后保底）。"""
        self.conn.execute(
            "UPDATE position SET stop_loss=? WHERE book=? AND code=?",
            (float(stop_loss), book, str(code).zfill(6)),
        )
        self.conn.commit()

    def set_state(self, code: str, state: str, *, book: str = "swing") -> None:
        self.conn.execute(
            "UPDATE position SET state=? WHERE book=? AND code=?",
            (state, book, str(code).zfill(6)),
        )
        self.conn.commit()

    def positions(self, book: Optional[str] = "swing") -> List[dict]:
        """当前持仓。book 传 None 表示两个账户一起返回。"""
        if book is None:
            rows = self.conn.execute(
                "SELECT * FROM position ORDER BY book, entry_date"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM position WHERE book=? ORDER BY entry_date", (book,)
            ).fetchall()
        return [dict(r) for r in rows]

    def position(self, code: str, *, book: str = "swing") -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM position WHERE book=? AND code=?",
            (book, str(code).zfill(6)),
        ).fetchone()
        return dict(row) if row else None

    # -- 自选与黑名单 -----------------------------------------------------

    def add_watch(self, code: str, name: str = "", *,
                  book: str = "swing", source: str = "") -> None:
        self.conn.execute(
            "INSERT INTO watchlist (book, code, name, added_on, source) "
            "VALUES (?,?,?,?,?) ON CONFLICT(book, code) DO UPDATE SET "
            "name=excluded.name, source=excluded.source",
            (book, str(code).zfill(6), name,
             datetime.now().strftime("%Y-%m-%d"), source),
        )
        self.conn.commit()

    def remove_watch(self, code: str, *, book: str = "swing") -> None:
        self.conn.execute("DELETE FROM watchlist WHERE book=? AND code=?",
                          (book, str(code).zfill(6)))
        self.conn.commit()

    def watchlist(self, book: str = "swing") -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM watchlist WHERE book=? ORDER BY added_on DESC", (book,)
        ).fetchall()
        return [dict(r) for r in rows]

    def blacklist_add(self, code: str, reason: str = "跌破黄线") -> None:
        """规格 01.3 [LOCKED]：跌破黄线的标的移出选股池，直到重新站上黄线。"""
        self.conn.execute(
            "INSERT INTO blacklist (code, reason, added_on, cleared_on) "
            "VALUES (?,?,?,NULL) ON CONFLICT(code) DO UPDATE SET "
            "reason=excluded.reason, added_on=excluded.added_on, cleared_on=NULL",
            (str(code).zfill(6), reason, datetime.now().strftime("%Y-%m-%d")),
        )
        self.conn.commit()

    def blacklist_clear(self, code: str) -> None:
        self.conn.execute(
            "UPDATE blacklist SET cleared_on=? WHERE code=?",
            (datetime.now().strftime("%Y-%m-%d"), str(code).zfill(6)),
        )
        self.conn.commit()

    def blacklisted(self) -> set:
        rows = self.conn.execute(
            "SELECT code FROM blacklist WHERE cleared_on IS NULL"
        ).fetchall()
        return {r["code"] for r in rows}

    # -- 账户 -------------------------------------------------------------

    def set_cash(self, cash: float, *, book: str = "swing") -> None:
        self.conn.execute(
            "INSERT INTO account (book, cash) VALUES (?,?) "
            "ON CONFLICT(book) DO UPDATE SET cash=excluded.cash",
            (book, float(cash)),
        )
        self.conn.commit()

    def cash(self, book: str = "swing") -> float:
        row = self.conn.execute(
            "SELECT cash FROM account WHERE book=?", (book,)
        ).fetchone()
        return float(row["cash"]) if row else 0.0

    def equity(self, prices: dict, book: str = "swing") -> float:
        """现金 + 持仓市值。prices: {code: 最新收盘价}"""
        total = self.cash(book)
        for p in self.positions(book):
            px = float(prices.get(p["code"], p["entry_price"]))
            total += px * int(p["shares"])
        return round(total, 2)

    # -- 成交记录 ---------------------------------------------------------

    def trades(self, book: Optional[str] = None, limit: int = 200) -> pd.DataFrame:
        sql = "SELECT * FROM trade_log"
        params: list = []
        if book:
            sql += " WHERE book=?"
            params.append(book)
        sql += " ORDER BY exit_date DESC LIMIT ?"
        params.append(int(limit))
        return pd.read_sql_query(sql, self.conn, params=params)

    def stats(self, book: Optional[str] = None) -> dict:
        df = self.trades(book, limit=100000)
        if df.empty:
            return {"trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
                    "avg_win": 0.0, "avg_loss": 0.0, "profit_loss_ratio": 0.0}
        wins = df[df["pnl"] > 0]["pnl"]
        losses = df[df["pnl"] <= 0]["pnl"]
        avg_win = float(wins.mean()) if len(wins) else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) else 0.0
        return {
            "trades": int(len(df)),
            "win_rate": round(len(wins) / len(df), 4),
            "total_pnl": round(float(df["pnl"].sum()), 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_loss_ratio": round(avg_win / avg_loss, 2) if avg_loss else 0.0,
        }
