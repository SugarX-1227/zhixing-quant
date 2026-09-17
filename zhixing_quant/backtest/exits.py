"""出场规则层：止损 / 移动止损 / 止盈 / 时间止损 / 防守阶梯，统一成一套可配置规则。

为什么需要这一层
----------------

在此之前，回测引擎的出场是**写死**的三条：

    止损 = 信号日最低价（且不高于 入场价 × 0.99）
    止盈 = 入场价 × 1.15
    满 20 个交易日无条件清仓

而实盘 `executor/daily_workflow.py` 走的是完全不同的一套：战法自己的
`exit_conditions` + `portfolio/defense.py` 的八级防守阶梯。

后果是回测页调出来的参数**不描述实盘会发生什么**。具体表现：

- 六套战法全都实现了 `exit_conditions`，回测里一次都没被调用过
  （`runner` 从不给 `engine.run` 传 `exit_fn`）。
- `config` 的 `strategies.brick.take_profit.red_streak_*`（四块砖分级止盈）
  和 `stop_loss.break_entry_low / break_yellow_line` 整段是死配置。
- 实测 b1 中位持有 1 天、362 笔里 285 笔止损——那不是战法的表现，
  是「止损贴着信号日最低价」这条写死规则的表现。

本模块把出场判据收敛成一个对象，回测引擎和实盘防守都从这里取，
改一处两边同时生效。

引擎需要的只有两件事
--------------------

1. **价位型**：这根 K 线的止损价和止盈价是多少 → `levels()`
   盘中触及即成交（引擎按跳空穿越取较差的一侧）。
2. **收盘型**：今天收盘后要不要挂明天的卖单 → `on_close()`
   返回卖出理由字符串，引擎在次日开盘成交（T+1，无未来函数）。

移动止损、分级止盈、防守阶梯、战法自身出场，全部落在这两个方法里面，
引擎不需要知道它们的存在。

配置
----

见 `config/settings.yaml` 的 `exits` 段。`exits.default` 是所有战法的
缺省，`exits.<战法名>` 可以整段或逐项覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from zhixing_quant.indicators.tdx import atr as atr_of

INF = float("inf")


# ---------------------------------------------------------------------------
# 规则声明
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StopSpec:
    """初始止损。

    kind:
        entry_low   入场那根 K 线的最低价（规格 07 防守阶梯 P2 的原始口径）
        pct         入场价 × (1 - pct)
        atr         入场价 - atr_mult × ATR
        none        不设初始止损（只靠移动止损或收盘型规则）
    """
    kind: str = "entry_low"
    pct: float = 0.08
    atr_mult: float = 2.0
    atr_window: int = 14
    buffer: float = 0.0          # 止损价再向下让出的比例，防被日内插针扫掉
    cap_pct: float = 0.99        # 止损价上限 = 入场价 × cap_pct，避免贴着成本


@dataclass(frozen=True)
class TrailSpec:
    """移动止损。只上移不下移。

    kind:
        none         不移动
        pct          持仓期间最高价 × (1 - pct)
        chandelier   持仓期间最高价 - atr_mult × ATR（吊灯止损）
        yellow_line  知行多空线（黄线随价格上移，天然是移动止损）
        white_line   白线（比黄线快，分批止盈后剩余仓位的兜底离场）
    """
    kind: str = "none"
    pct: float = 0.10
    atr_mult: float = 3.0
    atr_window: int = 14
    activate_profit: float = 0.0   # 浮盈达到此比例后才启用移动止损


@dataclass(frozen=True)
class TakeProfitSpec:
    """止盈。

    kind:
        none         不设固定止盈，靠移动止损和收盘型规则了结
        pct          入场价 × (1 + pct)
        tiered       按 red_streak（连续红砖根数）分批了结，见 tiers
        price_tiers  按涨幅分批了结（涨 8% 减 1/3、涨 20% 再减 1/3，
                     剩余交给移动止损），见 price_tiers
    """
    kind: str = "pct"
    pct: float = 0.15
    # {连续红砖根数: 减仓比例}。规划书 6.4.2 四块砖止盈定律。
    tiers: Dict[int, float] = field(default_factory=dict)
    # [(涨幅门槛, 减仓比例), ...] 按涨幅升序，如 [(0.08, 1/3), (0.20, 1/3)]。
    price_tiers: Tuple[Tuple[float, float], ...] = ()


@dataclass(frozen=True)
class TimeStopSpec:
    """时间止损。"""
    max_holding_days: int = 20      # 满 N 个交易日无条件走，0 = 不限
    no_progress_days: int = 0       # 持仓 N 日内涨幅不足 min_progress 就走，0 = 关闭
    min_progress: float = 0.0


@dataclass(frozen=True)
class ExitSpec:
    """一套完整的出场规则。"""
    stop: StopSpec = field(default_factory=StopSpec)
    trail: TrailSpec = field(default_factory=TrailSpec)
    take_profit: TakeProfitSpec = field(default_factory=TakeProfitSpec)
    time_stop: TimeStopSpec = field(default_factory=TimeStopSpec)
    defense_ladder: bool = False    # 启用 portfolio/defense.py 八级阶梯
    strategy_exit: bool = False     # 调用战法自身 exit_conditions
    break_yellow_line: bool = False  # 收盘跌破黄线即清仓（规格 01.3 [LOCKED]）
    # 盈转亏：浮盈曾超过此比例后，某天收盘跌回成本下方 → 当日清仓。0 = 关闭
    profit_to_loss: float = 0.0
    # 低低走人：浮盈曾超过此比例后，某天收盘 < 前一日最低价 → 当日清仓。0 = 关闭
    close_below_prev_low: float = 0.0

    def describe(self) -> str:
        parts = [f"止损={self.stop.kind}"]
        if self.trail.kind != "none":
            parts.append(f"移动止损={self.trail.kind}")
        parts.append(f"止盈={self.take_profit.kind}")
        if self.take_profit.kind == "price_tiers":
            tiers = "、".join(f"+{g:.0%}减{p:.0%}" for g, p in self.take_profit.price_tiers)
            parts.append(f"分批止盈[{tiers}]")
        if self.time_stop.max_holding_days:
            parts.append(f"最长持有{self.time_stop.max_holding_days}日")
        if self.break_yellow_line:
            parts.append("破黄线清仓")
        if self.profit_to_loss:
            parts.append(f"盈转亏清仓(浮盈>{self.profit_to_loss:.0%}后)")
        if self.close_below_prev_low:
            parts.append(f"低低走人(浮盈>{self.close_below_prev_low:.0%}后)")
        if self.defense_ladder:
            parts.append("防守阶梯")
        if self.strategy_exit:
            parts.append("战法出场")
        return " · ".join(parts)


# ---------------------------------------------------------------------------
# 出场决策
# ---------------------------------------------------------------------------

@dataclass
class ExitDecision:
    """一条收盘型出场指令。portion < 1 表示减仓而非清仓。

    tier 是分批止盈已执行到的档位（1 起数），引擎记进 Position.tier_done，
    避免同一档反复触发；红砖档位仍从 reason 里解析（兼容旧配置）。
    """
    reason: str
    portion: float = 1.0
    priority: int = 99
    tier: int = 0


# ---------------------------------------------------------------------------
# 策略对象
# ---------------------------------------------------------------------------

class ExitPolicy:
    """按 ExitSpec 求值出场。回测引擎与实盘防守共用。

    引擎只调三个方法，其余都是内部实现：
        initial_levels(frame, i, entry_price) -> (stop, take_profit)
        levels(frame, i, pos)                 -> (stop, take_profit)
        on_close(frame, i, pos)               -> Optional[ExitDecision]

    `frame` 是一个轻量列容器（见 ExitFrame），而不是 DataFrame——引擎主循环
    里每根 K 线做一次 `.iloc` 要几十微秒，几十万次就是几十秒。
    """

    def __init__(self, spec: ExitSpec, cfg: Optional[dict] = None,
                 strategy=None, defense=None):
        self.spec = spec
        self.cfg = cfg or {}
        self.strategy = strategy
        self.defense = defense

    # -- 价位型 ---------------------------------------------------------

    def initial_levels(self, frame: "ExitFrame", i: int,
                       entry_price: float) -> Tuple[float, float]:
        """建仓当日定下初始止损与止盈。

        Args:
            frame: 该标的的列容器。
            i: 建仓那根 K 线的位置。
            entry_price: 实际成交价（含滑点）。

        Returns:
            (止损价, 止盈价)。止盈为 inf 表示不设固定止盈。
        """
        s = self.spec.stop
        if s.kind == "none":
            stop = 0.0
        elif s.kind == "pct":
            stop = entry_price * (1.0 - s.pct)
        elif s.kind == "atr":
            a = frame.atr(s.atr_window)[i]
            stop = entry_price - s.atr_mult * a if np.isfinite(a) else entry_price * 0.92
        else:                                    # entry_low
            stop = frame.low[i]
        if stop > 0:
            stop *= (1.0 - s.buffer)
            # 止损贴着成本价没有意义：一个正常波动就出局
            stop = min(stop, entry_price * s.cap_pct)

        tp = self.spec.take_profit
        take = entry_price * (1.0 + tp.pct) if tp.kind == "pct" else INF
        return float(max(stop, 0.0)), float(take)

    def levels(self, frame: "ExitFrame", i: int, pos) -> Tuple[float, float]:
        """当日生效的止损 / 止盈。移动止损在这里上移，**只上不下**。"""
        stop = pos.stop_loss
        t = self.spec.trail
        if t.kind == "none":
            return stop, pos.take_profit

        # 未达启动浮盈门槛时不移动
        if t.activate_profit > 0:
            ref_price = frame.close[i]
            if ref_price < pos.entry_price * (1.0 + t.activate_profit):
                return stop, pos.take_profit

        if t.kind == "pct":
            cand = pos.highest_high * (1.0 - t.pct)
        elif t.kind == "chandelier":
            a = frame.atr(t.atr_window)[i]
            cand = pos.highest_high - t.atr_mult * a if np.isfinite(a) else -INF
        else:
            # 线型移动止损取**昨日**的线值：线是用当日收盘算的，
            # 拿当日线做当日盘中止损是未来函数。
            if t.kind == "yellow_line":
                line = frame.yellow_line[i - 1] if i > 0 else np.nan
            elif t.kind == "white_line":
                line = frame.white_line[i - 1] if i > 0 else np.nan
            else:
                line = np.nan
            cand = line if np.isfinite(line) else -INF

        if np.isfinite(cand) and cand > stop:
            stop = float(cand)
        return stop, pos.take_profit

    # -- 收盘型 ---------------------------------------------------------

    def on_close(self, frame: "ExitFrame", i: int, pos) -> Optional[ExitDecision]:
        """收盘后判断要不要挂明天的卖单。命中多条时取优先级最高的一条。"""
        hits: List[ExitDecision] = []

        # P1 分级止盈（四块砖定律）。tiers 里数字大的优先，因为减仓比例更高。
        tiers = self.spec.take_profit.tiers
        if self.spec.take_profit.kind == "tiered" and tiers:
            streak = frame.red_streak[i]
            if np.isfinite(streak):
                matched = [k for k in tiers if int(streak) >= k]
                if matched:
                    k = max(matched)
                    if k > pos.tier_done:
                        hits.append(ExitDecision(
                            reason=f"连{k}根红砖减仓", portion=float(tiers[k]),
                            priority=1))

        # P1' 涨幅分批止盈（+8% 减 1/3、+20% 再减 1/3）。收盘确认，次日开盘成交。
        ptiers = self.spec.take_profit.price_tiers
        if self.spec.take_profit.kind == "price_tiers" and ptiers:
            gain = frame.close[i] / pos.entry_price - 1.0
            _EPS = 1e-9      # 12/10-1 在浮点里是 0.19999...，恰好达标不能漏
            for tier_idx, (gain_th, portion) in enumerate(ptiers, start=1):
                if gain >= gain_th - _EPS and tier_idx > pos.tier_done:
                    # 找未执行的**最高**档（跳档：直接从 +8% 涨到 +21% 时两档
                    # 不会同日都触发，按高档执行，低档视为已过）
                    best = max(
                        (t for t, (g, _p) in enumerate(ptiers, start=1)
                         if gain >= g - _EPS and t > pos.tier_done),
                        default=None,
                    )
                    if best is not None:
                        g, p = ptiers[best - 1]
                        hits.append(ExitDecision(
                            reason=f"涨{g:.0%}减仓{p:.0%}",
                            portion=float(p), priority=5, tier=best))
                    break

        # P3-P8 防守阶梯
        if self.spec.defense_ladder and self.defense is not None:
            sig = self.defense.evaluate_row(frame.row(i), pos.as_dict())
            if sig is not None:
                hits.append(ExitDecision(reason=f"防守{sig.priority}:{sig.reason}",
                                         portion=1.0, priority=sig.priority))

        # 跌破黄线（规格 01.3 [LOCKED]：清仓并移出股票池）
        if self.spec.break_yellow_line:
            y = frame.yellow_line[i]
            if np.isfinite(y) and frame.close[i] < y:
                hits.append(ExitDecision(reason="跌破知行多空线", priority=7))

        # 盈转亏：浮盈曾达标，现在收盘跌回成本下方 → 清仓
        if self.spec.profit_to_loss > 0:
            peaked = pos.highest_high >= pos.entry_price * (1.0 + self.spec.profit_to_loss)
            if peaked and frame.close[i] < pos.entry_price:
                hits.append(ExitDecision(reason="盈转亏清仓", priority=3))

        # 低低走人：浮盈曾达标，今天收盘 < 昨天最低价 → 清仓
        if self.spec.close_below_prev_low > 0 and i > 0:
            peaked = pos.highest_high >= pos.entry_price * (1.0 + self.spec.close_below_prev_low)
            prev_low = frame.low[i - 1]
            if peaked and np.isfinite(prev_low) and frame.close[i] < prev_low:
                hits.append(ExitDecision(reason="高位收盘破前低", priority=3))

        # 战法自身出场
        if self.spec.strategy_exit and self.strategy is not None:
            sig = self._strategy_exit(frame, i, pos)
            if sig is not None:
                hits.append(sig)

        # 时间止损
        ts = self.spec.time_stop
        if ts.max_holding_days and pos.bars_held >= ts.max_holding_days:
            hits.append(ExitDecision(reason=f"持有满{ts.max_holding_days}日",
                                     priority=90))
        if ts.no_progress_days and pos.bars_held >= ts.no_progress_days:
            gain = frame.close[i] / pos.entry_price - 1.0
            if gain < ts.min_progress:
                hits.append(ExitDecision(
                    reason=f"{ts.no_progress_days}日涨幅不足{ts.min_progress:.0%}",
                    priority=80))

        if not hits:
            return None
        hits.sort(key=lambda d: d.priority)
        return hits[0]

    def _strategy_exit(self, frame: "ExitFrame", i: int, pos) -> Optional[ExitDecision]:
        """调战法的 exit_conditions。它收 DataFrame，所以这里才做一次切片。"""
        try:
            sig = self.strategy.exit_conditions(frame.df, i, pos.as_dict(), self.cfg)
        except Exception:
            return None
        if sig is None:
            return None
        return ExitDecision(reason=f"战法:{getattr(sig, 'reason', 'exit')}",
                            priority=int(getattr(sig, "priority", 50)))


# ---------------------------------------------------------------------------
# 列容器
# ---------------------------------------------------------------------------

class ExitFrame:
    """把一只标的的常用列预先转成 numpy，供主循环按位置索引。

    引擎每根 K 线都要读 close/low/yellow_line/red_streak。走 pandas 的
    `.iloc[i]` 每次要构造一个 Series，几十微秒；几百只标的 × 几百个交易日
    就是几十秒，全花在建临时对象上。这里一次转完，后面纯数组索引。
    """

    __slots__ = ("df", "open", "high", "low", "close", "yellow_line",
                 "white_line", "red_streak", "_atr", "_cols")

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.open = df["open"].to_numpy(float)
        self.high = df["high"].to_numpy(float)
        self.low = df["low"].to_numpy(float)
        self.close = df["close"].to_numpy(float)
        self.yellow_line = self._col(df, "yellow_line")
        self.white_line = self._col(df, "white_line")
        self.red_streak = self._col(df, "red_streak")
        self._atr: Dict[int, np.ndarray] = {}
        self._cols = df.columns

    @staticmethod
    def _col(df: pd.DataFrame, name: str) -> np.ndarray:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").to_numpy(float)
        return np.full(len(df), np.nan)

    def atr(self, window: int) -> np.ndarray:
        """按窗口缓存 ATR，同一只标的只算一次。"""
        arr = self._atr.get(window)
        if arr is None:
            arr = atr_of(self.df["high"], self.df["low"], self.df["close"],
                         window).to_numpy(float)
            self._atr[window] = arr
        return arr

    def row(self, i: int) -> pd.Series:
        """防守阶梯要按列名读一整行，这条路径只在真的启用阶梯时才走。"""
        return self.df.iloc[i]


# ---------------------------------------------------------------------------
# 从配置构建
# ---------------------------------------------------------------------------

def spec_from_config(cfg: dict, strategy: Optional[str] = None) -> ExitSpec:
    """读 cfg["exits"]，`exits.<战法>` 逐项覆盖 `exits.default`。

    Args:
        cfg: 全局配置。
        strategy: 战法名，None 只取 default。

    Returns:
        ExitSpec。配置缺失时返回与旧引擎行为一致的缺省值
        （入场低点止损 + 15% 固定止盈 + 满 20 日清仓）。
    """
    root = (cfg or {}).get("exits", {}) or {}
    merged = _deep_merge(root.get("default", {}) or {},
                         (root.get(strategy, {}) or {}) if strategy else {})

    stop = merged.get("stop", {}) or {}
    trail = merged.get("trailing", {}) or {}
    tp = merged.get("take_profit", {}) or {}
    ts = merged.get("time_stop", {}) or {}

    # tiers 的键从 YAML 读出来是字符串（red_streak_4 / "4"），统一成 int
    tiers = {}
    for k, v in (tp.get("tiers", {}) or {}).items():
        key = str(k).replace("red_streak_", "")
        try:
            tiers[int(key)] = float(v)
        except (TypeError, ValueError):
            continue

    # 涨幅分批止盈：支持 [{gain: 0.08, sell: 0.33}, ...] 和 [[0.08, 0.33], ...]
    price_tiers: list = []
    for item in (tp.get("price_tiers", []) or []):
        if isinstance(item, dict):
            gain, sell = item.get("gain"), item.get("sell")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            gain, sell = item
        else:
            continue
        try:
            price_tiers.append((float(gain), float(sell)))
        except (TypeError, ValueError):
            continue
    price_tiers.sort()

    return ExitSpec(
        stop=StopSpec(
            kind=str(stop.get("kind", "entry_low")),
            pct=float(stop.get("pct", 0.08)),
            atr_mult=float(stop.get("atr_mult", 2.0)),
            atr_window=int(stop.get("atr_window", 14)),
            buffer=float(stop.get("buffer", 0.0)),
            cap_pct=float(stop.get("cap_pct", 0.99)),
        ),
        trail=TrailSpec(
            kind=str(trail.get("kind", "none")),
            pct=float(trail.get("pct", 0.10)),
            atr_mult=float(trail.get("atr_mult", 3.0)),
            atr_window=int(trail.get("atr_window", 14)),
            activate_profit=float(trail.get("activate_profit", 0.0)),
        ),
        take_profit=TakeProfitSpec(
            kind=str(tp.get("kind", "pct")),
            pct=float(tp.get("pct", 0.15)),
            tiers=tiers,
            price_tiers=tuple(price_tiers),
        ),
        time_stop=TimeStopSpec(
            max_holding_days=int(ts.get("max_holding_days", 20)),
            no_progress_days=int(ts.get("no_progress_days", 0)),
            min_progress=float(ts.get("min_progress", 0.0)),
        ),
        defense_ladder=bool(merged.get("defense_ladder", False)),
        strategy_exit=bool(merged.get("strategy_exit", False)),
        break_yellow_line=bool(merged.get("break_yellow_line", False)),
        profit_to_loss=float(merged.get("profit_to_loss", 0.0) or 0.0),
        close_below_prev_low=float(merged.get("close_below_prev_low", 0.0) or 0.0),
    )


def policy_from_config(cfg: dict, strategy: Optional[str] = None) -> ExitPolicy:
    """按配置组装一个可直接交给引擎的 ExitPolicy。"""
    spec = spec_from_config(cfg, strategy)
    strat_obj = None
    if spec.strategy_exit and strategy:
        from zhixing_quant.strategies.registry import get_strategy
        strat_obj = get_strategy(strategy, cfg)
    defense = None
    if spec.defense_ladder:
        from zhixing_quant.portfolio.defense import DefenseEngine
        defense = DefenseEngine()
    return ExitPolicy(spec, cfg=cfg, strategy=strat_obj, defense=defense)


def _deep_merge(base: dict, override: dict) -> dict:
    """override 逐层覆盖 base，不修改任何一方。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
