"""Streamlit dashboard: 知行量化交易系统看板."""

from __future__ import annotations

import copy
import importlib
import sys
import tempfile
import types

import streamlit as st

st.set_page_config(page_title="知行量化系统", layout="wide")

st.title("知行量化交易系统")

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
if "scan_results" not in st.session_state:
    st.session_state.scan_results = {}
if "chart_data" not in st.session_state:
    st.session_state.chart_data = {}
if "scan_done" not in st.session_state:
    st.session_state.scan_done = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@st.cache_resource
def get_config():
    from zhixing_quant.config import load_config
    return load_config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_scanner(strategy: str, scan_limit: int, cfg: dict):
    """Run a scanner and return (candidates_df, chart_data_dict).

    Temporarily bumps cfg['universe']['max_candidates'] so the scanner
    does not clip results to the YAML default of 10.
    """
    scanner_map = {
        "砖型图": "zhixing_quant.scanner.daily_brick",
        "B1": "zhixing_quant.scanner.daily_b1",
        "B2": "zhixing_quant.scanner.daily_b2",
    }
    module_path = scanner_map[strategy]

    # Make a shallow copy so we don't mutate the cached config
    local_cfg = copy.deepcopy(cfg)
    local_cfg["universe"]["max_candidates"] = scan_limit

    mod = importlib.import_module(module_path)
    return mod.scan_daily(local_cfg, limit_universe=scan_limit)


# ---------------------------------------------------------------------------
# Page: 择时状态
# ---------------------------------------------------------------------------
def render_timing(cfg):
    st.subheader("择时状态")

    # --- Manual regime selector (what the user actually wants) ---
    current_regime = cfg.get("regime", {}).get("current", "NEUTRAL").upper()
    col1, col2 = st.columns(2)
    with col1:
        new_regime = st.radio(
            "选择当前市场区间",
            ["BULL", "NEUTRAL", "BEAR"],
            index=["BULL", "NEUTRAL", "BEAR"].index(current_regime),
            horizontal=True,
        )
    with col2:
        st.caption("手动切换会影响后续选股/仓位策略的过滤逻辑")

    if new_regime != current_regime:
        cfg["regime"]["current"] = new_regime
        st.toast(f"市场区间已切换为 {new_regime}", icon="✅")

    # --- Regime info ---
    from zhixing_quant.timing.regime import RegimeStateMachine
    from zhixing_quant.timing.strategy_mode import get_strategy_mode

    sm = RegimeStateMachine()
    proxy_val = {"BULL": 0.8, "NEUTRAL": 0.5, "BEAR": 0.2}[new_regime]
    state = sm.update(proxy_val)
    mode = get_strategy_mode(new_regime, state["regime_strength"])

    m1, m2, m3 = st.columns(3)
    m1.metric("当前市场状态", new_regime)
    m2.metric("策略模式", mode["mode"])
    m3.metric("最大仓位", f"{mode['max_position_pct']:.0%}")

    st.caption(
        f"允许开仓: {'是' if mode['allow_open'] else '否（仅防守）'} | "
        f"可用信号: {', '.join(mode['allowed_signals']) if mode['allowed_signals'] else '无'}"
    )

    # --- Portfolio caps ---
    st.divider()
    st.subheader("仓位上限配置")
    caps = cfg.get("portfolio", {})
    cap_cols = st.columns(3)
    cap_cols[0].metric("多头区间", f"{caps.get('bull_max_total', 0.8):.0%}")
    cap_cols[1].metric("中性区间", f"{caps.get('neutral_max_total', 0.5):.0%}")
    cap_cols[2].metric("空头区间", f"{caps.get('bear_max_total', 0.0):.0%}")


