"""事件驱动回测引擎。

相比旧版修掉的问题：
1. 旧版没有 import pandas，run() 一调用就 NameError —— 所以它从来没被跑通过。
2. 旧版买入只扣手续费不扣本金（equity -= cost，cost 只是佣金），
   资金曲线完全失真，收益率无意义。
3. 旧版在日期循环里对每只股票重新调用 get_data_fn，
   复杂度是 O(交易日 × 股票数) 次数据库查询，几百只股票要跑几小时。
4. 旧版用当日收盘价成交，而信号本身就是收盘后才产生的 —— 未来函数。
   现在统一为「T 日收盘出信号，T+1 开盘成交」，和 config.execution 一致。
5. 旧版硬编码 sig_b1，换策略就失效。

现在的口径：
- 信号在 T 日收盘确认，T+1 开盘按 open ± 滑点成交。
- T+1 开盘一字涨停买不进，一字跌停卖不出（顺延到下一日）。
- A 股 T+1：当日买入当日不可卖。
- 资金曲线 = 现金 + 持仓按当日收盘市值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from zhixing_quant.backtest.metrics import compute_metrics


@dataclass
class Trade:
    code: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    return_pct: float
    exit_reason: str
    holding_days: int


@dataclass
class Position:
    code: str
    entry_date: str
    entry_price: float
    shares: int
    cost_basis: float          # 含买入费用的总成本（按当前剩余股数摊销）
    stop_loss: float
    take_profit: float
    bars_held: int = 0
    entry_shares: int = 0      # 建仓股数，分批减仓后 shares 会小于它
    entry_low: float = 0.0     # 建仓那根 K 线的最低价，防守阶梯 P2 要用
    highest_high: float = 0.0  # 持仓期间最高价，移动止损的锚
    tier_done: int = 0         # 分级止盈已经执行到第几档
    exit_reason: str = ""      # 收盘型规则挂出的卖出理由

    def as_dict(self) -> dict:
        """交给防守阶梯 / 战法 exit_conditions 的持仓视图。"""
        return {
            "code": self.code, "entry_date": self.entry_date,
            "entry_price": self.entry_price, "shares": self.shares,
            "stop_loss": self.stop_loss, "take_profit": self.take_profit,
            "entry_low": self.entry_low, "bars_held": self.bars_held,
        }


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    metrics: dict = field(default_factory=dict)
    daily_positions: List[dict] = field(default_factory=list)

    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=[
                    "code", "entry_date", "exit_date", "entry_price", "exit_price",
                    "shares", "pnl", "return_pct", "exit_reason", "holding_days",
                ]
            )
        return pd.DataFrame([t.__dict__ for t in self.trades])


def _tier_of(reason: str) -> int:
    """从「连3根红砖减仓」这类理由里取出档位数字，用于记录已执行到第几档。"""
    digits = "".join(ch for ch in reason if ch.isdigit())
    return int(digits) if digits else 0


class BacktestEngine:
    """A 股日线回测引擎。"""

    def __init__(self, cfg: dict):
        bt = cfg.get("backtest", {})
        self.cfg = cfg
        self.initial_capital = float(bt.get("initial_capital", 500000))
        self.commission = float(bt.get("commission", 0.00025))
        self.commission_min = float(bt.get("commission_min", 5.0))
        self.stamp_tax = float(bt.get("stamp_tax", 0.001))
        self.transfer_fee = float(bt.get("transfer_fee", 0.00001))
        self.slippage = float(bt.get("slippage", 0.0015))
        self.max_positions = int(bt.get("max_positions", 5))
        self.max_holding_days = int(bt.get("max_holding_days", 20))
        self.max_entries_per_day = int(bt.get("max_entries_per_day", 2))

        ex = cfg.get("execution", {})
        self.abandon_gap_up = float(ex.get("abandon_gap_up", 0.07))

        # 仓位口径。"equal" 是旧行为（剩余现金按空槽等分），"risk" 走
        # portfolio/PositionSizer 的风险头寸公式，"pct" 每笔固定权益比例。
        # 代码缺省留 equal 以保证不带配置时行为不变，随仓配置设的是 pct。
        self.sizing = str(bt.get("sizing", "equal")).lower()
        self.position_pct = float(bt.get("position_pct", 0.20))
        pcfg = cfg.get("portfolio", {}) or {}
        self.risk_per_trade = float(pcfg.get("risk_per_trade", 0.02))
        self.kelly_fraction = float(pcfg.get("kelly_fraction", 0.25))
        self.apply_regime_cap = bool(bt.get("apply_regime_cap", False))

    # -- 主循环 -----------------------------------------------------------

    def run(
        self,
        price_data: Dict[str, pd.DataFrame],
        signal_col: str = "sig_brick",
        stop_col: str = "stop_loss",
        take_profit_pct: float = 0.15,
        exit_fn: Optional[Callable] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        regime: Optional[Dict[str, str]] = None,
        exit_policy=None,
    ) -> BacktestResult:
        """跑一次回测。

        Args:
            price_data: {code: DataFrame}，必须已经算好指标，含 signal_col。
                        索引为 DatetimeIndex，含 open/high/low/close。
            signal_col: 买入信号列名，True 即当日收盘触发。
            stop_col: 止损价列名，缺失时用当日 low。
            take_profit_pct: 止盈比例。
            exit_fn: 可选自定义卖出判断 (df, idx, position) -> Optional[str]，
                     返回卖出原因字符串表示卖出。exit_policy 优先。
            exit_policy: backtest/exits.ExitPolicy。给了就用它决定止损价、
                     止盈价、移动止损和收盘型出场，`take_profit_pct` 与内置
                     的「满 N 日清仓」一并让位。不给则保持旧行为
                     （信号日低点止损 + 固定百分比止盈 + max_holding_days）。
            start_date / end_date: YYYYMMDD。
            regime: {"YYYY-MM-DD": "BULL"/"BEAR"/"NEUTRAL"}，各交易日开盘时可知的
                    活跃市值区间（由 T-1 及之前的活跃市值收盘决定，无未来函数）。
                    BEAR 日：禁止开新仓，已有持仓当日开盘强制清仓。

        Returns:
            BacktestResult
        """
        if not price_data:
            return BacktestResult(metrics=compute_metrics([], []))

        prepared = self._prepare(price_data, signal_col, stop_col, start_date, end_date)
        if not prepared:
            return BacktestResult(metrics=compute_metrics([], []))

        if exit_policy is not None:
            from zhixing_quant.backtest.exits import ExitFrame
            for item in prepared.values():
                item["ef"] = ExitFrame(item["df"])

        calendar = self._build_calendar(prepared)
        if len(calendar) < 2:
            return BacktestResult(metrics=compute_metrics([], []))

        regime_map = regime or {}
        bear_days = 0

        cash = self.initial_capital
        positions: Dict[str, Position] = {}
        trades: List[Trade] = []
        equity_values: List[float] = []
        daily_positions: List[dict] = []
        pending_entries: List[dict] = []      # T 日收盘产生，T+1 开盘执行
        pending_exits: List[str] = []
        # 开盘下单时还不知道今天收盘的权益，用昨收的权益和持仓市值做仓位上限的
        # 分母。这是实盘也只能拿到的信息，不构成未来函数。
        last_equity = self.initial_capital
        last_held = 0.0

        for day_i, date in enumerate(calendar):
            date_str = date.strftime("%Y-%m-%d")

            # --- 0. 区间闸门：空头日开盘清掉全部持仓，全天禁止开新仓 ---
            if regime_map.get(date_str, "NEUTRAL") == "BEAR":
                bear_days += 1
                for code, pos in positions.items():
                    pos.exit_reason = "空头区间清仓"
                    pos.__dict__["_exit_portion"] = 1.0   # 清仓，覆盖任何分批指令
                    if code not in pending_exits:
                        pending_exits.append(code)
                pending_entries = []      # 昨日收盘生成的买单一律作废

            # --- 1. 开盘：先卖后买 ---
            for code in list(pending_exits):
                pos = positions.get(code)
                bar = self._bar(prepared, code, date)
                if pos is None or bar is None:
                    pending_exits.remove(code)
                    continue
                if self._is_limit_down_open(prepared, code, date):
                    continue          # 一字跌停卖不出，明天继续挂
                price = self._fill_price(bar["open"], "SELL")
                portion = float(pos.__dict__.pop("_exit_portion", 1.0))
                shares = self._portion_shares(pos, portion)
                reason = pos.exit_reason or "signal"
                pos.exit_reason = ""
                if shares <= 0:
                    pending_exits.remove(code)
                    continue
                cash += self._book_sale(trades, positions, pos, price, shares,
                                        date_str, reason)
                pending_exits.remove(code)

            for order in list(pending_entries):
                code = order["code"]
                if code in positions or len(positions) >= self.max_positions:
                    continue
                bar = self._bar(prepared, code, date)
                if bar is None:
                    continue
                # 高开放弃：开盘价较信号日收盘高开超过阈值就不追
                if bar["open"] >= order["signal_close"] * (1 + self.abandon_gap_up):
                    continue
                if self._is_limit_up_open(prepared, code, date):
                    continue          # 一字涨停买不进
                price = self._fill_price(bar["open"], "BUY")
                # 止损要先定下来，风险头寸公式需要它：股数 = 风险预算 ÷ 每股风险
                if exit_policy is not None:
                    stop, take = exit_policy.initial_levels(
                        prepared[code]["ef"], bar["i"], price)
                else:
                    raw = order["stop"] if order["stop"] > 0 else bar["low"] * 0.97
                    stop, take = min(raw, price * 0.99), price * (1 + take_profit_pct)
                shares = self._entry_shares(
                    cash, positions, price, stop,
                    equity=last_equity, held_value=last_held,
                    regime=regime_map.get(date_str, "NEUTRAL"))
                if shares <= 0:
                    continue
                cost, _ = self._buy_cost(price, shares)
                if cost > cash:
                    continue
                cash -= cost
                positions[code] = Position(
                    code=code,
                    entry_date=date_str,
                    entry_price=price,
                    shares=shares,
                    cost_basis=cost,
                    stop_loss=stop,
                    take_profit=take,
                    entry_shares=shares,
                    entry_low=bar["low"],
                    highest_high=bar["high"],
                )
            pending_entries = []

            # --- 2. 盘中：检查止损止盈（用当日 high/low 判定，成交价保守取触发价）---
            for code, pos in list(positions.items()):
                bar = self._bar(prepared, code, date)
                if bar is None:
                    continue
                if pos.entry_date == date_str:
                    continue                       # T+1，当日买入不可卖
                hit, price, reason = self._intraday_exit(bar, pos)
                if not hit:
                    continue
                fill = self._fill_price(price, "SELL")
                cash += self._book_sale(trades, positions, pos, fill, pos.shares,
                                        date_str, reason)

            # --- 3. 收盘：更新持仓、产生明日订单 ---
            equity = cash
            snapshot = []
            for code, pos in positions.items():
                bar = self._bar(prepared, code, date)
                mv = (bar["close"] if bar is not None else pos.entry_price) * pos.shares
                equity += mv
                pos.bars_held += 1
                if bar is not None:
                    pos.highest_high = max(pos.highest_high, bar["high"])
                snapshot.append({"code": code, "shares": pos.shares, "market_value": round(mv, 2)})

                # 收盘信号型卖出（出场规则层 / 自定义 exit_fn / 持有到期）
                if pos.entry_date == date_str:
                    continue
                if exit_policy is not None and bar is not None:
                    ef = prepared[code]["ef"]
                    # 移动止损在收盘后上移，次日盘中才生效——不能用当日
                    # 的最高价去判当日是否已被打掉，那是未来函数。
                    pos.stop_loss, pos.take_profit = exit_policy.levels(ef, bar["i"], pos)
                    decision = exit_policy.on_close(ef, bar["i"], pos)
                    if decision is not None:
                        pos.exit_reason = decision.reason
                        pos.__dict__["_exit_portion"] = float(decision.portion)
                        if decision.portion < 1.0:
                            pos.tier_done = max(pos.tier_done,
                                                decision.tier or _tier_of(decision.reason))
                        if code not in pending_exits:
                            pending_exits.append(code)
                    continue

                reason = None
                if exit_fn is not None and bar is not None:
                    reason = exit_fn(prepared[code]["df"], date, pos)
                if reason is None and pos.bars_held >= self.max_holding_days:
                    reason = f"持有满{self.max_holding_days}日"
                if reason:
                    pos.exit_reason = reason
                    pending_exits.append(code)

            equity_values.append(equity)
            last_equity = equity
            last_held = sum(p["market_value"] for p in snapshot)
            daily_positions.append({"date": date_str, "cash": round(cash, 2),
                                    "equity": round(equity, 2), "positions": snapshot})

            # 生成明日买单（明日开盘即知为空头区间则不生成）
            if (day_i < len(calendar) - 1 and len(positions) < self.max_positions
                    and regime_map.get(
                        calendar[day_i + 1].strftime("%Y-%m-%d"), "NEUTRAL") != "BEAR"):
                signals = self._signals_on(prepared, date, signal_col)
                for code, info in signals[: self.max_entries_per_day]:
                    if code not in positions:
                        pending_entries.append(info)

        equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(calendar), name="equity")
        metrics = compute_metrics(
            equity_values,
            [{"return_pct": t.return_pct, "entry_date": t.entry_date,
              "exit_date": t.exit_date} for t in trades],
        )
        metrics["initial_capital"] = self.initial_capital
        metrics["final_equity"] = round(equity_values[-1], 2) if equity_values else 0.0
        metrics["total_return"] = (
            round(equity_values[-1] / self.initial_capital - 1, 4) if equity_values else 0.0
        )
        if regime_map:
            metrics["bear_regime_days"] = bear_days
            metrics["bear_forced_exits"] = sum(
                1 for t in trades if t.exit_reason == "空头区间清仓")
        return BacktestResult(
            trades=trades, equity_curve=equity_curve,
            metrics=metrics, daily_positions=daily_positions,
        )

    # -- 内部工具 ---------------------------------------------------------

    def _prepare(self, price_data, signal_col, stop_col, start_date, end_date) -> dict:
        """把每只股票的数据切片并预转 numpy，避免循环里反复做 pandas 索引。"""
        out = {}
        lo = pd.Timestamp(start_date) if start_date else None
        hi = pd.Timestamp(end_date) if end_date else None
        for code, df in price_data.items():
            if df is None or df.empty or signal_col not in df.columns:
                continue
            d = df
            if lo is not None:
                d = d[d.index >= lo]
            if hi is not None:
                d = d[d.index <= hi]
            if len(d) < 2:
                continue
            out[code] = {
                "df": d,
                "pos": {ts: i for i, ts in enumerate(d.index)},
                "open": d["open"].to_numpy(float),
                "high": d["high"].to_numpy(float),
                "low": d["low"].to_numpy(float),
                "close": d["close"].to_numpy(float),
                "sig": d[signal_col].fillna(False).to_numpy(bool),
                "stop": (d[stop_col].to_numpy(float) if stop_col in d.columns
                         else d["low"].to_numpy(float)),
            }
        return out

    @staticmethod
    def _build_calendar(prepared: dict) -> List[pd.Timestamp]:
        all_dates = set()
        for item in prepared.values():
            all_dates.update(item["df"].index)
        return sorted(all_dates)

    @staticmethod
    def _bar(prepared: dict, code: str, date) -> Optional[dict]:
        item = prepared.get(code)
        if item is None:
            return None
        i = item["pos"].get(date)
        if i is None:
            return None
        return {
            "i": i, "open": item["open"][i], "high": item["high"][i],
            "low": item["low"][i], "close": item["close"][i],
        }

    def _signals_on(self, prepared: dict, date, signal_col: str) -> List[tuple]:
        """返回当日触发买入信号的股票，按成交额降序。"""
        hits = []
        for code, item in prepared.items():
            i = item["pos"].get(date)
            if i is None or not item["sig"][i]:
                continue
            amount = float(item["df"]["amount"].iloc[i]) if "amount" in item["df"].columns else 0.0
            hits.append(
                (code, {"code": code, "signal_close": item["close"][i],
                        "stop": float(item["stop"][i]), "amount": amount})
            )
        hits.sort(key=lambda x: x[1]["amount"], reverse=True)
        return hits

    def _is_limit_up_open(self, prepared: dict, code: str, date) -> bool:
        """开盘一字涨停判定：开=高=低 且 较昨收涨停。"""
        item = prepared[code]
        i = item["pos"][date]
        if i == 0:
            return False
        prev_close = item["close"][i - 1]
        limit = self._limit_pct(code)
        o, h, low = item["open"][i], item["high"][i], item["low"][i]
        return (
            abs(o - h) < 1e-6 and abs(o - low) < 1e-6
            and o >= prev_close * (1 + limit) - 0.011
        )

    def _is_limit_down_open(self, prepared: dict, code: str, date) -> bool:
        item = prepared[code]
        i = item["pos"][date]
        if i == 0:
            return False
        prev_close = item["close"][i - 1]
        limit = self._limit_pct(code)
        o, h, low = item["open"][i], item["high"][i], item["low"][i]
        return (
            abs(o - h) < 1e-6 and abs(o - low) < 1e-6
            and o <= prev_close * (1 - limit) + 0.011
        )

    def _limit_pct(self, code: str) -> float:
        bt = self.cfg.get("backtest", {})
        code = str(code).zfill(6)
        if code.startswith(("300", "301")):
            return float(bt.get("limit_up_chi_next", 0.20))
        if code.startswith("688"):
            return float(bt.get("limit_up_star", 0.20))
        return float(bt.get("limit_up_pct", 0.10))

    @staticmethod
    def _intraday_exit(bar: dict, pos: Position) -> tuple:
        """盘中止损优先于止盈（同一根 K 线无法判断先后，保守取最坏情况）。

        成交价必须考虑**跳空穿越**：止损单挂在 9.50，如果次日直接低开到 8.00，
        实盘只能在 8.00 附近成交，不可能在 9.50 成交。原实现无条件按触发价
        记账，等于假设每一次跳空都能在缺口上沿接住，会系统性高估收益——
        而且跳得越狠、虚增越多，恰好在最该扣分的那几笔上给了加分。

        止盈同理取较保守的一侧：跳空高开越过止盈价时按开盘价成交（对回测
        是利好，但这就是实盘会发生的事），否则按止盈价。
        """
        open_ = bar["open"]
        if bar["low"] <= pos.stop_loss:
            # 开盘已在止损价之下 = 跳空穿越，只能按开盘价出
            return True, min(open_, pos.stop_loss), "止损"
        if bar["high"] >= pos.take_profit:
            return True, max(open_, pos.take_profit), "止盈"
        return False, 0.0, ""

    @staticmethod
    def _portion_shares(pos: Position, portion: float) -> int:
        """按比例算减仓股数，向下取整到 100 股。

        比例算出来不足一手时：若这是清仓指令（portion>=1）就全卖，
        否则放弃本次减仓——A 股没有零股卖出这回事。剩余不足 200 股时
        任何减仓都会留下零股，所以直接清掉。
        """
        if portion >= 1.0:
            return pos.shares
        want = int(pos.shares * portion // 100) * 100
        if want <= 0:
            return 0
        if pos.shares - want < 100:
            return pos.shares
        return want

    def _book_sale(self, trades: List[Trade], positions: Dict[str, Position],
                   pos: Position, price: float, shares: int,
                   date_str: str, reason: str) -> float:
        """记一笔卖出，按股数摊销成本，返回到账现金。

        分批止盈要求同一个持仓能卖多次，所以成本必须按比例摊：卖掉一半就
        转走一半 cost_basis，剩下的留在持仓里。不这么做的话第一笔减仓会
        背走全部成本，后面几笔看起来全是纯利润。
        """
        shares = min(int(shares), pos.shares)
        if shares <= 0:
            return 0.0
        proceeds, _ = self._sell_proceeds(price, shares)
        cost_part = pos.cost_basis * (shares / pos.shares) if pos.shares else 0.0
        pnl = proceeds - cost_part
        trades.append(
            Trade(
                code=pos.code, entry_date=pos.entry_date, exit_date=date_str,
                entry_price=pos.entry_price, exit_price=price, shares=shares,
                pnl=round(pnl, 2),
                return_pct=pnl / cost_part if cost_part > 0 else 0.0,
                exit_reason=reason, holding_days=pos.bars_held,
            )
        )
        pos.shares -= shares
        pos.cost_basis -= cost_part
        if pos.shares <= 0:
            positions.pop(pos.code, None)
        return proceeds

    def _fill_price(self, price: float, side: str) -> float:
        slip = price * self.slippage
        return round(price + slip if side == "BUY" else price - slip, 2)

    def _buy_cost(self, price: float, shares: int) -> tuple:
        turnover = price * shares
        fee = max(turnover * self.commission, self.commission_min) + turnover * self.transfer_fee
        return turnover + fee, fee

    def _sell_proceeds(self, price: float, shares: int) -> tuple:
        turnover = price * shares
        fee = (
            max(turnover * self.commission, self.commission_min)
            + turnover * self.stamp_tax
            + turnover * self.transfer_fee
        )
        return turnover - fee, fee

    def _position_budget(self, cash: float, positions: dict, price: float) -> float:
        """等权分配剩余仓位，不超过现金。"""
        slots_left = max(1, self.max_positions - len(positions))
        return min(cash * 0.98, cash / slots_left)

    def _entry_shares(self, cash: float, positions: dict, price: float,
                      stop: float, equity: float, held_value: float,
                      regime: str) -> int:
        """算本笔该买多少股，三种口径。

        equal（旧行为）
            剩余现金按空槽等分，与止损位无关。止损再宽也买同样的钱，
            所以每笔的真实风险敞口差异极大。

        pct（固定比例）
            每笔目标市值 = 权益 × position_pct（默认 20%），仍被择时总仓位
            上限和现金封顶。想要「单笔 20%、多头不满仓闲置」就用这个。

        risk（实盘口径）
            走 portfolio.PositionSizer：单笔亏损 ≤ 权益 × risk_per_trade
            × kelly_fraction，再被单票上限和择时总仓位上限封顶。
            这是 daily_workflow 下单计划用的同一个公式。

        Args:
            equity / held_value: 昨收的权益与持仓市值，用作仓位上限的分母。
            regime: 当日开盘可知的活跃市值区间。

        Returns:
            股数，100 的整数倍。
        """
        if self.sizing == "pct":
            from zhixing_quant.portfolio.sizer import regime_cap

            eq = equity if equity > 0 else cash
            budget = eq * self.position_pct
            if self.apply_regime_cap:
                cap = regime_cap(self.cfg, regime)
                room = max(0.0, cap - (held_value / eq if eq > 0 else 0.0))
                budget = min(budget, eq * room)
            affordable = int(cash * 0.98 // (price * 100)) * 100
            want = int(budget // (price * 100)) * 100
            return max(0, min(want, affordable))

        if self.sizing != "risk":
            budget = self._position_budget(cash, positions, price)
            return int(budget // (price * 100)) * 100

        from zhixing_quant.portfolio.sizer import (PositionSizer, per_position_cap,
                                                   regime_cap)

        equity = equity if equity > 0 else cash
        room = 1.0
        if self.apply_regime_cap:
            cap = regime_cap(self.cfg, regime)
            room = max(0.0, cap - (held_value / equity if equity > 0 else 0.0))
            if room <= 0:
                return 0

        sizer = PositionSizer(risk_pct=self.risk_per_trade,
                              kelly_fraction=self.kelly_fraction)
        shares = sizer.size(price, stop, equity, regime_max_pct=room,
                            per_position_pct=per_position_cap(self.cfg, equity))
        # 现金约束：风险头寸算出来再多也不能超过手头的钱
        affordable = int(cash * 0.98 // (price * 100)) * 100
        return max(0, min(shares, affordable))
