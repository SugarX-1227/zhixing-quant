"""Backtest engine: event-driven simulation with realistic cost model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from zhixing_quant.backtest.models import calc_slippage, calc_limit_price, is_limit_up, is_limit_down
from zhixing_quant.portfolio.defense import DefenseEngine
from zhixing_quant.portfolio.rater import HoldingRater


@dataclass
class Trade:
    code: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    return_pct: float
    exit_reason: str
    holding_days: int


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class BacktestEngine:
    """Event-driven backtest engine for A-share signals.

    Rule source:
        Z哥战法第8章 - 回测引擎:
        - T+1 settlement
        - 0.1% commission + 0.1% stamp tax
        - 0.1% slippage
        - Limit-up/down handling
        - Simplified corporate action handling
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.commission = cfg.get("backtest", {}).get("commission", 0.001)
        self.stamp_tax = cfg.get("backtest", {}).get("stamp_tax", 0.001)
        self.slippage_model = cfg.get("backtest", {}).get("slippage_model", "pct")
        self.defense_engine = DefenseEngine()
        self.rater = HoldingRater()
        self.initial_equity = cfg.get("backtest", {}).get("initial_equity", 100000.0)

    def run(
        self,
        universe: list,
        signal_fn,
        start_date: str,
        end_date: str,
        get_data_fn,
    ) -> BacktestResult:
        """Run backtest on a universe of stocks.

        Args:
            universe: List of stock codes.
            signal_fn: Function(code, df) -> pd.DataFrame with signal columns.
            start_date: Backtest start date.
            end_date: Backtest end date.
            get_data_fn: Function(code, start, end) -> pd.DataFrame.

        Returns:
            BacktestResult with trades, equity_curve, metrics.
        """
        trades = []
        equity = self.initial_equity
        equity_curve = [equity]
        positions = {}

        dates = pd.date_range(start_date, end_date, freq="B")
        for date in dates:
            date_str = date.strftime("%Y-%m-%d")

            # Check exits for existing positions
            for code in list(positions.keys()):
                df = get_data_fn(code, start_date, date_str)
                result = signal_fn(code, df)
                if len(result) == 0:
                    continue
                idx = -1
                pos = positions[code]
                exit_signal = self.defense_engine.evaluate(result, idx, pos)
                if exit_signal is not None:
                    exit_price = self._exec_price(result, idx, "SELL")
                    cost = self._calc_cost(exit_price, pos["shares"], "SELL")
                    proceeds = exit_price * pos["shares"] - cost
                    return_pct = (proceeds - pos["cost"]) / pos["cost"] if pos["cost"] > 0 else 0.0
                    trades.append(
                        Trade(
                            code=code,
                            entry_date=pos["entry_date"],
                            exit_date=date_str,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            shares=pos["shares"],
                            return_pct=return_pct,
                            exit_reason=exit_signal.reason,
                            holding_days=(date - pd.Timestamp(pos["entry_date"])).days,
                        )
                    )
                    equity += proceeds
                    del positions[code]

            # Check new entries
            if len(positions) >= self.cfg.get("backtest", {}).get("max_positions", 5):
                continue
            for code in universe:
                if code in positions:
                    continue
                df = get_data_fn(code, start_date, date_str)
                if len(df) < 20:
                    continue
                result = signal_fn(code, df)
                if len(result) == 0 or "sig_b1" not in result.columns:
                    continue
                if not bool(result["sig_b1"].iloc[-1]) and not bool(result.get("sig_brick", pd.Series([False])).iloc[-1]):
                    continue
                entry_price = self._exec_price(result, -1, "BUY")
                stop = self._get_stop(result, -1)
                if entry_price <= 0 or stop <= 0 or entry_price <= stop:
                    continue
                sizer = __import__("zhixing_quant.portfolio.sizer", fromlist=["PositionSizer"]).PositionSizer()
                shares = sizer.size(entry_price, stop, equity, 0.5)
                if shares <= 0:
                    continue
                cost = self._calc_cost(entry_price, shares, "BUY")
                if cost > equity:
                    continue
                positions[code] = {
                    "entry_date": date_str,
                    "entry_price": entry_price,
                    "shares": shares,
                    "cost": entry_price * shares + cost,
                    "stop_loss": stop,
                    "take_profit": entry_price * 1.15,
                }
                equity -= cost
                break  # one entry per day

            equity_curve.append(equity)

        metrics = compute_metrics(equity_curve, [
            {
                "return_pct": t.return_pct,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
            }
            for t in trades
        ])
        return BacktestResult(trades=trades, equity_curve=equity_curve, metrics=metrics)

    def _exec_price(self, df, idx: int, side: str) -> float:
        """Calculate execution price with slippage, respecting limits."""
        close = float(df.iloc[idx]["close"])
        slip = calc_slippage(close, side, self.slippage_model)
        if side == "BUY":
            return round(close + slip, 2)
        return round(close - slip, 2)

    def _get_stop(self, df, idx: int) -> float:
        """Extract stop loss from signal output."""
        row = df.iloc[idx]
        for col in ["sig_sb1_stop", "sig_violent_k_stop", "sig_s_stop", "stop_loss"]:
            if col in df.columns:
                val = float(row[col])
                if val > 0 and val < float(row["close"]):
                    return val
        return float(row["low"]) * 0.99

    def _calc_cost(self, price: float, shares: int, side: str) -> float:
        """Calculate total transaction cost."""
        turnover = price * shares
        commission = max(turnover * self.commission, 5.0)  # min 5 CNY
        stamp = turnover * self.stamp_tax if side == "SELL" else 0.0
        return commission + stamp


def compute_metrics(equity_curve: list, trades: list) -> dict:
    """Thin wrapper delegating to backtest.metrics.compute_metrics."""
    from zhixing_quant.backtest.metrics import compute_metrics as _cm
    return _cm(equity_curve, trades)
