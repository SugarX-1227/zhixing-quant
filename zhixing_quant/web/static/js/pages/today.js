// 今日：择时 → 防守 → 进攻 → 下单。顺序是规格里锁定的，防守永远优先于买入。

import { html, useShared, api, fmt, toast, dataDate, tone } from "../lib.js";
import { Card, Field, Select, NumberInput, DateInput, Button, Badge, Callout, Empty, Notes, JobBar, JobError, Callouts } from "../ui.js";
import { useJob } from "../lib.js";
import { CandidateExplorer, Coverage } from "./common.js";

const REGIME = { BULL: "多头", NEUTRAL: "中性", BEAR: "空头" };

function Steps({ r }) {
  const room = Math.max(0, r.max_total_pct - r.position_pct);
  const ok = r.plans.filter((p) => !p.blocked).length;
  const steps = [
    { n: 1, t: "择时", s: r.allow_open ? `允许开仓 · 上限 ${fmt.pct(r.max_total_pct, 0)}` : "禁止开仓", c: r.allow_open ? "ok" : "bad" },
    { n: 2, t: "防守", s: r.actions_needed ? `${r.actions_needed} 个需要动作` : `${r.reviews.length} 个持仓正常`, c: r.actions_needed ? "bad" : "ok" },
    { n: 3, t: "进攻", s: r.offense_disabled_reason ? "已禁用" : `命中 ${r.candidates.rows.length} 只`, c: r.offense_disabled_reason ? "warn" : "ok" },
    { n: 4, t: "下单", s: r.plans.length ? `${ok} 条可执行 · ${r.plans.length - ok} 条拦截` : `可加仓空间 ${fmt.pct(room, 1)}`, c: ok ? "ok" : "" },
  ];
  return html`<div class="steps">${steps.map((s) => html`
    <div class=${"step " + s.c}><div class="step-n">${s.n}</div><div style="min-width:0"><b>${s.t}</b><span>${s.s}</span></div></div>`)}</div>`;
}

function Regime({ r }) {
  const room = Math.max(0, r.max_total_pct - r.position_pct);
  const cells = [
    ["活跃市值当日涨跌", fmt.pct(r.regime_score, 2, true), tone(r.regime_score)],
    ["触发依据", r.regime_trigger || "无新触发", "muted"],
    ["当前仓位", fmt.pct(r.position_pct, 1), ""],
    ["可加仓空间", fmt.pct(room, 1), room > 0 ? "up" : "muted"],
  ];
  return html`<div class="regime">
    <div class=${"regime-big " + r.regime}>${REGIME[r.regime] || r.regime}</div>
    <div class="regime-cells">${cells.map(([l, v, c]) => html`<div><div class="l">${l}</div><div class=${"v " + c}>${v}</div></div>`)}</div>
  </div>`;
}

