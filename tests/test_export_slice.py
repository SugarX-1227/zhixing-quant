"""行情切片导出的测试。

安全关键：导出文件会作为 GitHub Release 附件传到公网，且不可撤回
（附件会被缓存和索引）。一旦漏筛把 position / trade_log / account 带出去，
个人持仓和盈亏就公开了。所以「不导出个人数据」这条必须有测试锁死。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.export_slice import (DEFAULT_EXTRA, EXPORT_TABLES, NEVER_EXPORT,
                                  export, pick_codes, verify)


@pytest.fixture
def full_db(tmp_path) -> Path:
    """造一个「像真库」的库：行情表 + 个人交易表。"""
    db = tmp_path / "full.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE daily_bar (code TEXT, trade_date INTEGER, open REAL,
            high REAL, low REAL, close REAL, amount REAL, vol REAL,
            PRIMARY KEY (code, trade_date));
        CREATE INDEX idx_daily_bar_date ON daily_bar(trade_date);
        CREATE TABLE oamv_daily (trade_date INTEGER PRIMARY KEY, open REAL,
            high REAL, low REAL, close REAL, vol REAL, amount REAL);
        CREATE TABLE xdxr (code TEXT, ex_date INTEGER, bonus REAL,
            rights REAL, rights_px REAL, dividend REAL,
            PRIMARY KEY (code, ex_date));
        CREATE TABLE security (code TEXT PRIMARY KEY, name TEXT, market TEXT,
            board TEXT);
        CREATE TABLE security_name_hist (code TEXT, start_date INTEGER,
            name TEXT, PRIMARY KEY (code, start_date));
        -- 个人数据，一行都不能出现在导出文件里
        CREATE TABLE position (code TEXT, shares INTEGER, entry_price REAL);
        CREATE TABLE trade_log (id INTEGER, code TEXT, pnl REAL);
        CREATE TABLE account (book TEXT, cash REAL);
        CREATE TABLE watchlist (code TEXT);
        CREATE TABLE blacklist (code TEXT);
        CREATE TABLE sync_state (code TEXT PRIMARY KEY, last_date INTEGER);
    """)
    for i, code in enumerate(["600000", "600001", "600002", "sh000300"]):
        for d in range(20240101, 20240111):
            c.execute("INSERT INTO daily_bar VALUES (?,?,?,?,?,?,?,?)",
                      (code, d, 10.0, 10.5, 9.5, 10.2, 1e8 * (i + 1), 1e6))
        c.execute("INSERT INTO security VALUES (?,?,?,?)", (code, f"股{i}", "sh", "MAIN"))
        c.execute("INSERT INTO xdxr VALUES (?,?,?,?,?,?)", (code, 20240105, 1, 0, 0, 2))
        c.execute("INSERT INTO security_name_hist VALUES (?,?,?)",
                  (code, 20240101, f"股{i}"))
    for d in range(20240101, 20240111):
        c.execute("INSERT INTO oamv_daily VALUES (?,?,?,?,?,?,?)",
                  (d, 1000, 1010, 990, 1005, 1e9, 1e12))
    c.execute("INSERT INTO position VALUES ('600001', 1000, 12.3)")
    c.execute("INSERT INTO trade_log VALUES (1, '600002', -880.5)")
    c.execute("INSERT INTO account VALUES ('swing', 91210.0)")
    c.execute("INSERT INTO watchlist VALUES ('600003')")
    c.execute("INSERT INTO blacklist VALUES ('600004')")
    c.execute("INSERT INTO sync_state VALUES ('600000', 20240110)")
    c.commit()
    c.close()
    return db


def _tables(db: Path) -> set:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 安全：个人数据绝不出库
# ---------------------------------------------------------------------------

def test_export_contains_no_personal_tables(full_db, tmp_path):
    """这条挂了就意味着持仓和盈亏会被传到公网 Release 上。"""
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000", "sh000300"], 20240101, None, verbose=False)
    tables = _tables(out)
    for banned in NEVER_EXPORT:
        assert banned not in tables, f"导出文件里出现了个人数据表 {banned}"


def test_export_only_contains_whitelisted_tables(full_db, tmp_path):
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000"], 20240101, None, verbose=False)
    assert _tables(out) <= set(EXPORT_TABLES)


