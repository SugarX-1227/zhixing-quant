// 战法：单独扫描某一套战法，参数可临时改动（不写回配置）。

import { html, useShared, useJob, fmt, dataDate } from "../lib.js";
import { Card, Field, Select, NumberInput, DateInput, Button, Badge, Callout, Callouts, Empty, JobBar, JobError, ParamGrid, Accordion } from "../ui.js";
import { CandidateExplorer } from "./common.js";

export default function Strategies({ meta, status }) {
  const strategies = meta.strategies;
  const names = Object.keys(strategies);
  const [form, setForm] = useShared("strat:form", () => ({ strategy: names[0], date: dataDate(status), limit: 500 }));
  const set = (k) => (v) => setForm({ ...form, [k]: v });
  const { job, run, busy, result: r } = useJob("strat");

  const params = meta.params.strategy[form.strategy] || [];
  const [mine, setMine] = useShared(`strat:ov:${form.strategy}`, {});
  const nChanged = Object.entries(mine).filter(([k, v]) => v !== params.find((p) => p.key === k)?.value).length;

  const rate = r ? r.rows.length / Math.max(r.scanned, 1) : 0;
  const meta_ = r ? strategies[r.strategy] || {} : {};

  return html`
    <${Card} flush>
      <div class="card-body"><div class="row">
        <${Field} label="战法" class="w-lg"><${Select} value=${form.strategy} onChange=${set("strategy")}
          options=${names.map((n) => ({ value: n, label: `${strategies[n].label}（${meta.books[strategies[n].book]}）` }))} /><//>
        <${Field} label="日期"><${DateInput} value=${form.date} onChange=${set("date")} /><//>
        <${Field} label="扫描范围" class="w-sm" tip="按成交额降序取前 N 只">
          <${NumberInput} value=${form.limit} min=${50} max=${6000} step=${50} onChange=${set("limit")} /><//>
        <div class="grow"></div>
        <${Button} kind="primary" icon="search" loading=${busy}
          onClick=${() => run("scan", { ...form, overrides: mine })}>扫描<//>
      </div></div>
      <${Accordion} title="战法参数（改完重新扫描生效）"
        badge=${nChanged ? html`<${Badge} tone="accent">已改 ${nChanged} 项<//>` : null}>
        <${ParamGrid} params=${params} values=${mine} onChange=${setMine} />
        <p class="small muted" style="margin:12px 0 0">规格 10 里这些参数标为 [CALIBRATE]，给的是初始猜测值，需要用回测校准。改动只作用于这次扫描，不写回配置。</p>
      <//>
    <//>
    <${JobBar} job=${job} /><${JobError} job=${job} />

    ${!r ? !busy && html`<${Card}><${Empty} icon="radar" title="选一套战法开始扫描">
        每套战法的入场条件、止损位和适用账户都不同，规格 06 有完整说明。<//><//>`
    : html`
      <${Callouts} items=${r.warnings} />
      ${rate > 0.15 && html`<${Callout} tone="warn">命中率 ${fmt.pct(rate, 0)} 偏高。条件可能过松，参数需要校准。<//>`}
      <${Card} title=${meta_.label || r.strategy} hint=${`规格 ${meta_.spec || ""} · 扫描 ${r.scanned} 只`} flush
        extra=${html`<${Badge} tone=${rate > 0.15 ? "warn" : "accent"}>命中 ${r.rows.length} 只（${fmt.pct(rate, 1)}）<//>`}>
        ${(r.rank_note || Object.keys(r.changed).length > 0) && html`<div class="card-body kv" style="padding-bottom:4px">
          ${r.rank_note && html`<span>排序 <b>${r.rank_note}</b></span>`}
          ${Object.keys(r.changed).length > 0 && html`<span>改动的参数 <b>${Object.entries(r.changed).map(([k, v]) => `${k}=${v}`).join("、")}</b></span>`}</div>`}
        ${r.rows.length ? html`<${CandidateExplorer} jobId=${job.id} rows=${r.rows} slot="strat" />`
          : html`<${Empty} icon="search" title="今天没有命中">这套战法的信号本来就不是每天都有。连续一周为 0 才说明条件过严。<//>`}
      <//>`}`;
}
