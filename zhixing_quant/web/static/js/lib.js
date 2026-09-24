// 公共工具：Preact/htm 再导出、接口调用、后台任务轮询、跨页面保留状态、格式化。

import { h, render } from "preact";
import { useState, useEffect, useRef, useMemo, useCallback } from "preact/hooks";
import htm from "htm";

export const html = htm.bind(h);
export { render, useState, useEffect, useRef, useMemo, useCallback };

// ------------------------------------------------------------------ 接口

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* 非 JSON */ }
  if (!res.ok) {
    const msg = data && data.detail ? (typeof data.detail === "string" ? data.detail
      : JSON.stringify(data.detail)) : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b) => request("POST", p, b ?? {}),
};

// ------------------------------------------------------------------ 共享状态
// 切换页面时组件会卸载，结果和表单值放在模块级 Map 里，切回来还在。

const STORE = new Map();
const SUBS = new Map();

function emit(key) { (SUBS.get(key) || []).forEach((fn) => fn()); }

export function getShared(key, init) {
  if (!STORE.has(key)) STORE.set(key, typeof init === "function" ? init() : init);
  return STORE.get(key);
}

export function setShared(key, value) {
  const prev = STORE.get(key);
  STORE.set(key, typeof value === "function" ? value(prev) : value);
  emit(key);
}

function useSubscribe(key) {
  const [, tick] = useState(0);
  useEffect(() => {
    const fn = () => tick((n) => n + 1);
    SUBS.set(key, [...(SUBS.get(key) || []), fn]);
    return () => SUBS.set(key, (SUBS.get(key) || []).filter((f) => f !== fn));
  }, [key]);
}

/** 像 useState，但值在页面切换之间保留。 */
export function useShared(key, init) {
  useSubscribe(key);
  return [getShared(key, init), (v) => setShared(key, v)];
}

// ------------------------------------------------------------------ 后台任务

const POLL_MS = 450;

/**
 * 一个「槽位」对应一个任务：同一槽位再次运行会替换旧结果。
 * 轮询在模块级进行，页面切走也不会中断，切回来直接看到进度或结果。
 */
export function useJob(slot) {
  const key = `job:${slot}`;
  useSubscribe(key);
  const job = getShared(key, null);
  const run = async (kind, params) => {
    setShared(key, { status: "queued", progress: 0, text: "提交中", kind });
    try {
      let j = await api.post(`/api/jobs/${kind}`, params);
      setShared(key, j);
      while (j.status === "queued" || j.status === "running") {
        await new Promise((r) => setTimeout(r, POLL_MS));
        j = await api.get(`/api/jobs/${j.id}`);
        setShared(key, j);
      }
      if (j.status === "error") toast(j.error, "err");
      return j;
    } catch (e) {
      const j = { status: "error", error: e.message, kind };
      setShared(key, j);
      toast(e.message, "err");
      return j;
    }
  };
  const busy = !!job && (job.status === "queued" || job.status === "running");
  return { job, run, busy, result: job && job.status === "done" ? job.result : null,
           clear: () => setShared(key, null) };
}

// ------------------------------------------------------------------ 提示

export function toast(text, kind = "ok") {
  const id = Math.random().toString(36).slice(2);
  setShared("toasts", (list = []) => [...list, { id, text, kind }]);
  setTimeout(() => setShared("toasts", (list = []) => list.filter((t) => t.id !== id)),
    kind === "err" ? 7000 : 3500);
}

// ------------------------------------------------------------------ 主题

const THEME_KEY = "zx-theme";

export function themePref() {
  try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (_) { return "auto"; }
}

export function applyTheme(pref) {
  try { localStorage.setItem(THEME_KEY, pref); } catch (_) { /* 隐私模式 */ }
  const dark = pref === "dark" ||
    (pref === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  setShared("theme", document.documentElement.dataset.theme);
}

// ------------------------------------------------------------------ 格式化

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export const fmt = {
  num(v, d = 2) { return isNum(v) ? v.toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }) : "—"; },
  int(v) { return isNum(v) ? Math.round(v).toLocaleString("zh-CN") : "—"; },
  pct(v, d = 2, signed = false) {
    if (!isNum(v)) return "—";
    const s = (v * 100).toFixed(d) + "%";
    return signed && v > 0 ? "+" + s : s;
  },
  money(v) {
    if (!isNum(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(2) + "亿";
    if (a >= 1e4) return (v / 1e4).toFixed(1) + "万";
    return v.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  },
  date(v) {
    const s = String(v ?? "");
    return /^\d{8}$/.test(s) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : (s || "—");
  },
};

export const tone = (v) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "") : "");

export function isoDate(d = new Date()) {
  const z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
}

export function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
}

/** 数据库最新交易日（YYYYMMDD）→ date input 的值；没有就用今天。 */
export function dataDate(status) {
  const s = String(status?.data_date || "");
  return /^\d{8}$/.test(s) ? fmt.date(s) : isoDate();
}
