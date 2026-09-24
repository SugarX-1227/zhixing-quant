// ECharts 封装与各类图的配置。颜色全部取自 CSS 变量，切换主题时整图重建。

import { html, useEffect, useRef, useShared, fmt } from "./lib.js";

const echarts = window.echarts;

export function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return {
    text: v("--text"), text2: v("--text-2"), text3: v("--text-3"),
    line: v("--line"), lineSoft: v("--line-soft"), surface: v("--surface"),
    up: v("--up"), down: v("--down"), accent: v("--accent"),
    ma: v("--s-ma"), yellow: v("--s-yellow"), white: v("--s-white"),
    font: v("--font"),
  };
}

/** build(t) 返回 ECharts option；deps 变化或主题切换时重画。 */
export function Chart({ build, deps = [], height = 320 }) {
  const ref = useRef(null);
  const inst = useRef(null);
  const [theme] = useShared("theme", document.documentElement.dataset.theme);

  useEffect(() => {
    inst.current = echarts.init(ref.current, null, { renderer: "canvas" });
    const ro = new ResizeObserver(() => inst.current && inst.current.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); inst.current.dispose(); inst.current = null; };
  }, []);

  useEffect(() => {
    if (inst.current) inst.current.setOption(build(tokens()), true);
  }, [theme, ...deps]);

  return html`<div ref=${ref} style=${`height:${height}px;width:100%`}></div>`;
}

// ------------------------------------------------------------------ 共同部分

function base(t) {
  return {
    animation: false,
    textStyle: { fontFamily: t.font, color: t.text2 },
    tooltip: {
      trigger: "axis", backgroundColor: t.surface, borderColor: t.line,
      textStyle: { color: t.text, fontSize: 12 }, extraCssText: "box-shadow:0 8px 24px rgba(0,0,0,.12);border-radius:8px;",
      axisPointer: { type: "cross", lineStyle: { color: t.text3, type: "dashed" },
                     crossStyle: { color: t.text3 }, label: { backgroundColor: t.text2 } },
    },
    legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 3,
              textStyle: { color: t.text2, fontSize: 12 } },
  };
}

const xAxis = (t, data, extra = {}) => ({
  type: "category", data, boundaryGap: true,
  axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false },
  axisLabel: { color: t.text3, fontSize: 11, hideOverlap: true },
  splitLine: { show: false }, ...extra,
});

const yAxis = (t, extra = {}) => ({
  type: "value", scale: true, position: "right",           // A 股软件惯例：价格刻度在右
  axisLine: { show: false }, axisTick: { show: false },
  axisLabel: { color: t.text3, fontSize: 11 },
  splitLine: { lineStyle: { color: t.lineSoft } }, ...extra,
});

const pctAxis = (t, extra = {}) => yAxis(t, { axisLabel: { color: t.text3, fontSize: 11,
  formatter: (v) => +(v * 100).toFixed(2) + "%" }, ...extra });

// ------------------------------------------------------------------ K 线

/**
 * k: {dates, ohlc[[o,c,l,h]], vol, ma20, yellow, white, buys[[date,y]], brick[[p,c,lo,hi]]}
 * 阳线红色空心、阴线绿色实心（A 股惯例；ECharts 默认是绿涨红跌，必须覆盖）。
 */
