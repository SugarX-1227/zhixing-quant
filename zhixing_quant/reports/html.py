"""HTML report rendering with dependency-free inline SVG charts."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Dict, List

import pandas as pd

from zhixing_quant.config import PROJECT_ROOT


REPORT_DIR = PROJECT_ROOT / "reports" / "daily"


STRATEGY_NAMES = {
    "brick": "砖型图",
    "b1": "B1",
    "b2": "B2",
}

STRATEGY_REASONS = {
    "brick": "昨天绿柱 + 今天红柱 + 红柱高度达到昨日绿柱2/3 + 收盘站上知行多空线",
    "b1": "J<13超卖反弹 + 站上知行多空线 + 短期趋势确认 + 涨跌幅在±4%内",
    "b2": "近3日J<13超卖 + 涨幅>3.95% + 放量 + J<55确认",
}

INDICATOR_COLS = {
    "brick": ["brick_red_height", "brick_green_height"],
    "b1": ["kdj_j"],
    "b2": ["kdj_j"],
}


def render_daily_report(
    trade_date: str,
    candidates: pd.DataFrame,
    chart_data: Dict[str, pd.DataFrame],
    cfg: dict,
    strategy: str = "brick",
) -> Path:
    """Render daily stock-picking report as HTML.

    Args:
        trade_date: Report date string.
        candidates: Candidate rows.
        chart_data: Mapping from code to recent indicator DataFrame.
        cfg: Config dictionary.

    Returns:
        Path to generated HTML file.
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{trade_date}_candidates_{strategy}.html"
    regime = cfg["regime"]["current"]
    strategy_name = STRATEGY_NAMES.get(strategy, strategy)
    default_reason = STRATEGY_REASONS.get(strategy, "")
    rows_html = []
    for _, row in candidates.iterrows():
        code = str(row["code"])
        reason = row.get("reason", default_reason)
        rows_html.append(
            "<section class='candidate'>"
            f"<h2>{escape(code)} {escape(str(row['name']))}</h2>"
            "<div class='meta'>"
            f"<span>策略: {escape(strategy_name)}</span>"
            f"<span>市场状态: {escape(str(regime))}</span>"
            f"<span>收盘: {float(row['close']):.2f}</span>"
            f"<span>成交额: {float(row['amount']) / 100000000:.2f} 亿</span>"
        )
        if "abandon_gap_up_price" in row:
            rows_html.append(f"<span>高开放弃价: {float(row['abandon_gap_up_price']):.2f}</span>")
        if "stop_loss" in row:
            rows_html.append(f"<span>止损: {float(row['stop_loss']):.2f}</span>")
        if "kdj_j" in row:
            rows_html.append(f"<span>J值: {float(row['kdj_j']):.1f}</span>")
        if "kdj_k" in row:
            rows_html.append(f"<span>K值: {float(row['kdj_k']):.1f}</span>")
        if "kdj_d" in row:
            rows_html.append(f"<span>D值: {float(row['kdj_d']):.1f}</span>")
        if "pct_chg" in row:
            rows_html.append(f"<span>涨跌幅: {float(row['pct_chg']):.2f}%</span>")
        rows_html.append("</div>")
        rows_html.append(_render_svg_chart(chart_data[code], strategy))
        rows_html.append(f"<p class='reason'>{escape(str(reason))}</p>")
        rows_html.append("</section>")

    body = "\n".join(rows_html) if rows_html else "<p class='empty'>今日没有候选。</p>"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>知行砖型图每日选股 {escape(trade_date)}</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #1f2933;
      background: #f5f7fa;
    }}
    header {{
      padding: 24px 32px 16px;
      background: #ffffff;
      border-bottom: 1px solid #d9e2ec;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .summary {{ color: #52606d; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    .candidate {{
      background: #ffffff;
      border: 1px solid #d9e2ec;
      border-radius: 8px;
      margin-bottom: 18px;
      padding: 18px;
    }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .meta span {{
      background: #eef2f7;
      border-radius: 4px;
      padding: 5px 8px;
      font-size: 13px;
    }}
    .reason {{ margin: 10px 0 0; color: #52606d; font-size: 14px; }}
    .empty {{ background: #fff; border: 1px solid #d9e2ec; padding: 18px; border-radius: 8px; }}
    svg {{ width: 100%; height: auto; display: block; }}
    .axis {{ stroke: #bcccdc; stroke-width: 1; }}
    .line-yellow {{ fill: none; stroke: #d99a00; stroke-width: 1.6; }}
    .line-close {{ fill: none; stroke: #243b53; stroke-width: 1.4; }}
    .brick-red {{ fill: #d64545; }}
    .brick-green {{ fill: #2f9e44; }}
    .marker {{ fill: #0b7285; }}
  </style>
</head>
<body>
  <header>
    <h1>知行{escape(strategy_name)}每日选股</h1>
    <div class="summary">日期 {escape(trade_date)} · 市场状态 {escape(str(regime))} · 策略 {escape(strategy_name)} · 候选 {len(candidates)} 只 · 空头区间不拦截选股</div>
  </header>
  <main>{body}</main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path


def _render_svg_chart(df: pd.DataFrame, strategy: str = "brick") -> str:
    width = 1080
    price_h = 250
    indicator_h = 110
    pad_l = 48
    pad_r = 16
    pad_t = 16
    gap = 22
    height = pad_t + price_h + gap + indicator_h + 26
    plot_w = width - pad_l - pad_r
    data = df.tail(120).copy()
    if data.empty:
        return ""

    close = data["close"].astype(float)
    yellow = data["yellow_line"].astype(float)
    price_min = min(close.min(), yellow.min())
    price_max = max(close.max(), yellow.max())
    if price_max == price_min:
        price_max += 1
        price_min -= 1
    n = len(data)
    step = plot_w / max(n - 1, 1)

    def x(i: int) -> float:
        return pad_l + i * step

    def y_price(value: float) -> float:
        return pad_t + (price_max - value) / (price_max - price_min) * price_h

    close_path = _line_path([x(i) for i in range(n)], [y_price(v) for v in close])
    yellow_path = _line_path([x(i) for i in range(n)], [y_price(v) for v in yellow])

    signal_marks = []
    if "sig_brick" in data:
        for i, (_, row) in enumerate(data.iterrows()):
            if bool(row["sig_brick"]):
                signal_marks.append(
                    f"<circle class='marker' cx='{x(i):.2f}' cy='{y_price(float(row['close'])):.2f}' r='4' />"
                )

    indicator_label = "砖型图"
    indicator_rects = []

    if strategy == "brick" and "brick_value" in data:
        brick = data["brick_value"].astype(float)
        brick_max = max(float(brick.max()), 1.0)

        def y_brick(value: float) -> float:
            top = pad_t + price_h + gap
            return top + (brick_max - value) / brick_max * indicator_h

        for i, value in enumerate(brick):
            prev = float(brick.iloc[i - 1]) if i > 0 else float(value)
            color = "brick-red" if value > prev else "brick-green"
            bar_w = max(step * 0.72, 2)
            y0 = y_brick(float(value))
            base = y_brick(0)
            indicator_rects.append(
                f"<rect class='{color}' x='{x(i) - bar_w / 2:.2f}' y='{min(y0, base):.2f}' "
                f"width='{bar_w:.2f}' height='{abs(base - y0):.2f}' />"
            )
    elif strategy in ("b1", "b2") and "kdj_j" in data:
        kdj_j = data["kdj_j"].astype(float)
        kdj_max = max(float(kdj_j.max()), 100.0)
        kdj_min = min(0.0, float(kdj_j.min()))
        if kdj_max == kdj_min:
            kdj_max += 1

        def y_kdj(value: float) -> float:
            top = pad_t + price_h + gap
            return top + (kdj_max - value) / (kdj_max - kdj_min) * indicator_h

        indicator_label = "KDJ-J"
        for i, value in enumerate(kdj_j):
            bar_w = max(step * 0.72, 2)
            y0 = y_kdj(float(value))
            base = y_kdj(0)
            color = "brick-red" if float(value) >= 50 else "brick-green"
            indicator_rects.append(
                f"<rect class='{color}' x='{x(i) - bar_w / 2:.2f}' y='{min(y0, base):.2f}' "
                f"width='{bar_w:.2f}' height='{abs(base - y0):.2f}' />"
            )

        j_line_path = _line_path(
            [x(i) for i in range(n)],
            [y_kdj(v) for v in kdj_j],
        )
        signal_marks.append(f"<path class='line-yellow' d='{j_line_path}' />")

    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="price and indicator chart">
  <line class="axis" x1="{pad_l}" y1="{pad_t + price_h}" x2="{width - pad_r}" y2="{pad_t + price_h}" />
  <line class="axis" x1="{pad_l}" y1="{pad_t + price_h + gap + indicator_h}" x2="{width - pad_r}" y2="{pad_t + price_h + gap + indicator_h}" />
  <path class="line-close" d="{close_path}" />
  <path class="line-yellow" d="{yellow_path}" />
  {''.join(signal_marks)}
  {''.join(indicator_rects)}
  <text x="{pad_l}" y="14" font-size="12" fill="#52606d">收盘线 / 黄线</text>
  <text x="{pad_l}" y="{pad_t + price_h + gap - 4}" font-size="12" fill="#52606d">{indicator_label}</text>
</svg>"""


def _line_path(xs: List[float], ys: List[float]) -> str:
    parts = []
    for i, (x_value, y_value) in enumerate(zip(xs, ys)):
        command = "M" if i == 0 else "L"
        parts.append(f"{command}{x_value:.2f},{y_value:.2f}")
    return " ".join(parts)
