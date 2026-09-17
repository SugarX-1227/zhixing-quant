"""砖型图每日选股。

数据来自本地通达信文件（zhixing_quant.data），不再走 MCP。
结果不落 HTML，直接看终端输出或 `streamlit run app.py`。

选股条件来源：通达信行情指标与选股指标(1).md
    黄线 = (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4
    XG   = 昨天绿柱 AND 今天红柱 AND 红柱高度 >= 昨天绿柱高度*2/3 AND CLOSE > 黄线
"""

from __future__ import annotations

from zhixing_quant.indicators.brick import add_brick_indicators
from zhixing_quant.scanner._core import StrategySpec, build_cli, scan

SPEC = StrategySpec(
    key="brick",
    label="砖型图",
    signal_col="sig_brick",
    add_indicators=add_brick_indicators,
    min_bars=lambda cfg: max(cfg["dual_line"]["yellow_ma_windows"]),
    extra_fields=lambda r: {
        "yellow_line": float(r["yellow_line"]),
        "brick_value": float(r["brick_value"]),
        "brick_red_height": float(r["brick_red_height"]),
        "brick_green_height": float(r["brick_green_height"]),
        "brick_prev_height": float(r["brick_prev_height"]),
        "red_streak": int(r["red_streak"]),
        "stop_loss": float(r["stop_loss"]),
        "abandon_gap_up_price": float(r["abandon_gap_up_price"]),
    },
    reason=lambda r, cfg: (
        f"绿转强红：红柱高度{float(r['brick_red_height']):.1f}"
        f" ≥ 绿柱高度{float(r['brick_green_height']):.1f}×2/3"
        f" + 收盘站上知行多空线"
    ),
)


def scan_daily(cfg: dict, end_date: str = None, limit_universe: int = None, progress=None):
    """保持与旧版一致的签名，供 app.py 和既有调用方使用。"""
    return scan(SPEC, cfg, end_date=end_date, limit_universe=limit_universe, progress=progress)


main = build_cli(SPEC)

if __name__ == "__main__":
    main()