export function klineOption(k, t) {
  const hasBrick = !!(k.brick && k.brick.some((b) => b[0] != null));
  const grids = hasBrick
    ? [{ top: 28, height: "52%" }, { top: "63%", height: "13%" }, { top: "80%", height: "12%" }]
    : [{ top: 28, height: "64%" }, { top: "76%", height: "16%" }];
  grids.forEach((g) => Object.assign(g, { left: 12, right: 58 }));
  const idx = grids.map((_, i) => i);
  const vols = (k.vol || []).map((v, i) => ({
    value: v, itemStyle: { color: k.ohlc[i][1] >= k.ohlc[i][0] ? t.up : t.down, opacity: .75 },
  }));
  const line = (name, data, color, width = 1.3) => data && {
    name, type: "line", data, showSymbol: false, smooth: false,
    lineStyle: { width, color }, itemStyle: { color }, emphasis: { disabled: true },
  };
  const series = [
    { name: "K线", type: "candlestick", data: k.ohlc, barMaxWidth: 12,
      itemStyle: { color: t.surface, color0: t.down, borderColor: t.up, borderColor0: t.down, borderWidth: 1.1 } },
    line("MA20", k.ma20, t.ma),
    line("知行多空线", k.yellow, t.yellow, 1.8),
    line("白线", k.white, t.white, 1.1),
    k.buys && k.buys.length && { name: "买点", type: "scatter", data: k.buys, symbol: "triangle",
      symbolSize: 11, itemStyle: { color: t.up, borderColor: t.surface, borderWidth: 1.5 }, z: 5 },
    { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols, barMaxWidth: 10 },
    hasBrick && { name: "砖型图", type: "candlestick", xAxisIndex: 2, yAxisIndex: 2, data: k.brick,
      barMaxWidth: 8, itemStyle: { color: t.up, color0: t.down, borderColor: t.up, borderColor0: t.down } },
  ].filter(Boolean);
  const labels = k.dates;
  return {
    ...base(t),
    legend: { ...base(t).legend, data: ["K线", "MA20", "知行多空线", "白线", "买点"].filter((n) => series.some((s) => s.name === n)) },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: { ...base(t).tooltip, formatter: (ps) => klineTip(ps, k, t) },
    grid: grids,
    xAxis: idx.map((i) => xAxis(t, labels, { gridIndex: i, axisLabel: { show: i === idx.length - 1, color: t.text3, fontSize: 11, hideOverlap: true } })),
    yAxis: idx.map((i) => yAxis(t, { gridIndex: i, splitNumber: i ? 2 : 5,
      axisLabel: { color: t.text3, fontSize: 11, formatter: i === 1 ? (v) => fmt.money(v) : undefined } })),
    dataZoom: [{ type: "inside", xAxisIndex: idx, start: 0, end: 100 }],
    series,
  };
}

function klineTip(ps, k, t) {
  const i = ps[0]?.dataIndex;
  if (i == null) return "";
  const [o, c, l, h] = k.ohlc[i];
  const prev = i > 0 ? k.ohlc[i - 1][1] : o;
  const chg = prev ? c / prev - 1 : 0;
  const col = chg >= 0 ? t.up : t.down;
  const row = (a, b, color) => `<div style="display:flex;justify-content:space-between;gap:18px"><span style="color:${t.text3}">${a}</span><b style="color:${color || t.text}">${b}</b></div>`;
  return `<div style="min-width:150px"><div style="margin-bottom:4px;font-weight:600">${k.dates[i]}</div>`
    + row("开", fmt.num(o)) + row("高", fmt.num(h)) + row("低", fmt.num(l))
    + row("收", fmt.num(c), col) + row("涨跌", fmt.pct(chg, 2, true), col)
    + (k.vol ? row("成交量", fmt.money(k.vol[i])) : "")
    + (k.yellow && k.yellow[i] != null ? row("多空线", fmt.num(k.yellow[i]), t.yellow) : "")
    + (k.white && k.white[i] != null ? row("白线", fmt.num(k.white[i])) : "")
    + "</div>";
}

// ------------------------------------------------------------------ 回测

/** 策略 vs 基准：两条都是权益（基准已归一到同一起点），同一个轴。 */
export function equityOption(r, t) {
  const series = [{
    name: "策略", type: "line", data: r.equity.y, showSymbol: false,
    lineStyle: { width: 2, color: t.accent }, itemStyle: { color: t.accent },
    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
      { offset: 0, color: t.accent + "33" }, { offset: 1, color: t.accent + "00" }]) },
  }];
  if (r.benchmark && r.benchmark.y.length) {
    const map = new Map(r.benchmark.x.map((x, i) => [x, r.benchmark.y[i]]));
    series.push({ name: "基准指数", type: "line", data: r.equity.x.map((x) => map.get(x) ?? null),
      showSymbol: false, connectNulls: true,
      lineStyle: { width: 1.3, color: t.text3, type: [4, 3] }, itemStyle: { color: t.text3 } });
  }
  return {
    ...base(t), grid: { left: 8, right: 64, top: 30, bottom: 24 },
    tooltip: { ...base(t).tooltip, valueFormatter: (v) => fmt.money(v) },
    xAxis: xAxis(t, r.equity.x, { boundaryGap: false }),
    yAxis: yAxis(t, { axisLabel: { color: t.text3, fontSize: 11, formatter: (v) => fmt.money(v) } }),
    dataZoom: [{ type: "inside" }],
    series,
  };
}

