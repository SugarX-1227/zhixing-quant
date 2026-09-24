// 数据：行情库体检 + 同步。

import { html, useState, useEffect, useShared, useJob, api, fmt } from "../lib.js";
import { Card, Kpis, Switch, Button, Callout, Callouts } from "../ui.js";

export default function Data({ refreshStatus }) {
  const [h, setH] = useState(null);
  const [flags, setFlags] = useShared("data:flags", { names: false, xdxr: false, full: false, oamv: true });
  const { job, run, busy, result } = useJob("sync");
  const load = (fresh) => api.get(`/api/health${fresh ? "?fresh=1" : ""}`).then(setH).catch((e) => setH({ ok: false, message: e.message }));
  useEffect(() => { load(); }, []);
  const set = (k) => (v) => setFlags({ ...flags, [k]: v });

  const sync = async () => {
    await run("sync", flags);
    load(true);
    refreshStatus();
  };

  return html`
    ${h && (!h.ok ? html`<${Callout} tone="error">${h.message || "行情库未就绪"}<//>` : html`
      <${Kpis} items=${[
        { label: "股票数", value: fmt.int(h.codes), sub: `已命名 ${fmt.int(h.named)}` },
        { label: "K线总数", value: fmt.int(h.bars) },
        { label: "最新交易日", value: fmt.date(h.date_max), sub: `最早 ${fmt.date(h.date_min)}` },
        { label: "除权数据", value: fmt.int(h.xdxr_codes), sub: "只股票有除权除息记录" },
        { label: "库大小", value: `${h.db_size_mb} MB`, sub: `上次同步 ${h.last_sync}` },
      ]} />
      ${h.warnings.length ? html`<${Callouts} items=${h.warnings} />`
        : html`<${Callout} tone="success">数据正常，上次同步 ${h.last_sync}<//>`}`)}

    <${Card} title="同步行情" hint="从通达信 vipdoc 增量读取，秒级完成"
      extra=${html`<${Button} kind="primary" icon="refresh" loading=${busy} onClick=${sync}>立即同步<//>`}>
      <div class="row" style="gap:22px">
        <${Switch} checked=${flags.names} onChange=${set("names")} label="更新名称" />
        <${Switch} checked=${flags.xdxr} onChange=${set("xdxr")} label="更新除权除息" title="需要 pytdx" />
        <${Switch} checked=${flags.full} onChange=${set("full")} label="全量重建" />
        <${Switch} checked=${flags.oamv} onChange=${set("oamv")} label="同步活跃市值"
          title="指南针 0AMV 指标入库。需先完全退出指南针软件；当天数据要收盘后指南针写入才有" />
      </div>
      ${busy && html`<p class="small muted" style="margin:12px 0 0"><span class="spin" style="display:inline-block;vertical-align:-2px"></span> 同步中…</p>`}
      ${result && html`<div class="stack" style="margin-top:14px">
        ${result.returncode !== 0 && html`<${Callout} tone="error">
          同步有一步失败了（退出码 ${result.returncode}）${result.stderr.trim()
            ? html`<pre class="term" style="margin-top:8px">${result.stderr.trim()}</pre>` : "，原因见下方输出。"}<//>`}
        <pre class="term">${`$ python ${result.cmd}\n${result.stdout || result.stderr}`}</pre>
      </div>`}
      ${job && job.status === "error" && html`<${Callout} tone="error">${job.error}<//>`}
    <//>`;
}
