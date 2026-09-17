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
        Param("brick.min_height_ratio", "红柱/绿柱高度比", "float", 0.6667, 0.1, 2.0, 0.05,
              "红柱高度 >= 绿柱高度 × 此值。通达信原公式为 2/3，改它等于换掉"
              "选股条件本身", tag="LOCKED"),
        Param("brick.min_brick_height", "砖高绝对下限(附加)", "float", 0.0, 0.0, 20.0, 0.5,
              "通达信公式之外的可选门槛，0 = 关闭。砖高实测常年 20 以上，"
              "设成 4 几乎不起过滤作用"),
        Param("brick.min_brick_growth", "砖高增长倍数(附加)", "float", 0.0, 0.0, 3.0, 0.1,
              "通达信公式之外的可选门槛，0 = 关闭。设成 1.5 是要求慢速振荡"
              "指标单日跳涨 50%，实测只有 0.3% 的红柱日能满足"),
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
    # 「最长持有」已移到「出场规则」页签（exits.<战法>.time_stop.max_holding_days）。
    # 留在这里会变成又一个改了不生效的死参数：引擎一旦收到 exit_policy，
    # 内置的 max_holding_days 分支就不再走了。
]

COST_PARAMS: List[Param] = [
    Param("backtest.commission", "佣金率", "float", 0.00025, 0.0, 0.003, 0.00005),
    Param("backtest.stamp_tax", "印花税(仅卖出)", "float", 0.001, 0.0, 0.002, 0.0001,
          tag="LOCKED", help="法定税率，不该当成可调参数"),
    Param("backtest.slippage", "滑点", "float", 0.0015, 0.0, 0.01, 0.0005,
          "调低会让回测结果虚高。真实成交价通常比理想价差 0.1%~0.3%"),
]

# ---------------------------------------------------------------------------
# 出场规则（止损 / 移动止损 / 止盈 / 时间止损）
#
# 键是按战法动态拼的：exits.<战法>.stop.kind 之类。配置层做深合并，
# 界面上只写 exits.<战法> 这一层，没写到的键自动从 exits.default 继承。
# ---------------------------------------------------------------------------

STOP_KINDS = ("entry_low", "pct", "atr", "none")
TRAIL_KINDS = ("none", "pct", "chandelier", "yellow_line")
TP_KINDS = ("pct", "none", "tiered")

_KIND_LABEL = {
    "entry_low": "入场K线最低价", "pct": "固定百分比", "atr": "ATR 倍数",
    "none": "不设", "chandelier": "吊灯(最高价-ATR)", "yellow_line": "知行多空线",
    "tiered": "分级减仓(红砖)",
}


def kind_label(value: str) -> str:
    return _KIND_LABEL.get(str(value), str(value))


def exit_params_for(strategy: str, cfg: dict) -> List[Param]:
    """某个战法的出场规则参数。默认值取该战法合并后的实际生效值。"""
    from zhixing_quant.backtest.exits import spec_from_config

    spec = spec_from_config(cfg, strategy)
    k = f"exits.{strategy}"
    return [
        Param(f"{k}.stop.kind", "止损方式", "choice", spec.stop.kind,
              choices=STOP_KINDS,
              help="entry_low 贴着信号日最低价，离成本很近，容易被正常波动扫掉"),
        Param(f"{k}.stop.pct", "止损百分比", "float", spec.stop.pct, 0.01, 0.30, 0.01,
              "止损方式选「固定百分比」时生效"),
        Param(f"{k}.stop.atr_mult", "止损 ATR 倍数", "float", spec.stop.atr_mult,
              0.5, 6.0, 0.5, "止损方式选「ATR 倍数」时生效"),
        Param(f"{k}.trailing.kind", "移动止损", "choice", spec.trail.kind,
              choices=TRAIL_KINDS,
              help="只上移不下移。没有移动止损，浮盈会一路还回去"),
        Param(f"{k}.trailing.pct", "移动止损回撤", "float", spec.trail.pct,
              0.02, 0.40, 0.01, "从持仓期间最高价回撤多少就走"),
        Param(f"{k}.trailing.activate_profit", "移动止损启用浮盈", "float",
              spec.trail.activate_profit, 0.0, 0.50, 0.01,
              "浮盈达到此比例后才启用，0 = 建仓即启用"),
        Param(f"{k}.take_profit.kind", "止盈方式", "choice", spec.take_profit.kind,
              choices=TP_KINDS,
              help="tiered 走 config 里的 tiers（四块砖止盈定律）"),
        Param(f"{k}.take_profit.pct", "止盈百分比", "float", spec.take_profit.pct,
              0.03, 1.0, 0.01, "止盈方式选「固定百分比」时生效"),
        Param(f"{k}.time_stop.max_holding_days", "最长持有(交易日)", "int",
              spec.time_stop.max_holding_days, 0, 250, 1, "0 = 不限"),
        Param(f"{k}.time_stop.no_progress_days", "不涨就走(N日)", "int",
              spec.time_stop.no_progress_days, 0, 60, 1,
              "持有 N 日涨幅不足下面那个门槛就离场，0 = 关闭"),
        Param(f"{k}.time_stop.min_progress", "N日最低涨幅", "float",
              spec.time_stop.min_progress, -0.2, 0.5, 0.01),
        Param(f"{k}.break_yellow_line", "跌破黄线清仓", "bool",
              spec.break_yellow_line,
              help="规格 01.3 [LOCKED]：跌破知行多空线清仓并移出股票池"),
        Param(f"{k}.defense_ladder", "启用防守八级阶梯", "bool", spec.defense_ladder,
              help="需要 S系列/出货/MACD 等指标列，回测流水线里不一定都有"),
        Param(f"{k}.strategy_exit", "调用战法自身出场", "bool", spec.strategy_exit,
              help="走 Strategy.exit_conditions"),
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


def diff_from_default(overrides: Dict[str, Any],
                      extra: Sequence[Param] = ()) -> Dict[str, Any]:
    """只保留与声明默认值不同的项，用于在界面上显示"你改了什么"。

    Args:
        overrides: 界面收集到的全部覆盖值。
        extra: 动态生成的参数声明（出场规则那组的键带战法名，不在静态表里）。
    """
    defaults = {}
    for group in list(STRATEGY_PARAMS.values()) + [EXECUTION_PARAMS, COST_PARAMS,
                                                   RISK_PARAMS, list(extra)]:
        for p in group:
            defaults[p.key] = p.default
    return {k: v for k, v in overrides.items()
            if k in defaults and v != defaults[k]}
