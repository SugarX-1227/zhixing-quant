"""活跃市值多空区间规则测试（战法口径，2026-09 使用者确认）。

- 单日跌幅超过 -2.3% → 空头区间（持续到多头触发）
- 单日涨幅超过 +4% → 强势多头
- 连续 3 天全涨且单日涨幅简单相加超过 +4% → 多头（最低标准）
"""

import pandas as pd
import pytest

from zhixing_quant.data.store import BarStore
from zhixing_quant.timing.active_value import (
    current_regime,
    oamv_trigger_states,
    regime_before,
)


def _seed(store, closes, start=20240401):
    rows = [(start + i, 1.0, 1.0, 1.0, c, 1e10, 2e11)
            for i, c in enumerate(closes)]
    store.upsert_oamv(rows)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from zhixing_quant.data import tdx_loader

    s = BarStore(tmp_path / "t.db")
    monkeypatch.setattr(tdx_loader, "get_store", lambda: s)
    yield s
    s.close()


def test_single_day_crash_triggers_bear(store):
    _seed(store, [100.0, 100.0, 100.0, 97.5])       # -2.5%
    states = oamv_trigger_states({})
    assert states["regime"].tolist() == ["NEUTRAL", "NEUTRAL", "NEUTRAL", "BEAR"]
    assert states["trigger"].iloc[-1] == "单日跌幅超2.3%"


def test_bear_persists_until_bull_trigger(store):
    _seed(store, [100.0, 97.4, 97.5, 97.6, 97.7])   # 第一天触发，之后阴跌但无新触发
    states = oamv_trigger_states({})
    assert states["regime"].tolist() == ["NEUTRAL", "BEAR", "BEAR", "BEAR", "BEAR"]
    assert (states["trigger"].iloc[2:] == "").all()  # 持续态不算新触发


def test_single_day_surge_triggers_strong_bull(store):
    _seed(store, [100.0, 100.0, 104.2, 104.5])
    states = oamv_trigger_states({})
    assert states["regime"].tolist() == ["NEUTRAL", "NEUTRAL", "BULL", "BULL"]
    assert states["trigger"].iloc[2] == "单日涨幅超4%"
    assert bool(states["strong"].iloc[2])


def test_three_day_rule_rejects_down_day(store):
    # 用户原例：+2%、-0.2%、+3% 虽然和是 4.8%，但第二天是跌的，不算
    _seed(store, [100.0, 102.0, 101.796, 104.85])
    states = oamv_trigger_states({})
    assert (states["regime"] == "NEUTRAL").all()


def test_three_day_rule_simple_sum(store):
    # +2%、+1%、+1.5% 简单相加 4.5% > 4%，第三天收盘触发多头
    _seed(store, [100.0, 102.0, 103.02, 104.5653])
    states = oamv_trigger_states({})
    assert states["regime"].tolist() == ["NEUTRAL", "NEUTRAL", "NEUTRAL", "BULL"]
    assert states["trigger"].iloc[-1] == "三日连涨和超4%"


def test_near_thresholds_do_not_trigger(store):
    _seed(store, [100.0, 100.0, 97.8, 101.6])       # -2.2%、+3.9% 都没过线
    states = oamv_trigger_states({})
    assert (states["regime"] == "NEUTRAL").all()


def test_bear_trigger_ends_bull(store):
    _seed(store, [100.0, 104.2, 104.5, 101.8])      # 多头后一天 -2.6%
    states = oamv_trigger_states({})
    assert states["regime"].tolist() == ["NEUTRAL", "BULL", "BULL", "BEAR"]


def test_regime_before_shifts_to_next_session(store):
    _seed(store, [100.0, 100.0, 100.0, 97.5])       # 第4天收盘触发空头
    states = oamv_trigger_states({})
    # 触发日当天的持仓仍按之前的中性处理（收盘后才知道），次日开盘进入空头
    mapping = regime_before(
        [pd.Timestamp(f"2024-04-{d:02d}") for d in (3, 4, 5, 6)], states,
    )
    assert mapping == {"2024-04-03": "NEUTRAL", "2024-04-04": "NEUTRAL",
                       "2024-04-05": "BEAR", "2024-04-06": "BEAR"}


def test_current_regime_fields(store):
    _seed(store, [100.0, 100.0, 100.0, 97.5])
    reg = current_regime("20260917", {})
    assert reg["regime"] == "BEAR"
    assert reg["strength"] == 1.0
    assert reg["date"] == 20240404
    assert reg["oamv"] == 97.5
    assert reg["pct"] == pytest.approx(-0.025)
    assert reg["trigger"] == "单日跌幅超2.3%"


def test_thresholds_respect_cfg(store):
    _seed(store, [100.0, 100.0, 100.0, 97.5])       # -2.5%
    loose = {"timing": {"active_value": {"bear_drop": -0.03}}}
    states = oamv_trigger_states(loose)
    assert (states["regime"] == "NEUTRAL").all()


def test_raises_without_enough_data(tmp_path, monkeypatch):
    from zhixing_quant.data import tdx_loader

    store = BarStore(tmp_path / "empty.db")
    monkeypatch.setattr(tdx_loader, "get_store", lambda: store)
    with pytest.raises(RuntimeError, match="活跃市值历史不足"):
        current_regime("20260917", {})
    store.close()
