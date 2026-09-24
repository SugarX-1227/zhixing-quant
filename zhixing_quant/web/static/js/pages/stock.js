// 个股：K 线与全部指标，外加防守规则覆盖和历史信号统计。

import { html, useState, useShared, api, fmt, tone } from "../lib.js";
import { Card, Field, TextInput, Button, Badge, Callout, Callouts, Empty, Table, Segmented } from "../ui.js";
import { Chart, klineOption } from "../charts.js";
import { Coverage } from "./common.js";

const RANGES = [60, 120, 160, 250, 400];

export default function Stock() {
  const [code, setCode] = useShared("stock:code", "600000");
  const [days, setDays] = useShared("stock:days", 160);
  const [d, setD] = useShared("stock:data", null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = async (n = days) => {
    if (!code.trim()) return;
    setBusy(true); setErr("");
    try { setD(await api.get(`/api/stock/${encodeURIComponent(code.trim())}?days=${n}`)); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const setRange = (n) => { setDays(n); if (d) load(n); };

  return html`
    <${Card}>
      <div class="row">
        <${Field} label="代码" tip="个股 6 位；指数加市场前缀，如 sh000001 是上证指数（sz000001 是平安银行）">
          <${TextInput} value=${code} onChange=${setCode} onEnter=${() => load()} placeholder="600000" /><//>
        <${Field} label="显示天数"><${Segmented} value=${days} onChange=${setRange}
          options=${RANGES.map((n) => ({ value: n, label: String(n) }))} /><//>
        <div class="grow"></div>
        <${Button} kind="primary" icon="search" loading=${busy} onClick=${() => load()}>加载<//>
      </div>
    <//>
    ${err && html`<${Callout} tone="warn">${err}<//>`}
    ${!d ? html`<${Card}><${Empty} icon="candle" title="输入代码查看">会画出全部指标，并统计每种信号历史触发了多少次。<//><//>`
    : html`
      ${d.adjust === "none" && html`<${Callout}>当前是<b>不复权</b>价格。跑一次 <code>sync --xdxr</code> 后才是前复权。<//>`}
      <${Callouts} items=${d.warnings} />
      <div class="grid-73">
        <${Card} flush>
          <div class="chart-head" style="padding-bottom:4px">
            <b style="font-size:18px">${d.code}</b>
            <span style="font-size:18px" class=${"strong " + tone(d.last.chg)}>${fmt.num(d.last.close)}</span>
            <span class=${"pill " + tone(d.last.chg)}>${fmt.pct(d.last.chg, 2, true)}</span>
            <span class="small muted">${d.last.date} · 成交额 ${fmt.money(d.last.amount)} · ${d.bars} 根K线 · ${d.adjust === "qfq" ? "前复权" : "不复权"}</span>
          </div>
          <${Chart} height=${640} deps=${[d]} build=${(t) => klineOption(d.kline, t)} />
        <//>
        <div class="stack-lg">
          <${Card} title="关键价位">
            <div class="regime-cells">
              <div><div class="l">知行多空线</div><div class="v">${fmt.num(d.last.yellow)}</div></div>
              <div><div class="l">白线</div><div class="v">${fmt.num(d.last.white)}</div></div>
              <div><div class="l">收盘在多空线</div><div class=${"v " + (d.last.close >= d.last.yellow ? "up" : "down")}>
                ${d.last.yellow == null ? "—" : d.last.close >= d.last.yellow ? "上方" : "下方"}</div></div>
            </div>
          <//>
          <${Card} title="防守规则覆盖"><${Coverage} coverage=${d.coverage} /><//>
          <${Card} title="历史信号统计" hint="全部历史" flush>
            ${d.signals.length ? html`<${Table} maxHeight=${300} rows=${d.signals.map(([k, v]) => ({ k, v }))}
                columns=${[{ key: "k", label: "信号", fmt: (v) => html`<code>${v}</code>` }, { key: "v", label: "次数", align: "r" }]} />`
              : html`<p class="muted small" style="padding:14px 16px;margin:0">这段历史里没有任何信号触发。</p>`}
          <//>
        </div>
      </div>`}`;
}
