"""参数校准：样本内外切分 / 网格扫描 / 滚动前进验证。

为什么需要这一层
----------------

界面上一堆参数标着 `[CALIBRATE]`，写着「本文给的只是初始猜测值，必须通过
回测确定」。但此前只能一次跑一组参数、一个区间、看一个总收益数字。

在**同一段历史**上反复试参数、留下收益最高的那组，就是过拟合。这件事在
量化里的危害不是「结果偏乐观一点」，而是**实盘表现会和回测完全脱节**：
你调出来的是这段历史的噪声形状，不是策略的边际。

本模块提供三件武器，按严格程度递增：

    split_is_oos   把区间切成样本内 / 样本外，同一组参数两边都跑，看衰减
    sweep          在样本内扫参数网格，再把最好的几组拿到样本外验证
    walk_forward   滚动：每段只用之前的数据定参数，在后面那段上交易

判断一组参数能不能用，看三件事（缺一不可）
------------------------------------------

1. **样本外没有大幅衰减。** 样本内年化 30%、样本外 -5%，说明拟合的是噪声。
2. **参数曲面是平的，不是尖的。** 最优点旁边的参数也得差不多好。
   一个孤立尖峰意味着换一段数据它就塌了。见 `plateau_score`。
3. **交易笔数够。** 20 笔的胜率说明不了任何问题，随机都能凑出好看的数字。

这三条只要有一条不满足，那组参数就不该上实盘——哪怕回测收益再好看。
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

MIN_TRADES = 30          # 低于这个笔数的结论一律不可信
BAD_DECAY = 0.5          # 样本外收益跌掉样本内一半以上 = 过拟合警报


# ---------------------------------------------------------------------------
# 区间
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Period:
    """一段回测区间。"""
    start: str
    end: str
    label: str = ""

    def describe(self) -> str:
        return f"{self.label}{self.start}~{self.end}"


def split_is_oos(start: str, end: str, oos_frac: float = 0.3
                 ) -> Tuple[Period, Period]:
    """把区间按时间切成样本内 / 样本外。

    **必须按时间切，不能随机切。** 随机抽样会把未来的信息混进训练集——
    同一只票相邻两天高度相关，随机分组等于让样本内偷看到样本外。

    Args:
        start / end: YYYYMMDD。
        oos_frac: 样本外占比，默认后 30%。

    Returns:
        (样本内, 样本外)
    """
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    if hi <= lo:
        raise ValueError(f"结束日 {end} 不晚于开始日 {start}")
    if not 0.05 <= oos_frac <= 0.7:
        raise ValueError(f"oos_frac={oos_frac} 不合理，取 0.05~0.7")
    cut = lo + (hi - lo) * (1.0 - oos_frac)
    return (Period(start, cut.strftime("%Y%m%d"), "样本内 "),
            Period((cut + pd.Timedelta(days=1)).strftime("%Y%m%d"),
                   end, "样本外 "))


def rolling_windows(start: str, end: str, train_months: int = 12,
                    test_months: int = 3) -> List[Tuple[Period, Period]]:
    """滚动前进的 (训练段, 测试段) 序列。

    每一段的参数只用**该段之前**的数据定，然后在后面那段上交易，
    段与段不重叠。这是最接近真实使用方式的验证：实盘里你也只能用
    过去的数据定参数。

    Args:
        start / end: YYYYMMDD。
        train_months: 每次用多少个月定参数。
        test_months: 定完在后面多少个月上交易。

    Returns:
        [(训练段, 测试段), ...]。窗口放不下时返回空列表。
    """
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    out: List[Tuple[Period, Period]] = []
    train_lo = lo
    while True:
        train_hi = train_lo + pd.DateOffset(months=train_months)
        test_hi = train_hi + pd.DateOffset(months=test_months)
        if test_hi > hi:
            break
        out.append((
            Period(train_lo.strftime("%Y%m%d"), train_hi.strftime("%Y%m%d"),
                   "训练 "),
            Period((train_hi + pd.Timedelta(days=1)).strftime("%Y%m%d"),
                   test_hi.strftime("%Y%m%d"), "测试 "),
        ))
        train_lo = train_lo + pd.DateOffset(months=test_months)
    return out


# ---------------------------------------------------------------------------
# 参数网格
# ---------------------------------------------------------------------------

def grid_combos(grid: Dict[str, Sequence]) -> List[Dict[str, object]]:
    """笛卡尔积展开参数网格。

    Args:
        grid: {配置路径: [取值, ...]}，路径用点号，如 "exits.b2.stop.pct"。

    Returns:
        [{路径: 值}, ...]
    """
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, vals))
            for vals in itertools.product(*(list(grid[k]) for k in keys))]


def apply_params(cfg: dict, params: Dict[str, object]) -> dict:
    """在配置深拷贝上写入点号路径的参数值。"""
    out = copy.deepcopy(cfg)
    for path, value in params.items():
        node = out
        parts = path.split(".")
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
    return out


def config_signature(cfg: dict, strategy: str) -> tuple:
    """一份配置的**有效**签名：签名相同 = 回测结果必然相同。

    网格里经常有「条件参数」：`stop.kind=entry_low` 时 `stop.pct` 根本不被
    读取，但笛卡尔积照样会为每个 pct 值生成一组。实测 2×4 的网格里有
    4 组是完全重复的，白跑一半。

    所以签名不用原始配置，而用**解析后的 ExitSpec / EntrySpec**——
    它们只保留真正生效的字段。exits / entries 之外的配置直接进 JSON。
    """
    import json

    from zhixing_quant.backtest.exits import spec_from_config
    from zhixing_quant.portfolio.sizer import entry_spec_from_config

    rest = {k: v for k, v in cfg.items() if k not in ("exits", "entries")}
    return (
        _canonical_exit(spec_from_config(cfg, strategy)),
        repr(entry_spec_from_config(cfg, strategy)),
        json.dumps(rest, sort_keys=True, default=str),
    )


def _canonical_exit(spec) -> tuple:
    """只保留**当前 kind 下真正会被读到**的字段。

    dataclass 会把所有字段都记着：`StopSpec(kind='entry_low', pct=0.04)` 和
    `StopSpec(kind='entry_low', pct=0.10)` 的 repr 不同，但引擎跑出来一模一样，
    因为 kind=entry_low 那条分支根本不看 pct。直接比 repr 会漏掉这类重复。
    """
    s, t, tp, ts = spec.stop, spec.trail, spec.take_profit, spec.time_stop

    stop = (s.kind, s.buffer, s.cap_pct)
    if s.kind == "pct":
        stop += (s.pct,)
    elif s.kind == "atr":
        stop += (s.atr_mult, s.atr_window)

    trail = (t.kind,)
    if t.kind != "none":
        trail += (t.activate_profit,)
        if t.kind == "pct":
            trail += (t.pct,)
        elif t.kind == "chandelier":
            trail += (t.atr_mult, t.atr_window)

    take = (tp.kind,)
    if tp.kind == "pct":
        take += (tp.pct,)
    elif tp.kind == "tiered":
        take += (tuple(sorted(tp.tiers.items())),)
    elif tp.kind == "price_tiers":
        take += (tuple(tp.price_tiers),)

    time_stop = (ts.max_holding_days, ts.no_progress_days)
    if ts.no_progress_days:
        time_stop += (ts.min_progress,)

    defense = (spec.defense_ladder,)
    if spec.defense_ladder:
        defense += (tuple(spec.defense_rules), spec.defense_s1_portion,
                    spec.defense_distribution_portion)

    return (stop, trail, take, time_stop, defense, spec.break_yellow_line,
            spec.white_line_break, spec.profit_to_loss,
            spec.close_below_prev_low, spec.strategy_exit)


# ---------------------------------------------------------------------------
# 目标函数
# ---------------------------------------------------------------------------

def objective(metrics: dict, kind: str = "calmar") -> float:
    """把一次回测压成一个可排序的数字。

    默认用卡尔玛（年化 / 最大回撤）而不是总收益：只看收益会选出
    「年化 30% 但回撤 50%」这种实盘拿不住的参数。

    笔数不足时直接给 -inf——20 笔的胜率说明不了任何问题，
    让它参与排名只会让噪声组合冒头。
    """
    if int(metrics.get("total_trades", 0)) < MIN_TRADES:
        return float("-inf")
    if kind == "return":
        return float(metrics.get("total_return", 0.0))
    if kind == "sharpe":
        return float(metrics.get("sharpe", 0.0))
    if kind == "return_over_dd":
        dd = abs(float(metrics.get("max_drawdown", 0.0)))
        return float(metrics.get("total_return", 0.0)) / dd if dd > 1e-9 else 0.0
    return float(metrics.get("calmar", 0.0))


OBJECTIVES = {
    "calmar": "卡尔玛（年化/最大回撤）",
    "sharpe": "夏普",
    "return_over_dd": "总收益/最大回撤",
    "return": "总收益（最容易过拟合，慎用）",
}


# ---------------------------------------------------------------------------
# 跑一组
# ---------------------------------------------------------------------------

def _metrics_row(metrics: dict) -> dict:
    return {
        "总收益": round(float(metrics.get("total_return", 0.0)), 4),
        "年化": round(float(metrics.get("annualized_return", 0.0)), 4),
        "最大回撤": round(float(metrics.get("max_drawdown", 0.0)), 4),
        "夏普": round(float(metrics.get("sharpe", 0.0)), 3),
        "卡尔玛": round(float(metrics.get("calmar", 0.0)), 3),
        "胜率": round(float(metrics.get("win_rate", 0.0)), 3),
        "笔数": int(metrics.get("total_trades", 0)),
    }


def run_period(cfg: dict, strategy: str, period: Period, spec=None) -> dict:
    """在一段区间上跑一次回测，返回指标行。"""
    from zhixing_quant.backtest.runner import run_backtest

    run = run_backtest(cfg, strategy, period.start, period.end, spec=spec,
                       universe_as_of=period.start)
    return _metrics_row(run.metrics)


def run_is_oos(cfg: dict, strategy: str, start: str, end: str, spec=None,
               oos_frac: float = 0.3, progress=None) -> pd.DataFrame:
    """同一组参数，样本内外各跑一次，看衰减。

    Returns:
        两行的 DataFrame（样本内 / 样本外）+ 一列「衰减」。
    """
    is_p, oos_p = split_is_oos(start, end, oos_frac)
    rows = []
    for i, p in enumerate((is_p, oos_p)):
        if progress:
            progress(i, 2, p.describe())
        rows.append({"区间": p.describe(), **run_period(cfg, strategy, p, spec)})
    df = pd.DataFrame(rows)
    return df


# ---------------------------------------------------------------------------
# 网格扫描
# ---------------------------------------------------------------------------

@dataclass
class SweepResult:
    """扫描结果。"""
    table: pd.DataFrame                    # 每组参数一行，含样本内/外指标
    grid: Dict[str, Sequence] = field(default_factory=dict)
    objective_kind: str = "calmar"
    warnings: List[str] = field(default_factory=list)


def sweep(cfg: dict, strategy: str, grid: Dict[str, Sequence], start: str,
          end: str, spec=None, oos_frac: float = 0.3, top_n: int = 5,
          objective_kind: str = "calmar", max_combos: int = 60,
          progress: Optional[Callable] = None) -> SweepResult:
    """样本内扫参数网格，再把最好的几组拿到样本外验证。

    流程刻意分两步，而不是直接在全区间上找最优：**在哪段数据上选出来的
    参数，那段数据就不能再用来评价它**。样本外这一步才是真正的考试。

    Args:
        cfg: 基础配置。
        grid: {配置路径: [取值]}。
        start / end: 总区间。
        spec: UniverseSpec 建池条件。
        oos_frac: 样本外占比。
        top_n: 样本内最好的几组拿去样本外验证。
        objective_kind: 排序目标，见 OBJECTIVES。
        max_combos: 组合数上限，超了直接拒绝——网格越大越容易撞出
            一组「看起来很好」的噪声。
        progress: 回调 (已完成, 总数, 说明)。

    Returns:
        SweepResult
    """
    combos = grid_combos(grid)
    if len(combos) > max_combos:
        raise ValueError(
            f"{len(combos)} 组参数超过上限 {max_combos}。网格越大，"
            "撞出一组「看起来很好」的噪声的概率越高。先缩小范围，"
            "或者提高 max_combos 但要清楚自己在做什么。")

    is_p, oos_p = split_is_oos(start, end, oos_frac)
    warnings: List[str] = []
    total = len(combos) + min(top_n, len(combos))
    rows: List[dict] = []

    seen: Dict[tuple, dict] = {}       # 有效签名 -> 已算出的指标
    reused = 0
    for i, params in enumerate(combos):
        if progress:
            progress(i, total, f"样本内 {i + 1}/{len(combos)}")
        local = apply_params(cfg, params)
        try:
            sig = config_signature(local, strategy)
        except Exception:
            sig = None
        if sig is not None and sig in seen:
            # 条件参数造成的重复组合，直接复用，不重跑
            m = seen[sig]
            reused += 1
        else:
            try:
                m = run_period(local, strategy, is_p, spec)
            except Exception as exc:
                rows.append({**_label(params),
                             "错误": f"{type(exc).__name__}: {exc}"})
                continue
            if sig is not None:
                seen[sig] = m
        rows.append({**_label(params), **{f"内_{k}": v for k, v in m.items()},
                     "目标": round(objective(_unrow(m), objective_kind), 4)})

    if reused:
        warnings.append(
            f"{reused}/{len(combos)} 组参数的**有效配置**与其他组完全相同"
            "（多半是条件参数——比如 stop.kind=entry_low 时 stop.pct 根本不被读取），"
            "已复用结果不重跑。这也说明这些参数在当前设置下是无效的。")

    table = pd.DataFrame(rows)
    if table.empty or "目标" not in table.columns:
        warnings.append("样本内一组都没跑成，检查参数路径是否写对。")
        return SweepResult(table=table, grid=grid, objective_kind=objective_kind,
                           warnings=warnings)

    valid = table[table["目标"] > float("-inf")]
    if valid.empty:
        warnings.append(
            f"所有参数组的交易笔数都不足 {MIN_TRADES} 笔，排名没有意义。"
            "放宽入场条件或者拉长区间。")
        return SweepResult(table=table.sort_values("目标", ascending=False),
                           grid=grid, objective_kind=objective_kind,
                           warnings=warnings)

    table = table.sort_values("目标", ascending=False).reset_index(drop=True)
    best_idx = list(table.index[:top_n])

    for j, idx in enumerate(best_idx):
        if progress:
            progress(len(combos) + j, total, f"样本外 {j + 1}/{len(best_idx)}")
        params = {k: table.loc[idx, _col(k)] for k in grid}
        local = apply_params(cfg, params)
        try:
            m = run_period(local, strategy, oos_p, spec)
        except Exception:
            continue
        for k, v in m.items():
            table.loc[idx, f"外_{k}"] = v

    if "外_总收益" in table.columns:
        table["衰减"] = table["外_总收益"] - table["内_总收益"]
    warnings.extend(_sweep_warnings(table, is_p, oos_p))
    return SweepResult(table=table, grid=grid, objective_kind=objective_kind,
                       warnings=warnings)


def _label(params: Dict[str, object]) -> dict:
    return {_col(k): v for k, v in params.items()}


def _col(path: str) -> str:
    """配置路径压成短列名：exits.b2.stop.pct -> stop.pct。"""
    parts = path.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else path


def _unrow(row: dict) -> dict:
    """指标行反解成 metrics 形状，供 objective 复用。"""
    return {"total_return": row["总收益"], "annualized_return": row["年化"],
            "max_drawdown": row["最大回撤"], "sharpe": row["夏普"],
            "calmar": row["卡尔玛"], "win_rate": row["胜率"],
            "total_trades": row["笔数"]}


def _sweep_warnings(table: pd.DataFrame, is_p: Period, oos_p: Period
                    ) -> List[str]:
    out: List[str] = []
    if "外_总收益" not in table.columns:
        return out
    tested = table.dropna(subset=["外_总收益"])
    if tested.empty:
        return out

    best = tested.iloc[0]
    inn, ext = float(best["内_总收益"]), float(best["外_总收益"])
    if inn > 0 and ext < inn * (1 - BAD_DECAY):
        out.append(
            f"⚠️ 样本内最优那组在样本外大幅衰减："
            f"{inn:.1%} → {ext:.1%}。这是过拟合的典型症状——"
            "调出来的是这段历史的噪声形状，不是策略的边际。")
    if ext < 0 < inn:
        out.append("样本内赚钱、样本外亏钱。这组参数不该上实盘。")

    if len(tested) >= 3:
        rank_in = tested["内_总收益"].rank(ascending=False)
        rank_out = tested["外_总收益"].rank(ascending=False)
        corr = rank_in.corr(rank_out)
        if np.isfinite(corr) and corr < 0:
            out.append(
                f"样本内外的参数排名负相关（{corr:.2f}）——样本内越好的在样本外"
                "反而越差。说明这个参数在这段数据上没有稳定方向，别调它。")

    low = int((table["笔数"] < MIN_TRADES).sum()) if "笔数" in table else 0
    if low:
        out.append(f"{low} 组参数的样本内笔数不足 {MIN_TRADES}，已排除出排名。")

    out.append(f"样本内 {is_p.start}~{is_p.end}，样本外 {oos_p.start}~{oos_p.end}。"
               "只有「样本外不衰减 + 参数曲面平坦 + 笔数足够」三条都满足，"
               "这组参数才值得上实盘。")
    return out


# ---------------------------------------------------------------------------
# 参数平坦度
# ---------------------------------------------------------------------------

def plateau_score(table: pd.DataFrame, param_col: str,
                  value_col: str = "内_总收益") -> float:
    """参数曲面有多平：相邻取值之间结果的稳定程度，0~1。

    一个稳健的参数应该有**平台**而不是**尖峰**：最优点旁边的取值也差不多好。
    孤立尖峰意味着换一段数据它就塌了——那是噪声，不是边际。

    算法：按参数值排序，算相邻结果差的绝对值均值，用结果的极差归一化，
    再取 1 减去它。全部相同返回 1.0，剧烈震荡趋近 0。

    Returns:
        0~1，越大越平坦。低于 0.5 的参数不要采信最优点。
    """
    if table is None or param_col not in table.columns:
        return 0.0
    if value_col not in table.columns:
        return 0.0
    sub = table[[param_col, value_col]].dropna()
    if len(sub) < 3:
        return 0.0
    try:
        sub = sub.sort_values(param_col)
    except TypeError:
        return 0.0
    v = sub[value_col].to_numpy(float)
    span = float(v.max() - v.min())
    if span <= 1e-12:
        return 1.0
    jitter = float(np.abs(np.diff(v)).mean())
    return float(max(0.0, 1.0 - jitter / span))


# ---------------------------------------------------------------------------
# 滚动前进
# ---------------------------------------------------------------------------

def walk_forward(cfg: dict, strategy: str, grid: Dict[str, Sequence],
                 start: str, end: str, spec=None, train_months: int = 12,
                 test_months: int = 3, objective_kind: str = "calmar",
                 max_combos: int = 24,
                 progress: Optional[Callable] = None) -> pd.DataFrame:
    """滚动前进验证：每段只用之前的数据定参数，在后面那段上交易。

    这是最接近真实使用方式的验证——实盘里你也只能用过去的数据定参数。
    把各测试段的收益串起来，就是「如果我每季度重新调一次参」的真实曲线。

    Args:
        train_months / test_months: 训练段和测试段长度（月）。
        max_combos: 每个窗口的网格上限。窗口多，网格要小，否则跑不完。

    Returns:
        一行一个窗口：训练段 / 测试段 / 选中的参数 / 测试段各项指标。
        最后一行是所有测试段的汇总。
    """
    combos = grid_combos(grid)
    if len(combos) > max_combos:
        raise ValueError(f"{len(combos)} 组参数超过每窗口上限 {max_combos}。"
                         "滚动验证要跑 窗口数 × 组合数 次回测，先缩小网格。")
    windows = rolling_windows(start, end, train_months, test_months)
    if not windows:
        raise ValueError(
            f"{start}~{end} 放不下一个 {train_months}+{test_months} 个月的窗口。"
            "拉长区间，或者缩短 train_months / test_months。")

    total = len(windows) * (len(combos) + 1)
    done = 0
    rows: List[dict] = []

    for w, (train, test) in enumerate(windows, start=1):
        best, best_obj, best_params = None, float("-inf"), {}
        for params in combos:
            if progress:
                progress(done, total, f"窗口{w}/{len(windows)} 训练")
            done += 1
            try:
                m = run_period(apply_params(cfg, params), strategy, train, spec)
            except Exception:
                continue
            o = objective(_unrow(m), objective_kind)
            if o > best_obj:
                best, best_obj, best_params = m, o, params

        if progress:
            progress(done, total, f"窗口{w}/{len(windows)} 测试")
        done += 1
        if best is None:
            rows.append({"训练段": train.describe(), "测试段": test.describe(),
                         "参数": "无可用组合"})
            continue
        try:
            out_m = run_period(apply_params(cfg, best_params), strategy, test, spec)
        except Exception as exc:
            rows.append({"训练段": train.describe(), "测试段": test.describe(),
                         "参数": _params_text(best_params),
                         "错误": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({
            "训练段": train.describe(), "测试段": test.describe(),
            "参数": _params_text(best_params),
            "训练_总收益": best["总收益"], **out_m,
        })

    df = pd.DataFrame(rows)
    tested = df.dropna(subset=["总收益"]) if "总收益" in df.columns else df.iloc[:0]
    if not tested.empty:
        # 各测试段串联：这是「每季度重调一次参」的真实累计收益
        compounded = float(np.prod(1.0 + tested["总收益"].to_numpy(float)) - 1.0)
        df.loc[len(df)] = {
            "训练段": "——", "测试段": f"合计 {len(tested)} 段",
            "参数": "串联各测试段",
            "总收益": round(compounded, 4),
            "最大回撤": round(float(tested["最大回撤"].max()), 4),
            "胜率": round(float(tested["胜率"].mean()), 3),
            "笔数": int(tested["笔数"].sum()),
        }
    return df


def _params_text(params: Dict[str, object]) -> str:
    return " ".join(f"{_col(k)}={v}" for k, v in params.items()) or "默认"


def walk_forward_summary(df: pd.DataFrame) -> List[str]:
    """把滚动验证的结果翻译成结论。"""
    if df is None or df.empty or "总收益" not in df.columns:
        return []
    tested = df[df["训练段"] != "——"].dropna(subset=["总收益"])
    if tested.empty:
        return ["没有任何窗口跑成功。"]
    out = []
    wins = int((tested["总收益"] > 0).sum())
    out.append(f"{len(tested)} 个测试段里 {wins} 段赚钱"
               f"（{wins / len(tested):.0%}）。")
    if wins / len(tested) < 0.5:
        out.append("过半测试段亏钱——这套参数选法没有稳定的样本外表现，"
                   "不要拿去实盘。")
    chosen = tested["参数"].nunique()
    if chosen == 1:
        out.append("所有窗口都选中了同一组参数，说明这个参数确实稳定。")
    elif chosen >= len(tested) * 0.8:
        out.append(f"{len(tested)} 个窗口选出了 {chosen} 组不同参数——"
                   "每次重调都换一套，说明选出来的是噪声而不是规律。")
    return out