def test_verify_catches_a_leaked_table(tmp_path):
    """自查本身要能抓到泄露——否则它只是个摆设。"""
    bad = tmp_path / "bad.db"
    c = sqlite3.connect(bad)
    c.executescript("CREATE TABLE daily_bar (code TEXT);"
                    "CREATE TABLE position (code TEXT);")
    c.execute("INSERT INTO daily_bar VALUES ('600000')")
    c.commit()
    c.close()
    problems = verify(bad)
    assert any("position" in p for p in problems)


def test_verify_catches_an_empty_slice(tmp_path):
    empty = tmp_path / "empty.db"
    c = sqlite3.connect(empty)
    c.execute("CREATE TABLE daily_bar (code TEXT)")
    c.commit()
    c.close()
    assert any("空" in p for p in verify(empty))


def test_verify_passes_a_clean_slice(full_db, tmp_path):
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000"], 20240101, None, verbose=False)
    assert verify(out) == []


# ---------------------------------------------------------------------------
# 切片正确性
# ---------------------------------------------------------------------------

def test_export_filters_by_code(full_db, tmp_path):
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000"], 20240101, None, verbose=False)
    c = sqlite3.connect(out)
    codes = {r[0] for r in c.execute("SELECT DISTINCT code FROM daily_bar")}
    c.close()
    assert codes == {"600000"}


def test_export_filters_by_date_range(full_db, tmp_path):
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000"], 20240105, 20240107, verbose=False)
    c = sqlite3.connect(out)
    dates = sorted(r[0] for r in c.execute("SELECT trade_date FROM daily_bar"))
    c.close()
    assert dates == [20240105, 20240106, 20240107]


def test_export_keeps_the_whole_oamv_series(full_db, tmp_path):
    """活跃市值是全市场单序列，择时要用整段，不能按股票池筛掉。"""
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000"], 20240101, None, verbose=False)
    c = sqlite3.connect(out)
    n = c.execute("SELECT COUNT(*) FROM oamv_daily").fetchone()[0]
    c.close()
    assert n == 10


def test_export_preserves_schema_and_indexes(full_db, tmp_path):
    """schema 照搬，否则两边漂移，云端跑出来的和本地不是一回事。"""
    out = tmp_path / "slice.db"
    export(full_db, out, ["600000"], 20240101, None, verbose=False)
    c = sqlite3.connect(out)
    idx = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    c.close()
    assert "idx_daily_bar_date" in idx


def test_export_overwrites_an_existing_file(full_db, tmp_path):
    out = tmp_path / "slice.db"
    out.write_bytes(b"garbage")
    export(full_db, out, ["600000"], 20240101, None, verbose=False)
    assert verify(out) == []


def test_pick_codes_ranks_by_amount(full_db):
    c = sqlite3.connect(f"file:{full_db}?mode=ro", uri=True)
    try:
        codes = pick_codes(c, size=2, min_amount=0, as_of=None)
    finally:
        c.close()
    assert len(codes) == 2
    assert codes[0] == "sh000300", "成交额最大的应排第一"


def test_pick_codes_respects_min_amount(full_db):
    c = sqlite3.connect(f"file:{full_db}?mode=ro", uri=True)
    try:
        assert pick_codes(c, size=10, min_amount=1e12, as_of=None) == []
    finally:
        c.close()


def test_benchmark_indexes_are_in_the_default_extras():
    """不带基准指数的话，云端回测算不出超额收益。"""
    assert "sh000300" in DEFAULT_EXTRA


# ---------------------------------------------------------------------------
# 切片必须能当 market.db 直接用
# ---------------------------------------------------------------------------

def test_slice_is_a_usable_drop_in_database(full_db, tmp_path):
    from zhixing_quant.config import load_config
    from zhixing_quant.data import tdx_loader

    out = tmp_path / "slice.db"
    export(full_db, out, ["600000", "sh000300"], 20240101, None, verbose=False)

    cfg = load_config()
    cfg["data"]["db_path"] = str(out)
    try:
        tdx_loader.set_config(cfg)
        h = tdx_loader.data_health(cfg)
        assert h["ok"] and h["codes"] == 2 and h["bars"] == 20
        df = tdx_loader.load_daily("600000")
        assert len(df) == 10
    finally:
        tdx_loader.set_config(load_config())     # 别污染后面的测试
