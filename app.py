"""知行量化系统 Web 服务。

启动：
    streamlit run app.py

替代原来的 HTML 报告：所有结果直接在浏览器里看，不落地文件。
"""

from __future__ import annotations

import copy
import subprocess
import sys
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="知行量化", layout="wide", page_icon="📈")

SCANNERS = {
    "砖型图": ("zhixing_quant.scanner.daily_brick", "sig_brick", "brick"),
    "B1": ("zhixing_quant.scanner.daily_b1", "sig_b1", "b1"),
    "B2": ("zhixing_quant.scanner.daily_b2", "sig_b2", "b2"),
}

INDICATOR_FN = {
    "brick": ("zhixing_quant.indicators.brick", "add_brick_indicators"),
    "b1": ("zhixing_quant.indicators.b1", "add_b1_indicators"),
    "b2": ("zhixing_quant.indicators.b2", "add_b2_indicators"),
}


@st.cache_resource
def get_config():
    from zhixing_quant.config import load_config

    return load_config()


def _health():
    from zhixing_quant.data.tdx_loader import data_health

    return data_health()


# ---------------------------------------------------------------------------
# 页面：数据
# ---------------------------------------------------------------------------
def page_data(cfg):
    st.subheader("本地行情库")

    health = _health()
    if not health.get("ok"):
        st.error(health.get("message", "本地行情库未就绪"))
        st.markdown(
            "**首次使用步骤**\n\n"
            "1. 在 `config/settings.yaml` 里把 `data.tdx_install_dir` 指向通达信安装目录\n"
            "2. 打开通达信，让它下载完历史行情（盘后会自动下载当日数据）\n"
            "3. 回到这里点「立即同步」，或在命令行跑 "
            "`python -m zhixing_quant.data.sync --names --xdxr`"
        )
    else:
        c = st.columns(5)
        c[0].metric("股票数", f"{health['codes']:,}")
        c[1].metric("K线总数", f"{health['bars']:,}")
        c[2].metric("最新交易日", str(health["date_max"] or "-"))
        c[3].metric("库大小", f"{health['db_size_mb']} MB")
        c[4].metric("上次同步", health["last_sync"])
        for w in health.get("warnings", []):
            st.warning(w)
        if not health.get("warnings"):
            st.success("数据状态正常")

    st.divider()
    st.markdown("**增量同步**（只读通达信新写入的部分，几秒钟）")
    col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
    do_names = col1.checkbox("同时更新股票名称", value=False)
    do_xdxr = col2.checkbox("同时更新除权除息", value=False, help="需要 pytdx，耗时较长")
    full = col3.checkbox("全量重建", value=False)

    if st.button("立即同步", type="primary"):
        cmd = [sys.executable, "-m", "zhixing_quant.data.sync"]
        if full:
            cmd.append("--full")
        if do_names:
            cmd.append("--names")
        if do_xdxr:
            cmd.append("--xdxr")
        with st.spinner("同步中..."):
            proc = subprocess.run(cmd, capture_output=True, text=True)
        st.code(proc.stdout or proc.stderr, language="text")
        from zhixing_quant.data.tdx_loader import reset_store

        reset_store()
        st.rerun()


# ---------------------------------------------------------------------------
# 页面：选股
# ---------------------------------------------------------------------------
def page_scan(cfg):
    st.subheader("盘后选股")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    strategy = c1.selectbox("策略", list(SCANNERS))
    scan_date = c2.date_input("信号日期", value=datetime.now())
    limit = c3.number_input("扫描上限", 0, 5000, 300, 50,
                            help="按成交额降序取前 N 只，0 表示全市场")
    c4.write("")
    c4.write("")
    run = c4.button("开始扫描", type="primary", use_container_width=True)

    if run:
        import importlib

        module_path, sig_col, _ = SCANNERS[strategy]
        local_cfg = copy.deepcopy(cfg)
        local_cfg["universe"]["max_candidates"] = 9999   # 不在扫描阶段截断
        mod = importlib.import_module(module_path)
        bar = st.progress(0.0, text=f"扫描 {strategy}...")

        def on_progress(done: int, total: int):
            bar.progress(min(done / max(total, 1), 1.0),
                         text=f"扫描 {strategy}... {done}/{total}")

        try:
            candidates, chart_data = mod.scan_daily(
                local_cfg,
                end_date=scan_date.strftime("%Y%m%d"),
                limit_universe=int(limit) if limit else None,
                progress=on_progress,
            )
        except Exception as exc:
            bar.empty()
            st.error(f"扫描失败：{exc}")
            return
        bar.empty()
        st.session_state.update(
            scan_candidates=candidates, scan_charts=chart_data,
            scan_strategy=strategy, scan_sig=sig_col,
        )

    candidates = st.session_state.get("scan_candidates")
    if candidates is None:
        st.info("点击「开始扫描」运行选股。")
        return
    if candidates.empty:
        st.warning(f"{st.session_state['scan_strategy']} 当日无符合条件的标的。")
        return

    st.success(f"{st.session_state['scan_strategy']}：{len(candidates)} 只候选")
    if "adjust" in candidates.columns and (candidates["adjust"] == "none").any():
        st.warning(
            "部分标的使用的是**不复权**价格，除权日附近可能出现假信号。"
            "到「数据」页勾选「同时更新除权除息」同步一次即可。"
        )
    show = [c for c in ["date", "code", "name", "close", "amount", "yellow_line",
                        "kdj_j", "red_streak", "stop_loss", "reason"]
            if c in candidates.columns]
    disp = candidates[show].copy()
    if "amount" in disp:
        disp["amount"] = (disp["amount"] / 1e8).round(2)
        disp = disp.rename(columns={"amount": "成交额(亿)"})
    st.dataframe(disp, use_container_width=True, hide_index=True)

    st.divider()
    charts = st.session_state.get("scan_charts", {})
    codes = candidates["code"].tolist()
    sel = st.selectbox(
        "查看个股", codes,
        format_func=lambda c: f"{c} {candidates.loc[candidates['code'] == c, 'name'].iloc[0]}",
    )
    if sel in charts:
        _draw_kline(charts[sel], title=sel, signal_col=st.session_state.get("scan_sig"))


