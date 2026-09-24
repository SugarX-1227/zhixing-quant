// 通用界面组件。只负责渲染，不做业务判断。

import { html, useState, useMemo, fmt, useShared } from "./lib.js";

// ------------------------------------------------------------------ 图标（线性，24 栅格）

const PATHS = {
  today: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM9 15l2 2 4-4",
  wallet: "M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14a2 2 0 0 1 2 2v3m0 4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5M17 12h4v4h-4a2 2 0 0 1 0-4z",
  radar: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0M12 12l6-6M12 12h.01",
  chart: "M3 3v18h18M7 15l4-4 3 3 6-7",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  layers: "M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  candle: "M6 3v4M6 17v4M4 7h4v10H4zM18 3v6M18 15v6M16 9h4v6h-4zM12 5v3M12 16v3M10 8h4v8h-4z",
  db: "M12 3c4.97 0 9 1.34 9 3s-4.03 3-9 3-9-1.34-9-3 4.03-3 9-3zM21 12c0 1.66-4 3-9 3s-9-1.34-9-3M3 6v12c0 1.66 4 3 9 3s9-1.34 9-3V6",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  auto: "M12 3a9 9 0 1 0 0 18V3zM12 3a9 9 0 0 1 0 18",
  play: "M6 4l14 8-14 8V4z",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
  alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  chev: "M9 18l6-6-6-6",
  menu: "M3 6h18M3 12h18M3 18h18",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  flask: "M9 3h6M10 3v6L4 19a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-6-10V3",
  plus: "M12 5v14M5 12h14",
};

export const Icon = ({ name, size = 16 }) => html`
  <svg width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d=${PATHS[name] || ""} />
  </svg>`;

// ------------------------------------------------------------------ 布局

export const Card = ({ title, hint, extra, flush, children, class: cls = "" }) => html`
  <section class=${"card " + cls}>
    ${(title || extra) && html`<header class="card-head">
      ${title && html`<h3>${title}</h3>`}
      ${hint && html`<span class="hint">${hint}</span>`}
      ${extra && html`<div class="extra">${extra}</div>`}
    </header>`}
    <div class=${"card-body" + (flush ? " flush" : "")}>${children}</div>
  </section>`;

export const Kpis = ({ items }) => html`
  <div class="kpis">
    ${items.map((k) => html`<div class=${"kpi " + (k.tone || "")} title=${k.title || ""}>
      <div class="l">${k.label}</div>
      <div class="v">${k.value}</div>
      ${k.sub && html`<div class="s">${k.sub}</div>`}
    </div>`)}
  </div>`;

export const Badge = ({ tone = "", children, title }) =>
  html`<span class=${"badge " + tone} title=${title || ""}>${children}</span>`;

const CALLOUT_ICON = { info: "info", warn: "alert", error: "alert", success: "check" };

export const Callout = ({ tone = "info", children }) => html`
  <div class=${"callout " + tone} role=${tone === "error" ? "alert" : "note"}>
    <${Icon} name=${CALLOUT_ICON[tone]} /><div>${children}</div>
  </div>`;

/** 后端文案里用 **粗体** 和 `代码`，这里做最小的渲染。 */
export function Rich({ text }) {
  const parts = String(text ?? "").split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p) => p.startsWith("**") ? html`<b>${p.slice(2, -2)}</b>`
    : p.startsWith("`") ? html`<code>${p.slice(1, -1)}</code>` : p);
}

export const Callouts = ({ items = [], tone = "warn" }) =>
  items.length ? html`<div class="stack">${items.map((w) => html`
    <${Callout} tone=${typeof w === "object" ? w.level === "error" ? "error" : "info" : tone}>
      <${Rich} text=${typeof w === "object" ? w.text : w} /><//>`)}</div>` : null;

export const Empty = ({ icon = "info", title, children }) => html`
  <div class="empty">
    <div class="empty-icon"><${Icon} name=${icon} size=${22} /></div>
    <h4>${title}</h4><p>${children}</p>
  </div>`;

