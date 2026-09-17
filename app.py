"""知行量化 Web 服务。

    streamlit run app.py

页面按规格 01 的每日决策循环组织：
    今日 · 持仓 · 战法 · 回测 · 个股 · 数据
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="知行量化", layout="wide", page_icon="📈",
                   initial_sidebar_state="expanded")

from zhixing_quant.ui import components as C            # noqa: E402
from zhixing_quant.ui import param_schema as PS         # noqa: E402
from zhixing_quant.ui import theme as T                 # noqa: E402
from zhixing_quant.ui.theme import (                    # noqa: E402
    MA_FAST, SIGNAL_LINE, apply_layout, candlestick_colors, inject_css,
)

inject_css()

BOOKS = {"swing": "波段账户", "scalp": "超短账户"}
PAGE_ORDER = ["今日", "持仓", "战法", "回测", "个股", "数据"]


@st.cache_resource
def get_config():
    from zhixing_quant.config import load_config
    return load_config()


@st.cache_data(ttl=60, show_spinner=False)
def _health():
    from zhixing_quant.data.tdx_loader import data_health
    return data_health()


def _store(cfg):
    from zhixing_quant.data.sync import db_path
    from zhixing_quant.portfolio.store import PortfolioStore
    return PortfolioStore(db_path(cfg))


def _strategies():
    from zhixing_quant.scanner.strategy_scan import available
    return available()


def _param_widget(p: PS.Param, cfg: dict, key: str):
    """按声明渲染一个参数控件，返回当前值。"""
    cur = PS.get_value(cfg, p.key, p.default)
    label = p.label + ("  🔒" if p.tag == "LOCKED" else "")
    help_txt = (p.help + ("（规格标为 LOCKED，是规则本身，改动前想清楚）"
                          if p.tag == "LOCKED" else "")) or None
    if p.kind == "bool":
        return st.checkbox(label, value=bool(cur), key=key, help=help_txt)
    if p.kind == "choice":
        opts = list(p.choices)
        idx = opts.index(cur) if cur in opts else 0
        return st.selectbox(label, opts, index=idx, key=key, help=help_txt,
                            format_func=PS.kind_label)
    if p.kind == "int":
        return int(st.number_input(label, int(p.lo), int(p.hi), int(cur),
                                   int(p.step or 1), key=key, help=help_txt))
    return float(st.number_input(label, float(p.lo), float(p.hi), float(cur),
                                 float(p.step or 0.01), key=key, help=help_txt,
                                 format="%.5f" if (p.step or 1) < 0.001 else "%.4f"))


# ---------------------------------------------------------------------------
# 今日
# ---------------------------------------------------------------------------

def page_today(cfg, book):
    strategies = _strategies()
    names = list(strategies)
    c1, c2, c3, c4 = st.columns([2.2, 1.6, 1.2, 1])
    default = next((n for n, m in strategies.items() if m["book"] == book), names[0])
    strategy = c1.selectbox("战法", names, index=names.index(default),
                            format_func=lambda n: strategies[n]["label"])
    scan_date = c2.date_input("信号日期", value=datetime.now())
    limit = c3.number_input("扫描范围", 50, 6000, 500, 50,
                            help="按成交额降序取前 N 只。设大会更慢")
    c4.write("")
    c4.write("")
    run = c4.button("运行", type="primary", use_container_width=True)

    if run:
        from zhixing_quant.executor.daily_workflow import run_daily_cycle
        bar = st.progress(0.0, text="运行中...")
        try:
            result = run_daily_cycle(
                cfg, date=scan_date.strftime("%Y%m%d"), book=book,
                strategy_key=strategy, limit_universe=int(limit),
                progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0),
                                                   text=f"扫描 {d}/{t}"))
        except Exception as exc:
            bar.empty()
            st.error(f"运行失败：{exc}")
            return
        bar.empty()
        st.session_state[f"today_{book}"] = result

    result = st.session_state.get(f"today_{book}")
    if result is None:
        C.empty_state("还没有今日决策",
                      "选好战法和日期后点「运行」。系统按 择时 → 防守 → 进攻 → 下单 的顺序走一遍，"
                      "这个顺序是规格里锁定的，防守永远优先于买入。")
        return

    C.page_head("今日", f"{result.date} 收盘后 · {BOOKS[book]}")

    C.section("择时", "决定今天是否允许开新仓",
              f"允许开仓 · 上限 {result.max_total_pct:.0%}" if result.allow_open else "禁止开仓")
    C.regime_card(result)

    need = len(result.actions_needed)
    C.section("防守", "先处理持仓，优先级高于任何买入",
              f"{need} 个需要动作" if need else f"{len(result.reviews)} 个持仓正常",
              warn=need > 0)
    if not result.reviews:
        C.empty_state("当前没有持仓", "到「持仓」页登记，或在下面的下单计划里记录建仓。")
    else:
        for v in result.reviews:
            C.holding_card(v)
        C.defense_coverage_chips(result.defense_coverage)

    C.section("进攻", "择时放行后才评估",
              "已禁用" if result.offense_disabled_reason
              else f"命中 {len(result.candidates)} 只")
    if result.offense_disabled_reason:
        st.info(result.offense_disabled_reason)
    elif result.candidates.empty:
        C.empty_state("今天没有符合条件的标的",
                      "换个战法试试，或到「数据」页确认行情已同步到最新交易日。")
    else:
        _list_and_chart(result.candidates, result.charts, key="today")

    ok = [p for p in result.plans if not p.blocked]
    if result.plans:
        C.section("下单计划", "含止损与风险敞口，缺止损不允许下单",
                  f"{len(ok)} 条可执行 · {len(result.plans) - len(ok)} 条拦截")
        for p in result.plans:
            C.plan_card(p)
        if ok:
            st.caption("记录建仓（按计划的股数和止损写入持仓）")
            cols = st.columns(min(len(ok), 5))
            for i, p in enumerate(ok):
                if cols[i % 5].button(f"{p.code}", key=f"buy_{p.code}",
                                      use_container_width=True):
                    store = _store(cfg)
                    try:
                        store.open_position(p.code, p.shares, p.entry_price,
                                            p.stop_loss, book=book, name=p.name,
                                            strategy=p.strategy,
                                            take_profit=p.take_profit,
                                            entry_date=result.date)
                        st.success(f"已记录 {p.code} {p.shares} 股，止损 {p.stop_loss:.2f}")
                    except ValueError as exc:
                        st.error(str(exc))
                    finally:
                        store.close()
    C.notes(result.notes)


# ---------------------------------------------------------------------------
# 战法
# ---------------------------------------------------------------------------

def page_strategies(cfg, book):
    C.page_head("战法", "四套战法独立扫描")
    strategies = _strategies()

    c1, c2, c3, c4 = st.columns([2.2, 1.2, 1.2, 1])
    name = c1.selectbox("战法", list(strategies),
                        format_func=lambda n: f"{strategies[n]['label']}"
                                              f"（{BOOKS[strategies[n]['book']]}）")
    scan_date = c2.date_input("日期", value=datetime.now(), key="strat_date")
    limit = c3.number_input("扫描范围", 50, 6000, 500, 50, key="strat_limit")
    c4.write("")
    c4.write("")
    go = c4.button("扫描", type="primary", use_container_width=True)

    with st.expander("战法参数（改完重新扫描生效）"):
        params = PS.params_for(name)
        if not params:
            st.caption("这套战法没有声明可调参数。")
            overrides = {}
        else:
            overrides = {}
            cols = st.columns(min(len(params), 4))
            for i, p in enumerate(params):
                with cols[i % len(cols)]:
                    overrides[p.key] = _param_widget(p, cfg, f"sp_{name}_{p.key}")
            changed = PS.diff_from_default(overrides)
            if changed:
                st.caption("已改动：" + "、".join(f"{k}={v}" for k, v in changed.items()))
        st.caption("规格 10 里这些参数标为 [CALIBRATE]，给的是初始猜测值，需要用回测校准。")

    if go:
        from zhixing_quant.scanner.strategy_scan import scan_strategy
        local = PS.apply_overrides(cfg, overrides)
        local.setdefault("universe", {})["max_candidates"] = 9999   # 不截断
        bar = st.progress(0.0, text="扫描中...")
        try:
            cands, charts = scan_strategy(
                name, local, end_date=scan_date.strftime("%Y%m%d"),
                limit_universe=int(limit),
                progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0),
                                                   text=f"扫描 {d}/{t}"))
        except Exception as exc:
            bar.empty()
            st.error(f"扫描失败：{exc}")
            return
        bar.empty()
        st.session_state["strat_res"] = (name, cands, charts, int(limit))

    got = st.session_state.get("strat_res")
    if got is None:
        C.empty_state("选一套战法开始扫描",
                      "每套战法的入场条件、止损位和适用账户都不同，规格 06 有完整说明。")
        return

    sname, cands, charts, scanned = got
    meta = strategies.get(sname, {})
    # 扫描过程中被跳过的标的（K线不足 / 指标报错）必须说出来，
    # 否则「没命中」和「根本没算成」在界面上无法区分。
    for w in cands.attrs.get("warnings", []):
        st.warning(w)
    rate = len(cands) / max(scanned, 1)
    C.section(meta.get("label", sname),
              f"规格 {meta.get('spec','')} · 扫描 {scanned} 只",
              f"命中 {len(cands)} 只（{rate:.1%}）",
              warn=rate > 0.15)
    if rate > 0.15:
        st.warning(f"命中率 {rate:.0%} 偏高。条件可能过松，参数需要校准。")
    if cands.empty:
        C.empty_state("今天没有命中",
                      "这套战法的信号本来就不是每天都有。连续一周为 0 才说明条件过严。")
        return
    _list_and_chart(cands, charts, key="strat")


def _list_and_chart(cands: pd.DataFrame, charts: dict, key: str):
    """左列表 + 右K线。点列表里哪只，右边就画哪只。

    st.dataframe 的行选择事件在 Streamlit 1.40 上点击只聚焦单元格、
    不触发回调，所以列表用按钮行实现——点哪行画哪行。
    """
    left, right = st.columns([5, 7], gap="medium")

    with left:
        header = st.columns([2.2, 2.6, 1.4, 1.4, 1.6, 1])
        for col, text in zip(header, ["代码", "名称", "收盘", "成交额", "止损", "信心"]):
            col.markdown(f"<span style='color:{T.TEXT_3};font-size:12px'>{text}</span>",
                         unsafe_allow_html=True)
        default_code = str(cands.iloc[0]["code"])
        picked = st.session_state.get(f"picked_{key}", default_code)
        if not cands["code"].astype(str).eq(picked).any():
            picked = default_code

        with st.container(height=500):
            for _, row in cands.iterrows():
                code = str(row["code"])
                name = str(row.get("name", ""))[:6]
                label = (f"{code}  {name}　{row['close']:.2f}"
                         f"　{(row['amount'] / 1e8):.1f}亿"
                         f"　{row['stop_loss']:.2f}　{int(row.get('confidence', 0))}")
                if st.button(label, key=f"row_{key}_{code}",
                             use_container_width=True,
                             type="primary" if code == picked else "secondary"):
                    picked = code
                    st.session_state[f"picked_{key}"] = code
                    st.rerun()   # 让选中高亮立即跟着点选走
        st.caption(f"共 {len(cands)} 只，点任意一行看右边的K线")

    row = cands[cands["code"].astype(str) == picked].iloc[0]
    code = str(row["code"])
    with right:
        nm = str(row.get("name", ""))
        st.markdown(f"**{code}** {nm}")
        if code in charts:
            _draw_kline(charts[code], code, height=520)
        else:
            st.info("这只标的没有图表数据。")


# ---------------------------------------------------------------------------
# 回测
# ---------------------------------------------------------------------------

def page_backtest(cfg, book):
    from zhixing_quant.backtest.runner import BACKTESTABLE
    from zhixing_quant.data.universe import BOARDS, UniverseSpec

    C.page_head("回测", "T 日收盘出信号，T+1 开盘成交")
    strategies = _strategies()
    names = [n for n in strategies if n in BACKTESTABLE]
    blocked = [strategies[n]["label"] for n in strategies if n not in BACKTESTABLE]

    c = st.columns([2, 1.4, 1.4, 1])
    name = c[0].selectbox("战法", names, format_func=lambda n: strategies[n]["label"])
    start = c[1].date_input("开始", value=datetime.now() - timedelta(days=730))
    end = c[2].date_input("结束", value=datetime.now())
    c[3].write("")
    c[3].write("")
    go = c[3].button("运行回测", type="primary", use_container_width=True)
    if blocked:
        st.caption(f"暂不支持回测：{'、'.join(blocked)}"
                   "（信号需逐根K线求值，引擎读的是预算好的信号列）")

    tabs = st.tabs(["股票池", "战法参数", "出场规则", "交易规则", "成本与风控"])

    with tabs[0]:
        u = st.columns([2, 1, 1, 1])
        boards = u[0].multiselect("板块", list(BOARDS), default=["MAIN", "CHINEXT"],
                                  format_func=lambda b: BOARDS[b])
        size = u[1].number_input("池子大小", 10, 3000, 200, 10)
        min_amt = u[2].number_input("成交额下限(亿)", 0.0, 100.0, 1.0, 0.5)
        rank_by = u[3].selectbox("选池依据", ["amount", "random"],
                                 format_func=lambda x: {"amount": "成交额降序",
                                                        "random": "随机"}[x])
        u2 = st.columns([1, 1, 2])
        exclude_st = u2[0].checkbox("剔除 ST / 退市", value=True)
        min_bars = u2[1].number_input("上市至少(根K线)", 0, 500, 120, 10)
        u2[2].info(f"股票池按**开始日 {start:%Y-%m-%d}** 的数据选出。"
                   "用最新数据选池会引入前视偏差，回测收益会被系统性抬高。")
        spec = UniverseSpec(boards=tuple(boards) or ("MAIN",), size=int(size),
                            min_amount=min_amt * 1e8, exclude_st=exclude_st,
                            min_listed_bars=int(min_bars), rank_by=rank_by)

    overrides = {}
    with tabs[1]:
        params = PS.params_for(name)
        if params:
            cols = st.columns(min(len(params), 4))
            for i, p in enumerate(params):
                with cols[i % len(cols)]:
                    overrides[p.key] = _param_widget(p, cfg, f"bt_{name}_{p.key}")
        else:
            st.caption("这套战法没有声明可调参数。")

    exit_params = PS.exit_params_for(name, cfg)
    with tabs[2]:
        st.caption("止损 / 移动止损 / 止盈 / 时间止损。回测引擎和实盘防守读同一份规则，"
                   "改这里两边同时生效。没改到的项从 `exits.default` 继承。")
        cols = st.columns(4)
        for i, p in enumerate(exit_params):
            with cols[i % 4]:
                overrides[p.key] = _param_widget(p, cfg, f"bt_xt_{p.key}")

    with tabs[3]:
        cols = st.columns(4)
        for i, p in enumerate(PS.EXECUTION_PARAMS):
            with cols[i % 4]:
                overrides[p.key] = _param_widget(p, cfg, f"bt_ex_{p.key}")

    with tabs[4]:
        cols = st.columns(3)
        for i, p in enumerate(PS.COST_PARAMS):
            with cols[i % 3]:
                overrides[p.key] = _param_widget(p, cfg, f"bt_co_{p.key}")
        cols = st.columns(3)
        for i, p in enumerate(PS.RISK_PARAMS):
            with cols[i % 3]:
                overrides[p.key] = _param_widget(p, cfg, f"bt_ri_{p.key}")

    if go:
        from zhixing_quant.backtest.runner import run_backtest
        local = PS.apply_overrides(cfg, overrides)
        bar = st.progress(0.0, text="准备数据...")
        try:
            run = run_backtest(local, name, start.strftime("%Y%m%d"),
                               end.strftime("%Y%m%d"), spec=spec,
                               universe_as_of=start.strftime("%Y%m%d"),
                               progress=lambda d, t: bar.progress(
                                   min(d / max(t, 1), 1.0), text=f"计算指标 {d}/{t}"))
        except Exception as exc:
            bar.empty()
            st.error(f"回测失败：{exc}")
            return
        bar.empty()
        st.session_state["bt"] = (run, PS.diff_from_default(overrides, exit_params))

    got = st.session_state.get("bt")
    if got is None:
        C.empty_state("配置好参数后运行回测",
                      "参数大多标为 [CALIBRATE]，是规格给的初始猜测值。"
                      "回测的意义就是用你自己的数据把它们定下来。")
        return

    run, changed = got
    for w in run.warnings:
        st.warning(w)
    st.caption(f"股票池：{run.universe_note}　|　实际回测 {run.loaded} 只"
               + (f"，跳过 {run.skipped} 只（K线不足）" if run.skipped else ""))
    if getattr(run, "entry_note", ""):
        st.caption(f"建仓规则：{run.entry_note}")
    if getattr(run, "exit_note", ""):
        st.caption(f"出场规则：{run.exit_note}")
    if changed:
        st.caption("改动的参数：" + "、".join(f"`{k}`={v}" for k, v in changed.items()))

    m = run.metrics
    ex = run.excess_return
    C.stat_row([("总收益", f"{m.get('total_return',0):.2%}",
                 "up" if m.get("total_return", 0) >= 0 else "down"),
                ("年化", f"{m.get('annualized_return',0):.2%}",
                 "up" if m.get("annualized_return", 0) >= 0 else "down"),
                ("最大回撤", f"{m.get('max_drawdown',0):.2%}", ""),
                ("夏普", f"{m.get('sharpe',0):.2f}", "")])
    pl = "∞（本段没有亏损交易）" if m.get("profit_loss_ratio_is_inf") \
        else f"{m.get('profit_loss_ratio', 0):.2f}"
    C.stat_row([("胜率", f"{m.get('win_rate',0):.1%}", ""),
                ("盈亏比", pl, ""),
                ("交易笔数", m.get("total_trades", 0), ""),
                ("超额收益" if ex is not None else "期末权益",
                 f"{ex:.2%}" if ex is not None else f"{m.get('final_equity',0):,.0f}",
                 ("up" if (ex or 0) >= 0 else "down") if ex is not None else "")])

    if len(run.equity_curve) > 1:
        import plotly.graph_objects as go
        from zhixing_quant.ui.theme import DOWN, TEXT_3
        eq = run.equity_curve
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="策略",
                                 line=dict(width=2, color=SIGNAL_LINE)))
        if run.benchmark is not None and not run.benchmark.empty:
            fig.add_trace(go.Scatter(x=run.benchmark.index, y=run.benchmark.values,
                                     name="基准指数",
                                     line=dict(width=1.2, color=TEXT_3, dash="dot")))
        apply_layout(fig, height=320, title="资金曲线")
        st.plotly_chart(fig, use_container_width=True)

        dd = run.drawdown
        f2 = go.Figure(go.Scatter(x=dd.index, y=dd.values, name="回撤", fill="tozeroy",
                                  line=dict(color=DOWN, width=1)))
        apply_layout(f2, height=180, title="回撤")
        f2.update_layout(yaxis_tickformat=".0%")
        st.plotly_chart(f2, use_container_width=True)

    if not run.trades.empty:
        left, right = st.columns([7, 3], gap="medium")
        left.markdown("**成交明细**")
        left.dataframe(run.trades.sort_values("entry_date", ascending=False),
                       use_container_width=True, hide_index=True, height=320)
        right.markdown("**卖出原因**")
        counts = run.trades["exit_reason"].value_counts()
        right.dataframe(counts.rename("笔数"), use_container_width=True)

    # 活跃市值区间触发日志（空头=-2.3%，多头=单日+4%或三日连涨和>4%）
    regime_log = getattr(run, "regime_log", None)
    if regime_log is not None and not regime_log.empty:
        st.markdown("**活跃市值区间触发**")
        show = regime_log.copy()
        show["trade_date"] = pd.to_datetime(
            show["trade_date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
        show["pct"] = show["pct"].map(lambda v: f"{v:+.2%}")
        show.columns = ["日期", "活跃市值", "当日涨跌", "触发依据", "触发后区间"]
        st.dataframe(show, use_container_width=True, hide_index=True, height=200)
        st.caption("区间为持续态：触发空头后保持空头直到出现多头触发，反之亦然。"
                   "空头期间禁止开仓，持仓在触发次日开盘清仓。")


# ---------------------------------------------------------------------------
# 持仓 / 个股 / 数据
# ---------------------------------------------------------------------------

def page_positions(cfg, book):
    C.page_head("持仓", BOOKS[book])
    store = _store(cfg)
    try:
        positions = store.positions(book)
        cash = store.cash(book)
        try:
            from zhixing_quant.data.tdx_loader import fetch_a_spot
            spot = fetch_a_spot()
            prices = dict(zip(spot["code"], spot["close"]))
        except Exception:
            prices = {}
        equity = store.equity(prices, book)
        stats = store.stats(book)

        if cash == 0 and not positions:
            st.info("这个账户还没初始化。先在下面设置可用现金，权益和仓位才有意义。")
        C.stat_row([("账户权益", C.money(equity), ""),
                    ("可用现金", C.money(cash), ""),
                    ("累计盈亏", C.money(stats["total_pnl"]),
                     "up" if stats["total_pnl"] >= 0 else "down"),
                    ("胜率", f"{stats['win_rate']:.0%}" if stats["trades"] else "—", "")])

        C.section("当前持仓", "", f"{len(positions)} 只")
        if not positions:
            C.empty_state("还没有持仓记录",
                          "在「今日」页跑一遍决策，命中后点「记录建仓」；或在下面手工登记。")
        else:
            rows = []
            for p in positions:
                px = float(prices.get(p["code"], p["entry_price"]))
                rows.append({"code": p["code"], "name": p["name"], "close": px,
                             "pct_chg": (px / p["entry_price"] - 1) * 100,
                             "stop_loss": p["stop_loss"],
                             "take_profit": p.get("take_profit") or float("nan")})
            C.candidate_table(pd.DataFrame(rows))

            st.caption("平仓")
            cc = st.columns([2, 1, 1, 1])
            namemap = {p["code"]: p["name"] for p in positions}
            code = cc[0].selectbox("标的", list(namemap),
                                   format_func=lambda c: f"{c} {namemap[c]}")
            price = cc[1].number_input("成交价", 0.01, 100000.0,
                                       float(prices.get(code, 10.0)), 0.01)
            reason = cc[2].selectbox("原因", ["止盈", "止损", "防守触发", "手工"])
            cc[3].write("")
            cc[3].write("")
            if cc[3].button("平仓", use_container_width=True):
                r = store.close_position(code, price, book=book, reason=reason)
                st.success(f"已平仓 {code}，盈亏 {r['pnl']:,.0f}") if r else st.error("没找到")
                st.rerun()

        with st.expander("手工登记 / 设置资金"):
            f = st.columns(5)
            mcode = f[0].text_input("代码", key="m_code")
            msh = f[1].number_input("股数", 100, 10_000_000, 100, 100, key="m_sh")
            mpx = f[2].number_input("买入价", 0.01, 100000.0, 10.0, 0.01, key="m_px")
            mst = f[3].number_input("止损价", 0.0, 100000.0, 9.5, 0.01, key="m_st")
            f[4].write("")
            f[4].write("")
            if f[4].button("登记", use_container_width=True):
                try:
                    store.open_position(mcode.strip().zfill(6), int(msh), mpx, mst,
                                        book=book)
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
            st.dataframe(trades.drop(columns=["id", "book"]),
                         use_container_width=True, hide_index=True, height=280)
    finally:
        store.close()


def page_chart(cfg, book):
    C.page_head("个股", "K线与全部指标")
    c = st.columns([1, 1.4, 1])
    code = c[0].text_input("代码", value="600000")
    days = c[1].slider("显示天数", 60, 400, 160)
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
        st.session_state["chart"] = (code, r, df.attrs.get("adjust"), days)

    got = st.session_state.get("chart")
    if got is None:
        C.empty_state("输入代码查看", "会画出全部指标，并统计每种信号历史触发了多少次。")
        return
    code, r, adjust, days = got
    if adjust == "none":
        st.info("当前是**不复权**价格。跑一次 `sync --xdxr` 后才是前复权。")
    for w in r.warnings():
        st.warning(w)

    left, right = st.columns([7, 3], gap="medium")
    with left:
        _draw_kline(r.df.tail(days), code, height=560)
    with right:
        from zhixing_quant.indicators.pipeline import defense_coverage
        st.markdown("**防守规则覆盖**")
        C.defense_coverage_chips(defense_coverage(r.df))
        sig_cols = [c2 for c2 in r.df.columns if c2.startswith("sig_")]
        hits = {c2: int(r.df[c2].fillna(False).astype(bool).sum()) for c2 in sig_cols}
        hits = {k: v for k, v in hits.items() if v}
        st.markdown("**历史信号统计**")
        if hits:
            st.dataframe(pd.DataFrame([{"信号": k, "次数": v} for k, v in
                                       sorted(hits.items(), key=lambda x: -x[1])]),
                         use_container_width=True, hide_index=True, height=380)
        else:
            st.caption("这段历史里没有任何信号触发。")


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

def _draw_kline(df: pd.DataFrame, title: str = "", height: int = 640):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from zhixing_quant.indicators.tdx import ma
    from zhixing_quant.ui.theme import DOWN, TEXT_3, UP

    d = df.copy()
    x = d.index.strftime("%Y-%m-%d")
    sub = "brick_value" in d.columns
    fig = make_subplots(rows=3 if sub else 2, cols=1, shared_xaxes=True,
                        row_heights=[.62, .18, .20] if sub else [.75, .25],
                        vertical_spacing=.03)
    fig.add_trace(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"],
                                 close=d["close"], name="K线",
                                 **candlestick_colors()), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=ma(d["close"], 20), name="MA20",
                             line=dict(width=1.2, color=MA_FAST)), row=1, col=1)
    if "yellow_line" in d.columns:
        fig.add_trace(go.Scatter(x=x, y=d["yellow_line"], name="知行多空线",
                                 line=dict(width=1.8, color=SIGNAL_LINE)), row=1, col=1)
    if "white_line" in d.columns:
        fig.add_trace(go.Scatter(x=x, y=d["white_line"], name="白线",
                                 line=dict(width=1, color=TEXT_3)), row=1, col=1)
    for col in ("sig_strategy", "sig_brick", "sig_b1", "sig_b2"):
        if col in d.columns:
            hit = d[d[col].fillna(False).astype(bool)]
            if not hit.empty:
                fig.add_trace(go.Scatter(x=hit.index.strftime("%Y-%m-%d"),
                                         y=hit["low"] * .97, mode="markers",
                                         name="买点",
                                         marker=dict(symbol="triangle-up", size=11,
                                                     color=UP)), row=1, col=1)
                break
    fig.add_trace(go.Bar(x=x, y=d["vol"], name="成交量",
                         marker_color=[UP if c >= o else DOWN
                                       for o, c in zip(d["open"], d["close"])]),
                  row=2, col=1)
    if sub:
        # 通达信 STICKLINE：每根砖从前一日值画到当日值，涨红跌绿。
        # 首日没有前值，不画（否则会有一根从 0 冲上来的长假砖）
        prev = d["brick_value"].shift(1)
        heights = (d["brick_value"] - prev).abs()
        valid = prev.notna() & d["brick_value"].notna()
        fig.add_trace(go.Bar(
            x=x, y=heights.where(valid), base=prev,
            name="砖型图",
            marker_color=[UP if v >= p else DOWN
                          for v, p in zip(d["brick_value"], prev)]), row=3, col=1)
    apply_layout(fig, height=height, title="")
    st.plotly_chart(fig, use_container_width=True)


PAGES = {"今日": page_today, "持仓": page_positions, "战法": page_strategies,
         "回测": page_backtest, "个股": page_chart, "数据": page_data}


def main():
    cfg = get_config()

    st.sidebar.markdown(
        "<div style='padding:2px 4px 12px;font-size:16px;font-weight:600'>知行</div>",
        unsafe_allow_html=True)
    book = st.sidebar.segmented_control(
        "账户", list(BOOKS), format_func=BOOKS.get, default="swing",
        label_visibility="collapsed") or "swing"
    st.sidebar.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    page = st.sidebar.radio("页面", PAGE_ORDER, key="nav_page",
                            label_visibility="collapsed")

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