function Holding({ v }) {
  const stars = html`<span class="stars"><span class="up">${"★".repeat(v.stars)}</span><i>${"★".repeat(5 - v.stars)}</i></span>`;
  return html`<div class=${"hold" + (v.action ? " act" : "")}>
    <div class="grow">
      <div class="title-line"><b>${v.code}</b><span class="muted">${v.name}</span>
        <span class="small muted">${v.shares} 股 @ ${fmt.num(v.entry_price)}</span>
        ${stars}<span class="small muted">${v.stars}/5 ${v.risk_level}</span></div>
      <div class="small" style="margin-top:6px;display:flex;gap:8px;align-items:center;color:var(--text-2)">
        ${v.action ? html`<${Badge} tone="up">${v.priority_label}<//><span>${v.action_text}，次日开盘执行。</span>`
          : html`<${Badge} tone="down">持有<//><span>${(v.rating_reasons || []).slice(0, 2).join("、")}</span>`}
      </div>
    </div>
    <div style="text-align:right">
      <div class="strong" style="font-size:15px">${fmt.num(v.last_price)}</div>
      <div class=${"strong small " + tone(v.return_pct)}>${fmt.pct(v.return_pct, 2, true)}</div>
    </div>
  </div>`;
}

function Plan({ p, onBuy, busy }) {
  const cells = p.blocked ? [["结果", "不下单"]] : [
    ["股数", fmt.int(p.shares)], ["金额", fmt.money(p.amount)], ["止损", fmt.num(p.stop_loss)],
    ["风险敞口", html`${fmt.money(p.risk_amount)}<div class="small muted" style="font-weight:400">权益 ${fmt.pct(p.risk_pct_of_equity, 2)}</div>`],
  ];
  return html`<div class=${"plan" + (p.blocked ? " blocked" : "")}>
    <div class="grow"><div class="title-line"><b>${p.code}</b><span class="muted">${p.name}</span></div>
      <div class="small muted" style="margin-top:4px">${p.blocked ? p.blocked_reason : p.signal}</div></div>
    <div class="cells">${cells.map(([l, v]) => html`<div><div class="l">${l}</div><div class="v">${v}</div></div>`)}</div>
    ${!p.blocked && html`<${Button} kind="sm" icon="plus" loading=${busy} onClick=${() => onBuy(p)}
      title="按计划的股数和止损写入持仓">记录建仓<//>`}
  </div>`;
}

export default function Today({ meta, book, status, refreshStatus }) {
  const strategies = meta.strategies;
  const names = Object.keys(strategies);
  const def = names.find((n) => strategies[n].book === book) || names[0];
  const [form, setForm] = useShared(`today:form:${book}`, () => ({ strategy: def, date: dataDate(status), limit: 500 }));
  const set = (k) => (v) => setForm({ ...form, [k]: v });
  const { job, run, busy, result: r } = useJob(`today:${book}`);
  const [buying, setBuying] = useShared("today:buying", "");

  const buy = async (p) => {
    setBuying(p.code);
    try {
      await api.post("/api/positions/open", { book, code: p.code, shares: p.shares, price: p.entry_price,
        stop_loss: p.stop_loss, name: p.name, strategy: p.strategy, take_profit: p.take_profit, entry_date: r.date });
      toast(`已记录 ${p.code} ${p.shares} 股，止损 ${fmt.num(p.stop_loss)}`);
      refreshStatus();
    } catch (e) { toast(e.message, "err"); }
    setBuying("");
  };

  return html`
    <${Card}>
      <div class="row">
        <${Field} label="进攻战法" class="w-lg"><${Select} value=${form.strategy} onChange=${set("strategy")}
          options=${names.map((n) => ({ value: n, label: `${strategies[n].label}（${meta.books[strategies[n].book]}）` }))} /><//>
        <${Field} label="信号日期"><${DateInput} value=${form.date} onChange=${set("date")} /><//>
        <${Field} label="扫描范围" tip="按成交额降序取前 N 只，设大会更慢" class="w-sm">
          <${NumberInput} value=${form.limit} min=${50} max=${6000} step=${50} onChange=${set("limit")} /><//>
        <div class="grow"></div>
        <${Button} kind="primary" icon="play" loading=${busy}
          onClick=${() => run("today", { ...form, book }).then(refreshStatus)}>运行今日决策<//>
      </div>
    <//>
    <${JobBar} job=${job} /><${JobError} job=${job} />

    ${!r ? !busy && html`<${Card}><${Empty} icon="today" title="还没有今日决策">选好战法和日期后点「运行今日决策」。系统按 <b>择时 → 防守 → 进攻 → 下单</b> 的顺序走一遍，这个顺序是规格里锁定的，防守永远优先于买入。<//><//>`
    : html`
      <div class="kv"><span>信号日 <b>${fmt.date(r.date)}</b> 收盘后</span><span>账户 <b>${meta.books[r.book]}</b></span>
        <span>权益 <b>${fmt.money(r.equity)}</b></span><span>现金 <b>${fmt.money(r.cash)}</b></span></div>
      <${Steps} r=${r} />

      <${Card} title="① 择时" hint="决定今天是否允许开新仓"
        extra=${html`<${Badge} tone=${r.allow_open ? "down" : "up"}>${r.allow_open ? `允许开仓 · 上限 ${fmt.pct(r.max_total_pct, 0)}` : "禁止开仓"}<//>`}>
        <${Regime} r=${r} />
      <//>

      <${Card} title="② 防守" hint="先处理持仓，优先级高于任何买入" flush
        extra=${html`<${Badge} tone=${r.actions_needed ? "up" : ""}>${r.actions_needed ? `${r.actions_needed} 个需要动作` : `${r.reviews.length} 个持仓正常`}<//>`}>
        ${r.reviews.length ? html`${r.reviews.map((v) => html`<${Holding} v=${v} />`)}
            <div style="padding:12px 16px;border-top:1px solid var(--line-soft)"><${Coverage} coverage=${r.defense_coverage} /></div>`
          : html`<${Empty} icon="wallet" title="当前没有持仓">到「持仓」页登记，或在下面的下单计划里记录建仓。<//>`}
      <//>

      <${Card} title="③ 进攻" hint="择时放行后才评估" flush
        extra=${html`<${Badge} tone=${r.offense_disabled_reason ? "warn" : "accent"}>${r.offense_disabled_reason ? "已禁用" : `命中 ${r.candidates.rows.length} 只`}<//>`}>
        ${r.offense_disabled_reason ? html`<div class="card-body"><${Callout}>${r.offense_disabled_reason}<//></div>`
          : r.candidates.rows.length ? html`
            ${(r.candidates.warnings.length || r.candidates.rank_note) && html`<div class="card-body stack">
              <${Callouts} items=${r.candidates.warnings} />
              ${r.candidates.rank_note && html`<div class="small muted">排序：${r.candidates.rank_note}</div>`}</div>`}
            <${CandidateExplorer} jobId=${job.id} rows=${r.candidates.rows} slot=${"today:" + book} />`
          : html`<${Empty} icon="search" title="今天没有符合条件的标的">换个战法试试，或到「数据」页确认行情已同步到最新交易日。<//>`}
      <//>

      ${r.plans.length > 0 && html`<${Card} title="④ 下单计划" hint="含止损与风险敞口，缺止损不允许下单" flush
          extra=${html`<${Badge}>${r.plans.filter((p) => !p.blocked).length} 条可执行 · ${r.plans.filter((p) => p.blocked).length} 条拦截<//>`}>
        ${r.plans.map((p) => html`<${Plan} p=${p} onBuy=${buy} busy=${buying === p.code} />`)}
      <//>`}
      <${Notes} items=${r.notes} />
    `}`;
}