export const Notes = ({ items }) => items && items.length ? html`
  <div class="notes">${items.map((t) => html`<div><${Rich} text=${t} /></div>`)}</div>` : null;

// ------------------------------------------------------------------ 表单

export const Field = ({ label, help, tip, children, class: cls = "", extra }) => html`
  <div class=${"field " + cls}>
    ${label && html`<label>${label}${tip && html`<span class="tip" title=${tip}><${Icon} name="info" size=${13} /></span>`}${extra}</label>`}
    ${children}
    ${help && html`<div class="help">${help}</div>`}
  </div>`;

export const Select = ({ value, options, onChange, changed, disabled }) => html`
  <select class=${"select" + (changed ? " changed" : "")} value=${value} disabled=${disabled}
          onChange=${(e) => onChange(e.target.value)}>
    ${options.map((o) => html`<option value=${o.value} selected=${String(o.value) === String(value)}>${o.label}</option>`)}
  </select>`;

export function NumberInput({ value, onChange, min, max, step, changed }) {
  return html`<input class=${"input" + (changed ? " changed" : "")} type="number"
    value=${value} min=${min} max=${max} step=${step ?? "any"}
    onChange=${(e) => {
      let v = parseFloat(e.target.value);
      if (!Number.isFinite(v)) v = min ?? 0;
      if (min != null) v = Math.max(min, v);
      if (max != null) v = Math.min(max, v);
      onChange(v);
    }} />`;
}

export const TextInput = ({ value, onChange, placeholder, onEnter }) => html`
  <input class="input" value=${value} placeholder=${placeholder || ""}
    onInput=${(e) => onChange(e.target.value)}
    onKeyDown=${(e) => e.key === "Enter" && onEnter && onEnter()} />`;

export const DateInput = ({ value, onChange }) => html`
  <input class="input" type="date" value=${value} onChange=${(e) => onChange(e.target.value)} />`;

export const Switch = ({ checked, onChange, label, disabled, title }) => html`
  <label class=${"check" + (disabled ? " disabled" : "")} title=${title || ""}>
    <input type="checkbox" checked=${!!checked} disabled=${disabled}
           onChange=${(e) => onChange(e.target.checked)} />
    <span class="sw"></span>${label}
  </label>`;

export const Segmented = ({ value, options, onChange }) => html`
  <div class="seg" role="tablist">
    ${options.map((o) => html`<button type="button" class=${String(o.value) === String(value) ? "on" : ""}
      title=${o.title || ""} onClick=${() => onChange(o.value)}>${o.icon && html`<${Icon} name=${o.icon} size=${14} />`}${o.label}</button>`)}
  </div>`;

export function Chips({ value = [], options, onChange }) {
  const set = new Set(value.map(String));
  const toggle = (v) => onChange(set.has(String(v))
    ? value.filter((x) => String(x) !== String(v))
    : options.map((o) => o.value).filter((x) => set.has(String(x)) || String(x) === String(v)));
  return html`<div class="chips">
    ${options.map((o) => html`<button type="button" title=${o.title || ""}
      class=${"chip" + (set.has(String(o.value)) ? " on" : "")} onClick=${() => toggle(o.value)}>${o.label}</button>`)}
  </div>`;
}

export const Button = ({ kind = "", icon, loading, disabled, onClick, children, title, class: cls = "" }) => html`
  <button type="button" class=${`btn ${kind} ${cls}`} disabled=${disabled || loading}
          onClick=${onClick} title=${title || ""}>
    ${loading ? html`<span class="spin"></span>` : icon && html`<${Icon} name=${icon} size=${15} />`}
    ${children}
  </button>`;

export const Tabs = ({ tabs, active, onChange }) => html`
  <div class="tabs" role="tablist">
    ${tabs.map((t) => html`<button type="button" role="tab" class=${t.key === active ? "on" : ""}
      onClick=${() => onChange(t.key)}>${t.label}${t.dot && html`<span class="dot" title="有改动"></span>`}</button>`)}
  </div>`;

