// 校准：样本内外切分 + 网格扫描 + 滚动前进验证。核心是防过拟合。

import { html, useShared, useJob, fmt, daysAgo, dataDate } from "../lib.js";
import { Card, Field, Select, NumberInput, DateInput, Button, Badge, Callout, Callouts, Chips, Tabs, JobBar, JobError, FrameTable, Empty, Table } from "../ui.js";

function GridPicker({ strategy, meta, grid, setGrid, hint, defaultVals = (o) => o }) {
  const paths = Object.entries(meta.calib_params).map(([tpl, v]) => [tpl.replace("{s}", strategy), v]);
  const picked = Object.keys(grid);
  const labels = meta.kind_labels;
  return html`<div class="stack">
    <${Field} label=${hint}>
      <${Chips} value=${picked} options=${paths.map(([p, v]) => ({ value: p, label: v.label, title: p }))}
        onChange=${(keys) => setGrid(Object.fromEntries(keys.map((k) => [k, grid[k] ||
          defaultVals(meta.calib_params[k.replace(`.${strategy}.`, ".{s}.")].options)])))} />
    <//>
    ${picked.map((p) => {
      const opt = meta.calib_params[p.replace(`.${strategy}.`, ".{s}.")];
      return html`<div class="row" style="align-items:center">
        <div style="width:150px" class="small"><b>${opt.label}</b><div class="muted mono" style="font-size:11px">${p}</div></div>
        <div class="grow"><${Chips} value=${grid[p]} onChange=${(vals) => setGrid({ ...grid, [p]: vals })}
          options=${opt.options.map((o) => ({ value: o, label: typeof o === "string" ? labels[o] || o : String(o) }))} /></div>
      </div>`;
    })}
  </div>`;
}

const combos = (grid) => Object.values(grid).reduce((n, v) => n * (v.length || 0), Object.keys(grid).length ? 1 : 0);

