"""配置校验：让写错的配置**立刻报错**，而不是静默走默认值。

为什么必须有这个
----------------

本仓库栽在「配置改了不生效」上的次数，比栽在任何算法错误上都多：

- `timing` / `portfolio` 在 YAML 里各写两遍，后者覆盖前者，改上面那份
  永远不生效，而且没有任何提示；
- `sell` 整段从没被 `signals/sell_s.py` 读过，阈值硬编码在函数体里；
- `executor` / `strategies` 两段至今没有任何代码引用。

这类问题的共同点是：**看起来配了，实际没生效，界面上完全看不出来。**

新加的 `exits` / `entries` / `factors` 三段把这个风险放大了，因为它们大量
使用字符串枚举：

    exits.b2.stop.kind: ATR       # 大小写错 → 静默落回 entry_low
    exits.b2.stop.knid: atr       # 键名打错 → 静默落回 entry_low
    factors.by_strategy.b2: brekout   # 预设名打错 → 静默退回成交额排序

三种写法都不会报错，回测照跑，你以为在测 ATR 止损，其实测的是入场低点止损。
调参调半天，调的是个假的东西。

校验做什么
----------

1. **重复键**：YAML 允许重复键并静默取最后一个。这里换成严格 loader，
   出现重复直接报错。
2. **枚举值**：`stop.kind` / `trailing.kind` / `take_profit.kind` /
   `factors.ranking` 等只能取已知值，写错立刻报错并列出可选值。
3. **未知键**：`exits` / `entries` / `factors` 段内出现没见过的键就告警。
   不报错——你可能在试验新东西——但必须说出来。
4. **引用完整性**：`factors.weights` 里的因子名必须已注册；
   `exits.<战法>` / `entries.<战法>` 的战法名必须存在。

刻意**不做**的事：不给每个数值参数写取值范围。那种 schema 维护成本高、
收益低（数值写错通常一眼能看出来，而且回测结果会明显异常），
真正的杀手是上面这些「静默失效」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import yaml


# ---------------------------------------------------------------------------
# 枚举取值
# ---------------------------------------------------------------------------

STOP_KINDS = {"entry_low", "pct", "atr", "none"}
TRAIL_KINDS = {"none", "pct", "chandelier", "yellow_line", "white_line"}
TP_KINDS = {"pct", "none", "tiered", "price_tiers"}
RANKING_CHOICES = {"amount", "custom", "trend", "pullback", "breakout",
                   "liquidity_only"}

# 各段允许出现的键。用来抓打错的键名，不是用来限制取值。
EXITS_KEYS = {
    "stop", "trailing", "take_profit", "time_stop", "break_yellow_line",
    "white_line_break", "defense_ladder", "defense_rules", "defense_s1_portion",
    "defense_distribution_portion", "strategy_exit", "profit_to_loss",
    "close_below_prev_low",
}
EXITS_SUBKEYS = {
    "stop": {"kind", "pct", "atr_mult", "atr_window", "buffer", "cap_pct"},
    "trailing": {"kind", "pct", "atr_mult", "atr_window", "activate_profit"},
    "take_profit": {"kind", "pct", "tiers", "price_tiers"},
    "time_stop": {"max_holding_days", "no_progress_days", "min_progress"},
}
ENTRIES_KEYS = {"base_pct", "addon_pct", "max_addons", "addon_min_gain"}
FACTORS_KEYS = {"ranking", "by_strategy", "weights", "evaluate"}


@dataclass
class ValidationResult:
    """校验结果。errors 非空表示配置不可用。"""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"[错误] {e}")
        for w in self.warnings:
            lines.append(f"[告警] {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 严格 YAML loader：重复键直接报错
# ---------------------------------------------------------------------------

class DuplicateKeyError(ValueError):
    """YAML 里出现重复键。"""


class StrictLoader(yaml.SafeLoader):
    """拒绝重复键的 SafeLoader。

    PyYAML 默认允许重复键并静默取最后一个——`timing` 写两遍时，
    前一份就成了永远不生效的死配置，而且没有任何提示。
    """


def _no_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(
                f"配置里 `{key}` 出现了多次（第 {key_node.start_mark.line + 1} 行）。"
                "YAML 会静默取最后一份，前面那份改了永远不生效——"
                "本仓库在这上面栽过，所以这里直接报错。")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def validate(cfg: dict, strict_factors: bool = True) -> ValidationResult:
    """校验一份已经加载好的配置。

    Args:
        cfg: 配置字典。
        strict_factors: 是否检查因子名是否已注册。导入因子层有点开销，
            纯配置测试里可以关掉。

    Returns:
        ValidationResult。
    """
    r = ValidationResult()
    if not isinstance(cfg, dict):
        r.errors.append("配置根节点不是字典。")
        return r

    _check_exits(cfg.get("exits"), r)
    _check_entries(cfg.get("entries"), r)
    _check_factors(cfg.get("factors"), r, strict_factors)
    _check_strategy_names(cfg, r)
    return r


def _enum(value, allowed: Set[str], where: str, r: ValidationResult) -> None:
    if value is None:
        return
    if value not in allowed:
        r.errors.append(
            f"{where} = {value!r} 不是合法取值。可选：{sorted(allowed)}。"
            "写错不会报错只会静默落回默认值，你以为在测 A 其实在测 B。")


def _unknown(node: dict, allowed: Set[str], where: str,
             r: ValidationResult) -> None:
    extra = set(node) - allowed
    if extra:
        r.warnings.append(
            f"{where} 里有没见过的键：{sorted(extra)}。"
            "多半是打错了——打错的键不会生效，也不会报错。")


def _check_exits(exits, r: ValidationResult) -> None:
    if exits is None:
        return
    if not isinstance(exits, dict):
        r.errors.append("exits 段必须是字典。")
        return
    for name, block in exits.items():
        if not isinstance(block, dict):
            r.errors.append(f"exits.{name} 必须是字典。")
            continue
        where = f"exits.{name}"
        _unknown(block, EXITS_KEYS, where, r)
        for sub, allowed in EXITS_SUBKEYS.items():
            node = block.get(sub)
            if isinstance(node, dict):
                _unknown(node, allowed, f"{where}.{sub}", r)
        _enum((block.get("stop") or {}).get("kind"), STOP_KINDS,
              f"{where}.stop.kind", r)
        _enum((block.get("trailing") or {}).get("kind"), TRAIL_KINDS,
              f"{where}.trailing.kind", r)
        tp = block.get("take_profit") or {}
        _enum(tp.get("kind"), TP_KINDS, f"{where}.take_profit.kind", r)

        # 选了分档止盈却没给档位 = 这条规则不会触发，是个哑开关
        if tp.get("kind") == "tiered" and not tp.get("tiers"):
            r.errors.append(f"{where}.take_profit.kind=tiered 但没有 tiers，"
                            "这条止盈永远不会触发。")
        if tp.get("kind") == "price_tiers" and not tp.get("price_tiers"):
            r.errors.append(f"{where}.take_profit.kind=price_tiers 但没有 "
                            "price_tiers，这条止盈永远不会触发。")
        for tier in (tp.get("price_tiers") or []):
            if not isinstance(tier, dict) or "gain" not in tier or "sell" not in tier:
                r.errors.append(
                    f"{where}.take_profit.price_tiers 的每一档都要写成 "
                    f"{{gain: 0.08, sell: 0.33}}，实际是 {tier!r}。")

        # 开了防守阶梯却把级别过滤成空 = 阶梯不会触发
        if block.get("defense_ladder") and block.get("defense_rules") == []:
            r.warnings.append(f"{where} 开了 defense_ladder 但 defense_rules 是"
                              "空列表，阶梯一级都不会触发。")
        _check_portion(block.get("white_line_break"),
                       f"{where}.white_line_break", r)
        _check_portion(block.get("defense_s1_portion"),
                       f"{where}.defense_s1_portion", r)
        _check_portion(block.get("defense_distribution_portion"),
                       f"{where}.defense_distribution_portion", r)


def _check_portion(value, where: str, r: ValidationResult) -> None:
    if value is None:
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        r.errors.append(f"{where} 必须是数字，实际是 {value!r}。")
        return
    if not 0.0 <= v <= 1.0:
        r.errors.append(f"{where} = {v} 不在 0~1 之间。这是**减仓比例**，"
                        "不是百分数——减一半写 0.5 不是 50。")


def _check_entries(entries, r: ValidationResult) -> None:
    if entries is None:
        return
    if not isinstance(entries, dict):
        r.errors.append("entries 段必须是字典。")
        return
    for name, block in entries.items():
        if not isinstance(block, dict):
            r.errors.append(f"entries.{name} 必须是字典。")
            continue
        _unknown(block, ENTRIES_KEYS, f"entries.{name}", r)
        addon = block.get("addon_pct")
        max_addons = block.get("max_addons")
        if addon and not max_addons:
            r.warnings.append(
                f"entries.{name} 配了 addon_pct 但 max_addons 是 0，"
                "不会加仓。要加仓两个都得设。")
        for key in ("base_pct", "addon_pct"):
            v = block.get(key)
            if v is not None and not 0.0 <= float(v) <= 1.0:
                r.errors.append(f"entries.{name}.{key} = {v} 不在 0~1 之间，"
                                "它是占权益的比例。")


def _check_factors(factors, r: ValidationResult, strict: bool) -> None:
    if factors is None:
        return
    if not isinstance(factors, dict):
        r.errors.append("factors 段必须是字典。")
        return
    _unknown(factors, FACTORS_KEYS, "factors", r)
    _enum(factors.get("ranking"), RANKING_CHOICES, "factors.ranking", r)
    for strategy, choice in (factors.get("by_strategy") or {}).items():
        _enum(choice, RANKING_CHOICES, f"factors.by_strategy.{strategy}", r)

    weights = factors.get("weights") or {}
    if factors.get("ranking") == "custom" and not weights:
        r.errors.append("factors.ranking=custom 但 weights 是空的，"
                        "会静默退回成交额排序。")
    if strict and weights:
        from zhixing_quant.factors.base import FACTOR_REGISTRY
        unknown = set(weights) - set(FACTOR_REGISTRY)
        if unknown:
            r.errors.append(
                f"factors.weights 里有未注册的因子：{sorted(unknown)}。"
                f"已注册的：{sorted(FACTOR_REGISTRY)}")


def _check_strategy_names(cfg: dict, r: ValidationResult) -> None:
    """exits / entries 里出现的战法名必须真的存在。

    写 `exits.b3:` 这种不存在的战法，整段配置就是死的，而且没人会发现。
    """
    try:
        from zhixing_quant.strategies.registry import STRATEGY_REGISTRY
    except Exception:
        return
    known = set(STRATEGY_REGISTRY) | {"default"}
    for section in ("exits", "entries"):
        for name in (cfg.get(section) or {}):
            if name not in known:
                r.warnings.append(
                    f"{section}.{name} 对应的战法没有注册，这段配置不会生效。"
                    f"已注册：{sorted(STRATEGY_REGISTRY)}")
