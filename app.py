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
PAGE_ORDER = ["今日", "持仓", "战法", "回测", "校准", "因子", "个股", "数据"]


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


def _active_switches(cfg, strategy):
    from zhixing_quant.backtest.ablation import active_switches
    try:
        return active_switches(cfg, strategy)
    except Exception:
        return []


def local_cfg_of(run):
    """消融要用本次回测实际生效的那份配置（含界面上的参数覆盖）。

    拿全局配置去跑会把「出场规则」页签上改过的参数丢掉，
    消融出来的结论和上面那张结果表就不是一回事了。
    """
    return getattr(run, "used_cfg", None) or get_config()


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

    # ---- 出场规则消融：逐条关掉，看每条各自贡献多少 ----
    C.section("出场规则消融", "规则叠多了靠直觉判断不出哪条有用，逐条关掉实测",
              f"当前启用 {len(_active_switches(local_cfg_of(run), name))} 条")
    st.caption("保持其余规则不变，只关掉一条重跑。关掉后收益**变高** = 这条在亏钱；"
               "**变低** = 这条在赚钱；**不变** = 从未触发，是摆设。"
               "⚠️ 这是诊断工具，不是调参工具——在同一段历史上反复删规则留下最好看的"
               "组合就是在拟合噪声，删之前先在样本外确认。")
    ab1, ab2 = st.columns([1.4, 4])
    mode = ab1.radio("视角", ["逐一关掉", "逐一只开"], horizontal=True,
                     key="ab_mode",
                     help="两种视角一起看：两条规则能救同一笔单子时，"
                          "各自的「关掉」影响都会显得很小，但「只开」能看出真实效果")
    if ab2.button(f"跑消融（要跑 N+1 次回测，比单次慢很多）", key="ab_go"):
        from zhixing_quant.backtest.ablation import ablate_exits
        bar = st.progress(0.0, text="准备...")
        try:
            table = ablate_exits(
                local_cfg_of(run), name, start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"), spec=spec,
                universe_as_of=start.strftime("%Y%m%d"),
                only_one=(mode == "逐一只开"),
                progress=lambda d, t, n: bar.progress(min(d / max(t, 1), 1.0),
                                                      text=f"{d}/{t} {n}"))
        except Exception as exc:
            bar.empty()
            st.error(f"消融失败：{exc}")
            table = None
        else:
            bar.empty()
            st.session_state["ablation"] = table

    table = st.session_state.get("ablation")
    if table is not None and not table.empty:
        from zhixing_quant.backtest.ablation import summarize
        for line in summarize(table):
            st.info(line)
        view = table[["规则", "总收益", "最大回撤", "夏普", "胜率", "笔数",
                      "Δ总收益", "Δ最大回撤", "Δ笔数", "判定"]]
        st.dataframe(view, use_container_width=True, hide_index=True,
                     column_config={
                         "总收益": st.column_config.NumberColumn(format="%.2%"),
                         "最大回撤": st.column_config.NumberColumn(format="%.2%"),
                         "胜率": st.column_config.NumberColumn(format="%.1%"),
                         "夏普": st.column_config.NumberColumn(format="%.2f"),
                         "Δ总收益": st.column_config.NumberColumn(format="%.2%"),
                         "Δ最大回撤": st.column_config.NumberColumn(format="%.2%"),
                     })

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

    c = st.columns([1, 1, 1, 1.3])
    names = c[0].checkbox("更新名称")
    xdxr = c[1].checkbox("更新除权除息", help="需要 pytdx")
    full = c[2].checkbox("全量重建")
    oamv = c[3].checkbox("同步活跃市值", value=True,
                         help="指南针 0AMV 指标入库。需先完全退出指南针软件；"
                              "当天数据要收盘后指南针写入才有")
    if st.button("立即同步", type="primary"):
        cmd = [sys.executable, "-m", "zhixing_quant.data.sync"]
        cmd += (["--full"] * full + ["--names"] * names + ["--xdxr"] * xdxr
                + ["--oamv"] * oamv)
        with st.spinner("同步中..."):
            p = subprocess.run(cmd, capture_output=True, text=True)
        st.code(p.stdout or p.stderr, language="text")
        # K 线同步成功但活跃市值/除权等后续步骤失败时，报错在 stderr，
        # 只显示 stdout 会把它吞掉
        if p.returncode != 0 and p.stderr.strip():
            st.error(f"同步有一步失败了（退出码 {p.returncode}）：\n\n{p.stderr.strip()}")
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