export default function Calibrate({ meta, status }) {
  const names = meta.backtestable;
  const [form, setForm] = useShared("cal:form", () => ({
    strategy: names[0], start: daysAgo(1000), end: dataDate(status), oos: 0.3, objective: "calmar",
    size: 150, min_amount: 1, top_n: 3, train_months: 12, test_months: 3,
  }));
  const set = (k) => (v) => setForm({ ...form, [k]: v });
  const [tab, setTab] = useShared("cal:tab", "sweep");
  const [grid, setGrid] = useShared(`cal:grid:${form.strategy}`, {});
  const [wgrid, setWgrid] = useShared(`cal:wgrid:${form.strategy}`, {});
  const sw = useJob("cal:sweep");
  const wf = useJob("cal:wf");
  const n = combos(grid);

  return html`
    <${Callout}>判断一组参数能不能用，三件事缺一不可：<b>① 样本外没有大幅衰减　② 参数曲面是平的不是尖的　③ 交易笔数够（≥ 30）</b>。
      只要一条不满足就不该上实盘，哪怕回测收益再好看。<//>
    <${Card} title="区间与股票池">
      <div class="row">
        <${Field} label="战法"><${Select} value=${form.strategy} onChange=${set("strategy")}
          options=${names.map((n) => ({ value: n, label: meta.strategies[n].label }))} /><//>
        <${Field} label="开始"><${DateInput} value=${form.start} onChange=${set("start")} /><//>
        <${Field} label="结束"><${DateInput} value=${form.end} onChange=${set("end")} /><//>
        <${Field} label=${`样本外占比 ${fmt.pct(form.oos, 0)}`} class="w-lg">
          <input type="range" min="0.1" max="0.5" step="0.05" value=${form.oos}
                 onInput=${(e) => set("oos")(parseFloat(e.target.value))} /><//>
        <${Field} label="排序目标" class="w-lg"><${Select} value=${form.objective} onChange=${set("objective")}
          options=${Object.entries(meta.objectives).map(([value, label]) => ({ value, label }))} /><//>
        <${Field} label="股票池" class="w-sm"><${NumberInput} value=${form.size} min=${20} max=${800} step=${10} onChange=${set("size")} /><//>
        <${Field} label="成交额下限(亿)" class="w-sm"><${NumberInput} value=${form.min_amount} min=${0} max=${50} step=${0.5} onChange=${set("min_amount")} /><//>
      </div>
    <//>

    <section class="card">
      <${Tabs} active=${tab} onChange=${setTab} tabs=${[{ key: "sweep", label: "网格扫描" }, { key: "wf", label: "滚动前进验证" }]} />
      <div class="card-body stack">
        ${tab === "sweep" ? html`
          <${GridPicker} strategy=${form.strategy} meta=${meta} grid=${grid} setGrid=${setGrid}
            hint="要扫哪些参数（选 1-3 个，越多越容易撞出噪声）" />
          <div class="row" style="align-items:center">
            <${Field} label="样本外验证前 N 组" class="w-sm"><${NumberInput} value=${form.top_n} min=${1} max=${10} step=${1} onChange=${set("top_n")} /><//>
            <div class="grow small muted">${n ? `${n} 组参数 × (样本内 + 前 ${form.top_n} 组的样本外) ≈ ${n + form.top_n} 次回测。有效配置相同的组会自动复用。` : ""}</div>
            <${Button} kind="primary" icon="play" loading=${sw.busy} disabled=${!n}
              onClick=${() => sw.run("sweep", { ...form, grid })}>开始扫描<//>
          </div>`
        : html`
          <p class="small muted" style="margin:0">每段只用<b>之前</b>的数据定参数，在后面那段上交易。把各测试段串起来，就是「如果我每季度重调一次参」的真实曲线——这是最接近实盘的验证方式。</p>
          <${GridPicker} strategy=${form.strategy} meta=${meta} grid=${wgrid} setGrid=${setWgrid}
            hint="要滚动优化的参数（建议只选 1 个）" defaultVals=${(o) => o.slice(0, 3)} />
          <div class="row">
            <${Field} label="训练(月)" class="w-sm"><${NumberInput} value=${form.train_months} min=${3} max=${36} step=${1} onChange=${set("train_months")} /><//>
            <${Field} label="测试(月)" class="w-sm"><${NumberInput} value=${form.test_months} min=${1} max=${12} step=${1} onChange=${set("test_months")} /><//>
            <div class="grow"></div>
            <${Button} kind="primary" icon="play" loading=${wf.busy} disabled=${!combos(wgrid)}
              onClick=${() => wf.run("walkforward", { ...form, grid: wgrid })}>开始滚动验证<//>
          </div>`}
      </div>
    </section>

    ${tab === "sweep" ? html`<${JobBar} job=${sw.job} /><${JobError} job=${sw.job} />
      ${sw.result ? html`<${Callouts} items=${sw.result.warnings} />
        ${sw.result.table.rows.length > 0 && html`<${Card} title="扫描结果" hint="内_ = 样本内，外_ = 样本外；衰减 = 样本外相对样本内的变化" flush>
          <${FrameTable} frame=${sw.result.table} maxHeight=${420} /><//>`}
        ${sw.result.flatness.length > 0 && html`<${Card} title="参数平坦度" hint="稳健的参数应该有平台而不是尖峰——最优点旁边的取值也得差不多好" flush>
          <${Table} rows=${sw.result.flatness} columns=${[
            { key: "参数", label: "参数", fmt: (v) => html`<code>${v}</code>` },
            { key: "平坦度", label: "平坦度", align: "r", fmt: (v) => fmt.num(v, 3) },
            { key: "判定", label: "判定", fmt: (v) => html`<${Badge} tone=${v.startsWith("平台") ? "down" : "up"}>${v}<//>` }]} /><//>`}`
      : !sw.busy && html`<${Card}><${Empty} icon="sliders" title="选参数和取值后开始扫描">样本内选出前 N 组，再拿到样本外考试。在哪段数据上选出来的参数，那段数据就不能再用来评价它。<//><//>`}`
    : html`<${JobBar} job=${wf.job} /><${JobError} job=${wf.job} />
      ${wf.result ? html`<${Callouts} items=${wf.result.summary} />
        <${Card} title="滚动前进结果" flush><${FrameTable} frame=${wf.result.table} maxHeight=${420} /><//>`
      : !wf.busy && html`<${Card}><${Empty} icon="sliders" title="选参数后开始滚动验证">窗口多、网格要小，否则跑不完。<//><//>`}`}`;
}
