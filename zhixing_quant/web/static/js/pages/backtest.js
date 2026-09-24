// 回测：左侧配置，右侧结果（指标 → 资金曲线/回撤 → 月度收益 → 成交 → 出场规则消融 → 区间日志）。

import { html, useEffect, useShared, useJob, api, fmt, tone, daysAgo, dataDate } from "../lib.js";
import {
  Card, Kpis, Field, Select, NumberInput, DateInput, Switch, Chips, Segmented, Button, Badge,
  Callout, Callouts, Empty, JobBar, JobError, ParamGrid, Accordion, FrameTable, Rich,
} from "../ui.js";
import { Chart, equityOption, drawdownOption, hbarOption, monthlyReturns, heatColor, tokens } from "../charts.js";

const changedCount = (params, values) =>
  (params || []).filter((p) => values[p.key] != null && values[p.key] !== p.value).length;

function Group({ title, params, values, onChange, kindLabels, open, children }) {
  const n = changedCount(params, values);
  return html`<${Accordion} open=${open} title=${title} badge=${n ? html`<${Badge} tone="accent">改 ${n}<//>` : null}>
    ${children}${params && html`<${ParamGrid} params=${params} values=${values} onChange=${onChange} kindLabels=${kindLabels} />`}
  <//>`;
}

