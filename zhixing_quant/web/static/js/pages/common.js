// 页面之间共用的业务组件。

import { html, useState, useEffect, api, fmt, useShared } from "../lib.js";
import { Table, Badge, Callout } from "../ui.js";
import { Chart, klineOption } from "../charts.js";

export const CANDIDATE_COLUMNS = [
  { key: "code", label: "代码 / 名称", fmt: (v, r) => html`<b>${v}</b><span class="sub">${(r.name || "").slice(0, 6)}</span>` },
  { key: "close", label: "收盘", align: "r", fmt: (v) => html`<b>${fmt.num(v)}</b>` },
  { key: "amount", label: "成交额", align: "r", fmt: fmt.money },
  { key: "stop_loss", label: "止损", align: "r", fmt: (v) => fmt.num(v) },
  { key: "take_profit", label: "止盈", align: "r", fmt: (v) => fmt.num(v), cls: () => "muted" },
  { key: "kdj_j", label: "J", align: "r", fmt: (v) => fmt.num(v, 1), cls: () => "muted" },
  { key: "confidence", label: "信心", align: "r", fmt: (v) => (v == null ? "—" : `${v}/5`) },
];

/** 左边候选列表，右边选中标的的 K 线。K 线按需从任务里取，不随结果一次性下发。 */
export function CandidateExplorer({ jobId, rows, slot }) {
  const [picked, setPicked] = useShared(`picked:${slot}`, null);
  const code = rows.some((r) => r.code === picked) ? picked : rows[0]?.code;
  const row = rows.find((r) => r.code === code) || {};
  const [k, setK] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!code || !jobId) return;
    setK(null); setErr("");
    api.get(`/api/jobs/${jobId}/chart/${code}`).then(setK).catch((e) => setErr(e.message));
  }, [jobId, code]);

  const cols = CANDIDATE_COLUMNS.filter((c) => c.key === "code" || rows.some((r) => r[c.key] != null));
  return html`<div class="explorer">
    <div>
      <${Table} columns=${cols} rows=${rows} rowKey="code" selected=${code}
        onRowClick=${(r) => setPicked(r.code)} maxHeight=${520}
        foot=${`共 ${rows.length} 只 · 点任意一行在右侧看 K 线`} />
    </div>
    <div>
      <div class="chart-head"><b>${code}</b><span class="muted">${row.name || ""}</span>
        ${row.adjust === "none" && html`<${Badge} tone="warn" title="没有除权除息数据，价格未复权">不复权</${Badge}>`}
        ${row.reason && html`<${Badge} tone="outline">${row.reason}</${Badge}>`}
      </div>
      ${err ? html`<div style="padding:16px"><${Callout} tone="warn">${err}<//></div>`
        : k ? html`<${Chart} height=${500} deps=${[k]} build=${(t) => klineOption(k, t)} />`
        : html`<div style="height:500px;display:grid;place-items:center"><span class="spin muted"></span></div>`}
    </div>
  </div>`;
}

/** 防守规则覆盖：把哑规则显式标出来，「没触发」和「规则根本没运行」必须能区分。 */
export const Coverage = ({ coverage }) => {
  const items = Object.entries(coverage || {});
  if (!items.length) return null;
  return html`<div class="cov">${items.map(([rule, ok]) => html`
    <${Badge} tone=${ok ? "down" : "up"}>${ok ? "✓" : "✗"} ${rule}${ok ? "" : " · 规则未生效"}<//>`)}</div>`;
};
