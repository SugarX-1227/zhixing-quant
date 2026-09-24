// 应用外壳：侧栏导航、顶栏（账户 / 数据状态 / 主题）、哈希路由。

import { html, render, useState, useEffect, api, fmt, applyTheme, themePref, useShared } from "./lib.js";
import { Icon, Segmented, Toasts, Callout, Rich } from "./ui.js";
import Today from "./pages/today.js";
import Positions from "./pages/positions.js";
import Strategies from "./pages/strategies.js";
import Backtest from "./pages/backtest.js";
import Calibrate from "./pages/calibrate.js";
import Factors from "./pages/factors.js";
import Stock from "./pages/stock.js";
import Data from "./pages/data.js";

// 页面按每日决策循环组织：先交易，再研究，最后是数据
const NAV = [
  { group: "交易" },
  { key: "today", label: "今日", icon: "today", page: Today, title: "今日决策", sub: "择时 → 防守 → 进攻 → 下单" },
  { key: "positions", label: "持仓", icon: "wallet", page: Positions, title: "持仓", sub: "账户与成交记录" },
  { key: "strategies", label: "战法", icon: "radar", page: Strategies, title: "战法扫描", sub: "四套战法独立扫描" },
  { group: "研究" },
  { key: "backtest", label: "回测", icon: "chart", page: Backtest, title: "回测", sub: "T 日收盘出信号，T+1 开盘成交" },
  { key: "calibrate", label: "校准", icon: "sliders", page: Calibrate, title: "参数校准", sub: "样本内选、样本外验" },
  { key: "factors", label: "因子", icon: "layers", page: Factors, title: "因子检验", sub: "战法决定能不能买，因子决定先买哪只" },
  { group: "行情" },
  { key: "stock", label: "个股", icon: "candle", page: Stock, title: "个股", sub: "K 线与全部指标" },
  { key: "data", label: "数据", icon: "db", page: Data, title: "数据", sub: "本地通达信行情库" },
];
const PAGES = NAV.filter((n) => n.key);

const route = () => {
  const k = location.hash.replace(/^#\/?/, "");
  return PAGES.some((p) => p.key === k) ? k : "today";
};

function Sidebar({ current, status, onNav }) {
  const s = status || {};
  const pos = Math.max(0, Math.min(1, s.position_pct || 0));
  return html`<aside class="side">
    <div class="brand"><div class="brand-mark">知</div>
      <div><b>知行量化</b><span>A 股盘后选股 · 回测</span></div></div>
    <nav>
      ${NAV.map((n) => n.group ? html`<div class="nav-group">${n.group}</div>` : html`
        <a href=${"#/" + n.key} class=${"nav-item" + (n.key === current ? " active" : "")}
           onClick=${onNav} aria-current=${n.key === current ? "page" : undefined}>
          <${Icon} name=${n.icon} size=${17} />${n.label}</a>`)}
    </nav>
    <div class="side-foot">
      <div class="acct">
        ${s.data_ok ? html`
          <div class="acct-row"><span>账户权益</span><span>${fmt.money(s.equity)}</span></div>
          <div class="acct-row"><span>可用现金</span><span>${fmt.money(s.cash)}</span></div>
          <div class="acct-bar" title="仓位"><i style=${`width:${pos * 100}%`}></i></div>
          <div class="acct-row"><span>仓位</span><span>${fmt.pct(pos, 1)}</span></div>`
        : html`<div class="acct-row"><span class="up">数据未就绪</span></div>`}
      </div>
    </div>
  </aside>`;
}

function DataPill({ status }) {
  if (!status) return null;
  const ok = status.data_ok && !status.data_warn;
  const color = !status.data_ok ? "up" : status.data_warn ? "warn" : "down";
  return html`<a href="#/data" class=${"badge outline " + color} style="text-decoration:none"
     title=${ok ? "数据正常" : "数据需要同步，点击查看"}>
    <span class="dotmark"></span>${status.data_ok ? "数据至 " + fmt.date(status.data_date) : "数据未就绪"}</a>`;
}

function ThemeSwitch() {
  const [pref, setPref] = useState(themePref());
  return html`<${Segmented} value=${pref} onChange=${(v) => { setPref(v); applyTheme(v); }}
    options=${[{ value: "light", icon: "sun", label: "", title: "浅色" },
               { value: "dark", icon: "moon", label: "", title: "深色" },
               { value: "auto", icon: "auto", label: "", title: "跟随系统" }]} />`;
}

function App() {
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState("");
  const [current, setCurrent] = useState(route());
  const [book, setBook] = useShared("book", "swing");
  const [status, setStatus] = useShared("status", null);
  const [navOpen, setNavOpen] = useState(false);

  const refreshStatus = () => api.get(`/api/status?book=${book}`).then(setStatus)
    .catch((e) => setStatus({ data_ok: false, data_message: e.message }));

  useEffect(() => { api.get("/api/meta").then(setMeta).catch((e) => setErr(e.message)); }, []);
  useEffect(() => {
    const fn = () => { setCurrent(route()); setNavOpen(false); window.scrollTo(0, 0); };
    addEventListener("hashchange", fn);
    return () => removeEventListener("hashchange", fn);
  }, []);
  useEffect(() => { refreshStatus(); }, [book]);

  if (err) return html`<div class="boot"><${Callout} tone="error">服务连接失败：${err}<//></div>`;
  if (!meta || !status) return html`<div class="boot"><span class="spin"></span></div>`;
  if (meta.config_error) {
    // 配置校验没过就别往下走了——带着静默失效的配置跑出来的结果比报错更糟
    return html`<div class="boot"><div style="max-width:720px"><${Callout} tone="error">
      <b>配置加载失败</b><pre class="code-block" style="margin-top:8px">${meta.config_error}</pre>
      修好 <code>config/settings.yaml</code> 后重启服务。<//></div></div>`;
  }

  const page = PAGES.find((p) => p.key === current);
  const Page = page.page;
  return html`<div class=${"shell" + (navOpen ? " nav-open" : "")}
                   onClick=${(e) => navOpen && e.target === e.currentTarget && setNavOpen(false)}>
    <${Sidebar} current=${current} status=${status} onNav=${() => setNavOpen(false)} />
    <main class="main">
      <header class="top">
        <button class="btn ghost sm menu-btn" onClick=${() => setNavOpen(true)} aria-label="菜单"><${Icon} name="menu" /></button>
        <div class="top-title"><h1>${page.title}</h1><span class="sub">${page.sub}</span></div>
        <div class="top-right">
          <${Segmented} value=${book} onChange=${setBook}
            options=${Object.entries(meta.books).map(([value, label]) => ({ value, label }))} />
          <${DataPill} status=${status} />
          <${ThemeSwitch} />
        </div>
      </header>
      <div class="content">
        ${meta.config_warnings.map((w) => html`<${Callout} tone="warn"><${Rich} text=${w} /><//>`)}
        <${Page} key=${current} meta=${meta} book=${book} status=${status} refreshStatus=${refreshStatus} />
      </div>
    </main>
    <${Toasts} />
  </div>`;
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (themePref() === "auto") applyTheme("auto");
});
applyTheme(themePref());
render(html`<${App} />`, document.getElementById("app"));
