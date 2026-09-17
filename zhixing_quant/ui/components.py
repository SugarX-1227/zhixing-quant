"""可复用界面组件（浅色主题）。

组件只负责渲染，不做业务判断。业务结果由 executor/daily_workflow 产出后传进来。
"""

from __future__ import annotations

from html import escape
from typing import List, Optional, Sequence

import pandas as pd
import streamlit as st

from zhixing_quant.ui import theme as T

# DefenseEngine 返回的是英文代号，界面上不该出现 stop_loss_hit 这种东西
EXIT_REASON_TEXT = {
    "take_profit_hit": "触发止盈位",
    "stop_loss_hit": "触发止损位",
    "sell_signal": "S 系列卖点触发",
    "distribution": "识别到主力出货形态",
    "macd_death": "MACD 死叉加速",
    "white_break": "跌破白线",
    "yellow_break": "跌破黄线",
    "volume_stagnation": "放量滞涨",
    "dual_line_break_white": "跌破白线且次日未收回",
    "yoga_pants_stop": "触发止损位",
    "single_needle_break_tip": "跌破针尖",
    "b1_break_yellow": "跌破知行多空线",
}

PRIORITY_LABEL = {
    1: "P1 止盈", 2: "P2 止损", 3: "P3 卖点", 4: "P4 出货",
    5: "P5 MACD", 6: "P6 白线", 7: "P7 黄线", 8: "P8 滞涨",
}


def exit_reason(code: str) -> str:
    return EXIT_REASON_TEXT.get(str(code), str(code))


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------

def money(v) -> str:
    v = float(v or 0)
    if abs(v) >= 1e8:
        return f"{v / 1e8:.1f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.1f}万"
    return f"{v:,.0f}"


def pct(v, signed: bool = True) -> str:
    return f"{v:+.2%}" if signed else f"{v:.2%}"


def tone(v) -> str:
    return T.UP if float(v) >= 0 else T.DOWN


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

def page_head(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<h1 style="margin:0 0 22px">{escape(title)}'
        f'<span style="color:{T.TEXT_3};font-weight:400;font-size:15px;'
        f'margin-left:10px">{escape(subtitle)}</span></h1>',
        unsafe_allow_html=True,
    )


def section(title: str, hint: str = "", tag: str = "", warn: bool = False) -> None:
    """流水线里的一个阶段标题。"""
    tag_bg = T.UP_DIM if warn else T.SURFACE_2
    tag_fg = T.UP if warn else T.TEXT_2
    tag_html = (
        f'<span style="margin-left:auto;font-size:12px;padding:2px 10px;'
        f'border-radius:11px;background:{tag_bg};color:{tag_fg};'
        f'{"font-weight:600" if warn else f"border:1px solid {T.LINE_SOFT}"}">'
        f'{escape(tag)}</span>' if tag else ""
    )
    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:12px;margin:24px 0 10px">'
        f'<h2 style="font-size:15px;font-weight:600;margin:0">{escape(title)}</h2>'
        f'<em style="font-style:normal;font-size:12.5px;color:{T.TEXT_3}">'
        f'{escape(hint)}</em>{tag_html}</div>',
        unsafe_allow_html=True,
    )


def empty_state(title: str, hint: str) -> None:
    st.markdown(
        f'<div style="background:{T.SURFACE};border:1px solid {T.LINE_SOFT};'
        f'border-radius:{T.RADIUS};padding:32px 24px;text-align:center">'
        f'<div style="font-size:15px;font-weight:600;margin-bottom:6px">{escape(title)}</div>'
        f'<div style="font-size:13px;color:{T.TEXT_2};max-width:460px;margin:0 auto">'
        f'{escape(hint)}</div></div>',
        unsafe_allow_html=True,
    )


