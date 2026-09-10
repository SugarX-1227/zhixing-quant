"""知行双线（白线 / 黄线）及其派生趋势判据。

⚠️ 本模块曾经是全项目最严重的一个 bug 源，改动前请先读完这段。

原实现：

    white_line = ema(close, fast)      # fast=10  →  单条 EMA10
    yellow_line = ma(close, slow)      # slow=20  →  MA20

而战法文档附录 B.1 的通达信原始定义是：

    白线: EMA(EMA(C,10),10)
    黄线: (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4

两处都错。后果不是"精度差一点"：

1. 黄线含 MA114 才是中长期锚，"跌破黄线 = 清仓 + 移出股票池、永不观察"
   这种重处置才站得住。换成 MA20 后，实测 250 根 K 线内破线天数
   从 73 天涨到 103 天，多出四成的永久拉黑。
2. b1/b2/brick 三个模块用的是正确公式，但它们和本模块都往 yellow_line
   这一列写值，而流水线里 dual_line 排在它们后面 —— 后写覆盖先写。
   于是进攻端按四线均值判"站上黄线可以买"，防守端按 MA20 判
   "跌破黄线该清仓"，同一只票可以同时满足两者。
3. pipeline.DEFENSE_REQUIRED 检查的是"列在不在"。列一直都在，
   只是值是错的，所以覆盖检查全绿，界面上完全看不出来。

现在双线统一由 tdx.attach_zhixing_lines 计算，四个模块调同一个函数，
算出来的值逐位相同，谁覆盖谁都无所谓。本模块只负责派生列。
"""

from __future__ import annotations

import pandas as pd

from zhixing_quant.indicators.tdx import attach_zhixing_lines


def add_dual_line(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """追加白线 / 黄线及趋势判据列。

    Args:
        df: 日线 OHLCV。
        cfg: 配置字典，读 cfg["dual_line"]。缺省用文档定义的参数。

    Returns:
        df 的副本，含 white_line / yellow_line / above_* / regime_* /
        golden_cross / death_cross。

    Rule source:
        Z哥战法-完整战法详解.md 附录 B.1、3.1 节
        - 白线在黄线之上 = 强势多头（regime_strong）
        - 白线在黄线之下 = 纯空头（regime_weak），任何反弹都无参与价值
    """
    return attach_zhixing_lines(df.copy(), cfg)
