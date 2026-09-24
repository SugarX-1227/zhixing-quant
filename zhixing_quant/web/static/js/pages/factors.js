// 因子：有效性检验。回答的是「这个因子有没有用」，不是「回测能赚多少」。

import { html, useShared, useJob, api, fmt, daysAgo, dataDate, toast } from "../lib.js";
import {
  Card, Field, Select, NumberInput, DateInput, Switch, Chips, Button, Badge, Callout, Callouts,
  JobBar, JobError, FrameTable, Table, Empty, Accordion, Rich,
} from "../ui.js";
import { Chart, quantileOption, lineOption } from "../charts.js";

function Registry({ meta }) {
  return html`<div class="grid-2">
    <${Card} title="已注册的因子" flush>
      <${Table} maxHeight=${420} rows=${meta.factors} columns=${[
        { key: "name", label: "因子", fmt: (v, r) => html`<b>${r.label}</b><span class="sub mono">${v}</span>` },
        { key: "category", label: "分类" },
        { key: "direction", label: "方向", fmt: (v) => (v > 0 ? "越大越好" : "越小越好") },
        { key: "help", label: "说明", fmt: (v) => html`<span class="muted" style="white-space:normal">${v}</span>`, sort: false }]} />
    <//>
    <${Card} title="预设权重组合" hint="初始猜测值，必须用 IC 校准后再用于排序">
      <div class="stack">${Object.entries(meta.presets).map(([k, p]) => html`<div>
        <div><b>${p.label}</b> <code class="muted">${k}</code></div>
        <div class="small muted">${p.help}</div>
        <div class="chips" style="margin-top:6px">${Object.entries(p.weights).map(([f, w]) => html`<span class="badge outline">${f} × ${w}</span>`)}</div>
      </div>`)}</div>
    <//>
  </div>`;
}

function Weights({ jobId, population, backtestable }) {
  const [w, setW] = useShared("fac:weights:form", { max_factors: 6, min_t: 2, allow_flip: true, target: null });
  const [out, setOut] = useShared("fac:weights:out", null);
  const set = (k) => (v) => setW({ ...w, [k]: v });
  const target = w.target || (backtestable.includes(population) ? population : "b2");
  const gen = () => api.post(`/api/jobs/${jobId}/weights`, { ...w, target })
    .then((r) => setOut({ jobId, ...r })).catch((e) => toast(e.message, "err"));
  return html`<${Accordion} title="按实测 IC 生成权重">
    <div class="stack">
      ${population === "all" && html`<${Callout} tone="warn">这组 IC 是在<b>全市场</b>上算的，不适合直接给战法排序——
        全量实测 B2 照这样配，样本外 -23.2%，手写预设 +9.7%。上面「检验人群」选对应战法的命中标的后再生成。<//>`}
      <div class="row">
        <${Field} label="最多留几个因子" class="w-sm" tip="留太多等于在短样本上过拟合"><${NumberInput} value=${w.max_factors} min=${2} max=${12} step=${1} onChange=${set("max_factors")} /><//>
        <${Field} label="显著性门槛 |t|" class="w-sm"><${NumberInput} value=${w.min_t} min=${1} max=${5} step=${0.5} onChange=${set("min_t")} /><//>
        <${Field} label="写给哪个战法" class="w-sm"><${Select} value=${target} onChange=${set("target")}
          options=${backtestable.map((k) => ({ value: k, label: k }))} /><//>
        <${Switch} checked=${w.allow_flip} onChange=${set("allow_flip")} label="允许反向使用" title="关掉则直接剔除方向相反的因子，而不是反过来用" />
        <${Button} icon="play" onClick=${gen}>生成<//>
      </div>
      ${out && out.jobId === jobId && html`<pre class="code-block">${out.yaml}</pre>`}
      <p class="small muted" style="margin:0">负权重表示该因子实测方向与声明相反，反着用。⚠️ 这是在<b>当前区间</b>上拟合出来的权重，区间只能是样本内：
        粘进 <code>settings.yaml</code> 后，在区间之后的一段数据上回测，没考过现有预设就不换。全量实测过一次条件 IC 权重，B2 样本外 -7.0%，仍输给预设的 +9.7%。</p>
    </div>
  <//>`;
}

