"""指南针活跃市值(0AMV)模块测试：合成 day.vdat 二进制 + 择时评分。"""

from __future__ import annotations

import struct

import pytest

from zhixing_quant.data.store import BarStore


# ---------------------------------------------------------------------------
# 合成 day.vdat：与真实文件同构（块tag+24零 + 250条上限 + 块间空隙 + 暂存槽）
# ---------------------------------------------------------------------------

BLOCK_TAG = b"Z_SK0AMV"


def _record(date, o, h, l, c, vol=1e10, amt=2e11) -> bytes:
    return struct.pack("<I6f", date, o, h, l, c, vol, amt)


def _block(records: list[bytes]) -> bytes:
    # 真实文件每块固定 250 条容量，不满的部分补零；块间还有 16 字节空隙
    payload = b"".join(records)
    payload += b"\x00" * (250 * 28 - len(payload))
    return BLOCK_TAG + b"\x00" * 24 + payload + b"\x00" * 16


def _write_vdat(path, body: bytes) -> None:
    path.write_bytes(b"\x00" * 1024 + body)   # 文件头部有一段非数据区


def _cfg(tmp_path):
    return {"data": {"compass_dir": str(tmp_path / "ChinaStk"),
                     "db_path": str(tmp_path / "market.db")}}


def _make_vdat_dir(tmp_path, body: bytes):
    d = tmp_path / "ChinaStk" / "Z_SK"
    d.mkdir(parents=True, exist_ok=True)
    _write_vdat(d / "day.vdat", body)
    return d


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_read_compass_oamv_parses_blocks_and_skips_staging(tmp_path):
    from zhixing_quant.data.oamv import read_compass_oamv

    recs = [_record(20260910 + i, 100 + i, 102 + i, 99 + i, 101 + i)
            for i in range(5)]
    # 暂存槽：一条全零、一条只有 close 有值（forming bar 中间态），都不能
    # 覆盖正式块里的完整记录（真实文件里 20260916 就发生过）
    staging = (BLOCK_TAG + b"\x00" * 24
               + _record(20260914, 0, 0, 0, 0)
               + _record(20260914, 0, 0, 0, 57594.0))
    body = staging + _block(recs) + _block(recs[:2])
    _make_vdat_dir(tmp_path, body)

    rows = read_compass_oamv(tmp_path / "ChinaStk" / "Z_SK" / "day.vdat")
    # 同一交易日出现两次时保留正式块里的记录；暂存槽残缺行被跳过
    assert [r[0] for r in rows] == [20260910, 20260911, 20260912, 20260913, 20260914]
    # vol/amount 以 float32 存储，用近似比较（真实数据同理，有 f32 精度损失）
    assert rows[-1][:5] == (20260914, 104.0, 106.0, 103.0, 105.0)
    assert rows[-1][5] == pytest.approx(1e10)
    assert rows[-1][6] == pytest.approx(2e11, rel=1e-6)
    assert rows[0][4] == 101.0          # 第一块的值，不被第二块覆盖


def test_sync_oamv_is_incremental(tmp_path):
    from zhixing_quant.data import oamv

    recs = [_record(20260910 + i, 10, 11, 9, 10.5) for i in range(6)]
    _make_vdat_dir(tmp_path, _block(recs))
    cfg = _cfg(tmp_path)

    assert oamv.sync_oamv(cfg, verbose=False) == 6

    # 再同步：无新数据不重复插入
    store = BarStore(tmp_path / "market.db")
    assert store.load_oamv().shape[0] == 6
    store.close()

    # 文件里追加一条新记录后再同步，只进新的
    recs.append(_record(20260916, 10, 11, 9, 10.8))
    _make_vdat_dir(tmp_path, _block(recs))
    assert oamv.sync_oamv(cfg, verbose=False) == 7
    store = BarStore(tmp_path / "market.db")
    df = store.load_oamv(end_date=20260916)
    store.close()
    assert float(df["close"].iloc[-1]) == 10.8


def test_sync_oamv_reports_locked_file(tmp_path, monkeypatch):
    from zhixing_quant.data import oamv

    d = tmp_path / "ChinaStk" / "Z_SK"
    d.mkdir(parents=True)
    (d / "day.vdat").write_bytes(BLOCK_TAG)
    cfg = _cfg(tmp_path)

    def raise_locked(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("pathlib.Path.read_bytes", raise_locked)
    with pytest.raises(RuntimeError, match="退出指南针"):
        oamv.sync_oamv(cfg, verbose=False)


# ---------------------------------------------------------------------------
# 择时评分见 tests/test_active_value.py（用真实区间规则）
# ---------------------------------------------------------------------------


def test_active_value_proxy_fails_loud_without_data(tmp_path, monkeypatch):
    from zhixing_quant.data import tdx_loader
    from zhixing_quant.timing.active_value import oamv_trigger_states

    store = BarStore(tmp_path / "empty.db")
    monkeypatch.setattr(tdx_loader, "get_store", lambda: store)

    with pytest.raises(RuntimeError, match="活跃市值历史不足"):
        oamv_trigger_states({})
    store.close()