def page_factors(cfg, book):
    """因子有效性检验。回答的是「这个因子有没有用」，不是「回测能赚多少」。"""
    import zhixing_quant.factors as FA
    from zhixing_quant.factors.evaluate import evaluate_factors, monotonicity
    from zhixing_quant.factors.library import PRESETS
    from zhixing_quant.data.universe import UniverseSpec

    C.page_head("因子", "战法决定能不能买，因子决定先买哪只")

    all_factors = FA.list_factors()
    names_all = [f.name for f in all_factors]
    label_of = {f.name: f"{f.label}（{f.category}）" for f in all_factors}

    c = st.columns([2.6, 1.2, 1.2, 1])
    picked = c[0].multiselect("参与检验的因子", names_all, default=names_all,
                              format_func=lambda n: label_of[n])
    horizon = c[1].number_input("未来收益天数", 1, 60,
                                int(PS.get_value(cfg, "factors.evaluate.horizon", 5)))
    quant = c[2].number_input("分层数", 3, 10,
                              int(PS.get_value(cfg, "factors.evaluate.quantiles", 5)))
    c[3].write("")
    c[3].write("")
    go = c[3].button("开始检验", type="primary", use_container_width=True)

    u = st.columns([1.4, 1.4, 1, 1])
    start = u[0].date_input("开始", value=datetime.now() - timedelta(days=540),
                            key="fac_start")
    end = u[1].date_input("结束", value=datetime.now(), key="fac_end")
    size = u[2].number_input("股票池大小", 20, 6000, 200, 100, key="fac_size")
    min_amt = u[3].number_input("成交额下限(亿)", 0.0, 50.0, 1.0, 0.5, key="fac_amt")

    from zhixing_quant.backtest.runner import BACKTESTABLE
    strategies = _strategies()
    pop_opts = ["all"] + [k for k in BACKTESTABLE if k in strategies]
    v = st.columns([2, 1.4, 3.6])
    pop_key = v[0].selectbox(
        "检验人群", pop_opts, index=0, key="fac_pop",
        format_func=lambda k: "全市场" if k == "all" else f"{strategies[k]['label']} 命中标的")
    bull_only = v[1].checkbox("只看多头区间", value=True, key="fac_bull",
                              disabled=pop_key == "all",
                              help="空头区间引擎禁止开仓，那天的命中根本不会被排序")
    v[2].caption("给战法配排序权重，要看**命中人群**的 IC：排序只作用于当日命中"
                 "信号的那十几只，因子规律和全市场不同。全量实测 B2：`amount_cv` "
                 "全市场最强、命中人群里失效；`vol_ratio` 在命中人群里符号翻转。"
                 "选命中人群时股票池要放到全市场（6000 只、成交额下限 0），"
                 "否则每天命中不足 10 只，算不出 IC；全市场约需 5 分钟。")

    st.caption("IC = 当日因子排序与未来 N 日收益排序的秩相关。"
               "|IC均值| > 0.03 算有信号，|ICIR| > 0.3 算稳定，"
               "样本不足 60 个交易日的结论不要当真。")

    if go:
        if not picked:
            st.warning("至少选一个因子。")
            return
        from zhixing_quant.data.tdx_loader import load_daily_many
        from zhixing_quant.data.universe import build_universe
        from zhixing_quant.indicators.pipeline import run_steps

        bar = st.progress(0.0, text="建池...")
        try:
            spec = UniverseSpec(boards=("MAIN", "CHINEXT"), size=int(size),
                                min_amount=min_amt * 1e8, exclude_st=True,
                                min_listed_bars=120)
            uni = build_universe(cfg, as_of=start.strftime("%Y%m%d"), spec=spec)
            warm = (pd.Timestamp(start) - timedelta(days=400)).strftime("%Y%m%d")
            bar.progress(0.2, text=f"加载 {len(uni.codes)} 只行情...")
            raw = load_daily_many(uni.codes, start_date=warm,
                                  end_date=end.strftime("%Y%m%d"))
            steps = FA.required_steps(picked)
            if pop_key != "all":
                from zhixing_quant.indicators.pipeline import PIPELINES
                steps = list(dict.fromkeys(steps + PIPELINES.get(pop_key, [pop_key])))
            bar.progress(0.5, text=f"计算指标（{', '.join(steps) or '无'}）...")
            data = {c2: run_steps(df, cfg, steps).df
                    for c2, df in raw.items() if df is not None and len(df) > 150}
            population = None
            if pop_key != "all":
                from zhixing_quant.factors.evaluate import strategy_population
                from zhixing_quant.timing.active_value import regime_by_close
                regime = regime_by_close(cfg) if bull_only else None
                population = strategy_population(data, BACKTESTABLE[pop_key], regime)
            bar.progress(0.8, text="计算 IC 与分层收益...")
            res = evaluate_factors(data, picked, horizon=int(horizon), q=int(quant),
                                   start=start.strftime("%Y%m%d"),
                                   end=end.strftime("%Y%m%d"),
                                   population=population)
        except Exception as exc:
            bar.empty()
            st.error(f"检验失败：{exc}")
            return
        bar.empty()
        st.session_state["fac"] = (res, len(data), pop_key)

    got = st.session_state.get("fac")
    if got is None:
        C.empty_state("选好因子和区间后点「开始检验」",
                      "预设权重组合（回踩买点 / 放量突破 / 趋势跟随）给的都是初始"
                      "猜测值，必须用这一页的 IC 和分层收益校准后再用于选股排序。")
        with st.expander("已注册的因子"):
            st.dataframe(pd.DataFrame(
                [{"因子": f.name, "中文名": f.label, "分类": f.category,
                  "方向": "越大越好" if f.direction > 0 else "越小越好",
                  "说明": f.help} for f in all_factors]),
                use_container_width=True, hide_index=True)
        with st.expander("预设权重组合"):
            for key, meta in PRESETS.items():
                st.markdown(f"**{meta['label']}** (`{key}`) — {meta['help']}")
                st.caption("、".join(f"{k}×{v}" for k, v in meta["weights"].items()))
        return

    res, n_loaded, got_pop = got
    for w in res["warnings"]:
        st.warning(w)
    st.caption(f"实际参与 {n_loaded} 只标的，{len(res['ic'])} 个交易日")

    summary = res["summary"]
    if summary.empty:
        C.empty_state("没有算出任何 IC", "多半是区间太短或股票池太小。")
        return

    C.section("IC 汇总", "按 |ICIR| 降序——稳定性比幅度更值得先看",
              f"{len(summary)} 个因子")
    st.dataframe(summary, use_container_width=True, hide_index=True,
                 column_config={
                     "IC均值": st.column_config.NumberColumn(format="%.4f"),
                     "ICIR": st.column_config.NumberColumn(format="%.3f"),
                     "t值": st.column_config.NumberColumn(format="%.2f"),
                     "正IC占比": st.column_config.NumberColumn(format="%.1%"),
                 })
    st.caption("「方向」列最该先看：IC 已按因子声明的方向调过符号，所以"
               "**负 IC 意味着这个因子在这段样本里是反着的**。")

    # 方向诊断：IC 已按声明方向调过符号，所以负 IC = 这个因子在这段样本里是反着的
    flipped = summary[summary["方向"] == "⚠ 相反"]
    agreed = summary[summary["方向"] == "一致"]
    if not flipped.empty:
        st.error(
            f"**{len(flipped)} 个因子的实测方向与声明相反**："
            + "、".join(flipped["因子"])
            + "。按声明方向给它们正权重，等于系统性地挑最差的标的。"
              "下面的「按实测 IC 生成权重」会自动把这些反过来用。")
    if agreed.empty:
        st.warning("没有任何因子的方向是显著一致的。这批因子在这段样本里都没用，"
                   "应该退回不排序（`factors.by_strategy.<战法>: amount`），"
                   "而不是硬凑一个组合。")

    # 预设组合在实测 IC 下的净方向——预设是拍脑袋给的，必须拿数据打分
    from zhixing_quant.factors.library import PRESETS

    icir_of = dict(zip(summary["因子"], summary["ICIR"]))
    preset_rows = []
    for key, meta in PRESETS.items():
        w = {k: v for k, v in meta["weights"].items() if k in icir_of}
        if not w:
            continue
        tot = sum(abs(v) for v in w.values()) or 1.0
        preset_rows.append({
            "预设组合": f"{meta['label']}（{key}）",
            "加权净ICIR": round(sum(icir_of[k] * v for k, v in w.items()) / tot, 3),
            "反向因子数": sum(1 for k, v in w.items() if icir_of[k] * v < 0),
            "因子数": len(w),
        })
    if preset_rows:
        pr = pd.DataFrame(preset_rows).sort_values("加权净ICIR", ascending=False)
        st.markdown("**预设组合打分**（净 ICIR 为负 = 这个组合在帮你挑最差的）")
        st.dataframe(pr, use_container_width=True, hide_index=True,
                     column_config={"加权净ICIR":
                                    st.column_config.NumberColumn(format="%.3f")})

    # 按实测 IC 直接生成权重，替代拍脑袋
    with st.expander("按实测 IC 生成权重", expanded=False):
        from zhixing_quant.factors.evaluate import weights_as_yaml, weights_from_ic

        if got_pop == "all":
            st.warning("这组 IC 是在**全市场**上算的，不适合直接给战法排序——"
                       "全量实测 B2 照这样配，样本外 -23.2%，手写预设 +9.7%。"
                       "上面「检验人群」选对应战法的命中标的后再生成。")
        g = st.columns([1, 1, 1, 2])
        n_max = g[0].number_input("最多留几个因子", 2, 12, 6, key="wg_n",
                                  help="留太多等于在短样本上过拟合")
        min_t = g[1].number_input("显著性门槛 |t|", 1.0, 5.0, 2.0, 0.5, key="wg_t")
        flip = g[2].checkbox("允许反向使用", value=True, key="wg_flip",
                             help="关掉则直接剔除方向相反的因子，而不是反过来用")
        targets = ["b1", "b2", "brick"]
        target = g[3].selectbox("写给哪个战法", targets,
                                index=targets.index(got_pop) if got_pop in targets else 1,
                                key="wg_target")
        gen = weights_from_ic(summary, max_factors=int(n_max),
                              min_abs_t=float(min_t), allow_flip=bool(flip))
        st.code(weights_as_yaml(gen, target), language="yaml")
        st.caption("负权重表示该因子实测方向与声明相反，反着用。⚠️ 这是在**当前区间**"
                   "上拟合出来的权重，区间只能是样本内：粘进 `settings.yaml` 后，"
                   "在区间之后的一段数据上回测，没考过现有预设就不换。全量实测过一次"
                   "条件 IC 权重，B2 样本外 -7.0%，仍输给预设的 +9.7%。")

    pick = st.selectbox("看哪个因子的分层收益", list(summary["因子"]),
                        format_func=lambda n: label_of.get(n, n))
    qret = res["quantiles"].get(pick)
    if qret is not None and not qret.empty:
        mono = monotonicity(qret)
        left, right = st.columns([5, 5], gap="medium")
        left.markdown(f"**分层收益**　单调性 {mono:.0%}")
        left.dataframe(qret, use_container_width=True)
        if mono < 0.6:
            left.caption("单调性偏低：只有两头有差异、中间乱，多半是几个极端值"
                         "造成的假象，不是稳定的方向性。")
        import plotly.graph_objects as go
        from zhixing_quant.ui.theme import DOWN, UP
        vals = qret["平均未来收益"]
        fig = go.Figure(go.Bar(x=list(vals.index), y=vals.values,
                               marker_color=[UP if v >= 0 else DOWN for v in vals]))
        apply_layout(fig, height=260, title=f"{label_of.get(pick, pick)} 分层未来收益")
        fig.update_layout(yaxis_tickformat=".2%")
        right.plotly_chart(fig, use_container_width=True)

    ic = res["ic"]
    if pick in ic.columns:
        import plotly.graph_objects as go
        s_ic = ic[pick].dropna()
        f2 = go.Figure()
        f2.add_trace(go.Scatter(x=s_ic.index, y=s_ic.cumsum(), name="IC 累计",
                                line=dict(width=2, color=SIGNAL_LINE)))
        apply_layout(f2, height=240, title="IC 累计曲线（一路向上才说明稳定有效）")
        st.plotly_chart(f2, use_container_width=True)

    st.caption(f"覆盖率最低的三个因子：" + "、".join(
        f"{k} {v:.0%}" for k, v in res["coverage"].head(3).items()))