# ---------------------------------------------------------------------------
# Page: 候选清单
# ---------------------------------------------------------------------------
def render_candidates(cfg):
    st.subheader("当日候选清单")

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        strategy = st.selectbox("策略", ["砖型图", "B1", "B2"])
    with col2:
        limit = st.slider("扫描数量上限", 10, 200, 50, 10)
    with col3:
        st.write("")
        st.write("")
        run = st.button("开始扫描", type="primary")

    if run:
        with st.spinner(f"正在扫描 {strategy} 策略，最多 {limit} 只..."):
            try:
                candidates, chart_data = _run_scanner(strategy, limit, cfg)
                st.session_state.scan_results = candidates
                st.session_state.chart_data = chart_data
                st.session_state.scan_done = True
                st.session_state.scan_strategy = strategy
            except Exception as exc:
                st.error(f"扫描失败: {exc}")
                return

    # Show persisted results even after navigation
    if not st.session_state.get("scan_done"):
        st.info("点击「开始扫描」运行选股策略。")
        return

    candidates = st.session_state.scan_results
    chart_data = st.session_state.chart_data

    if candidates is None or (hasattr(candidates, "empty") and candidates.empty):
        strategy_label = st.session_state.get("scan_strategy", strategy)
        st.warning(f"{strategy_label} 今日无符合条件的候选股。")
        return

    strategy_label = st.session_state.get("scan_strategy", strategy)
    st.success(f"找到 {len(candidates)} 只候选股")

    # Summary table
    display_cols = ["code", "name", "close", "amount", "reason"]
    available_cols = [c for c in display_cols if c in candidates.columns]
    st.dataframe(
        candidates[available_cols].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
    )

    # Detail view
    st.divider()
    st.subheader("个股详情")

    if "code" not in candidates.columns:
        st.info("当前结果无个股详情数据。")
        return

    selected = st.selectbox(
        "选择股票查看 K 线图",
        options=candidates["code"].tolist(),
        format_func=lambda c: (
            f"{c} {candidates[candidates['code'] == c]['name'].values[0]}"
        ),
    )

    if selected not in chart_data:
        st.info("该股无 K 线数据。")
        return

    df = chart_data[selected].copy()
    # Always produce a 'date' column for plotly
    if "date" not in df.columns:
        df = df.reset_index()
        if "date" not in df.columns:
            df.rename(columns={df.columns[0]: "date"}, inplace=True)

    # Convert date to string for plotly
    df["date"] = df["date"].astype(str)

    from zhixing_quant.indicators.tdx import ma

    close = df["close"]
    df["ma5"] = ma(close, 5)
    df["ma10"] = ma(close, 10)
    df["ma20"] = ma(close, 20)

    chart_df = df.tail(60)
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.05,
    )
    fig.add_trace(go.Candlestick(
        x=chart_df["date"], open=chart_df["open"], high=chart_df["high"],
        low=chart_df["low"], close=chart_df["close"], name="K线",
        increasing_line_color="#d64545", decreasing_line_color="#2f9e44",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=chart_df["date"], y=chart_df["ma5"], name="MA5", line=dict(width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=chart_df["date"], y=chart_df["ma10"], name="MA10", line=dict(width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=chart_df["date"], y=chart_df["ma20"], name="MA20", line=dict(width=1)), row=1, col=1)
    if "brick_value" in chart_df.columns:
        fig.add_trace(go.Scatter(
            x=chart_df["date"], y=chart_df["brick_value"],
            name="砖型图", line=dict(color="#6366f1", width=1.5),
        ), row=2, col=1)

    stock_name = candidates[candidates["code"] == selected]["name"].values
    stock_name = stock_name[0] if len(stock_name) else ""
    fig.update_layout(
        title=f"{selected} {stock_name}",
        xaxis_rangeslider_visible=False,
        height=600,
        showlegend=True,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Key metrics
    latest = df.iloc[-1]
    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    mcol1.metric("收盘", f"{float(latest['close']):.2f}")
    mcol2.metric("成交量", f"{float(latest['vol']):,.0f}")
    if "kdj_j" in latest:
        mcol3.metric("KDJ J", f"{float(latest['kdj_j']):.1f}")
    if "yellow_line" in latest:
        mcol4.metric("黄线", f"{float(latest['yellow_line']):.2f}")


# ---------------------------------------------------------------------------
# Page: 持仓管理
# ---------------------------------------------------------------------------
def render_portfolio(cfg):
    st.subheader("持仓 + 评级 + 防守状态")

    # Demo: let user input a mock position
    with st.expander("模拟持仓输入"):
        c1, c2, c3 = st.columns(3)
        code = c1.text_input("股票代码", value="603986")
        entry_price = c2.number_input("入场价", value=663.49)
        stop_loss = c3.number_input("止损价", value=630.0)

    if st.button("评测持仓"):
        from zhixing_quant.data.tdx_loader import load_daily
        from zhixing_quant.indicators.brick import add_brick_indicators
        from zhixing_quant.indicators.macd import add_macd
        from zhixing_quant.indicators.dual_line import add_dual_line
        from zhixing_quant.indicators.volume_price import add_volume_price
        from zhixing_quant.indicators.key_kline import detect_key_k
        from zhixing_quant.signals.macd_veto import macd_veto
        from zhixing_quant.signals.distribution import detect_distribution
        from zhixing_quant.signals.sell_s import detect_s_series
        from zhixing_quant.portfolio.rater import HoldingRater
        from zhixing_quant.portfolio.defense import DefenseEngine
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=260)).strftime("%Y%m%d")
        try:
            daily = load_daily(code, start_date=start_date, end_date=end_date, adjust="qfq", cache=True)
            if len(daily) < 60:
                st.warning("数据不足，无法评测。")
                return
            enriched = add_brick_indicators(daily, cfg)
            enriched = add_macd(enriched)
            enriched = add_dual_line(enriched)
            enriched = add_volume_price(enriched)
            enriched = detect_key_k(enriched)
            enriched = detect_s_series(enriched, cfg)
            enriched = detect_distribution(enriched, 1e9, cfg)

            row = enriched.iloc[-1]

            # Defense
            engine = DefenseEngine()
            pos = {"stop_loss": stop_loss, "take_profit": entry_price * 1.15}
            exit_signal = engine.evaluate(enriched, -1, pos)

            # Rating
            rater = HoldingRater()
            rating = rater.rate(enriched, -1, pos)

            # Display
            r1, r2, r3 = st.columns(3)
            r1.metric("评级", f"{'★' * rating.stars}{'☆' * (5 - rating.stars)}")
            r2.metric("风险等级", rating.risk_level)
            r3.metric("收盘价", f"{float(row['close']):.2f}")

            if exit_signal:
                st.warning(f"防守触发: {exit_signal.reason} (优先级 P{exit_signal.priority})")
            else:
                st.success("未触发任何防守规则")

            st.caption(f"评级原因: {', '.join(rating.reasons) if rating.reasons else '无异常'}")

            # Show key signal flags
            flags = {
                "MACD 零轴上": bool(row.get("above_zero", False)),
                "死叉加速": bool(row.get("false_death_cross", False)),
                "S1 止损": bool(row.get("sig_s1", False)),
                "S2 止盈": bool(row.get("sig_s2", False)),
                "S3 危险": bool(row.get("sig_s3", False)),
                "主力出货": bool(row.get("sig_distribution", False)),
            }
            st.write("**信号状态:**")
            flag_cols = st.columns(3)
            for i, (k, v) in enumerate(flags.items()):
                flag_cols[i % 3].write(f"{'✅' if v else '⬜'} {k}")

        except Exception as exc:
            st.error(f"评测失败: {exc}")


# ---------------------------------------------------------------------------
# Page: K线图
# ---------------------------------------------------------------------------
def render_chart(cfg):
    st.subheader("K线图 + 指标叠加")
    st.caption("先在「候选清单」中点击某只股票查看 K 线图，或在此输入代码查询。")

    c1, c2 = st.columns([2, 1])
    with c1:
        code = st.text_input("股票代码", value="603986")
    with c2:
        days = st.slider("显示天数", 30, 120, 60)

    if st.button("加载K线", type="primary"):
        from zhixing_quant.data.tdx_loader import load_daily
        from zhixing_quant.indicators.tdx import ma
        from datetime import datetime, timedelta
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=260)).strftime("%Y%m%d")
        try:
            daily = load_daily(code, start_date=start_date, end_date=end_date, adjust="qfq", cache=True)
            df = daily.tail(days).copy().reset_index()
            if "date" not in df.columns:
                df.rename(columns={df.columns[0]: "date"}, inplace=True)
            df["date"] = df["date"].astype(str)
            close = df["close"]
            df["ma5"] = ma(close, 5)
            df["ma10"] = ma(close, 10)
            df["ma20"] = ma(close, 20)

            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                row_heights=[0.7, 0.3], vertical_spacing=0.05,
            )
            fig.add_trace(go.Candlestick(
                x=df["date"], open=df["open"], high=df["high"],
                low=df["low"], close=df["close"], name="K线",
                increasing_line_color="#d64545", decreasing_line_color="#2f9e44",
            ), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["date"], y=df["ma5"], name="MA5", line=dict(width=1)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["date"], y=df["ma10"], name="MA10", line=dict(width=1)), row=1, col=1)
            fig.add_trace(go.Scatter(x=df["date"], y=df["ma20"], name="MA20", line=dict(width=1)), row=1, col=1)
            fig.update_layout(
                title=f"{code} K线图",
                xaxis_rangeslider_visible=False,
                height=600,
            )
            st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            st.error(f"加载失败: {exc}")


# ---------------------------------------------------------------------------
# Page: 绩效
# ---------------------------------------------------------------------------
def render_performance(cfg):
    st.subheader("近30日绩效")
    st.caption("回测引擎接入后可在此展示策略绩效指标。")

    # Show backtest config for reference
    bt = cfg.get("backtest", {})
    c1, c2, c3 = st.columns(3)
    c1.metric("初始资金", f"{bt.get('initial_capital', 500000):,.0f}")
    c2.metric("佣金率", f"{bt.get('commission', 0.00025):.4%}")
    c3.metric("印花税", f"{bt.get('stamp_tax', 0.001):.3%}")

    st.info("回测引擎已就绪（zhixing_quant/backtest/engine.py），接入真实数据后即可运行回测。")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    cfg = get_config()

    page = st.sidebar.radio(
        "导航",
        ["择时状态", "候选清单", "持仓管理", "K线图", "绩效"],
    )
    if page == "择时状态":
        render_timing(cfg)
    elif page == "候选清单":
        render_candidates(cfg)
    elif page == "持仓管理":
        render_portfolio(cfg)
    elif page == "K线图":
        render_chart(cfg)
    elif page == "绩效":
        render_performance(cfg)


if __name__ == "__main__":
    main()
