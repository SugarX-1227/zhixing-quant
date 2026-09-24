// 持仓：账户概览、当前持仓（可平仓）、手工登记与资金、成交记录。

import { html, useState, useEffect, api, fmt, toast, tone } from "../lib.js";
import { Card, Kpis, Table, FrameTable, Field, NumberInput, TextInput, Select, Button, Callout, Empty, Modal } from "../ui.js";

const REASONS = ["止盈", "止损", "防守触发", "手工"];

function CloseDialog({ pos, book, onDone, onClose }) {
  const [price, setPrice] = useState(pos.last_price);
  const [reason, setReason] = useState("手工");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const r = await api.post("/api/positions/close", { book, code: pos.code, price, reason });
      toast(`已平仓 ${pos.code}，盈亏 ${fmt.num(r.pnl, 0)}`);
      onDone();
    } catch (e) { toast(e.message, "err"); setBusy(false); }
  };
  return html`<${Modal} title=${`平仓 ${pos.code} ${pos.name || ""}`} onClose=${onClose}
    footer=${html`<${Button} kind="ghost" onClick=${onClose}>取消<//><${Button} kind="primary" loading=${busy} onClick=${submit}>确认平仓<//>`}>
    <div class="kv"><span>持有 <b>${fmt.int(pos.shares)} 股</b></span><span>成本 <b>${fmt.num(pos.entry_price)}</b></span>
      <span>止损 <b>${fmt.num(pos.stop_loss)}</b></span></div>
    <${Field} label="成交价"><${NumberInput} value=${price} min=${0.01} max=${100000} step=${0.01} onChange=${setPrice} /><//>
    <${Field} label="原因"><${Select} value=${reason} onChange=${setReason} options=${REASONS.map((x) => ({ value: x, label: x }))} /><//>
  <//>`;
}

export default function Positions({ book, refreshStatus }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [closing, setClosing] = useState(null);
  const [m, setM] = useState({ code: "", shares: 100, price: 10, stop: 9.5 });
  const [cash, setCash] = useState(null);

  const load = () => api.get(`/api/positions?book=${book}`)
    .then((x) => { setD(x); setCash(x.cash); setErr(""); }).catch((e) => setErr(e.message));
  useEffect(() => { setD(null); load(); }, [book]);
  const after = () => { setClosing(null); load(); refreshStatus(); };

  const register = async () => {
    try {
      await api.post("/api/positions/open", { book, code: m.code, shares: m.shares, price: m.price, stop_loss: m.stop });
      toast(`已登记 ${m.code.trim().padStart(6, "0")}`);
      setM({ ...m, code: "" });
      after();
    } catch (e) { toast(e.message, "err"); }
  };
  const saveCash = async () => {
    try { await api.post("/api/cash", { book, cash }); toast("现金已保存"); after(); }
    catch (e) { toast(e.message, "err"); }
  };

  if (err) return html`<${Callout} tone="error">${err}<//>`;
  if (!d) return html`<div class="muted">加载中…</div>`;
  const s = d.stats;
  const cols = [
    { key: "code", label: "代码 / 名称", fmt: (v, r) => html`<b>${v}</b><span class="sub">${r.name || ""}</span>` },
    { key: "shares", label: "股数", align: "r", fmt: fmt.int },
    { key: "entry_price", label: "成本", align: "r", fmt: (v) => fmt.num(v) },
    { key: "last_price", label: "现价", align: "r", fmt: (v) => html`<b>${fmt.num(v)}</b>` },
    { key: "return_pct", label: "涨跌", align: "r", fmt: (v) => html`<span class=${"pill " + tone(v)}>${fmt.pct(v, 2, true)}</span>`, cls: tone },
    { key: "pnl", label: "浮动盈亏", align: "r", fmt: (v) => fmt.num(v, 0), cls: tone },
    { key: "market_value", label: "市值", align: "r", fmt: fmt.money },
    { key: "stop_loss", label: "止损", align: "r", fmt: (v) => fmt.num(v) },
    { key: "take_profit", label: "止盈", align: "r", fmt: (v) => fmt.num(v), cls: () => "muted" },
    { key: "entry_date", label: "建仓日", fmt: fmt.date, cls: () => "muted" },
    { key: "_act", label: "", align: "r", sort: false,
      fmt: (_, r) => html`<${Button} kind="sm danger" onClick=${() => setClosing(r)}>平仓<//>` },
  ];

  return html`
    ${d.cash === 0 && !d.positions.length && html`<${Callout}>这个账户还没初始化。先在下面设置可用现金，权益和仓位才有意义。<//>`}
    <${Kpis} items=${[
      { label: "账户权益", value: fmt.money(d.equity) },
      { label: "可用现金", value: fmt.money(d.cash) },
      { label: "累计盈亏", value: fmt.money(s.total_pnl), tone: tone(s.total_pnl) },
      { label: "胜率", value: s.trades ? fmt.pct(s.win_rate, 0) : "—", sub: s.trades ? `${s.trades} 笔已平仓` : "" },
      { label: "盈亏比", value: s.trades ? fmt.num(s.profit_loss_ratio) : "—",
        sub: s.trades ? `均盈 ${fmt.money(s.avg_win)} / 均亏 ${fmt.money(s.avg_loss)}` : "" },
    ]} />

    <${Card} title="当前持仓" extra=${html`<span class="badge">${d.positions.length} 只</span>`} flush>
      ${d.positions.length ? html`<${Table} columns=${cols} rows=${d.positions} rowKey="code" />`
        : html`<${Empty} icon="wallet" title="还没有持仓记录">在「今日」页跑一遍决策，命中后点「记录建仓」；或在下面手工登记。<//>`}
    <//>

    <div class="grid-2">
      <${Card} title="手工登记" hint="止损必填，缺止损不允许建仓">
        <div class="row">
          <${Field} label="代码" class="w-sm"><${TextInput} value=${m.code} placeholder="600000" onChange=${(v) => setM({ ...m, code: v })} /><//>
          <${Field} label="股数" class="w-sm"><${NumberInput} value=${m.shares} min=${100} max=${10000000} step=${100} onChange=${(v) => setM({ ...m, shares: v })} /><//>
          <${Field} label="买入价" class="w-sm"><${NumberInput} value=${m.price} min=${0.01} max=${100000} step=${0.01} onChange=${(v) => setM({ ...m, price: v })} /><//>
          <${Field} label="止损价" class="w-sm"><${NumberInput} value=${m.stop} min=${0} max=${100000} step=${0.01} onChange=${(v) => setM({ ...m, stop: v })} /><//>
          <${Button} icon="plus" disabled=${!m.code.trim()} onClick=${register}>登记<//>
        </div>
      <//>
      <${Card} title="账户资金">
        <div class="row">
          <${Field} label="账户现金" class="grow"><${NumberInput} value=${cash} min=${0} max=${1e9} step=${1000} onChange=${setCash} /><//>
          <${Button} onClick=${saveCash}>保存现金<//>
        </div>
      <//>
    </div>

    ${d.trades.rows.length > 0 && html`<${Card} title="成交记录" extra=${html`<span class="badge">${d.trades.rows.length} 笔</span>`} flush>
      <${FrameTable} frame=${d.trades} maxHeight=${360} />
    <//>`}
    ${closing && html`<${CloseDialog} pos=${closing} book=${book} onDone=${after} onClose=${() => setClosing(null)} />`}`;
}
