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
