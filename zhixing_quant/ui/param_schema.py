"""各战法可调参数的声明。

回测页需要让人调参再看效果，但每套战法的参数不同，写死在界面里会很快失控。
这里用声明式描述：参数名、配置路径、取值范围、含义、以及规格里的标记。

规格 10-params-and-backtest.md 里绝大多数参数标了 [CALIBRATE]，
意思是"本文给的只是初始猜测值，必须通过回测确定"。界面上把这个标记显示出来，
提醒使用者哪些数字是可以动的、哪些是规则本身（[LOCKED]）不该动。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence


@dataclass(frozen=True)
class Param:
    key: str                 # 配置路径，点号分隔，如 "b1.j_threshold"
    label: str
    kind: str                # int / float / pct / bool / choice
    default: Any
    lo: Any = None
    hi: Any = None
    step: Any = None
    help: str = ""
    tag: str = "CALIBRATE"   # CALIBRATE 可调 / LOCKED 规则本身
    choices: Sequence = ()


# ---------------------------------------------------------------------------
# 各战法
# ---------------------------------------------------------------------------

STRATEGY_PARAMS: Dict[str, List[Param]] = {
    "brick": [
        Param("brick.n1", "短周期 N1", "int", 4, 2, 12, 1,
              "砖型图快线周期"),
        Param("brick.n2", "长周期 N2", "int", 6, 3, 20, 1,
              "砖型图慢线周期"),
        Param("brick.min_brick_height", "砖高绝对下限", "float", 4.0, 0.0, 20.0, 0.5,
              "附录 B.5 的「砖型图 > 4」。调低会放行贴地的微红盘"),
        Param("brick.min_brick_growth", "砖高增长倍数", "float", 1.5, 1.0, 3.0, 0.1,
              "今日砖高 ÷ 昨日砖高 的下限。附录 B.5 给 1.5"),
    ],
    "b1": [
        Param("b1.j_threshold", "J值上限", "float", 13.0, 0.0, 40.0, 1.0,
              "KDJ 的 J 值低于此值才算超卖。规格给 13"),
        Param("b1.chg_min", "涨跌幅下限 %", "float", -4.0, -10.0, 0.0, 0.5,
              "当日涨跌幅区间下沿"),
        Param("b1.chg_max", "涨跌幅上限 %", "float", 4.0, 0.0, 10.0, 0.5,
              "当日涨跌幅区间上沿。区间放宽会大幅增加命中数"),
        Param("b1.rsi_n", "RSI 周期", "int", 9, 3, 24, 1),
    ],
    "b2": [
        Param("b2.j_oversold", "前置超卖 J值", "float", 13.0, 0.0, 40.0, 1.0,
              "近 N 日内 J 值需低于此值"),
        Param("b2.exist_window", "回看窗口", "int", 3, 1, 10, 1,
              "往前看几根K线找超卖"),
        Param("b2.chg_min", "确认阳线涨幅 %", "float", 3.95, 0.0, 10.0, 0.05,
              "确认阳线的最小涨幅"),
        Param("b2.j_threshold", "当日 J值上限", "float", 55.0, 20.0, 100.0, 5.0),
    ],
    "dual_line": [
        # 白线 EMA(EMA(C,10),10) 的周期。黄线的 [14,28,57,114] 是附录 B.1 的
        # 原始定义，不作为可调项暴露——改它等于换掉整套体系的骨架，
        # 而界面上一个滑块看不出这个分量。
        Param("dual_line.white_span", "白线 EMA 周期", "int", 10, 3, 30, 1,
              "白线 = EMA(EMA(C,N),N)，二次平滑。规格给 10", tag="LOCKED"),
        Param("volume_price.vol_ma_window", "量均线周期", "int", 5, 3, 20, 1),
    ],
    "yoga_pants": [
        Param("b1.j_threshold", "B1 J值上限", "float", 13.0, 0.0, 40.0, 1.0),
        Param("strategies.yoga_pants.allow_b3_addon", "允许 B3 加仓", "bool", True,
              help="B3 是中继买点，用于加仓而非开仓", tag="LOCKED"),
    ],
    "single_needle": [
        Param("single_needle.n1", "短周期", "int", 10, 3, 30, 1),
        Param("single_needle.n2", "长周期", "int", 20, 5, 60, 1),
    ],
}


# ---------------------------------------------------------------------------
# 交易与风控（所有战法共用）
# ---------------------------------------------------------------------------

EXECUTION_PARAMS: List[Param] = [
    Param("backtest.initial_capital", "初始资金", "int", 500000, 10000, 10_000_000, 10000),
    Param("backtest.max_positions", "最大持仓数", "int", 5, 1, 20, 1,
          "同时最多持有几只"),
    Param("backtest.max_entries_per_day", "每日最多开仓", "int", 2, 1, 10, 1),
    Param("backtest.max_holding_days", "最长持有(交易日)", "int", 20, 3, 120, 1,
          "超过就无条件卖出"),
]

COST_PARAMS: List[Param] = [
    Param("backtest.commission", "佣金率", "float", 0.00025, 0.0, 0.003, 0.00005),
    Param("backtest.stamp_tax", "印花税(仅卖出)", "float", 0.001, 0.0, 0.002, 0.0001,
          tag="LOCKED", help="法定税率，不该当成可调参数"),
    Param("backtest.slippage", "滑点", "float", 0.0015, 0.0, 0.01, 0.0005,
          "调低会让回测结果虚高。真实成交价通常比理想价差 0.1%~0.3%"),
]

RISK_PARAMS: List[Param] = [
    Param("portfolio.risk_per_trade", "单笔风险", "float", 0.02, 0.002, 0.10, 0.002,
          "单笔最大亏损占权益比例。规格 08 仓位铁律"),
    Param("portfolio.kelly_fraction", "凯利系数", "float", 0.25, 0.05, 1.0, 0.05,
          "在单笔风险基础上再乘一道安全系数"),
]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def params_for(strategy: str) -> List[Param]:
    return STRATEGY_PARAMS.get(strategy, [])


def get_value(cfg: dict, key: str, default=None):
    node = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def apply_overrides(cfg: dict, overrides: Dict[str, Any]) -> dict:
    """深拷贝 cfg 后写入覆盖值，不污染全局配置。"""
    out = copy.deepcopy(cfg)
    for key, value in overrides.items():
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                break
        else:
            node[parts[-1]] = value
    return out


def diff_from_default(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """只保留与声明默认值不同的项，用于在界面上显示"你改了什么"。"""
    defaults = {}
    for group in list(STRATEGY_PARAMS.values()) + [EXECUTION_PARAMS, COST_PARAMS,
                                                   RISK_PARAMS]:
        for p in group:
            defaults[p.key] = p.default
    return {k: v for k, v in overrides.items()
            if k in defaults and v != defaults[k]}