export function Accordion({ title, children, open: initial = false, badge }) {
  const [open, setOpen] = useState(initial);
  return html`<div class=${"acc" + (open ? " open" : "")}>
    <button type="button" onClick=${() => setOpen(!open)} aria-expanded=${open}>
      ${title}${badge}<span class="chev"><${Icon} name="chev" size=${14} /></span>
    </button>
    ${open && html`<div>${children}</div>`}
  </div>`;
}

export const Modal = ({ title, children, onClose, footer }) => html`
  <div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal" role="dialog" aria-label=${title}>
      <header class="card-head"><h3>${title}</h3>
        <div class="extra"><button class="btn ghost sm" onClick=${onClose} aria-label="关闭"><${Icon} name="x" size=${14} /></button></div>
      </header>
      <div class="card-body">${children}</div>
      ${footer && html`<div class="modal-foot">${footer}</div>`}
    </div>
  </div>`;

// ------------------------------------------------------------------ 任务进度

export function JobBar({ job }) {
  if (!job || !(job.status === "queued" || job.status === "running")) return null;
  const indet = !job.progress;
  return html`<div class="job" aria-live="polite">
    <div class="job-top"><span>${job.text || "运行中"}</span>
      <span class="muted">${job.progress ? Math.round(job.progress * 100) + "%" : ""}
        ${job.elapsed ? ` · ${job.elapsed.toFixed(0)}s` : ""}</span></div>
    <div class=${"bar" + (indet ? " indet" : "")}><i style=${`width:${(job.progress || 0) * 100}%`}></i></div>
  </div>`;
}

export const JobError = ({ job }) => job && job.status === "error"
  ? html`<${Callout} tone="error">运行失败：${job.error}<//>` : null;

// ------------------------------------------------------------------ 表格

/**
 * columns: [{key, label, align, fmt(v,row), cls(v,row), sort:false}]
 * rows: 对象数组。rowKey: 行主键字段，配合 selected/onRowClick 做选中高亮。
 */
export function Table({ columns, rows, maxHeight, rowKey, selected, onRowClick, boxed, foot, initialSort }) {
  const [sort, setSort] = useState(initialSort || null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const { key, dir } = sort;
    return [...rows].sort((a, b) => {
      const x = a[key], y = b[key];
      if (x == null) return 1;
      if (y == null) return -1;
      return (x > y ? 1 : x < y ? -1 : 0) * dir;
    });
  }, [rows, sort]);
  const click = (c) => c.sort !== false && setSort(
    sort && sort.key === c.key ? (sort.dir === -1 ? { key: c.key, dir: 1 } : null) : { key: c.key, dir: -1 });
  return html`<div class=${"table-wrap" + (boxed ? " boxed" : "")} style=${maxHeight ? `max-height:${maxHeight}px` : ""}>
    <table class="t">
      <thead><tr>${columns.map((c) => html`<th class=${(c.align || "") + (c.sort !== false ? " sortable" : "")}
        onClick=${() => click(c)} title=${c.title || ""}>${c.label}${sort && sort.key === c.key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}</th>`)}</tr></thead>
      <tbody>${sorted.map((r, i) => html`<tr key=${rowKey ? r[rowKey] : i}
          class=${(onRowClick ? "click " : "") + (rowKey && selected != null && r[rowKey] === selected ? "sel" : "")}
          onClick=${onRowClick ? () => onRowClick(r) : undefined}>
        ${columns.map((c) => html`<td class=${(c.align || "") + " " + (c.cls ? c.cls(r[c.key], r) : "")}>
          ${c.fmt ? c.fmt(r[c.key], r) : r[c.key] ?? "—"}</td>`)}
      </tr>`)}</tbody>
    </table>
  </div>${foot && html`<div class="table-foot">${foot}</div>`}`;
}

// 服务端返回的 {columns, rows} 表格 → Table。按列名猜格式。
const PCT_COL = /收益|回撤|胜率|占比|衰减|年化|return_pct|当日涨跌|覆盖/;
const RATIO_COL = /夏普|卡尔玛|ICIR|IC|t值|目标|平坦度|净ICIR/;