# 常用的可调参数：路径 -> (中文名, 建议取值)
CALIB_PARAMS = {
    "exits.{s}.stop.kind": ("止损方式", ["entry_low", "pct", "atr"]),
    "exits.{s}.stop.pct": ("止损百分比", [0.04, 0.06, 0.08, 0.10]),
    "exits.{s}.stop.atr_mult": ("止损ATR倍数", [1.5, 2.0, 2.5, 3.0]),
    "exits.{s}.trailing.kind": ("移动止损", ["none", "pct", "chandelier",
                                            "white_line", "yellow_line"]),
    "exits.{s}.trailing.pct": ("移动止损回撤", [0.06, 0.10, 0.15]),
    "exits.{s}.trailing.activate_profit": ("移动止损启用浮盈", [0.0, 0.05, 0.10]),
    "exits.{s}.take_profit.pct": ("止盈百分比", [0.10, 0.15, 0.25]),
    "exits.{s}.time_stop.max_holding_days": ("最长持有", [0, 5, 10, 20, 30]),
    "exits.{s}.time_stop.no_progress_days": ("N日不拉升", [0, 2, 3, 5]),
    "exits.{s}.profit_to_loss": ("盈转亏门槛", [0.0, 0.02, 0.05]),
    "entries.{s}.base_pct": ("底仓比例", [0.10, 0.15, 0.20]),
    "entries.{s}.max_addons": ("最多加仓次数", [0, 2, 4]),
}