export function drawdownOption(r, t) {
  return {
    ...base(t), legend: { show: false }, grid: { left: 8, right: 64, top: 10, bottom: 24 },
    tooltip: { ...base(t).tooltip, valueFormatter: (v) => fmt.pct(v, 2) },
    xAxis: xAxis(t, r.drawdown.x, { boundaryGap: false }),
    yAxis: pctAxis(t, { scale: false, max: 0, splitNumber: 3 }),
    dataZoom: [{ type: "inside" }],
    series: [{ name: "回撤", type: "line", data: r.drawdown.y, showSymbol: false,
      lineStyle: { width: 1, color: t.down }, itemStyle: { color: t.down },
      areaStyle: { color: t.down, opacity: .16 } }],
  };
}

/** 横向条形：[[标签, 数值], ...]。 */
export function hbarOption(pairs, t, { color, fmtV = (v) => v } = {}) {
  const rows = [...pairs].reverse();
  return {
    ...base(t), legend: { show: false },
    tooltip: { trigger: "item", backgroundColor: t.surface, borderColor: t.line,
      textStyle: { color: t.text, fontSize: 12 }, formatter: (p) => `${p.name}<br/><b>${fmtV(p.value)}</b>` },
    grid: { left: 8, right: 40, top: 6, bottom: 6, containLabel: true },
    xAxis: { type: "value", show: false },
    yAxis: { type: "category", data: rows.map((r) => r[0]), axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.text2, fontSize: 12, width: 130, overflow: "truncate" } },
    series: [{ type: "bar", data: rows.map((r) => r[1]), barMaxWidth: 14,
      itemStyle: { color: color || t.accent, borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: "right", color: t.text2, fontSize: 11, formatter: (p) => fmtV(p.value) } }],
  };
}

/** 分层收益：正红负绿，同一个轴。 */
export function quantileOption(bars, t) {
  return {
    ...base(t), legend: { show: false }, grid: { left: 8, right: 56, top: 16, bottom: 24 },
    tooltip: { ...base(t).tooltip, axisPointer: { type: "shadow" }, valueFormatter: (v) => fmt.pct(v, 2, true) },
    xAxis: xAxis(t, bars.x.map(String)),
    yAxis: pctAxis(t, { scale: false }),
    series: [{ name: "平均未来收益", type: "bar", barMaxWidth: 34,
      data: bars.y.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? t.up : t.down,
        borderRadius: v >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } })) }],
  };
}

export function lineOption(s, t, name) {
  return {
    ...base(t), legend: { show: false }, grid: { left: 8, right: 56, top: 16, bottom: 24 },
    tooltip: { ...base(t).tooltip, valueFormatter: (v) => fmt.num(v, 3) },
    xAxis: xAxis(t, s.x, { boundaryGap: false }),
    yAxis: yAxis(t),
    dataZoom: [{ type: "inside" }],
    series: [{ name, type: "line", data: s.y, showSymbol: false,
      lineStyle: { width: 2, color: t.accent }, itemStyle: { color: t.accent },
      markLine: { silent: true, symbol: "none", lineStyle: { color: t.text3, type: "dashed" }, data: [{ yAxis: 0 }], label: { show: false } } }],
  };
}

// ------------------------------------------------------------------ 月度收益

/** 权益曲线 → {年: {月: 收益}}，外加年度合计。纯展示，不参与任何计算。 */
export function monthlyReturns(eq) {
  const last = new Map();
  let first = null;
  eq.x.forEach((d, i) => {
    if (eq.y[i] == null) return;
    if (first == null) first = eq.y[i];
    last.set(d.slice(0, 7), eq.y[i]);
  });
  const out = {};
  let prev = first;
  for (const [ym, v] of last) {
    const [y, m] = ym.split("-");
    (out[y] = out[y] || { months: {}, start: prev }).months[+m] = v / prev - 1;
    out[y].end = v;
    prev = v;
  }
  Object.values(out).forEach((r) => { r.year = r.end / r.start - 1; });
  return out;
}

export function heatColor(v, t) {
  if (v == null) return "transparent";
  const a = Math.min(Math.abs(v) / 0.12, 1) * 0.55 + 0.06;
  const hex = v >= 0 ? t.up : t.down;
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a.toFixed(2)})`;
}