def notes(items: Sequence[str]) -> None:
    if not items:
        return
    body = "".join(f'<div style="margin-bottom:6px">· {escape(str(t))}</div>' for t in items)
    st.markdown(
        f'<div style="font-size:12.5px;color:{T.TEXT_3};margin-top:22px;'
        f'padding-top:14px;border-top:1px solid {T.LINE_SOFT}">{body}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 今日页
# ---------------------------------------------------------------------------

REGIME_TEXT = {"BULL": "多头", "NEUTRAL": "中性", "BEAR": "空头"}


def regime_card(r) -> None:
    label = REGIME_TEXT.get(r.regime, r.regime)
    color = {"BULL": T.UP, "BEAR": T.DOWN}.get(r.regime, T.TEXT_2)
    room = max(0.0, r.max_total_pct - r.position_pct)
    trigger = getattr(r, "regime_trigger", "")
    items = [
        ("活跃市值当日涨跌", f"{r.regime_score:+.2%}", T.TEXT),
        ("触发依据", trigger or "无新触发", T.TEXT_3),
        ("当前仓位", f"{r.position_pct:.1%}", T.TEXT),
        ("可加仓空间", f"{room:.1%}", T.UP if room > 0 else T.TEXT_3),
    ]
    dl = "".join(
        f'<div><div style="color:{T.TEXT_3};margin-bottom:3px">{k}</div>'
        f'<div style="font-weight:600;font-size:14px;color:{c}">{v}</div></div>'
        for k, v, c in items
    )
    st.markdown(
        f'<div style="background:{T.SURFACE};border:1px solid {T.LINE_SOFT};'
        f'border-left:2px solid {color};border-radius:0 {T.RADIUS} {T.RADIUS} 0;'
        f'padding:14px 18px;display:flex;align-items:center;gap:18px">'
        f'<div style="font-size:19px;font-weight:600;color:{color}">{label}</div>'
        f'<div style="display:flex;gap:26px;margin-left:auto;font-size:12.5px;'
        f'text-align:right">{dl}</div></div>',
        unsafe_allow_html=True,
    )


def holding_card(v) -> None:
    """单个持仓的复核结果。"""
    border = T.UP if v.action else T.DOWN
    stars = (f'<span style="color:{T.UP}">' + "★" * v.stars + "</span>"
             f'<span style="color:{T.TEXT_3}">' + "★" * (5 - v.stars) + "</span>")
    if v.action:
        tag = PRIORITY_LABEL.get(v.action_priority, f"P{v.action_priority}")
        act = (
            f'<span style="font-size:11px;padding:1px 7px;border-radius:3px;'
            f'background:{T.UP_DIM};color:{T.UP};font-weight:600">{tag}</span>'
            f'<span>{escape(exit_reason(v.action_reason))}，次日开盘执行。</span>'
        )
    else:
        act = (
            f'<span style="font-size:11px;padding:1px 7px;border-radius:3px;'
            f'background:{T.DOWN_DIM};color:{T.DOWN};font-weight:600">持有</span>'
            f'<span>{escape("、".join(v.rating_reasons[:2]))}</span>'
        )
    st.markdown(
        f'<div style="background:{T.SURFACE};border:1px solid {T.LINE_SOFT};'
        f'border-left:2px solid {border};border-radius:0 {T.RADIUS} {T.RADIUS} 0;'
        f'padding:13px 16px;margin-bottom:8px;display:flex;justify-content:space-between;gap:14px">'
        f'<div><div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
        f'<b style="font-size:14.5px">{escape(v.code)}</b>'
        f'<span style="color:{T.TEXT_2};font-size:13px">{escape(v.name)}</span>'
        f'<span style="color:{T.TEXT_3};font-size:12.5px">{v.shares} 股 @ {v.entry_price:.2f}</span>'
        f'<span style="font-size:12px;letter-spacing:1px">{stars}</span>'
        f'<span style="color:{T.TEXT_3};font-size:12px">{v.stars}/5 {escape(v.risk_level)}</span>'
        f'</div><div style="margin-top:7px;font-size:13px;display:flex;gap:8px;'
        f'align-items:baseline;color:{T.TEXT_2}">{act}</div></div>'
        f'<div style="text-align:right;white-space:nowrap">'
        f'<div style="font-size:15px;font-weight:600">{v.last_price:.2f}</div>'
        f'<div style="font-size:12.5px;font-weight:600;margin-top:2px;'
        f'color:{tone(v.return_pct)}">{pct(v.return_pct)}</div></div></div>',
        unsafe_allow_html=True,
    )


def defense_coverage_chips(coverage: dict) -> None:
    """把哑规则显式标出来。"没触发"和"规则根本没运行"必须能区分。"""
    if not coverage:
        return
    chips = []
    for rule, ok in coverage.items():
        bg, fg = (T.DOWN_DIM, T.DOWN) if ok else (T.UP_DIM, T.UP)
        mark = "✓" if ok else "✗ 规则未生效"
        chips.append(
            f'<span style="font-size:11px;padding:2px 8px;border-radius:10px;'
            f'background:{bg};color:{fg}">{escape(rule)} {mark}</span>'
        )
    st.markdown(
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">'
        + "".join(chips) + "</div>",
        unsafe_allow_html=True,
    )


def plan_card(p) -> Optional[str]:
    """下单计划。返回被点击的代码（用于记录建仓）。"""
    border = T.TEXT_3 if p.blocked else T.SIGNAL
    bg = T.SURFACE_2 if p.blocked else T.SURFACE
    if p.blocked:
        right = (f'<div><div style="color:{T.TEXT_3};margin-bottom:3px">结果</div>'
                 f'<div style="font-weight:600;color:{T.TEXT_3}">不下单</div></div>')
    else:
        cells = [("股数", f"{p.shares:,}", T.TEXT), ("金额", f"{p.amount:,.0f}", T.TEXT),
                 ("止损", f"{p.stop_loss:.2f}", T.TEXT),
                 ("风险敞口", f"{p.risk_amount:,.0f}", T.SIGNAL)]
        right = "".join(
            f'<div><div style="color:{T.TEXT_3};margin-bottom:3px">{k}</div>'
            f'<div style="font-weight:600;font-size:13.5px;color:{c}">{v}'
            + (f'<div style="font-weight:400;font-size:11px;color:{T.TEXT_3}">'
               f'权益 {p.risk_pct_of_equity:.2%}</div>' if k == "风险敞口" else "")
            + "</div></div>"
            for k, v, c in cells
        )
    why = p.blocked_reason if p.blocked else p.signal
    st.markdown(
        f'<div style="background:{bg};border:1px solid {T.LINE_SOFT};'
        f'border-left:2px solid {border};border-radius:0 {T.RADIUS} {T.RADIUS} 0;'
        f'padding:14px 16px;margin-bottom:8px;display:flex;gap:24px;align-items:center">'
        f'<div style="flex:1"><b style="font-size:14.5px">{escape(p.code)}</b>'
        f'<span style="color:{T.TEXT_2};font-size:13px;margin-left:8px">{escape(p.name)}</span>'
        f'<div style="font-size:12.5px;color:{T.TEXT_3};margin-top:5px">{escape(why)}</div></div>'
        f'<div style="display:flex;gap:24px;font-size:12px;text-align:right">{right}</div></div>',
        unsafe_allow_html=True,
    )
    return None


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------

COLUMN_LABELS = {
    "close": "收盘", "pct_chg": "涨跌", "amount": "成交额",
    "yellow_line": "知行多空线", "white_line": "白线", "stop_loss": "止损",
    "take_profit": "止盈", "red_streak": "连红", "kdj_j": "J值", "confidence": "信心",
}
MUTED = {"yellow_line", "white_line", "take_profit"}


def candidate_table(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    cols = [c for c in COLUMN_LABELS if c in df.columns]
    header = "".join(
        f'<th style="text-align:right;font-size:11.5px;font-weight:500;color:{T.TEXT_3};'
        f'padding:0 12px 8px;border-bottom:1px solid {T.LINE};white-space:nowrap">'
        f'{COLUMN_LABELS[c]}</th>' for c in cols
    )
    rows = []
    for _, r in df.iterrows():
        cells = [
            f'<td style="padding:11px 12px;border-bottom:1px solid {T.LINE_SOFT};'
            f'text-align:left"><b>{escape(str(r["code"]))}</b>'
            f'<span style="color:{T.TEXT_2};margin-left:9px;font-size:13px">'
            f'{escape(str(r.get("name", "")))}</span></td>'
        ]
        for c in cols:
            v = r.get(c)
            style = (f'padding:11px 12px;border-bottom:1px solid {T.LINE_SOFT};'
                     f'text-align:right;white-space:nowrap;')
            if v is None or (isinstance(v, float) and pd.isna(v)):
                inner = "—"
                style += f"color:{T.TEXT_3};"
            elif c == "close":
                inner = f'<span style="font-weight:600;font-size:15px">{float(v):.2f}</span>'
            elif c == "pct_chg":
                col, dim = (T.UP, T.UP_DIM) if float(v) >= 0 else (T.DOWN, T.DOWN_DIM)
                inner = (f'<span style="display:inline-block;min-width:60px;padding:1px 7px;'
                         f'border-radius:4px;font-size:12.5px;font-weight:600;'
                         f'background:{dim};color:{col}">{float(v):+.2f}%</span>')
            elif c == "amount":
                inner = money(v)
            elif c in ("red_streak", "confidence"):
                inner = f"{int(v)}" + ("/5" if c == "confidence" else "")
            else:
                inner = f"{float(v):.2f}"
                if c in MUTED:
                    style += f"color:{T.TEXT_3};"
            cells.append(f'<td style="{style}">{inner}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        f'<div style="background:{T.SURFACE};border:1px solid {T.LINE_SOFT};'
        f'border-radius:{T.RADIUS};padding:2px 4px 0">'
        f'<table style="width:100%;border-collapse:collapse"><thead><tr>'
        f'<th style="text-align:left;font-size:11.5px;font-weight:500;color:{T.TEXT_3};'
        f'padding:0 12px 8px;border-bottom:1px solid {T.LINE}">代码 / 名称</th>'
        f'{header}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def stat_row(items: Sequence[tuple]) -> None:
    cells = []
    for label, value, t in items:
        color = {"up": T.UP, "down": T.DOWN}.get(t, T.TEXT)
        cells.append(
            f'<div style="background:{T.SURFACE};padding:14px 16px">'
            f'<div style="font-size:11.5px;color:{T.TEXT_3};margin-bottom:5px">{escape(label)}</div>'
            f'<div style="font-size:22px;font-weight:600;color:{color}">{escape(str(value))}</div></div>'
        )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat({len(cells)},1fr);'
        f'gap:1px;background:{T.LINE_SOFT};border:1px solid {T.LINE_SOFT};'
        f'border-radius:{T.RADIUS};overflow:hidden;margin-bottom:22px">'
        + "".join(cells) + "</div>",
        unsafe_allow_html=True,
    )


def sidebar_status(equity: float, cash: float, position_pct: float,
                   data_date: str, warn: bool = False) -> None:
    rows = [("权益", money(equity)), ("现金", money(cash)),
            ("仓位", f"{position_pct:.1%}"),
            ("数据至", (data_date[4:6] + "-" + data_date[6:8])
             if len(str(data_date)) == 8 else str(data_date))]
    body = "".join(
        f'<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
        f'<span style="color:{T.TEXT_3}">{k}</span>'
        f'<span style="color:{T.TEXT_2};font-weight:500">{v}</span></div>'
        for k, v in rows
    )
    st.sidebar.markdown(
        f'<div style="border-top:1px solid {T.LINE_SOFT};padding-top:14px;'
        f'margin-top:10px;font-size:11.5px">{body}'
        + (f'<div style="color:{T.UP};margin-top:6px">⚠ 数据需要同步</div>' if warn else "")
        + "</div>",
        unsafe_allow_html=True,
    )