def page_calibrate(cfg, book):
    """参数校准：样本内外切分 / 网格扫描 / 滚动前进。核心是防过拟合。"""
    from zhixing_quant.backtest.calibrate import OBJECTIVES, grid_combos
    from zhixing_quant.backtest.runner import BACKTESTABLE
    from zhixing_quant.data.universe import BOARDS, UniverseSpec

    C.page_head("校准", "在同一段历史上反复试参数、留下最好看的那组，就是过拟合")
    st.caption("判断一组参数能不能用，三件事缺一不可："
               "**① 样本外没有大幅衰减　② 参数曲面是平的不是尖的　③ 交易笔数够**。"
               "只要一条不满足就不该上实盘，哪怕回测收益再好看。")

    strategies = _strategies()
    names = [n for n in strategies if n in BACKTESTABLE]
    c = st.columns([1.6, 1.3, 1.3, 1.2, 1.2])
    name = c[0].selectbox("战法", names, key="cal_strat",
                          format_func=lambda n: strategies[n]["label"])
    start = c[1].date_input("开始", value=datetime.now() - timedelta(days=1000),
                            key="cal_start")
    end = c[2].date_input("结束", value=datetime.now(), key="cal_end")
    oos = c[3].slider("样本外占比", 0.1, 0.5, 0.3, 0.05, key="cal_oos")
    obj = c[4].selectbox("排序目标", list(OBJECTIVES), key="cal_obj",
                         format_func=lambda k: OBJECTIVES[k])

    u = st.columns([1.2, 1.2, 1.2, 2])
    size = u[0].number_input("股票池", 20, 800, 150, 10, key="cal_size")
    min_amt = u[1].number_input("成交额下限(亿)", 0.0, 50.0, 1.0, 0.5, key="cal_amt")
    top_n = u[2].number_input("样本外验证前N组", 1, 10, 3, key="cal_topn")
    spec = UniverseSpec(boards=("MAIN", "CHINEXT"), size=int(size),
                        min_amount=min_amt * 1e8, exclude_st=True,
                        min_listed_bars=120)

    tabs = st.tabs(["网格扫描", "滚动前进验证"])

    # ---- 网格扫描 ----
    with tabs[0]:
        paths = {k.format(s=name): v for k, v in CALIB_PARAMS.items()}
        picked = st.multiselect(
            "要扫哪些参数（选 1-3 个，越多越容易撞出噪声）",
            list(paths), key="cal_grid_pick",
            format_func=lambda p: f"{paths[p][0]}　{p}")
        grid = {}
        if picked:
            cols = st.columns(min(len(picked), 3))
            for i, path in enumerate(picked):
                label, options = paths[path]
                with cols[i % len(cols)]:
                    vals = st.multiselect(label, options, default=options,
                                          key=f"cal_v_{path}")
                    if vals:
                        grid[path] = vals
        n = len(grid_combos(grid)) if grid else 0
        if grid:
            st.caption(f"{n} 组参数 × (样本内 + 前 {top_n} 组的样本外) "
                       f"≈ {n + int(top_n)} 次回测。有效配置相同的组会自动复用。")
        if st.button("开始扫描", type="primary", key="cal_sweep",
                     disabled=not grid):
            from zhixing_quant.backtest.calibrate import sweep
            bar = st.progress(0.0, text="准备...")
            try:
                res = sweep(cfg, name, grid, start.strftime("%Y%m%d"),
                            end.strftime("%Y%m%d"), spec=spec,
                            oos_frac=float(oos), top_n=int(top_n),
                            objective_kind=obj,
                            progress=lambda d, t, s2: bar.progress(
                                min(d / max(t, 1), 1.0), text=f"{d}/{t} {s2}"))
            except Exception as exc:
                bar.empty()
                st.error(f"扫描失败：{exc}")
            else:
                bar.empty()
                st.session_state["cal_sweep_res"] = (res, list(grid))

        got = st.session_state.get("cal_sweep_res")
        if got:
            res, gkeys = got
            for w in res.warnings:
                (st.error if "⚠️" in w or "不该上实盘" in w else st.info)(w)
            if not res.table.empty:
                st.dataframe(res.table, use_container_width=True, hide_index=True)
                from zhixing_quant.backtest.calibrate import plateau_score
                flat = []
                for path in gkeys:
                    col = ".".join(path.split(".")[-2:])
                    sc = plateau_score(res.table, col)
                    if sc:
                        flat.append({"参数": col, "平坦度": round(sc, 3),
                                     "判定": "平台，稳健" if sc >= 0.5
                                             else "尖峰，换段数据就会塌"})
                if flat:
                    st.markdown("**参数平坦度**　"
                                "稳健的参数应该有平台而不是尖峰——最优点旁边"
                                "的取值也得差不多好")
                    st.dataframe(pd.DataFrame(flat), use_container_width=True,
                                 hide_index=True)

    # ---- 滚动前进 ----
    with tabs[1]:
        st.caption("每段只用**之前**的数据定参数，在后面那段上交易。"
                   "把各测试段串起来，就是「如果我每季度重调一次参」的真实曲线——"
                   "这是最接近实盘的验证方式。")
        w = st.columns([1.2, 1.2, 3])
        train_m = w[0].number_input("训练(月)", 3, 36, 12, key="cal_train")
        test_m = w[1].number_input("测试(月)", 1, 12, 3, key="cal_test")
        wpaths = {k.format(s=name): v for k, v in CALIB_PARAMS.items()}
        wpick = st.multiselect("要滚动优化的参数（建议只选 1 个）",
                               list(wpaths), key="cal_wf_pick",
                               format_func=lambda p: f"{wpaths[p][0]}　{p}")
        wgrid = {}
        for path in wpick:
            label, options = wpaths[path]
            vals = st.multiselect(label, options, default=options[:3],
                                  key=f"cal_wv_{path}")
            if vals:
                wgrid[path] = vals
        if st.button("开始滚动验证", type="primary", key="cal_wf",
                     disabled=not wgrid):
            from zhixing_quant.backtest.calibrate import (walk_forward,
                                                          walk_forward_summary)
            bar = st.progress(0.0, text="准备...")
            try:
                wf = walk_forward(cfg, name, wgrid, start.strftime("%Y%m%d"),
                                  end.strftime("%Y%m%d"), spec=spec,
                                  train_months=int(train_m),
                                  test_months=int(test_m), objective_kind=obj,
                                  progress=lambda d, t, s2: bar.progress(
                                      min(d / max(t, 1), 1.0),
                                      text=f"{d}/{t} {s2}"))
            except Exception as exc:
                bar.empty()
                st.error(f"滚动验证失败：{exc}")
            else:
                bar.empty()
                st.session_state["cal_wf_res"] = (wf, walk_forward_summary(wf))

        gotw = st.session_state.get("cal_wf_res")
        if gotw:
            wf, lines = gotw
            for line in lines:
                (st.error if "不要拿去实盘" in line or "噪声" in line
                 else st.info)(line)
            st.dataframe(wf, use_container_width=True, hide_index=True)


PAGES = {"今日": page_today, "持仓": page_positions, "战法": page_strategies,
         "校准": page_calibrate, "因子": page_factors,
         "回测": page_backtest, "个股": page_chart, "数据": page_data}


def main():
    try:
        cfg = get_config()
    except Exception as exc:
        # 配置校验没过就别往下走了——带着静默失效的配置跑出来的结果
        # 比报错更糟，因为你会当真。
        st.error(f"配置加载失败\n\n```\n{exc}\n```")
        st.stop()

    from zhixing_quant.config import config_warnings
    for w in config_warnings():
        st.sidebar.warning(w)

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
