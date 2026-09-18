"""出场规则消融分析：逐条关掉，看每条规则各自贡献了多少。

为什么需要它
------------

B2 现在叠了 9 条出场规则：入场低点止损、白线移动止损、两档涨幅止盈、
破黄线清仓、盈转亏、低低走人、2 日不拉升、防守 S1 减半、防守出货减半。

9 条规则互相影响——一条先触发，后面几条就没机会了。靠看回测总收益
根本判断不出哪条在帮忙、哪条在添乱，加一条改一个数字，越改越乱，
最后只剩「感觉」。

消融分析把这件事变成可测量的：**保持其余全部不变，只关掉一条**，
看指标怎么动。

    关掉后收益变高  → 这条规则在亏钱，应该去掉或放宽
    关掉后收益变低  → 这条规则在赚钱，留着
    关掉后几乎不变  → 这条规则没触发几次，是摆设

这是逐一剔除（leave-one-out），回答的是「这条规则的**边际**贡献」。
注意它不等于「这条规则单独用有多好」——规则之间有替代关系，
两条都能救同一笔单子时，各自的边际贡献都会显得很小。
所以还提供 `only_one=True` 的单开模式做交叉验证。

⚠️ 过拟合警告
-------------

消融是**诊断**工具，不是调参工具。在同一段历史上反复删规则、留下
看起来最好的组合，就是在拟合噪声。正确用法：

1. 在样本内（比如前 70% 的时间）跑消融，找出明显在亏钱的规则；
2. 只删掉那些**边际贡献明显为负、且有合理解释**的；
3. 在样本外（后 30%）确认结论仍然成立。

`split_date` 参数就是为第 3 步准备的。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

from zhixing_quant.backtest.exits import spec_from_config


# ---------------------------------------------------------------------------
# 规则开关：每条规则怎么关掉，以及它当前是否启用
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleSwitch:
    """一条可开关的出场规则。

    Attributes:
        key: 内部标识。
        label: 中文名，报告里显示。
        off: {配置路径: 关闭值}，路径相对 `exits.<战法>`。
        active: (ExitSpec) -> bool，这条规则当前是否启用。不启用就不用测。
    """
    key: str
    label: str
    off: Dict[str, object]
    active: Callable[[object], bool]


SWITCHES: List[RuleSwitch] = [
    RuleSwitch("stop", "初始止损", {"stop.kind": "none"},
               lambda s: s.stop.kind != "none"),
    RuleSwitch("trailing", "移动止损", {"trailing.kind": "none"},
               lambda s: s.trail.kind != "none"),
    RuleSwitch("take_profit", "止盈", {"take_profit.kind": "none"},
               lambda s: s.take_profit.kind != "none"),
    RuleSwitch("break_yellow_line", "跌破黄线清仓", {"break_yellow_line": False},
               lambda s: bool(s.break_yellow_line)),
    RuleSwitch("white_line_break", "跌破白线减仓", {"white_line_break": 0.0},
               lambda s: s.white_line_break > 0),
    RuleSwitch("profit_to_loss", "盈转亏清仓", {"profit_to_loss": 0.0},
               lambda s: s.profit_to_loss > 0),
    RuleSwitch("close_below_prev_low", "低低走人", {"close_below_prev_low": 0.0},
               lambda s: s.close_below_prev_low > 0),
    RuleSwitch("no_progress", "N日不拉升清仓",
               {"time_stop.no_progress_days": 0},
               lambda s: s.time_stop.no_progress_days > 0),
    RuleSwitch("max_holding", "最长持有天数",
               {"time_stop.max_holding_days": 0},
               lambda s: s.time_stop.max_holding_days > 0),
    RuleSwitch("defense_ladder", "防守阶梯", {"defense_ladder": False},
               lambda s: bool(s.defense_ladder)),
]


def active_switches(cfg: dict, strategy: str) -> List[RuleSwitch]:
    """该战法当前实际启用了哪几条规则。"""
    spec = spec_from_config(cfg, strategy)
    return [sw for sw in SWITCHES if sw.active(spec)]


def _apply(cfg: dict, strategy: str, overrides: Dict[str, object]) -> dict:
    """把 `exits.<战法>.<路径>` 的覆盖值写进配置的深拷贝。"""
    out = copy.deepcopy(cfg)
    node = out.setdefault("exits", {}).setdefault(strategy, {})
    for path, value in overrides.items():
        cur = node
        parts = path.split(".")
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

METRIC_COLS = ("总收益", "年化", "最大回撤", "夏普", "胜率", "盈亏比", "笔数")


def _row(metrics: dict) -> dict:
    return {
        "总收益": float(metrics.get("total_return", 0.0)),
        "年化": float(metrics.get("annualized_return", 0.0)),
        "最大回撤": float(metrics.get("max_drawdown", 0.0)),
        "夏普": float(metrics.get("sharpe", 0.0)),
        "胜率": float(metrics.get("win_rate", 0.0)),
        "盈亏比": float(metrics.get("profit_loss_ratio", 0.0)),
        "笔数": int(metrics.get("total_trades", 0)),
    }


def ablate_exits(
    cfg: dict,
    strategy: str,
    start: str,
    end: str,
    spec=None,
    universe_as_of: Optional[str] = None,
    only_one: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    """逐条关掉出场规则，报告每条的边际贡献。

    Args:
        cfg: 配置（已套用界面上的参数覆盖）。
        strategy: 战法名。
        start / end: YYYYMMDD。
        spec: UniverseSpec 建池条件。
        universe_as_of: 建池基准日，默认取 start。
        only_one: False（默认）逐一**关掉**一条，看边际贡献；
            True 则反过来，基线关掉全部规则、逐一**只开**一条，
            看单条规则自己的效果。两种视角一起看才不会被替代关系骗到。
        progress: 回调 (已完成, 总数, 当前规则名)。

    Returns:
        DataFrame，一行一条规则，含关掉后的各项指标和相对完整配置的差值，
        以及一列「判定」。第一行是完整配置的基线。
    """
    from zhixing_quant.backtest.runner import run_backtest

    switches = active_switches(cfg, strategy)
    if not switches:
        return pd.DataFrame(columns=["规则", *METRIC_COLS])

    all_off = {}
    for sw in switches:
        all_off.update(sw.off)

    base_cfg = _apply(cfg, strategy, all_off) if only_one else cfg
    base_label = "全部关闭（基线）" if only_one else "完整配置（基线）"

    total = len(switches) + 1
    rows: List[dict] = []

    def run(local_cfg, label):
        r = run_backtest(local_cfg, strategy, start, end, spec=spec,
                         universe_as_of=universe_as_of or start)
        return {"规则": label, **_row(r.metrics)}

    if progress:
        progress(0, total, base_label)
    base = run(base_cfg, base_label)
    rows.append(base)

    for i, sw in enumerate(switches, start=1):
        if progress:
            progress(i, total, sw.label)
        if only_one:
            # 基线是全关，这里把这一条单独打开 = 从全关里去掉它的 off
            local = dict(all_off)
            for path in sw.off:
                local.pop(path, None)
            label = f"只开：{sw.label}"
            local_cfg = _apply(cfg, strategy, local)
        else:
            label = f"关掉：{sw.label}"
            local_cfg = _apply(cfg, strategy, sw.off)
        try:
            rows.append(run(local_cfg, label))
        except Exception as exc:
            rows.append({"规则": label, **{c: float("nan") for c in METRIC_COLS},
                         "错误": f"{type(exc).__name__}: {exc}"})

    df = pd.DataFrame(rows)
    for col in ("总收益", "最大回撤", "夏普"):
        df[f"Δ{col}"] = df[col] - base[col]
    df["Δ笔数"] = df["笔数"] - base["笔数"]
    df["判定"] = [""] + [_verdict(r, base, only_one)
                        for r in df.iloc[1:].to_dict("records")]
    return df


def _verdict(row: dict, base: dict, only_one: bool) -> str:
    """把一行差值翻译成人话。

    阈值取 1 个百分点：低于这个量级的差异在单次回测里说明不了问题，
    换个起止日期就能翻过来。
    """
    d_ret = row["总收益"] - base["总收益"]
    d_trades = row["笔数"] - base["笔数"]
    if pd.isna(d_ret):
        return "跑挂了"
    if abs(d_trades) == 0 and abs(d_ret) < 1e-9:
        return "从未触发，是摆设"
    if abs(d_ret) < 0.01:
        return "影响很小"
    if only_one:
        return "单独用就有正收益" if d_ret > 0 else "单独用是负贡献"
    # 逐一剔除：关掉后变好 = 这条在亏钱
    return f"关掉后收益 {d_ret:+.1%} → 这条在亏钱" if d_ret > 0 else \
           f"关掉后收益 {d_ret:+.1%} → 这条在赚钱，留着"


def summarize(df: pd.DataFrame) -> List[str]:
    """从消融表里挑出值得说的几句话。"""
    if df is None or len(df) < 2:
        return []
    base = df.iloc[0]
    body = df.iloc[1:]
    out = []

    dead = body[body["判定"].str.contains("摆设", na=False)]
    if not dead.empty:
        out.append("从未触发的规则（去掉不影响任何结果，只是让配置更难读）："
                   + "、".join(dead["规则"].str.replace("关掉：", "", regex=False)))

    hurt = body[body["Δ总收益"] > 0.01].sort_values("Δ总收益", ascending=False)
    if not hurt.empty:
        items = "、".join(
            f"{r['规则'].replace('关掉：', '')}（+{r['Δ总收益']:.1%}）"
            for _, r in hurt.iterrows())
        out.append(f"关掉之后反而变好的规则：{items}。这些是当前最值得怀疑的。")

    help_ = body[body["Δ总收益"] < -0.01].sort_values("Δ总收益")
    if not help_.empty:
        items = "、".join(
            f"{r['规则'].replace('关掉：', '')}（{r['Δ总收益']:.1%}）"
            for _, r in help_.iterrows())
        out.append(f"确实在赚钱的规则：{items}。")

    if hurt.empty and help_.empty:
        out.append("没有任何一条规则的边际贡献超过 1 个百分点。"
                   "说明当前收益主要由入场信号和择时决定，出场规则怎么调都是噪声——"
                   "与其继续调出场，不如先去看入场信号和因子排序。")

    out.append(f"基线：总收益 {base['总收益']:.2%}，最大回撤 {base['最大回撤']:.2%}，"
               f"{base['笔数']} 笔。消融是诊断工具，别在同一段历史上反复删规则"
               "留下最好看的组合——那是在拟合噪声，务必用样本外确认。")
    return out