export default function Factors({ meta, status }) {
  const all = meta.factors.map((f) => f.name);
  const labelOf = Object.fromEntries(meta.factors.map((f) => [f.name, f.label]));
  const [form, setForm] = useShared("fac:form", () => ({
    factors: all, horizon: meta.factor_defaults.horizon, quantiles: meta.factor_defaults.quantiles,
    start: daysAgo(540), end: dataDate(status), size: 200, min_amount: 1, population: "all", bull_only: true,
  }));
  const set = (k) => (v) => setForm({ ...form, [k]: v });
  const { job, run, busy, result: r } = useJob("fac");
  const [pick, setPick] = useShared("fac:pick", null);

  const summaryRows = r ? r.summary.rows.map((row) => Object.fromEntries(r.summary.columns.map((c, i) => [c, row[i]]))) : [];
  const cur = r && (r.quantiles[pick] || r.ic_cum[pick]) ? pick : summaryRows[0]?.["因子"];
  const q = r && r.quantiles[cur];
  const ic = r && r.ic_cum[cur];

  return html`
    <${Card} title="检验设置" extra=${html`<${Button} kind="primary" icon="play" loading=${busy} disabled=${!form.factors.length}
        onClick=${() => run("factors", form)}>开始检验<//>`}>
      <div class="stack">
        <${Field} label=${html`参与检验的因子 <span class="muted">${form.factors.length}/${all.length}</span>
            <button class="reset" style="margin-left:8px" onClick=${() => set("factors")(form.factors.length === all.length ? [] : all)}>
              ${form.factors.length === all.length ? "全不选" : "全选"}</button>`}>
          <${Chips} value=${form.factors} onChange=${set("factors")}
            options=${meta.factors.map((f) => ({ value: f.name, label: f.label, title: `${f.name} · ${f.category}` }))} />
        <//>
        <div class="row">
          <${Field} label="开始"><${DateInput} value=${form.start} onChange=${set("start")} /><//>
          <${Field} label="结束"><${DateInput} value=${form.end} onChange=${set("end")} /><//>
          <${Field} label="未来收益天数" class="w-sm"><${NumberInput} value=${form.horizon} min=${1} max=${60} step=${1} onChange=${set("horizon")} /><//>
          <${Field} label="分层数" class="w-sm"><${NumberInput} value=${form.quantiles} min=${3} max=${10} step=${1} onChange=${set("quantiles")} /><//>
          <${Field} label="股票池大小" class="w-sm"><${NumberInput} value=${form.size} min=${20} max=${6000} step=${100} onChange=${set("size")} /><//>
          <${Field} label="成交额下限(亿)" class="w-sm"><${NumberInput} value=${form.min_amount} min=${0} max=${50} step=${0.5} onChange=${set("min_amount")} /><//>
          <${Field} label="检验人群" class="w-lg"><${Select} value=${form.population} onChange=${set("population")}
            options=${[{ value: "all", label: "全市场" }, ...meta.backtestable.map((k) => ({ value: k, label: `${meta.strategies[k].label} 命中标的` }))]} /><//>
          <${Switch} checked=${form.bull_only} onChange=${set("bull_only")} disabled=${form.population === "all"}
            label="只看多头区间" title="空头区间引擎禁止开仓，那天的命中根本不会被排序" />
        </div>
        <${Callout}>给战法配排序权重，要看<b>命中人群</b>的 IC：排序只作用于当日命中信号的那十几只，因子规律和全市场不同。
          全量实测 B2：<code>amount_cv</code> 全市场最强、命中人群里失效；<code>vol_ratio</code> 在命中人群里符号翻转。
          选命中人群时股票池要放到全市场（6000 只、成交额下限 0），否则每天命中不足 10 只，算不出 IC；全市场约需 5 分钟。<//>
        <div class="small muted">IC = 当日因子排序与未来 N 日收益排序的秩相关。|IC均值| > 0.03 算有信号，|ICIR| > 0.3 算稳定，样本不足 60 个交易日的结论不要当真。</div>
      </div>
    <//>
    <${JobBar} job=${job} /><${JobError} job=${job} />

    ${!r ? !busy && html`<${Registry} meta=${meta} />`
    : html`
      <${Callouts} items=${r.warnings} />
      <div class="kv"><span>实际参与 <b>${r.n_loaded}</b> 只标的</span><span><b>${r.n_days}</b> 个交易日</span>
        <span>人群 <b>${r.population === "all" ? "全市场" : `${meta.strategies[r.population]?.label} 命中标的`}</b></span>
        ${r.coverage.length > 0 && html`<span>覆盖率最低 <b>${r.coverage.map(([k, v]) => `${k} ${fmt.pct(v, 0)}`).join("、")}</b></span>`}</div>
      ${!summaryRows.length ? html`<${Card}><${Empty} title="没有算出任何 IC">多半是区间太短或股票池太小。<//><//>`
      : html`
        ${r.flipped.length > 0 && html`<${Callout} tone="error"><b>${r.flipped.length} 个因子的实测方向与声明相反</b>：${r.flipped.join("、")}。
          按声明方向给它们正权重，等于系统性地挑最差的标的。下面的「按实测 IC 生成权重」会自动把这些反过来用。<//>`}
        ${!r.any_agreed && html`<${Callout} tone="warn">没有任何因子的方向是显著一致的。这批因子在这段样本里都没用，应该退回不排序（<code>${"factors.by_strategy.<战法>: amount"}</code>），而不是硬凑一个组合。<//>`}
        <${Card} title="IC 汇总" hint="按 |ICIR| 降序——稳定性比幅度更值得先看。IC 已按声明方向调过符号，负 IC = 这个因子在这段样本里是反着的" flush
            extra=${html`<${Badge}>${summaryRows.length} 个因子<//>`}>
          <${FrameTable} frame=${r.summary} rowKey="因子" selected=${cur} onRowClick=${(row) => setPick(row["因子"])}
            overrides=${{
              "因子": { fmt: (v) => html`<b>${labelOf[v] || v}</b><span class="sub mono">${v}</span>` },
              "方向": { fmt: (v) => html`<${Badge} tone=${v === "一致" ? "down" : v.includes("相反") ? "up" : ""}>${v}<//>` },
            }} foot="点一行看该因子的分层收益与 IC 累计曲线" />
        <//>
        ${r.presets.length > 0 && html`<${Card} title="预设组合打分" hint="净 ICIR 为负 = 这个组合在帮你挑最差的" flush>
          <${Table} rows=${r.presets} columns=${[
            { key: "预设组合", label: "预设组合" },
            { key: "加权净ICIR", label: "加权净ICIR", align: "r", fmt: (v) => fmt.num(v, 3), cls: (v) => (v < 0 ? "up" : "") },
            { key: "反向因子数", label: "反向因子数", align: "r" }, { key: "因子数", label: "因子数", align: "r" }]} />
        <//>`}
        <section class="card"><${Weights} jobId=${job.id} population=${r.population} backtestable=${meta.backtestable} /></section>

        <div class="grid-2">
          <${Card} title=${`${labelOf[cur] || cur} · 分层收益`} extra=${q && html`<${Badge} tone=${q.mono < 0.6 ? "warn" : "down"}>单调性 ${fmt.pct(q.mono, 0)}<//>`}>
            ${q ? html`<${Chart} height=${240} deps=${[q]} build=${(t) => quantileOption(q.bars, t)} />
              ${q.mono < 0.6 && html`<p class="small muted">单调性偏低：只有两头有差异、中间乱，多半是几个极端值造成的假象，不是稳定的方向性。</p>`}
              <${FrameTable} frame=${q.table} boxed />` : html`<p class="muted">这个因子没有分层结果。</p>`}
          <//>
          <${Card} title="IC 累计曲线" hint="一路向上才说明稳定有效">
            ${ic ? html`<${Chart} height=${300} deps=${[ic]} build=${(t) => lineOption(ic, t, "IC 累计")} />` : html`<p class="muted">没有 IC 序列。</p>`}
          <//>
        </div>`}
    `}`;
}
