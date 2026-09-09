"""知行量化 Web 服务。

    streamlit run app.py

页面按规格 01 的每日决策循环组织，而不是按功能清单：

    今日   择时 → 防守 → 进攻 → 下单计划（这就是 daily_cycle 的可视化）
    持仓   持仓明细、成交记录、账户设置
    战法   六套战法各自扫描
    回测   策略回测
    个股   K线 + 全部指标
    数据   本地行情库同步
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="知行量化", layout="wide", page_icon="📈")

from zhixing_quant.ui import components as C          # noqa: E402
from zhixing_quant.ui.theme import (                  # noqa: E402
    apply_layout, candlestick_colors, inject_css, MA_FAST, SIGNAL_LINE,
)

inject_css()

BOOKS = {"swing": "波段账户", "scalp": "超短账户"}


@st.cache_resource
def get_config():
    from zhixing_quant.config import load_config
    return load_config()


@st.cache_data(ttl=60, show_spinner=False)
def _health():
    """Cache the expensive database-wide health check between reruns."""
    from zhixing_quant.data.tdx_loader import data_health
    return data_health()


def _store(cfg):
    from zhixing_quant.data.sync import db_path
    from zhixing_quant.portfolio.store import PortfolioStore
    return PortfolioStore(db_path(cfg))


def _strategies():
    from zhixing_quant.scanner.strategy_scan import available
    return available()


# ---------------------------------------------------------------------------
# 今日
# ---------------------------------------------------------------------------

def page_today(cfg, book):
    strategies = _strategies()
    c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
    names = list(strategies)
    default = next((n for n, m in strategies.items() if m["book"] == book), names[0])
    strategy = c1.selectbox(
        "战法", names, index=names.index(default),
        format_func=lambda n: f"{strategies[n]['label']}（规格 {strategies[n].get('spec','')}）",
    )
    scan_date = c2.date_input("信号日期", value=datetime.now())
    c3.write("")
    c3.write("")
    limit = c3.number_input("扫描上限", 0, 5000, 0, 50,
                            help="0 表示扫描全部股票。")
    c4.write("")
    c4.write("")
    run = c4.button("运行", type="primary", use_container_width=True)

    if run:
        from zhixing_quant.executor.daily_workflow import run_daily_cycle

        bar = st.progress(0.0, text="扫描中...")
        try:
            result = run_daily_cycle(
                cfg, date=scan_date.strftime("%Y%m%d"), book=book,
                strategy_key=strategy,
                limit_universe=int(limit) or None,
                progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0),
                                                   text=f"扫描中... {d}/{t}"),
            )
        except Exception as exc:
            bar.empty()
            st.error(f"运行失败：{exc}")
            return
        bar.empty()
        st.session_state[f"today_{book}"] = result

    result = st.session_state.get(f"today_{book}")
    if result is None:
        C.empty_state("还没有今日决策",
                      "选好战法和日期后点「运行」。系统会按 择时 → 防守 → 进攻 → 下单 的顺序走一遍。")
        return

    C.page_head("今日", f"{result.date} 收盘后 · {BOOKS[book]}")

    # 阶段 1
    C.section("择时", "决定今天是否允许开新仓",
              f"{'允许开仓 · 上限 ' + format(result.max_total_pct, '.0%') if result.allow_open else '禁止开仓'}")
    C.regime_card(result)

    # 阶段 2
    need = len(result.actions_needed)
    C.section("防守", "先处理持仓，优先级高于任何买入",
              f"{need} 个需要动作" if need else f"{len(result.reviews)} 个持仓正常",
              warn=need > 0)
    if not result.reviews:
        C.empty_state("当前没有持仓", "到「持仓」页手工登记，或在下面的下单计划里记录建仓。")
    else:
        for v in result.reviews:
            C.holding_card(v)
        C.defense_coverage_chips(result.defense_coverage)

    # 阶段 3
    label = _strategies().get(result.candidates.attrs.get("key", ""), {}).get("label", "")
    total_candidates = int(result.candidates.attrs.get(
        "total_matches", len(result.candidates)
    ))
    count_label = (f"命中 {total_candidates} 只（显示前 {len(result.candidates)} 只）"
                   if total_candidates > len(result.candidates)
                   else f"命中 {len(result.candidates)} 只")
    C.section("进攻", "择时放行后才评估",
              result.offense_disabled_reason and "已禁用" or count_label)
    if result.offense_disabled_reason:
        st.info(result.offense_disabled_reason)
    elif result.candidates.empty:
        C.empty_state("今天没有符合条件的标的",
                      "可以换个战法试试，或到「数据」页确认行情已同步到最新交易日。")
    else:
        C.candidate_table(result.candidates)

    # 阶段 4
    ok = [p for p in result.plans if not p.blocked]
    blocked = [p for p in result.plans if p.blocked]
    if result.plans:
        C.section("下单计划", "含止损与风险敞口，缺止损不允许下单",
                  f"{len(ok)} 条可执行 · {len(blocked)} 条拦截")
        for p in result.plans:
            C.plan_card(p)
        if ok:
            st.markdown("**记录建仓**（按计划的股数和止损写入持仓）")
            cols = st.columns(min(len(ok), 4))
            for i, p in enumerate(ok):
                if cols[i % 4].button(f"{p.code} {p.name}", key=f"buy_{p.code}"):
                    store = _store(cfg)
                    try:
                        store.open_position(
                            p.code, p.shares, p.entry_price, p.stop_loss,
                            book=book, name=p.name, strategy=p.strategy,
                            take_profit=p.take_profit, entry_date=result.date,
                        )
                        st.success(f"已记录 {p.code} {p.shares} 股，止损 {p.stop_loss:.2f}")
                    except ValueError as exc:
                        st.error(str(exc))
                    finally:
                        store.close()

    C.notes(result.notes)


# ---------------------------------------------------------------------------
# 持仓
# ---------------------------------------------------------------------------

def page_positions(cfg, book):
    C.page_head("持仓", BOOKS[book])
    store = _store(cfg)
    try:
        positions = store.positions(book)
        cash = store.cash(book)

        from zhixing_quant.data.tdx_loader import fetch_a_spot
        try:
            spot = fetch_a_spot()
            prices = dict(zip(spot["code"], spot["close"]))
        except Exception:
            prices = {}

        equity = store.equity(prices, book)
        stats = store.stats(book)
        C.stat_row([
            ("账户权益", C.money(equity), ""),
            ("可用现金", C.money(cash), ""),
            ("累计盈亏", C.money(stats["total_pnl"]),
             "up" if stats["total_pnl"] >= 0 else "down"),
            ("胜率", f"{stats['win_rate']:.0%}" if stats["trades"] else "—", ""),
        ])

        C.section("当前持仓", "", f"{len(positions)} 只")
        if not positions:
            C.empty_state("还没有持仓记录",
                          "在「今日」页跑一遍决策，命中后点「记录建仓」；或在下面手工登记。")
        else:
            rows = []
            for p in positions:
                px = float(prices.get(p["code"], p["entry_price"]))
                rows.append({
                    "code": p["code"], "name": p["name"], "close": px,
                    "pct_chg": (px / p["entry_price"] - 1) * 100,
                    "stop_loss": p["stop_loss"],
                    "take_profit": p.get("take_profit") or float("nan"),
                })
            C.candidate_table(pd.DataFrame(rows))

            st.markdown("**平仓**")
            cc = st.columns([2, 1, 1, 1])
            code = cc[0].selectbox("标的", [p["code"] for p in positions],
                                   format_func=lambda c: f"{c} {dict((p['code'], p['name']) for p in positions).get(c,'')}")
            price = cc[1].number_input("成交价", 0.01, 100000.0,
                                       float(prices.get(code, 10.0)), 0.01)
            reason = cc[2].selectbox("原因", ["止盈", "止损", "防守触发", "手工"])
            cc[3].write("")
            cc[3].write("")
            if cc[3].button("平仓", use_container_width=True):
                r = store.close_position(code, price, book=book, reason=reason)
                st.success(f"已平仓 {code}，盈亏 {r['pnl']:,.0f}") if r else st.error("没找到该持仓")
                st.rerun()

        with st.expander("手工登记建仓 / 设置资金"):
            f = st.columns(5)
            mcode = f[0].text_input("代码", key="m_code")
            mshares = f[1].number_input("股数", 100, 10_000_000, 100, 100, key="m_sh")
            mpx = f[2].number_input("买入价", 0.01, 100000.0, 10.0, 0.01, key="m_px")
            mstop = f[3].number_input("止损价", 0.0, 100000.0, 9.5, 0.01, key="m_st")
            f[4].write("")
            f[4].write("")
            if f[4].button("登记", use_container_width=True):
                try:
                    store.open_position(mcode.strip().zfill(6), int(mshares), mpx, mstop,
                                        book=book)
                    st.success("已登记")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            nc = st.columns([2, 1])
            newcash = nc[0].number_input("账户现金", 0.0, 1e9, float(cash), 1000.0)
            nc[1].write("")
            nc[1].write("")
            if nc[1].button("保存现金", use_container_width=True):
                store.set_cash(newcash, book=book)
                st.rerun()

        trades = store.trades(book)
        if not trades.empty:
            C.section("成交记录", "", f"{len(trades)} 笔")
            st.dataframe(trades.drop(columns=["id", "book"]), use_container_width=True,
                         hide_index=True, height=280)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 战法
# ---------------------------------------------------------------------------

def page_strategies(cfg, book):
    C.page_head("战法", "六套战法独立扫描")
    strategies = _strategies()

    c1, c2, c3 = st.columns([2, 1, 1])
    name = c1.selectbox("选择战法", list(strategies),
                        format_func=lambda n: f"{strategies[n]['label']}"
                                              f"（{BOOKS[strategies[n]['book']]}）")
    limit = c2.number_input("扫描上限", 0, 5000, 0, 50,
                            help="0 表示扫描全部股票。")
    c3.write("")
    c3.write("")
    if c3.button("扫描", type="primary", use_container_width=True):
        from zhixing_quant.scanner.strategy_scan import scan_strategy

        bar = st.progress(0.0, text="扫描中...")
        try:
            cands, charts = scan_strategy(
                name, cfg, limit_universe=int(limit) or None,
                progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0),
                                                   text=f"扫描中... {d}/{t}"))
        except Exception as exc:
            bar.empty()
            st.error(f"扫描失败：{exc}")
            return
        bar.empty()
        st.session_state["strat_res"] = (name, cands, charts)

    got = st.session_state.get("strat_res")
    if got is None:
        C.empty_state("选一套战法开始扫描",
                      "每套战法的入场条件、止损位和适用账户都不同，规格 06 有完整说明。")
        return

    sname, cands, charts = got
    meta = strategies.get(sname, {})
    total_matches = int(cands.attrs.get("total_matches", len(cands)))
    count_label = (f"命中 {total_matches} 只（显示前 {len(cands)} 只）"
                   if total_matches > len(cands) else f"命中 {len(cands)} 只")
    C.section(meta.get("label", sname),
              f"规格 {meta.get('spec','')} · {BOOKS.get(meta.get('book','swing'))}",
              count_label)
    if cands.empty:
        C.empty_state("今天没有命中",
                      "这套战法的信号本来就不是每天都有。可以换个战法或换个日期。")
        return
    C.candidate_table(cands)

    codes = cands["code"].tolist()
    sel = st.selectbox("查看K线", codes,
                       format_func=lambda c: f"{c} {cands.loc[cands['code']==c,'name'].iloc[0]}")
    if sel in charts:
        _draw_kline(charts[sel], sel)


# ---------------------------------------------------------------------------
# 回测 / 个股 / 数据
# ---------------------------------------------------------------------------

def page_backtest(cfg, book):
    C.page_head("回测", "T 日收盘出信号，T+1 开盘成交")
    strategies = _strategies()
    c = st.columns(4)
    name = c[0].selectbox("战法", list(strategies),
                          format_func=lambda n: strategies[n]["label"])
    start = c[1].date_input("开始", value=datetime.now() - timedelta(days=730))
    end = c[2].date_input("结束", value=datetime.now())
    pool = c[3].number_input("股票池", 20, 2000, 200, 10)

    if st.button("运行回测", type="primary"):
        from zhixing_quant.backtest.engine import BacktestEngine
        from zhixing_quant.data.tdx_loader import (fetch_a_spot, filter_universe,
                                                   load_daily_many)
        from zhixing_quant.indicators.pipeline import run_pipeline

        try:
            with st.spinner("准备数据..."):
                uni = filter_universe(fetch_a_spot(), cfg).head(int(pool))
                warm = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y%m%d")
                raw = load_daily_many(uni["code"].tolist(), start_date=warm,
                                      end_date=end.strftime("%Y%m%d"))
                data = {}
                for code, df in raw.items():
                    if len(df) < 130:
                        continue
                    data[code] = run_pipeline(df, cfg, name if name in
                                              ("brick", "b1", "b2") else "full").df
            sig = {"brick": "sig_brick", "b1": "sig_b1", "b2": "sig_b2"}.get(name)
            if sig is None:
                st.warning(
                    f"{strategies[name]['label']} 的信号需要逐根 K 线求值，"
                    "回测引擎目前只支持预先算好信号列的战法（砖型图 / B1 / B2）。"
                    "其余战法的回测支持在下一阶段。")
                return
            with st.spinner(f"回测 {len(data)} 只..."):
                res = BacktestEngine(cfg).run(data, signal_col=sig,
                                              start_date=start.strftime("%Y%m%d"),
                                              end_date=end.strftime("%Y%m%d"))
        except Exception as exc:
            st.error(f"回测失败：{exc}")
            return
        st.session_state["bt"] = res

    res = st.session_state.get("bt")
    if res is None:
        C.empty_state("配置参数后运行回测",
                      "参数多数标记为 [CALIBRATE]，当前是规格给的初始猜测值，需要用你的数据校准。")
        return
    m = res.metrics
    C.stat_row([("总收益", f"{m.get('total_return',0):.2%}",
                 "up" if m.get("total_return", 0) >= 0 else "down"),
                ("年化", f"{m.get('annualized_return',0):.2%}",
                 "up" if m.get("annualized_return", 0) >= 0 else "down"),
                ("最大回撤", f"{m.get('max_drawdown',0):.2%}", ""),
                ("夏普", f"{m.get('sharpe',0):.2f}", "")])
    C.stat_row([("胜率", f"{m.get('win_rate',0):.1%}", ""),
                ("盈亏比", str(m.get("profit_loss_ratio", "—")), ""),
                ("交易笔数", m.get("total_trades", 0), ""),
                ("期末权益", f"{m.get('final_equity',0):,.0f}", "")])

    if len(res.equity_curve) > 1:
        import plotly.graph_objects as go
        eq = res.equity_curve
        fig = go.Figure(go.Scatter(x=eq.index, y=eq.values, name="权益",
                                   line=dict(width=2, color=SIGNAL_LINE)))
        apply_layout(fig, height=320, title="资金曲线")
        st.plotly_chart(fig, use_container_width=True)
    tf = res.trades_frame()
    if not tf.empty:
        st.dataframe(tf.sort_values("entry_date", ascending=False),
                     use_container_width=True, hide_index=True, height=300)


def page_chart(cfg, book):
    C.page_head("个股", "K线与全部指标")
    c = st.columns([1, 1, 1])
    code = c[0].text_input("代码", value="600000")
    days = c[1].slider("显示天数", 60, 300, 140)
    c[2].write("")
    c[2].write("")
    if c[2].button("加载", type="primary", use_container_width=True):
        from zhixing_quant.data.tdx_loader import load_daily
        from zhixing_quant.indicators.pipeline import defense_coverage, run_pipeline

        try:
            df = load_daily(code.strip().zfill(6), adjust="qfq")
            if df.empty:
                st.warning("库里没有这只股票。确认代码，或到「数据」页同步。")
                return
            r = run_pipeline(df, cfg, "full")
        except Exception as exc:
            st.error(f"加载失败：{exc}")
            return
        if df.attrs.get("adjust") == "none":
            st.info("当前是**不复权**价格。跑一次 `--xdxr` 后才是前复权。")
        for w in r.warnings():
            st.warning(w)
        _draw_kline(r.df.tail(days), code)
        C.defense_coverage_chips(defense_coverage(r.df))

        sig_cols = [c2 for c2 in r.df.columns if c2.startswith("sig_")]
        hits = {c2: int(r.df[c2].fillna(False).astype(bool).sum()) for c2 in sig_cols}
        hits = {k: v for k, v in hits.items() if v}
        if hits:
            C.section("历史信号统计", f"最近 {len(r.df)} 根K线")
            st.dataframe(pd.DataFrame([{"信号": k, "触发次数": v} for k, v in
                                       sorted(hits.items(), key=lambda x: -x[1])]),
                         use_container_width=True, hide_index=True)


def page_data(cfg, book):
    C.page_head("数据", "本地通达信行情库")
    h = _health()
    if not h.get("ok"):
        st.error(h.get("message", "行情库未就绪"))
    else:
        C.stat_row([("股票数", f"{h['codes']:,}", ""), ("K线总数", f"{h['bars']:,}", ""),
                    ("最新交易日", str(h["date_max"] or "—"), ""),
                    ("库大小", f"{h['db_size_mb']} MB", "")])
        for w in h.get("warnings", []):
            st.warning(w)
        if not h.get("warnings"):
            st.success(f"数据正常，上次同步 {h['last_sync']}")

    c = st.columns([1, 1, 1, 2])
    names = c[0].checkbox("更新名称")
    xdxr = c[1].checkbox("更新除权除息", help="需要 pytdx")
    full = c[2].checkbox("全量重建")
    if st.button("立即同步", type="primary"):
        cmd = [sys.executable, "-m", "zhixing_quant.data.sync"]
        cmd += ["--full"] * full + ["--names"] * names + ["--xdxr"] * xdxr
        with st.spinner("同步中..."):
            p = subprocess.run(cmd, capture_output=True, text=True)
        st.code(p.stdout or p.stderr, language="text")
        from zhixing_quant.data.tdx_loader import reset_store
        reset_store()
        _health.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# K线
# ---------------------------------------------------------------------------

def _draw_kline(df: pd.DataFrame, title: str = ""):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from zhixing_quant.indicators.tdx import ma
    from zhixing_quant.ui.theme import DOWN, UP

    d = df.copy()
    x = d.index.strftime("%Y-%m-%d")
    sub = "brick_value" in d.columns
    fig = make_subplots(rows=3 if sub else 2, cols=1, shared_xaxes=True,
                        row_heights=[.62, .18, .20] if sub else [.75, .25],
                        vertical_spacing=.03)
    fig.add_trace(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"],
                                 close=d["close"], name="K线", **candlestick_colors()),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=ma(d["close"], 20), name="MA20",
                             line=dict(width=1.2, color=MA_FAST)), row=1, col=1)
    if "yellow_line" in d.columns:
        fig.add_trace(go.Scatter(x=x, y=d["yellow_line"], name="知行多空线",
                                 line=dict(width=1.8, color=SIGNAL_LINE)), row=1, col=1)
    if "white_line" in d.columns:
        fig.add_trace(go.Scatter(x=x, y=d["white_line"], name="白线",
                                 line=dict(width=1, color="#8A93A0")), row=1, col=1)
    for col, mark, color in (("sig_brick", "triangle-up", UP), ("sig_b1", "triangle-up", UP),
                             ("sig_strategy", "triangle-up", UP)):
        if col in d.columns:
            hit = d[d[col].fillna(False).astype(bool)]
            if not hit.empty:
                fig.add_trace(go.Scatter(x=hit.index.strftime("%Y-%m-%d"),
                                         y=hit["low"] * .97, mode="markers", name="信号",
                                         marker=dict(symbol=mark, size=11, color=color)),
                              row=1, col=1)
                break
    fig.add_trace(go.Bar(x=x, y=d["vol"], name="成交量",
                         marker_color=[UP if c >= o else DOWN
                                       for o, c in zip(d["open"], d["close"])]),
                  row=2, col=1)
    if sub:
        fig.add_trace(go.Bar(x=x, y=d["brick_value"], name="砖型图",
                             marker_color=[UP if v >= 0 else DOWN
                                           for v in d["brick_value"]]), row=3, col=1)
    apply_layout(fig, height=680, title=title)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------

PAGES = {"今日": page_today, "持仓": page_positions, "战法": page_strategies,
         "回测": page_backtest, "个股": page_chart, "数据": page_data}


def main():
    cfg = get_config()
    st.sidebar.markdown(
        "<div style='padding:0 4px 14px'><b style='font-size:16px'>知行</b></div>",
        unsafe_allow_html=True)
    book = st.sidebar.radio("账户", list(BOOKS), format_func=BOOKS.get,
                            horizontal=True, label_visibility="collapsed")
    page = st.sidebar.radio("页面", list(PAGES), label_visibility="collapsed")

    h = _health()
    if h.get("ok"):
        store = _store(cfg)
        try:
            positions = store.positions(book)
            prices = {}
            if positions:
                from zhixing_quant.data.tdx_loader import fetch_a_spot
                try:
                    spot = fetch_a_spot()
                    prices = dict(zip(spot["code"], spot["close"]))
                except Exception:
                    pass
            eq = store.equity(prices, book)
            held = sum(prices.get(p["code"], p["entry_price"]) * p["shares"]
                       for p in positions)
            C.sidebar_status(eq, store.cash(book), held / eq if eq else 0.0,
                             str(h.get("date_max", "")), bool(h.get("warnings")))
        finally:
            store.close()
    else:
        st.sidebar.error("数据未就绪")

    PAGES[page](cfg, book)


if __name__ == "__main__":
    main()