# ---------------------------------------------------------------------------
# 页面：回测
# ---------------------------------------------------------------------------
def page_backtest(cfg):
    st.subheader("策略回测")
    st.caption("T 日收盘出信号 → T+1 开盘成交，含手续费、印花税、滑点、涨跌停、T+1 限制。")

    c1, c2, c3 = st.columns(3)
    strategy = c1.selectbox("策略", list(SCANNERS), key="bt_strategy")
    start = c2.date_input("开始日期", value=datetime.now() - timedelta(days=730))
    end = c3.date_input("结束日期", value=datetime.now())

    c4, c5, c6, c7 = st.columns(4)
    pool_size = c4.number_input("股票池大小", 10, 2000, 200, 10)
    take_profit = c5.number_input("止盈 %", 3.0, 100.0, 15.0, 1.0) / 100
    max_pos = c6.number_input("最大持仓数", 1, 20, 5)
    max_hold = c7.number_input("最长持有(交易日)", 3, 120, 20)

    if st.button("运行回测", type="primary"):
        import importlib

        from zhixing_quant.backtest.engine import BacktestEngine
        from zhixing_quant.data.tdx_loader import (
            fetch_a_spot, filter_universe, load_daily_many,
        )

        _, sig_col, ind_key = SCANNERS[strategy]
        mod_name, fn_name = INDICATOR_FN[ind_key]
        add_indicators = getattr(importlib.import_module(mod_name), fn_name)

        local_cfg = copy.deepcopy(cfg)
        local_cfg.setdefault("backtest", {})
        local_cfg["backtest"]["max_positions"] = int(max_pos)
        local_cfg["backtest"]["max_holding_days"] = int(max_hold)

        try:
            with st.spinner("准备数据..."):
                spot = fetch_a_spot()
                uni = filter_universe(spot, local_cfg).head(int(pool_size))
                warmup = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
                raw = load_daily_many(uni["code"].tolist(), start_date=warmup,
                                      end_date=end.strftime("%Y%m%d"))
                data = {}
                for code, df in raw.items():
                    if len(df) < 130:
                        continue
                    data[code] = add_indicators(df, local_cfg)
            if not data:
                st.error("可用数据不足，先去「数据」页同步。")
                return
            with st.spinner(f"回测 {len(data)} 只..."):
                engine = BacktestEngine(local_cfg)
                result = engine.run(
                    data, signal_col=sig_col, take_profit_pct=take_profit,
                    start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
                )
        except Exception as exc:
            st.error(f"回测失败：{exc}")
            return
        st.session_state["bt_result"] = result

    result = st.session_state.get("bt_result")
    if result is None:
        st.info("配置好参数后点「运行回测」。")
        return

    m = result.metrics
    r1 = st.columns(4)
    r1[0].metric("总收益", f"{m.get('total_return', 0):.2%}")
    r1[1].metric("年化收益", f"{m.get('annualized_return', 0):.2%}")
    r1[2].metric("最大回撤", f"{m.get('max_drawdown', 0):.2%}")
    r1[3].metric("夏普", f"{m.get('sharpe', 0):.2f}")
    r2 = st.columns(4)
    r2[0].metric("胜率", f"{m.get('win_rate', 0):.1%}")
    r2[1].metric("盈亏比", str(m.get("profit_loss_ratio", "-")))
    r2[2].metric("交易笔数", m.get("total_trades", 0))
    r2[3].metric("期末权益", f"{m.get('final_equity', 0):,.0f}")

    if len(result.equity_curve) > 1:
        import plotly.graph_objects as go

        eq = result.equity_curve
        dd = eq / eq.cummax() - 1.0
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="权益", line=dict(width=2)))
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), title="资金曲线")
        st.plotly_chart(fig, use_container_width=True)

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=dd.index, y=dd.values, name="回撤",
                                  fill="tozeroy", line=dict(color="#d64545", width=1)))
        fig2.update_layout(height=200, margin=dict(l=0, r=0, t=30, b=0),
                           title="回撤", yaxis_tickformat=".0%")
        st.plotly_chart(fig2, use_container_width=True)

    tf = result.trades_frame()
    if not tf.empty:
        st.divider()
        st.markdown("**成交明细**")
        left, right = st.columns([3, 1])
        left.dataframe(tf.sort_values("entry_date", ascending=False),
                       use_container_width=True, hide_index=True, height=320)
        right.markdown("**卖出原因**")
        right.dataframe(tf["exit_reason"].value_counts().rename("笔数"),
                        use_container_width=True)


