"""每日决策主循环。

规格来源：01-architecture.md 1.2 每日主循环

    择时 (Timing)  >  防守 (Defense)  >  进攻 (Offense)        [LOCKED]

这个顺序在代码里体现为硬优先级，不是并列条件：
- 择时为空头 → 进攻模块整体禁用
- 防守先于进攻执行，避免同日对同一标的先买后卖
- 任何买入计划缺少止损位直接抛错，不填默认值（规格 01.2 关键约束 2）

原实现是个 stub（"Steps 3-5: simplified"），且没有任何业务代码引用它。
本版接上了持仓存储、防守引擎、持仓评级、仓位计算和下单前检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class HoldingReview:
    """单个持仓的每日复核结果。"""
    code: str
    name: str
    strategy: str
    shares: int
    entry_price: float
    last_price: float
    pnl: float
    return_pct: float
    stars: int                      # 0-5 持仓评级
    risk_level: str
    rating_reasons: List[str] = field(default_factory=list)
    action: Optional[str] = None    # None=持有, "SELL"/"REDUCE"
    action_reason: str = ""
    action_priority: Optional[int] = None   # 防守阶梯 1-8，数字越小越优先


@dataclass
class TradePlan:
    """一条买入计划。规格要求必须同时产出止损。"""
    code: str
    name: str
    strategy: str
    signal: str
    entry_price: float
    shares: int
    amount: float
    stop_loss: float
    take_profit: Optional[float]
    risk_amount: float              # 打到止损会亏多少钱
    risk_pct_of_equity: float
    blocked: bool = False
    blocked_reason: str = ""


@dataclass
class DailyResult:
    date: str
    book: str
    # 阶段 1 择时
    regime: str
    regime_strength: float
    regime_score: float
    mode: str
    allow_open: bool
    max_total_pct: float
    allowed_signals: List[str] = field(default_factory=list)
    regime_trigger: str = ""          # 最近一次区间触发的依据（无触发为空）
    # 阶段 2 防守
    reviews: List[HoldingReview] = field(default_factory=list)
    # 阶段 3 进攻
    candidates: pd.DataFrame = field(default_factory=pd.DataFrame)
    charts: Dict[str, pd.DataFrame] = field(default_factory=dict)
    offense_disabled_reason: str = ""
    # 阶段 4 仓位
    plans: List[TradePlan] = field(default_factory=list)
    # 账户
    cash: float = 0.0
    equity: float = 0.0
    position_pct: float = 0.0
    notes: List[str] = field(default_factory=list)
    defense_coverage: Dict[str, bool] = field(default_factory=dict)

    @property
    def actions_needed(self) -> List[HoldingReview]:
        return [r for r in self.reviews if r.action]


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def run_daily_cycle(
    cfg: dict,
    date: Optional[str] = None,
    book: str = "swing",
    strategy_key: str = "brick",
    limit_universe: Optional[int] = 300,
    progress=None,
) -> DailyResult:
    """跑一遍完整的每日决策流程。

    Args:
        cfg: 配置字典。
        date: YYYYMMDD，默认取行情库最新交易日。
        book: 账户。规格 01.6 要求超短与波段分账户，"swing" / "scalp"。
        strategy_key: 进攻阶段使用的战法。
        limit_universe: 扫描上限。
        progress: 可选进度回调，透传给扫描器。

    Returns:
        DailyResult，四个阶段的结果都在里面。
    """
    from zhixing_quant.data.tdx_loader import fetch_a_spot, load_daily
    from zhixing_quant.portfolio.store import PortfolioStore
    from zhixing_quant.data.sync import db_path

    spot = fetch_a_spot(trade_date=_normalize_date(date))
    trade_date = spot["date"].iloc[0]
    date_str = trade_date.strftime("%Y-%m-%d")

    store = PortfolioStore(db_path(cfg))
    result = DailyResult(date=date_str, book=book, regime="", regime_strength=0.0,
                         regime_score=0.0, mode="", allow_open=False, max_total_pct=0.0)

    # ---------- 阶段 1：择时 ----------
    _stage_timing(cfg, spot, result)

    # ---------- 阶段 2：防守（先处理持仓）----------
    positions = store.positions(book)
    prices = dict(zip(spot["code"], spot["close"]))
    # 决策日取快照的真实交易日，而不是用户输入的原始字符串——用户可能选了
    # 一个非交易日，fetch_a_spot 已经落到上一交易日了。
    _stage_defense(cfg, store, positions, result,
                   as_of=trade_date.strftime("%Y%m%d"))

    result.cash = store.cash(book)
    result.equity = store.equity(prices, book)
    if result.equity > 0:
        held = sum(prices.get(p["code"], p["entry_price"]) * p["shares"] for p in positions)
        result.position_pct = round(held / result.equity, 4)

    # ---------- 阶段 3：进攻（受择时闸门约束）----------
    if not result.allow_open:
        result.offense_disabled_reason = (
            f"择时为{result.regime}，按规格进攻模块整体禁用，今日不开新仓。"
        )
    elif result.position_pct >= result.max_total_pct:
        result.offense_disabled_reason = (
            f"当前仓位 {result.position_pct:.0%} 已达{result.regime}区间上限 "
            f"{result.max_total_pct:.0%}，不再开新仓。"
        )
    else:
        _stage_offense(cfg, result, strategy_key, _normalize_date(date),
                       limit_universe, progress,
                       held_codes={p["code"] for p in positions},
                       blacklisted=store.blacklisted())

    # ---------- 阶段 4：仓位与下单计划 ----------
    _stage_sizing(cfg, result)

    store.close()
    return result


# ---------------------------------------------------------------------------
# 阶段实现
# ---------------------------------------------------------------------------

def _stage_timing(cfg: dict, spot: pd.DataFrame, result: DailyResult) -> None:
    from zhixing_quant.timing.active_value import current_regime
    from zhixing_quant.timing.strategy_mode import get_strategy_mode

    date_key = result.date.replace("-", "")
    try:
        reg = current_regime(date_key, cfg)
    except Exception as exc:
        reg = {"regime": "NEUTRAL", "strength": 0.5, "pct": 0.0, "trigger": ""}
        result.notes.append(f"活跃市值区间判断失败，按中性处理：{exc}")

    mode = get_strategy_mode(reg["regime"], reg.get("strength", 0.5))

    pcfg = cfg.get("portfolio", {})
    caps = {
        "BULL": float(pcfg.get("bull_max_total", 0.80)),
        "NEUTRAL": float(pcfg.get("neutral_max_total", 0.50)),
        "BEAR": float(pcfg.get("bear_max_total", 0.0)),
    }

    result.regime = reg["regime"]
    result.regime_strength = float(reg.get("strength", 0.0))
    result.regime_score = round(float(reg.get("pct", 0.0)), 4)   # 当日涨跌幅
    result.regime_trigger = str(reg.get("trigger", ""))
    result.mode = mode.get("mode", "")
    result.allowed_signals = list(mode.get("allowed_signals", []))
    result.max_total_pct = caps.get(result.regime, 0.0)
    result.allow_open = bool(mode.get("allow_open", False)) and result.max_total_pct > 0

    if cfg.get("regime", {}).get("block_open_in_bear", True) is False and \
            result.regime == "BEAR":
        result.notes.append(
            "配置里 block_open_in_bear=false，空头区间仍被允许开仓。"
            "规格 01.1 建议保持空头禁止开仓。"
        )

    trigger_note = f"，触发依据：{reg['trigger']}" if reg.get("trigger") else "（无新触发）"
    result.notes.append(
        f"活跃市值区间：{result.regime}，判断基准日 {reg.get('date', '—')}"
        f"{trigger_note}。空头区间禁止开仓且需清仓已有持仓。"
    )


def _stage_defense(cfg: dict, store, positions: List[dict], result: DailyResult,
                   as_of: Optional[str] = None) -> None:
    """对每个持仓跑防守阶梯 + 五分制评级。

    Args:
        as_of: YYYYMMDD 决策日。**必须传**：原实现调 `load_daily(code)` 不带
            end_date，永远拿库里最新一根 K 线。于是在「今日」页选一个历史日期
            复盘时，择时和选股都按那天算，唯独防守引擎在用今天的收盘价判
            止损、判跌破黄线——一个标准的未来函数，而且只在复盘时发作，
            界面上没有任何痕迹。
    """
    if not positions:
        return

    from zhixing_quant.data.tdx_loader import load_daily
    from zhixing_quant.portfolio.defense import DefenseEngine
    from zhixing_quant.portfolio.rater import HoldingRater

    engine = DefenseEngine()
    rater = HoldingRater()
    reported = False

    for pos in positions:
        code = pos["code"]
        try:
            df = load_daily(code, end_date=as_of, adjust="qfq")
            if df.empty or len(df) < 120:
                result.notes.append(f"{code} K线不足 120 根，跳过防守评估。")
                continue
            df, warns, coverage = _enrich(df, cfg)
            if not reported:
                reported = True
                result.notes.extend(warns)
                dead = [r for r, ok in coverage.items() if not ok]
                if dead:
                    result.notes.append(
                        "以下防守规则因缺少指标列不会触发：" + "、".join(dead)
                    )
                result.defense_coverage = coverage
        except Exception as exc:
            result.notes.append(f"{code} 数据加载失败，未做防守评估：{exc}")
            continue

        last = df.iloc[-1]
        last_price = float(last["close"])
        pnl = (last_price - float(pos["entry_price"])) * int(pos["shares"])
        ret = last_price / float(pos["entry_price"]) - 1.0

        try:
            rating = rater.rate(df, -1, pos)
        except Exception:
            rating = None
        try:
            exit_sig = engine.evaluate(df, -1, pos)
        except Exception:
            exit_sig = None

        review = HoldingReview(
            code=code, name=pos.get("name", ""), strategy=pos.get("strategy", ""),
            shares=int(pos["shares"]), entry_price=float(pos["entry_price"]),
            last_price=last_price, pnl=round(pnl, 2), return_pct=round(ret, 4),
            stars=int(rating.stars) if rating else 0,
            risk_level=rating.risk_level if rating else "",
            rating_reasons=list(rating.reasons) if rating else [],
        )
        if exit_sig is not None:
            review.action = exit_sig.action
            review.action_reason = exit_sig.reason
            review.action_priority = int(exit_sig.priority)
        result.reviews.append(review)

    # 防守阶梯是有序的，先命中先执行，所以按优先级排在最前面
    result.reviews.sort(key=lambda r: (r.action_priority is None,
                                       r.action_priority or 99, -r.stars))


def _stage_offense(cfg, result, strategy_key, date, limit_universe, progress,
                   held_codes: set, blacklisted: set) -> None:
    """扫描候选。已持有的和黑名单里的排除。

    走 scanner/strategy_scan 的统一适配器，六套战法都能扫，
    不再依赖手写的 scanner/daily_*.py。
    """
    from zhixing_quant.scanner.strategy_scan import available, scan_strategy

    if strategy_key not in available():
        result.offense_disabled_reason = (
            f"战法 {strategy_key} 不可扫描。可用：{', '.join(sorted(available()))}"
        )
        return

    try:
        candidates, charts = scan_strategy(
            strategy_key, cfg, end_date=date,
            limit_universe=limit_universe, progress=progress,
        )
    except Exception as exc:
        result.offense_disabled_reason = f"扫描失败：{exc}"
        return

    result.charts = charts
    result.notes.extend(candidates.attrs.get("warnings", []))
    if candidates.empty:
        return

    # 规格 01.3 [LOCKED]：跌破黄线的标的不再进入选股池
    excluded = held_codes | blacklisted
    before = len(candidates)
    candidates = candidates[~candidates["code"].isin(excluded)].reset_index(drop=True)
    if before > len(candidates):
        result.notes.append(
            f"排除 {before - len(candidates)} 只（已持有或在黑名单中）。"
        )
    result.candidates = candidates


def _stage_sizing(cfg: dict, result: DailyResult) -> None:
    """给每个候选算股数和止损，产出可执行的下单计划。"""
    if result.candidates is None or result.candidates.empty:
        return

    from zhixing_quant.portfolio.sizer import PositionSizer, per_position_cap

    pcfg = cfg.get("portfolio", {})
    sizer = PositionSizer(
        risk_pct=float(pcfg.get("risk_per_trade", 0.02)),
        kelly_fraction=float(pcfg.get("kelly_fraction", 0.25)),
    )
    per_pos = per_position_cap(cfg, result.equity)
    equity = result.equity if result.equity > 0 else float(
        cfg.get("capital", {}).get("initial_cash", 0) or 0
    )
    if equity <= 0:
        result.notes.append(
            "账户权益为 0，无法计算仓位。到持仓页设置初始资金后重新运行。"
        )
        return

    # 剩余可用仓位
    room = max(0.0, result.max_total_pct - result.position_pct)

    for _, row in result.candidates.iterrows():
        entry = float(row["close"])
        stop = float(row.get("stop_loss") or 0.0)
        plan = TradePlan(
            code=str(row["code"]), name=str(row.get("name", "")),
            strategy=result.mode or "", signal=str(row.get("reason", "")),
            entry_price=entry, shares=0, amount=0.0,
            stop_loss=stop, take_profit=None, risk_amount=0.0, risk_pct_of_equity=0.0,
        )

        # 规格 01.2：缺止损的计划不允许存在
        if stop <= 0 or stop >= entry:
            plan.blocked = True
            plan.blocked_reason = "该信号未产出有效止损位，按规格不允许下单。"
            result.plans.append(plan)
            continue

        shares = sizer.size(entry, stop, equity,
                            regime_max_pct=room, per_position_pct=per_pos)
        if shares <= 0:
            risk_one_lot = (entry - stop) * 100
            budget = equity * sizer.risk_pct * sizer.kelly_fraction
            plan.blocked = True
            plan.blocked_reason = (
                f"一手（100 股）风险 {risk_one_lot:,.0f} 元，超过单笔风险预算 "
                f"{budget:,.0f} 元。要么放弃，要么把止损收紧到 "
                f"{entry - budget / 100:.2f} 以上。"
            )
            result.plans.append(plan)
            continue

        plan.shares = shares
        plan.amount = round(shares * entry, 2)
        plan.risk_amount = round(sizer.risk_exposure(shares, entry, stop), 2)
        plan.risk_pct_of_equity = round(plan.risk_amount / equity, 4)
        tp = row.get("take_profit")
        plan.take_profit = float(tp) if tp and float(tp) > 0 else None
        result.plans.append(plan)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _normalize_date(date: Optional[str]) -> Optional[str]:
    """接受 "20240615" 和 "2024-06-15" 两种写法，统一成 YYYYMMDD。"""
    if not date:
        return None
    d = str(date).replace("-", "").replace("/", "").strip()
    return d if len(d) == 8 and d.isdigit() else None


def _enrich(df: pd.DataFrame, cfg: dict):
    """给持仓补上防守引擎需要的全部指标列。

    走 indicators/pipeline.py 的 defense 流水线。它会显式报告哪一步没跑成，
    以及防守阶梯哪几级因为缺列而变成哑规则——这些必须让使用者看见，
    否则「没触发」和「规则根本没运行」在界面上长得一模一样。
    """
    from zhixing_quant.indicators.pipeline import defense_coverage, run_pipeline

    result = run_pipeline(df, cfg, "defense")
    return result.df, result.warnings(), defense_coverage(result.df)


# ---------------------------------------------------------------------------
# 旧入口（保留）
# ---------------------------------------------------------------------------

@dataclass
class ExecutionPlan:
    """旧版返回结构。只做择时 + 模式判断，不访问行情库。

    保留是因为它能在无数据环境下独立运行（既有测试依赖这一点）。
    完整的四阶段流程请用 run_daily_cycle。
    """
    date: str
    regime: str
    regime_strength: float
    mode: str
    actions: list


def run_daily_workflow(date: str, cfg: dict) -> ExecutionPlan:
    """仅执行择时与策略模式判断，不碰持仓和行情。

    这是 run_daily_cycle 的阶段 1。需要完整决策流程时请直接调用 run_daily_cycle。
    """
    from zhixing_quant.timing.active_value import current_regime
    from zhixing_quant.timing.strategy_mode import get_strategy_mode

    reg = current_regime(date, cfg)
    mode = get_strategy_mode(reg["regime"], reg["strength"])

    actions = [f"SCAN:{s}" for s in mode["allowed_signals"]] if mode["allow_open"] else []
    return ExecutionPlan(
        date=date,
        regime=reg["regime"],
        regime_strength=reg["strength"],
        mode=mode["mode"],
        actions=actions,
    )
