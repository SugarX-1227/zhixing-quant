"""指标流水线：按名字组装一组指标/信号函数，并显式报告哪些没跑成。

指标层的调用约定不统一是历史遗留问题：
`add_brick_indicators(df, cfg)` 收字典，而 `add_dual_line(df, fast, slow)`、
`add_macd(df, fast, slow, signal, divergence_window)`、`add_volume_price(df, vol_ma_window)`、
`detect_key_k(df, atr_window, lookback)` 收的是位置标量。
本模块是唯一的适配层，各步骤在这里读配置并按各自的真实签名调用。
新增指标时请在这里加一个 _step_*，不要在业务代码里直接调。

为什么需要这个：

上一版 `daily_workflow._enrich` 用 try/except 逐个补指标列，某个函数签名不符
就静默跳过。实际后果是 `detect_distribution(df, market_cap, cfg)` 是三参数，
被当成两参数调用，每次都抛异常被吞掉——**防守阶梯 P4「主力出货」从未生效过**，
而界面上完全看不出来。

这里改成：
- 每个步骤显式声明怎么调用，签名不一致在这里适配，不靠 try/except 猜。
- 跑完返回 `PipelineResult`，里面有 applied / failed / missing_columns，
  调用方（尤其是 UI）必须能看到哪条规则实际上是哑的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd


@dataclass
class PipelineResult:
    df: pd.DataFrame
    applied: List[str] = field(default_factory=list)
    failed: List[tuple] = field(default_factory=list)      # [(step, error), ...]

    @property
    def ok(self) -> bool:
        return not self.failed

    def missing(self, required: List[str]) -> List[str]:
        return [c for c in required if c not in self.df.columns]

    def warnings(self) -> List[str]:
        return [f"{step} 未执行（{err}），依赖它的规则不会生效。"
                for step, err in self.failed]


# ---------------------------------------------------------------------------
# 步骤定义
# ---------------------------------------------------------------------------

def _step_brick(df, cfg):
    from zhixing_quant.indicators.brick import add_brick_indicators
    return add_brick_indicators(df, cfg)


def _step_b1(df, cfg):
    from zhixing_quant.indicators.b1 import add_b1_indicators
    return add_b1_indicators(df, cfg)


def _step_b2(df, cfg):
    from zhixing_quant.indicators.b2 import add_b2_indicators
    return add_b2_indicators(df, cfg)


def _step_dual_line(df, cfg):
    from zhixing_quant.indicators.dual_line import add_dual_line
    return add_dual_line(df, cfg)


def _step_macd(df, cfg):
    """签名是 (df, fast, slow, signal, divergence_window)。"""
    from zhixing_quant.indicators.macd import add_macd
    c = cfg.get("macd", {}) or {}
    return add_macd(df, int(c.get("fast", 12)), int(c.get("slow", 26)),
                    int(c.get("signal", 9)), int(c.get("divergence_window", 60)))


def _step_volume_price(df, cfg):
    """签名是 (df, vol_ma_window)。"""
    from zhixing_quant.indicators.volume_price import add_volume_price
    c = cfg.get("volume_price", {}) or {}
    return add_volume_price(df, int(c.get("vol_ma_window", 5)))


def _step_single_needle(df, cfg):
    from zhixing_quant.indicators.single_needle import add_single_needle
    return add_single_needle(df, cfg)


def _step_key_kline(df, cfg):
    """签名是 (df, atr_window, lookback)。"""
    from zhixing_quant.indicators.key_kline import detect_key_k
    c = cfg.get("key_kline", {}) or {}
    return detect_key_k(df, int(c.get("atr_window", 14)), int(c.get("lookback", 60)))


def _step_sb1(df, cfg):
    from zhixing_quant.signals.buy_sb1 import detect_sb1
    return detect_sb1(df, cfg)


def _step_b3(df, cfg):
    from zhixing_quant.signals.buy_b3 import detect_b3
    return detect_b3(df, cfg)


def _step_violent_k(df, cfg):
    from zhixing_quant.signals.buy_violent_k import detect_violent_k
    return detect_violent_k(df, cfg)


def _step_sell_s(df, cfg):
    from zhixing_quant.signals.sell_s import detect_s_series
    return detect_s_series(df, cfg)


def _step_distribution(df, cfg):
    """主力出货。注意签名是 (df, market_cap, cfg)，比其他函数多一个参数。

    本地行情库没有流通市值数据（规格 00.5 标为「重要」但非必需），
    所以传 0 表示未知，让 detect_distribution 走不分档的通用判定。
    """
    from zhixing_quant.signals.distribution import detect_distribution
    market_cap = float(cfg.get("_market_cap", 0.0) or 0.0)
    return detect_distribution(df, market_cap, cfg)


STEPS: Dict[str, Callable] = {
    "brick": _step_brick,
    "b1": _step_b1,
    "b2": _step_b2,
    "dual_line": _step_dual_line,
    "macd": _step_macd,
    "volume_price": _step_volume_price,
    "single_needle": _step_single_needle,
    "key_kline": _step_key_kline,
    "sb1": _step_sb1,
    "b3": _step_b3,
    "violent_k": _step_violent_k,
    "sell_s": _step_sell_s,
    "distribution": _step_distribution,
}


# ---------------------------------------------------------------------------
# 命名流水线
# ---------------------------------------------------------------------------

PIPELINES: Dict[str, List[str]] = {
    # 各战法进攻阶段所需
    "brick":         ["brick"],
    "b1":            ["b1"],
    "b2":            ["b2"],
    "single_needle": ["brick", "single_needle"],

    # 防守阶段：DefenseEngine 八级阶梯要读的全部列
    "defense":       ["brick", "dual_line", "macd", "volume_price",
                      "sell_s", "distribution"],

    # 个股页：能画的全画上
    "full":          ["brick", "b1", "b2", "dual_line", "macd", "volume_price",
                      "single_needle", "key_kline", "sb1", "b3", "violent_k",
                      "sell_s", "distribution"],
}

# 防守阶梯每一级依赖的列。缺列 = 该级规则是哑的。
DEFENSE_REQUIRED = {
    "P3 S系列卖点": ["sig_s1", "sig_s2", "sig_s3"],
    "P4 主力出货": ["sig_distribution"],
    "P5 MACD死叉": ["dif", "dea", "macd_hist"],
    "P6 跌破白线": ["white_line"],
    "P7 跌破黄线": ["yellow_line"],
    "P8 放量滞涨": ["is_double_vol"],
}


def run_pipeline(df: pd.DataFrame, cfg: dict, name: str = "defense") -> PipelineResult:
    """按名字跑一组指标。

    Args:
        df: 原始日线，需含 open/high/low/close/vol/amount。
        cfg: 配置字典。
        name: PIPELINES 里的键，或用 steps 参数自定义。

    Returns:
        PipelineResult，含结果 DataFrame 与失败清单。
    """
    steps = PIPELINES.get(name)
    if steps is None:
        raise KeyError(f"未知流水线 {name}，可选：{sorted(PIPELINES)}")
    return run_steps(df, cfg, steps)


def run_steps(df: pd.DataFrame, cfg: dict, steps: List[str]) -> PipelineResult:
    out = df
    result = PipelineResult(df=df)
    for step in steps:
        fn = STEPS.get(step)
        if fn is None:
            result.failed.append((step, "未注册的步骤"))
            continue
        try:
            got = fn(out, cfg)
            if got is None or not isinstance(got, pd.DataFrame):
                result.failed.append((step, "返回值不是 DataFrame"))
                continue
            out = got
            result.applied.append(step)
        except Exception as exc:
            result.failed.append((step, f"{type(exc).__name__}: {exc}"))
    result.df = out
    return result


def defense_coverage(df: pd.DataFrame) -> Dict[str, bool]:
    """检查防守阶梯每一级的依赖列是否齐全。

    Returns:
        {"P4 主力出货": True/False, ...}  False 表示该级规则不会触发。
    """
    return {
        rule: all(c in df.columns for c in cols)
        for rule, cols in DEFENSE_REQUIRED.items()
    }
