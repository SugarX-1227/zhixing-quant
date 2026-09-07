"""本地通达信数据层测试。

这些测试全部自造二进制文件，不依赖真实的通达信安装目录，可以在 CI 里跑。
"""

from __future__ import annotations

import struct
import threading
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from zhixing_quant.data import tdx_reader as tr
from zhixing_quant.data.store import BarStore
from zhixing_quant.data.xdxr import apply_qfq
from zhixing_quant.data.names import _from_tnf

DAY_STRUCT = struct.Struct("<IIIIIfII")


def _pack_day(d: int, o: float, h: float, low: float, c: float,
              amount: float, vol: int) -> bytes:
    return DAY_STRUCT.pack(
        d, int(round(o * 100)), int(round(h * 100)),
        int(round(low * 100)), int(round(c * 100)), amount, vol, 0
    )


def _write_day_file(path: Path, n: int = 50, start: date = date(2024, 1, 2)) -> list:
    """写一个合法的 .day 文件，返回写入的日期列表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    dates, cur = [], start
    chunks = []
    px = 10.0
    while len(dates) < n:
        if cur.weekday() < 5:
            d = int(cur.strftime("%Y%m%d"))
            dates.append(d)
            chunks.append(_pack_day(d, px, px * 1.02, px * 0.98, px * 1.01, 1e8, 1_000_000))
            px *= 1.01
        cur += timedelta(days=1)
    path.write_bytes(b"".join(chunks))
    return dates


# ---------------------------------------------------------------------------
# 二进制解析
# ---------------------------------------------------------------------------


def test_read_day_file_roundtrip(tmp_path):
    f = tmp_path / "sh600000.day"
    dates = _write_day_file(f, n=30)
    df = tr.read_day_file(f)
    assert len(df) == 30
    assert df["trade_date"].tolist() == dates
    # 价格是 int/100，第一条 open 应为 10.00
    assert df["open"].iloc[0] == pytest.approx(10.0)
    assert (df["high"] >= df["low"]).all()


def test_read_day_file_from_offset(tmp_path):
    """增量读取：从第 20 条开始只应读到后 10 条。"""
    f = tmp_path / "sh600000.day"
    _write_day_file(f, n=30)
    df = tr.read_day_file(f, start_offset=20 * tr.DAY_RECORD_SIZE)
    assert len(df) == 10


def test_offset_must_be_aligned(tmp_path):
    f = tmp_path / "sh600000.day"
    _write_day_file(f, n=10)
    with pytest.raises(ValueError):
        tr.read_day_file(f, start_offset=17)


def test_corrupt_record_is_skipped(tmp_path):
    """日期字段明显越界的记录不能进数据库。"""
    f = tmp_path / "sh600000.day"
    _write_day_file(f, n=10)
    f.write_bytes(f.read_bytes() + _pack_day(99999999, 1, 1, 1, 1, 0, 0))
    df = tr.read_day_file(f)
    assert len(df) == 10


def test_read_record_at_matches_full_read(tmp_path):
    f = tmp_path / "sh600000.day"
    _write_day_file(f, n=25)
    full = tr.read_day_file(f)
    probe = tr.read_record_at(f, 17)
    assert probe["trade_date"] == full["trade_date"].iloc[17]


def test_trailing_partial_record_ignored(tmp_path):
    """文件末尾有半条记录（正在写入中）时不应崩溃。"""
    f = tmp_path / "sh600000.day"
    _write_day_file(f, n=10)
    f.write_bytes(f.read_bytes() + b"\x00" * 13)
    assert len(tr.read_day_file(f)) == 10


def test_is_tradable_stock():
    assert tr.is_tradable_stock("600000", "sh")
    assert tr.is_tradable_stock("688001", "sh")
    assert tr.is_tradable_stock("000001", "sz")
    assert tr.is_tradable_stock("300750", "sz")
    assert not tr.is_tradable_stock("510300", "sh")   # ETF
    assert not tr.is_tradable_stock("000001", "sh")   # 上证指数
    assert not tr.is_tradable_stock("430047", "bj", include_bj=False)
    assert tr.is_tradable_stock("430047", "bj", include_bj=True)


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------


def _sample_frame(dates: list) -> pd.DataFrame:
    n = len(dates)
    return pd.DataFrame({
        "trade_date": dates,
        "open": np.linspace(10, 11, n),
        "high": np.linspace(10.5, 11.5, n),
        "low": np.linspace(9.5, 10.5, n),
        "close": np.linspace(10.2, 11.2, n),
        "amount": np.full(n, 1e8),
        "vol": np.full(n, 1e6),
    })


def test_upsert_is_idempotent(tmp_path):
    store = BarStore(tmp_path / "t.db")
    dates = [20240102, 20240103, 20240104]
    df = _sample_frame(dates)
    store.upsert_bars("600000", df)
    store.upsert_bars("600000", df)          # 重跑同一天
    assert store.bar_count("600000") == 3
    store.close()


def test_load_bars_returns_datetime_index(tmp_path):
    store = BarStore(tmp_path / "t.db")
    store.upsert_bars("600000", _sample_frame([20240102, 20240103, 20240104]))
    df = store.load_bars("600000")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert list(df.columns) == ["open", "high", "low", "close", "amount", "vol"]
    store.close()


def test_index_and_stock_codes_do_not_collide(tmp_path):
    """上证指数 sh000001 与平安银行 sz000001 必须分开存。"""
    store = BarStore(tmp_path / "t.db")
    store.upsert_bars("000001", _sample_frame([20240102, 20240103]))       # 平安银行
    store.upsert_bars("sh000001", _sample_frame([20240102, 20240103]))     # 上证指数
    assert store.bar_count("000001") == 2
    assert store.bar_count("sh000001") == 2
    store.close()


def test_load_bars_many(tmp_path):
    store = BarStore(tmp_path / "t.db")
    for code in ("600000", "000001", "300750"):
        store.upsert_bars(code, _sample_frame([20240102, 20240103, 20240104]))
    got = store.load_bars_many(["600000", "000001"], start_date=20240103)
    assert set(got) == {"600000", "000001"}
    assert all(len(v) == 2 for v in got.values())
    store.close()


def test_latest_snapshot_computes_pct_chg(tmp_path):
    store = BarStore(tmp_path / "t.db")
    df = pd.DataFrame({
        "trade_date": [20240102, 20240103],
        "open": [10.0, 10.0], "high": [11.0, 11.0], "low": [9.0, 9.0],
        "close": [10.0, 11.0], "amount": [1e8, 2e8], "vol": [1e6, 1e6],
    })
    store.upsert_bars("600000", df)
    store.upsert_securities([("600000", "浦发银行", "sh", "MAIN")])
    snap = store.latest_snapshot()
    assert len(snap) == 1
    assert snap["name"].iloc[0] == "浦发银行"
    assert snap["pct_chg"].iloc[0] == pytest.approx(10.0)
    store.close()


def test_latest_snapshot_uses_previous_trading_day(tmp_path):
    store = BarStore(tmp_path / "t.db")
    store.upsert_bars("600000", pd.DataFrame({
        "trade_date": [20240104, 20240105],
        "open": [10.0, 11.0], "high": [10.5, 11.5],
        "low": [9.5, 10.5], "close": [10.0, 11.0],
        "amount": [1e8, 2e8], "vol": [1e6, 1e6],
    }))
    snap = store.latest_snapshot(20240107)  # 周末，无当日 K 线
    assert len(snap) == 1
    assert snap["date"].iloc[0] == pd.Timestamp("2024-01-05")
    store.close()


def test_latest_snapshot_ignores_isolated_newer_bar(tmp_path):
    store = BarStore(tmp_path / "t.db")
    complete = _sample_frame([20240104, 20240105])
    store.upsert_bars("sh000001", complete)
    store.upsert_bars("600000", complete)
    store.upsert_bars("603448", _sample_frame([20240108]))

    snap = store.latest_snapshot(20240108)

    assert set(snap["code"]) == {"sh000001", "600000"}
    assert snap["date"].nunique() == 1
    assert snap["date"].iloc[0] == pd.Timestamp("2024-01-05")
    store.close()


def test_sync_state_roundtrip(tmp_path):
    store = BarStore(tmp_path / "t.db")
    store.set_sync_state("600000", "sh", 3200, 1700000000.0, 100, 20240104)
    state = store.get_sync_state("600000")
    assert state["record_count"] == 100
    assert state["last_date"] == 20240104
    store.set_sync_state("600000", "sh", 3232, 1700000100.0, 101, 20240105)
    assert store.get_sync_state("600000")["record_count"] == 101
    store.close()


def test_store_can_be_used_from_streamlit_worker_thread(tmp_path):
    """Streamlit may reuse the cached store from a different script thread."""
    store = BarStore(tmp_path / "threaded.db")
    store.upsert_bars("600000", _sample_frame([20240102]))
    errors = []

    def read_stats():
        try:
            assert store.stats()["bars"] == 1
        except Exception as exc:  # pragma: no cover - assertion reports the failure
            errors.append(exc)

    worker = threading.Thread(target=read_stats)
    worker.start()
    worker.join()
    store.close()
    assert errors == []


def test_from_tnf_reads_current_360_byte_records(tmp_path):
    cache = tmp_path / "T0002" / "hq_cache"
    cache.mkdir(parents=True)
    block = bytearray(360)
    block[0:6] = b"300328"
    block[30:30 + len("神农科技".encode("gbk"))] = "神农科技".encode("gbk")
    (cache / "szs.tnf").write_bytes(bytes(50) + block)
    assert _from_tnf(tmp_path) == {"300328": "神农科技"}


# ---------------------------------------------------------------------------
# 复权
# ---------------------------------------------------------------------------


def _flat_frame(prices: list) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.date_range("2024-01-01", periods=len(prices), freq="B"), name="date")
    arr = np.array(prices, dtype=float)
    return pd.DataFrame({"open": arr, "high": arr, "low": arr, "close": arr,
                         "amount": arr * 1e6, "vol": np.full(len(arr), 1e6)}, index=idx)


def test_qfq_removes_bonus_share_gap():
    """10 送 10 后价格腰斩，前复权应把历史价格也砍半，缺口消失。"""
    df = _flat_frame([20, 20, 20, 10, 10, 10])
    ex = int(df.index[3].strftime("%Y%m%d"))
    xdxr = pd.DataFrame([{"ex_date": ex, "bonus": 10.0, "rights": 0.0,
                          "rights_px": 0.0, "dividend": 0.0}])
    out = apply_qfq(df, xdxr)
    assert out.attrs["adjust"] == "qfq"
    assert np.allclose(out["close"].to_numpy(), 10.0)


def test_qfq_removes_dividend_gap():
    """每 10 股派 2 元，除息日价格降 0.2。"""
    df = _flat_frame([20, 20, 19.8, 19.8])
    ex = int(df.index[2].strftime("%Y%m%d"))
    xdxr = pd.DataFrame([{"ex_date": ex, "bonus": 0.0, "rights": 0.0,
                          "rights_px": 0.0, "dividend": 2.0}])
    out = apply_qfq(df, xdxr)
    assert np.allclose(out["close"].to_numpy(), 19.8)


def test_qfq_without_events_is_identity():
    df = _flat_frame([10, 11, 12])
    empty = pd.DataFrame(columns=["ex_date", "bonus", "rights", "rights_px", "dividend"])
    out = apply_qfq(df, empty)
    assert out.attrs["adjust"] == "none"
    assert np.allclose(out["close"].to_numpy(), df["close"].to_numpy())


def test_qfq_preserves_amount():
    """复权不应改变成交额，成交量反向缩放。"""
    df = _flat_frame([20, 20, 10, 10])
    ex = int(df.index[2].strftime("%Y%m%d"))
    xdxr = pd.DataFrame([{"ex_date": ex, "bonus": 10.0, "rights": 0.0,
                          "rights_px": 0.0, "dividend": 0.0}])
    out = apply_qfq(df, xdxr)
    assert np.allclose(out["amount"].to_numpy(), df["amount"].to_numpy())
    assert out["vol"].iloc[0] > df["vol"].iloc[0]