function Config({ meta, status, form, setForm, busy, onRun }) {
  const { strategies, timing_only: TIMING } = meta;
  const names = [...meta.backtestable, TIMING];
  const set = (k) => (v) => setForm({ ...form, [k]: v });
  const setU = (k) => (v) => setForm({ ...form, universe: { ...form.universe, [k]: v } });
  const isTiming = form.strategy === TIMING;

  const [ov, setOv] = useShared(`bt:ov:${form.strategy}`, {});
  const [common, setCommon] = useShared("bt:ov:common", {});
  const [exitParams, setExitParams] = useShared(`bt:exit:${form.strategy}`, null);
  useEffect(() => {
    if (!exitParams && !isTiming) api.get(`/api/exit-params/${form.strategy}`).then(setExitParams).catch(() => setExitParams([]));
  }, [form.strategy]);

  const coreKeys = (isTiming ? [] : [""]).concat(Object.keys(meta.core_indexes));
  const core = coreKeys.includes(form.core_code) ? form.core_code : coreKeys[0];
  const blocked = Object.keys(strategies).filter((n) => !meta.backtestable.includes(n)).map((n) => strategies[n].label);
  const u = form.universe;

  const run = () => onRun({ ...form, core_code: core, overrides: { ...common, ...ov } });

  return html`<div class="card bt-config">
    <header class="card-head"><h3>回测配置</h3>
      <div class="extra"><${Button} kind="primary" icon="play" loading=${busy} onClick=${run}>运行回测<//></div></header>
    <div class="card-body flush" style="overflow-y:auto">
      <div class="stack" style="padding:16px">
        <${Field} label="战法"><${Select} value=${form.strategy} onChange=${set("strategy")}
          options=${names.map((n) => ({ value: n, label: n === TIMING ? "只择时（不选股，多头持有指数）" : strategies[n].label }))} /><//>
        ${blocked.length > 0 && html`<div class="small muted">暂不支持回测：${blocked.join("、")}（信号需逐根K线求值，引擎读的是预算好的信号列）</div>`}
        <div class="row" style="flex-wrap:nowrap">
          <${Field} label="开始" class="grow"><${DateInput} value=${form.start} onChange=${set("start")} /><//>
          <${Field} label="结束" class="grow"><${DateInput} value=${form.end} onChange=${set("end")} /><//>
        </div>
        <${Field} label="核心仓" help="多头区间把闲置现金放进指数 ETF。个股要买时先卖核心仓腾钱，非多头日开盘清掉。ETF 按佣金 + 0.05% 滑点记账，免印花税。">
          <${Select} value=${core} onChange=${set("core_code")}
            options=${coreKeys.map((k) => ({ value: k, label: meta.core_indexes[k] || "不用（闲置现金留着）" }))} /><//>
      </div>

      <${Accordion} open=${true} title="股票池">
        <div class="stack">
          <${Field} label="板块"><${Chips} value=${u.boards} onChange=${setU("boards")}
            options=${Object.entries(meta.boards).map(([value, label]) => ({ value, label }))} /><//>
          <div class="params">
            <${Field} label="池子大小"><${NumberInput} value=${u.size} min=${10} max=${3000} step=${10} onChange=${setU("size")} /><//>
            <${Field} label="成交额下限(亿)"><${NumberInput} value=${u.min_amount} min=${0} max=${100} step=${0.5} onChange=${setU("min_amount")} /><//>
            <${Field} label="选池依据"><${Select} value=${u.rank_by} onChange=${setU("rank_by")}
              options=${[{ value: "amount", label: "成交额降序" }, { value: "random", label: "随机" }]} /><//>
            <${Field} label="上市至少(根K线)"><${NumberInput} value=${u.min_bars} min=${0} max=${500} step=${10} onChange=${setU("min_bars")} /><//>
          </div>
          <${Switch} checked=${u.exclude_st} onChange=${setU("exclude_st")} label="剔除 ST / 退市" />
          <${Callout}>股票池按 <b>开始日 ${form.start}</b> 的数据选出。用最新数据选池会引入前视偏差，回测收益会被系统性抬高。<//>
        </div>
      <//>
      ${!isTiming && html`
        <${Group} title="战法参数" params=${meta.params.strategy[form.strategy]} values=${ov} onChange=${setOv} />
        <${Group} title="出场规则" params=${exitParams || []} values=${ov} onChange=${setOv} kindLabels=${meta.kind_labels}>
          <p class="small muted" style="margin:0 0 12px">止损 / 移动止损 / 止盈 / 时间止损。回测引擎和实盘防守读同一份规则。没改到的项从 <code>exits.default</code> 继承。</p>
        <//>`}
      <${Group} title="交易规则" params=${meta.params.execution} values=${common} onChange=${setCommon} />
      <${Group} title="成本与风控" params=${[...meta.params.cost, ...meta.params.risk]} values=${common} onChange=${setCommon} />
    </div>
  </div>`;
}

function Monthly({ eq }) {
  const data = monthlyReturns(eq);
  const years = Object.keys(data).sort();
  if (!years.length) return null;
  useShared("theme", "");     // 订阅主题：切换时重新取色
  const t = tokens();
  return html`<div style="overflow-x:auto"><table class="heat">
    <thead><tr><th></th>${Array.from({ length: 12 }, (_, i) => html`<th>${i + 1}月</th>`)}<th>全年</th></tr></thead>
    <tbody>${years.map((y) => html`<tr><th>${y}</th>
      ${Array.from({ length: 12 }, (_, i) => {
        const v = data[y].months[i + 1];
        return html`<td style=${`background:${heatColor(v, t)}`} title=${v == null ? "" : `${y}-${i + 1}: ${fmt.pct(v, 2, true)}`}>
          ${v == null ? "" : (v * 100).toFixed(1)}</td>`;
      })}
      <td style=${`background:${heatColor(data[y].year, t)};font-weight:700`}>${fmt.pct(data[y].year, 1, true)}</td>
    </tr>`)}</tbody></table></div>`;
}

function Ablation({ source, nSwitches }) {
  const { job, run, busy, result } = useJob("bt:ablation");
  const [mode, setMode] = useShared("bt:ablation:mode", "off");
  // 消融结果属于哪次回测：重新回测后旧消融不再显示
  const [abSource, setAbSource] = useShared("bt:ablation:source", null);
  const mine = !!job && abSource === source;
  const r = mine ? result : null;
  return html`<${Card} title="出场规则消融" hint="规则叠多了靠直觉判断不出哪条有用，逐条关掉实测"
    extra=${nSwitches != null && html`<${Badge}>当前启用 ${nSwitches} 条<//>`}>
    <div class="stack">
      <p class="small muted" style="margin:0">保持其余规则不变，只关掉一条重跑。关掉后收益<b>变高</b> = 这条在亏钱；<b>变低</b> = 这条在赚钱；<b>不变</b> = 从未触发，是摆设。
        ⚠️ 这是诊断工具，不是调参工具——在同一段历史上反复删规则留下最好看的组合就是在拟合噪声，删之前先在样本外确认。</p>
      <div class="row">
        <${Segmented} value=${mode} onChange=${setMode} options=${[
          { value: "off", label: "逐一关掉" },
          { value: "only", label: "逐一只开", title: "两条规则能救同一笔单子时，各自的「关掉」影响都会显得很小，但「只开」能看出真实效果" }]} />
        <${Button} icon="flask" loading=${busy && mine} disabled=${busy && !mine}
          onClick=${() => { setAbSource(source); run("ablation", { source, only_one: mode === "only" }); }}>跑消融（N+1 次回测，比单次慢很多）<//>
      </div>
      ${mine && html`<${JobBar} job=${job} /><${JobError} job=${job} />`}
      ${r && html`<${Callouts} items=${r.summary} tone="info" />
        <${FrameTable} frame=${r.table} boxed />`}
    </div>
  <//>`;
}