export function frameRows(frame) {
  if (!frame) return [];
  return frame.rows.map((r) => Object.fromEntries(frame.columns.map((c, i) => [c, r[i]])));
}

export function autoColumns(frame, overrides = {}) {
  if (!frame) return [];
  return frame.columns.map((c) => {
    if (overrides[c]) return { key: c, label: c, ...overrides[c] };
    const sample = frame.rows.map((r) => r[frame.columns.indexOf(c)]).find((v) => v != null);
    if (typeof sample !== "number") return { key: c, label: c };
    const isPct = PCT_COL.test(c) && !/笔数|天数/.test(c);
    if (isPct) {
      const signed = /收益|Δ|衰减|涨跌/.test(c);
      return { key: c, label: c, align: "r",
        fmt: (v) => fmt.pct(v, 2, signed),
        cls: signed ? (v) => (v > 0 ? "up" : v < 0 ? "down" : "") : undefined };
    }
    if (RATIO_COL.test(c)) return { key: c, label: c, align: "r", fmt: (v) => fmt.num(v, /IC均值|IC标准差/.test(c) ? 4 : 3) };
    const intish = frame.rows.every((r) => { const v = r[frame.columns.indexOf(c)]; return v == null || Number.isInteger(v); });
    return { key: c, label: c, align: "r", fmt: (v) => (intish ? fmt.int(v) : fmt.num(v, Math.abs(v) < 1 ? 4 : 2)) };
  });
}

export const FrameTable = ({ frame, overrides, ...rest }) =>
  html`<${Table} columns=${autoColumns(frame, overrides)} rows=${frameRows(frame)} ...${rest} />`;

// ------------------------------------------------------------------ 参数

/** 战法/出场/成本参数的控件。values 里没有的键用 param.value（当前配置值）。 */
export function ParamGrid({ params, values, onChange, kindLabels = {} }) {
  if (!params || !params.length) return html`<p class="muted small">这套战法没有声明可调参数。</p>`;
  return html`<div class="params">${params.map((p) => {
    const v = values[p.key] ?? p.value;
    const changed = values[p.key] != null && values[p.key] !== p.value;
    const set = (x) => onChange({ ...values, [p.key]: x });
    const reset = () => { const n = { ...values }; delete n[p.key]; onChange(n); };
    const help = (p.help || "") + (p.tag === "LOCKED" ? "（规格标为 LOCKED，是规则本身，改动前想清楚）" : "");
    const extra = html`${p.tag === "LOCKED" && html`<span class="lock">LOCKED</span>`}
      ${changed && html`<button class="reset" onClick=${reset} title="恢复配置值">恢复</button>`}`;
    let ctl;
    if (p.kind === "bool") {
      ctl = html`<${Switch} checked=${v} onChange=${set} label=${v ? "开" : "关"} />`;
    } else if (p.kind === "choice") {
      const labels = p.choice_labels || kindLabels;
      ctl = html`<${Select} value=${v} changed=${changed} onChange=${set}
        options=${p.choices.map((c) => ({ value: c, label: labels[c] || c }))} />`;
    } else {
      const step = p.step || (p.kind === "int" ? 1 : 0.01);
      ctl = html`<${NumberInput} value=${v} min=${p.lo} max=${p.hi} step=${step} changed=${changed}
        onChange=${(x) => set(p.kind === "int" ? Math.round(x) : x)} />`;
    }
    return html`<${Field} label=${p.label} tip=${help || null} extra=${extra}>${ctl}<//>`;
  })}</div>`;
}

export const Toasts = () => {
  const [list] = useShared("toasts", []);
  return html`<div class="toasts">${list.map((t) => html`
    <div class=${"toast " + t.kind} role="status">
      <${Icon} name=${t.kind === "err" ? "alert" : "check"} /><div>${t.text}</div>
    </div>`)}</div>`;
};
