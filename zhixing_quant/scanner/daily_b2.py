"""B2 每日选股：超卖后放量反抽。

选股条件来源：通达信行情指标与选股指标(1).md
"""

from __future__ import annotations

from zhixing_quant.indicators.b2 import add_b2_indicators
from zhixing_quant.scanner._core import StrategySpec, build_cli, scan

SPEC = StrategySpec(
    key="b2",
    label="B2",
    signal_col="sig_b2",
    add_indicators=add_b2_indicators,
    min_bars=lambda cfg: max(cfg["dual_line"]["yellow_ma_windows"]),
    extra_fields=lambda r: {
        "kdj_j": float(r["kdj_j"]),
        "kdj_k": float(r["kdj_k"]),
        "kdj_d": float(r["kdj_d"]),
        "pct_chg": float(r["pct_chg"]),
    },
    reason=lambda r, cfg: (
        f"近{cfg['b2']['exist_window']}日J<{cfg['b2']['j_oversold']}"
        f" + 涨幅>{cfg['b2']['chg_min']}%"
        f" + 放量"
        f" + J值({float(r['kdj_j']):.1f})<{cfg['b2']['j_threshold']}"
    ),
)


def scan_daily(cfg: dict, end_date: str = None, limit_universe: int = None, progress=None):
    return scan(SPEC, cfg, end_date=end_date, limit_universe=limit_universe, progress=progress)


main = build_cli(SPEC)

if __name__ == "__main__":
    main()
