"""B1 每日选股：KDJ 超卖 + 知行多空线确认。

选股条件来源：通达信行情指标与选股指标(1).md
    XG: J<13 AND C>ZXDKX AND ZXDQ>ZXDKX AND -4% < 涨跌幅 < +4%
"""

from __future__ import annotations

from zhixing_quant.indicators.b1 import add_b1_indicators
from zhixing_quant.scanner._core import StrategySpec, build_cli, scan

SPEC = StrategySpec(
    key="b1",
    label="B1",
    signal_col="sig_b1",
    add_indicators=add_b1_indicators,
    min_bars=lambda cfg: max(cfg["dual_line"]["yellow_ma_windows"]),
    extra_fields=lambda r: {
        "yellow_line": float(r["yellow_line"]),
        "kdj_j": float(r["kdj_j"]),
        "kdj_k": float(r["kdj_k"]),
        "kdj_d": float(r["kdj_d"]),
        "pct_chg": float(r["pct_chg"]),
    },
    reason=lambda r, cfg: (
        f"J值({float(r['kdj_j']):.1f})<{cfg['b1']['j_threshold']}"
        f" + 站上知行多空线"
        f" + 短期趋势线确认"
        f" + 涨跌幅{float(r['pct_chg']):.2f}%在±4%内"
    ),
)


def scan_daily(cfg: dict, end_date: str = None, limit_universe: int = None, progress=None):
    return scan(SPEC, cfg, end_date=end_date, limit_universe=limit_universe, progress=progress)


main = build_cli(SPEC)

if __name__ == "__main__":
    main()
