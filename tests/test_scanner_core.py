import pandas as pd

from zhixing_quant.scanner import _core


def test_scan_limit_zero_scans_entire_universe(monkeypatch):
    codes = [f"600{i:03d}" for i in range(12)]
    spot = pd.DataFrame({
        "code": codes,
        "name": [f"股票{i}" for i in range(12)],
        "amount": [float(100 - i) for i in range(12)],
        "date": [pd.Timestamp("2026-09-04")] * 12,
    })
    seen = []

    monkeypatch.setattr(_core, "fetch_a_spot", lambda trade_date=None: spot)
    monkeypatch.setattr(_core, "filter_universe", lambda value, cfg: value)

    def load_many(requested, **kwargs):
        seen.extend(requested)
        idx = pd.date_range("2026-09-03", periods=2, freq="B", name="date")
        return {
            code: pd.DataFrame({
                "open": [10.0, 11.0], "high": [10.5, 11.5],
                "low": [9.5, 10.5], "close": [10.0, 11.0],
                "amount": [1e8, 1e8], "vol": [1e6, 1e6],
            }, index=idx)
            for code in requested
        }

    monkeypatch.setattr(_core, "load_daily_many", load_many)
    spec = _core.StrategySpec(
        key="test", label="测试", signal_col="signal",
        add_indicators=lambda df, cfg: df.assign(signal=True),
        min_bars=lambda cfg: 1,
        extra_fields=lambda row: {},
        reason=lambda row, cfg: "test",
    )

    candidates, _ = _core.scan(spec, {"universe": {"max_candidates": 99}}, limit_universe=0)

    assert seen == codes
    assert len(candidates) == len(codes)
    assert candidates.attrs["total_matches"] == len(codes)


def _breadth_scan(monkeypatch, min_breadth, limit_universe=0):
    codes = [f"600{i:03d}" for i in range(12)]
    spot = pd.DataFrame({"code": codes, "name": codes, "amount": [1e8] * 12,
                         "date": [pd.Timestamp("2026-09-04")] * 12})
    monkeypatch.setattr(_core, "fetch_a_spot", lambda trade_date=None: spot)
    monkeypatch.setattr(_core, "filter_universe", lambda value, cfg: value)
    idx = pd.date_range("2026-09-03", periods=2, freq="B", name="date")
    frame = pd.DataFrame({"open": [10.0, 11.0], "high": [10.5, 11.5], "low": [9.5, 10.5],
                          "close": [10.0, 11.0], "amount": [1e8, 1e8], "vol": [1e6, 1e6]},
                         index=idx)
    monkeypatch.setattr(_core, "load_daily_many",
                        lambda requested, **kw: {c: frame for c in requested})
    spec = _core.StrategySpec(key="test", label="测试", signal_col="signal",
                              add_indicators=lambda df, cfg: df.assign(signal=True),
                              min_bars=lambda cfg: 1, extra_fields=lambda row: {},
                              reason=lambda row, cfg: "test")
    cfg = {"universe": {"max_candidates": 99}, "test": {"min_breadth": min_breadth}}
    return _core.scan(spec, cfg, limit_universe=limit_universe)[0]


def test_scan_blocks_day_below_min_breadth(monkeypatch):
    out = _breadth_scan(monkeypatch, min_breadth=20)          # 当天只有 12 只命中
    assert out.empty
    assert out.attrs["total_matches"] == 12
    assert out.attrs["breadth_blocked"] is True
    assert any("少于门槛 20" in w for w in out.attrs["warnings"])


def test_scan_keeps_day_at_or_above_min_breadth(monkeypatch):
    assert len(_breadth_scan(monkeypatch, min_breadth=12)) == 12


def test_scan_skips_breadth_filter_when_universe_is_limited(monkeypatch):
    out = _breadth_scan(monkeypatch, min_breadth=20, limit_universe=5)
    assert len(out) == 5
    assert any("未应用" in w for w in out.attrs["warnings"])