export default function Backtest({ meta, status }) {
  const [form, setForm] = useShared("bt:form", () => ({
    strategy: meta.backtestable[0] || meta.timing_only, start: daysAgo(730), end: dataDate(status),
    core_code: meta.core_default,
    universe: { boards: ["MAIN", "CHINEXT"], size: 200, min_amount: 1, rank_by: "amount", exclude_st: true, min_bars: 120 },
  }));
  const { job, run, busy, result: r } = useJob("bt");
  const onRun = (params) => run("backtest", params);

  return html`<div class="bt">
    <${Config} meta=${meta} status=${status} form=${form} setForm=${setForm} busy=${busy} onRun=${onRun} />
    <div class="stack-lg" style="min-width:0">
      <${JobBar} job=${job} /><${JobError} job=${job} />
      ${!r ? !busy && html`<${Card}><${Empty} icon="chart" title="配置好参数后运行回测">
          参数大多标为 [CALIBRATE]，是规格给的初始猜测值。回测的意义就是用你自己的数据把它们定下来。
          T 日收盘出信号、T+1 开盘成交，含佣金、印花税、滑点、整手与涨跌停约束。<//><//>`
        : html`<${Results} r=${r} job=${job} meta=${meta} />`}
    </div>
  </div>`;
}

function Results({ r, job, meta }) {
  const m = r.metrics;
  const ex = r.excess_return;
  const isTiming = r.strategy === meta.timing_only;
  const pl = m.profit_loss_ratio_is_inf ? "∞" : fmt.num(m.profit_loss_ratio);
  const changed = Object.entries(r.changed || {});
  return html`
    <${Callouts} items=${r.warnings} />
    <${Kpis} items=${[
      { label: "总收益", value: fmt.pct(m.total_return, 2, true), tone: tone(m.total_return),
        sub: `期末权益 ${fmt.money(m.final_equity)}` },
      { label: "年化收益", value: fmt.pct(m.annualized_return, 2, true), tone: tone(m.annualized_return) },
      { label: "超额收益", value: ex == null ? "—" : fmt.pct(ex, 2, true), tone: tone(ex),
        sub: ex == null ? "没有可用的基准指数" : "相对基准指数" },
      { label: "最大回撤", value: fmt.pct(m.max_drawdown, 2), sub: m.calmar != null ? `卡尔玛 ${fmt.num(m.calmar)}` : "" },
      { label: "夏普", value: fmt.num(m.sharpe), sub: m.sortino != null ? `索提诺 ${fmt.num(m.sortino)}` : "" },
      { label: "胜率", value: fmt.pct(m.win_rate, 1) },
      { label: "盈亏比", value: pl, sub: m.profit_loss_ratio_is_inf ? "本段没有亏损交易" : "" },
      { label: "交易笔数", value: fmt.int(m.total_trades),
        sub: m.total_trades < 30 ? "不足 30 笔，结论不可靠" : "" },
    ]} />
    <div class="kv">
      <span>股票池 <b>${r.universe_note}</b>${r.loaded ? html` · 实际回测 <b>${r.loaded}</b> 只` : ""}${r.skipped ? html` · 跳过 <b>${r.skipped}</b> 只（K线不足）` : ""}</span>
      ${r.entry_note && html`<span>建仓规则 <b>${r.entry_note}</b></span>`}
      ${r.exit_note && html`<span>出场规则 <b>${r.exit_note}</b></span>`}
      ${m.avg_core_weight != null && html`<span>平均仓位 个股 <b>${fmt.pct(m.avg_stock_weight, 0)}</b> · 核心仓 <b>${fmt.pct(m.avg_core_weight, 0)}</b>
        · 核心仓累计成交 <b>${fmt.num(m.core_turnover, 1)}</b> 倍本金，费用 <b>${fmt.int(m.core_fees)}</b> 元</span>`}
      ${m.bear_regime_days != null && html`<span>空头区间 <b>${m.bear_regime_days}</b> 天 · 强制清仓 <b>${m.bear_forced_exits ?? 0}</b> 笔</span>`}
      ${changed.length > 0 && html`<span>改动的参数 <b>${changed.map(([k, v]) => `${k}=${v}`).join("、")}</b></span>`}
    </div>

    ${r.equity.x.length > 1 && html`<${Card} title="资金曲线" hint="策略 vs 基准指数（已归一到同一起点）">
      <${Chart} height=${320} deps=${[r]} build=${(t) => equityOption(r, t)} />
      <div class="small muted" style="margin:8px 0 2px">回撤</div>
      <${Chart} height=${150} deps=${[r]} build=${(t) => drawdownOption(r, t)} />
    <//>
    <${Card} title="月度收益" hint="按月末权益计算，%"><${Monthly} eq=${r.equity} /><//>`}

    ${r.trades.rows.length > 0 && html`<div class="grid-73">
      <${Card} title="成交明细" extra=${html`<${Badge}>${r.trades.rows.length} 笔<//>`} flush>
        <${FrameTable} frame=${r.trades} maxHeight=${380} overrides=${{
          entry_date: { label: "买入日", fmt: fmt.date }, exit_date: { label: "卖出日", fmt: fmt.date },
          code: { label: "代码", fmt: (v) => html`<b>${v}</b>` },
          entry_price: { label: "买入价", align: "r", fmt: (v) => fmt.num(v) },
          exit_price: { label: "卖出价", align: "r", fmt: (v) => fmt.num(v) },
          shares: { label: "股数", align: "r", fmt: fmt.int },
          pnl: { label: "盈亏", align: "r", fmt: (v) => fmt.num(v, 0), cls: tone },
          return_pct: { label: "收益率", align: "r", fmt: (v) => fmt.pct(v, 2, true), cls: tone },
          exit_reason: { label: "卖出原因" }, holding_days: { label: "持有天数", align: "r", fmt: fmt.int },
        }} />
      <//>
      <${Card} title="卖出原因" hint="笔数">
        <${Chart} height=${Math.max(160, r.exit_reasons.length * 30)} deps=${[r]}
          build=${(t) => hbarOption(r.exit_reasons, t, { fmtV: (v) => `${v} 笔` })} />
      <//>
    </div>`}

    ${!isTiming && html`<${Ablation} source=${job.id} nSwitches=${r.active_switches} />`}

    ${r.regime_log && html`<${Card} title="活跃市值区间触发" flush>
      <${FrameTable} frame=${r.regime_log} maxHeight=${240} />
      <div class="table-foot">区间为持续态：触发空头后保持空头直到出现多头触发，反之亦然。空头期间禁止开仓，持仓在触发次日开盘清仓。</div>
    <//>`}`;
}