# ---------------------------------------------------------------------------
# 页面：K线
# ---------------------------------------------------------------------------
def page_chart(cfg):
    st.subheader("个股 K 线")
    c1, c2, c3 = st.columns([1, 1, 1])
    code = c1.text_input("股票代码", value="600000")
    days = c2.slider("显示天数", 30, 250, 120)
    overlay = c3.selectbox("叠加指标", ["砖型图", "B1", "B2", "无"])

    if st.button("加载", type="primary"):
        import importlib

        from zhixing_quant.data.tdx_loader import load_daily

        try:
            df = load_daily(code.strip().zfill(6), adjust="qfq")
            if df.empty:
                st.warning("库里没有这只股票，确认代码或先同步数据。")
                return
            if overlay != "无":
                mod_name, fn_name = INDICATOR_FN[SCANNERS[overlay][2]]
                df = getattr(importlib.import_module(mod_name), fn_name)(df, cfg)
            if df.attrs.get("adjust") == "none":
                st.info("当前为**不复权**价格。跑一次 `--xdxr` 后才是前复权。")
            _draw_kline(df.tail(days), title=code,
                        signal_col=SCANNERS[overlay][1] if overlay != "无" else None)
        except Exception as exc:
            st.error(f"加载失败：{exc}")


def _draw_kline(df: pd.DataFrame, title: str = "", signal_col: str = None):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from zhixing_quant.indicators.tdx import ma

    d = df.copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        d.index = pd.to_datetime(d.index)
    x = d.index.strftime("%Y-%m-%d")

    has_sub = "brick_value" in d.columns or "kdj_j" in d.columns
    fig = make_subplots(rows=3 if has_sub else 2, cols=1, shared_xaxes=True,
                        row_heights=[0.6, 0.2, 0.2] if has_sub else [0.75, 0.25],
                        vertical_spacing=0.03)

    fig.add_trace(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"],
                                 close=d["close"], name="K线",
                                 increasing_line_color="#d64545",
                                 decreasing_line_color="#2f9e44"), row=1, col=1)
    for w, color in ((5, "#f59f00"), (10, "#4c6ef5"), (20, "#ae3ec9")):
        fig.add_trace(go.Scatter(x=x, y=ma(d["close"], w), name=f"MA{w}",
                                 line=dict(width=1, color=color)), row=1, col=1)
    if "yellow_line" in d.columns:
        fig.add_trace(go.Scatter(x=x, y=d["yellow_line"], name="知行多空线",
                                 line=dict(width=2, color="#fab005")), row=1, col=1)

    if signal_col and signal_col in d.columns:
        hits = d[d[signal_col].fillna(False)]
        if not hits.empty:
            fig.add_trace(go.Scatter(
                x=hits.index.strftime("%Y-%m-%d"), y=hits["low"] * 0.97,
                mode="markers", name="信号",
                marker=dict(symbol="triangle-up", size=12, color="#e03131")), row=1, col=1)

    colors = ["#d64545" if c >= o else "#2f9e44" for o, c in zip(d["open"], d["close"])]
    fig.add_trace(go.Bar(x=x, y=d["vol"], name="成交量", marker_color=colors), row=2, col=1)

    if has_sub:
        if "brick_value" in d.columns:
            fig.add_trace(go.Bar(x=x, y=d["brick_value"], name="砖型图",
                                 marker_color="#6366f1"), row=3, col=1)
        elif "kdj_j" in d.columns:
            for col, color in (("kdj_k", "#4c6ef5"), ("kdj_d", "#f59f00"), ("kdj_j", "#e03131")):
                if col in d.columns:
                    fig.add_trace(go.Scatter(x=x, y=d[col], name=col.upper(),
                                             line=dict(width=1, color=color)), row=3, col=1)

    fig.update_layout(title=title, xaxis_rangeslider_visible=False,
                      height=700, showlegend=True, margin=dict(l=0, r=0, t=40, b=0))
    fig.update_xaxes(type="category", nticks=12)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
def main():
    cfg = get_config()
    st.sidebar.title("知行量化")
    health = _health()
    if health.get("ok"):
        st.sidebar.caption(
            f"数据至 {health['date_max']} · {health['codes']} 只"
            + ("  ⚠" if health.get("warnings") else "")
        )
    else:
        st.sidebar.error("数据未就绪")

    page = st.sidebar.radio("", ["选股", "回测", "K线", "数据"], label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption(f"市场状态：{cfg.get('regime', {}).get('current', '-')}")

    {"选股": page_scan, "回测": page_backtest,
     "K线": page_chart, "数据": page_data}[page](cfg)


if __name__ == "__main__":
    main()
