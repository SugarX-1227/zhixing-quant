"""知行量化 Web 服务：JSON 接口 + 静态单页应用。

    python -m zhixing_quant.web            # http://127.0.0.1:8501

接口分两类：
- 秒级的直接返回（行情体检、持仓、个股K线）；
- 扫描 / 回测 / 校准这类慢活提交成后台任务，前端轮询 /api/jobs/{id}。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from zhixing_quant.ui import param_schema as PS
from zhixing_quant.web import tasks, views
from zhixing_quant.web.jobs import JobManager
from zhixing_quant.web.labels import BOOKS, CORE_INDEXES
from zhixing_quant.web.serialize import frame, jsonable

STATIC = Path(__file__).parent / "static"


def create_app(cfg: Optional[dict] = None) -> FastAPI:
    """cfg 为空时读 config/settings.yaml。测试里传入指向临时库的配置。"""
    from zhixing_quant.config import config_warnings, load_config
    from zhixing_quant.data import tdx_loader

    config_error = ""
    if cfg is None:
        try:
            cfg = load_config()
        except Exception as exc:
            # 配置校验没过就别往下走了——带着静默失效的配置跑出来的结果
            # 比报错更糟，因为你会当真。界面会整页显示这个错误。
            config_error, cfg = str(exc), {}
    if cfg:
        tdx_loader.set_config(cfg)

    app = FastAPI(title="知行量化", docs_url="/api/docs", redoc_url=None)
    jobs = JobManager()
    health_lock = threading.Lock()
    health_cache: Dict[str, Any] = {}

    def need_cfg() -> dict:
        if config_error:
            raise HTTPException(503, f"配置加载失败：{config_error}")
        return cfg

    def store():
        from zhixing_quant.data.sync import db_path
        from zhixing_quant.portfolio.store import PortfolioStore
        return PortfolioStore(db_path(need_cfg()))

    def spot_prices() -> dict:
        try:
            from zhixing_quant.data.tdx_loader import fetch_a_spot
            spot = fetch_a_spot()
            return dict(zip(spot["code"], spot["close"]))
        except Exception:
            return {}

    def health(fresh: bool = False) -> dict:
        with health_lock:
            if fresh or "v" not in health_cache:
                health_cache["v"] = jsonable(tdx_loader.data_health(need_cfg()))
            return health_cache["v"]

    def job_or_404(job_id: str, kind: Optional[str] = None):
        job = jobs.get(job_id)
        if job is None or (kind and job.kind != kind):
            raise HTTPException(404, "任务不存在或已过期，请重新运行")
        if job.status != "done":
            raise HTTPException(409, "任务还没跑完")
        return job

    # ---- 元信息 ------------------------------------------------------------

    @app.get("/api/meta")
    def meta():
        if config_error:
            return {"config_error": config_error}
        import zhixing_quant.factors as FA
        from zhixing_quant.backtest.calibrate import OBJECTIVES
        from zhixing_quant.backtest.runner import BACKTESTABLE, TIMING_ONLY
        from zhixing_quant.data.universe import BOARDS
        from zhixing_quant.factors.library import PRESETS
        from zhixing_quant.scanner.strategy_scan import available

        strategies = {k: {"label": v.get("label", k), "book": v.get("book", "swing"),
                          "spec": v.get("spec", "")} for k, v in available().items()}
        return jsonable({
            "config_error": "",
            "config_warnings": config_warnings(),
            "books": BOOKS,
            "strategies": strategies,
            "backtestable": [k for k in BACKTESTABLE if k in strategies],
            "timing_only": TIMING_ONLY,
            "boards": BOARDS,
            "core_indexes": CORE_INDEXES,
            "core_default": str(PS.get_value(cfg, "backtest.core.code", "") or ""),
            "params": {
                "strategy": {k: [views.param(p, cfg) for p in PS.params_for(k)]
                             for k in strategies},
                "execution": [views.param(p, cfg) for p in PS.EXECUTION_PARAMS],
                "cost": [views.param(p, cfg) for p in PS.COST_PARAMS],
                "risk": [views.param(p, cfg) for p in PS.RISK_PARAMS],
            },
            "objectives": OBJECTIVES,
            "calib_params": {k: {"label": v[0], "options": v[1]}
                             for k, v in tasks.calib_params().items()},
            "kind_labels": {str(k): PS.kind_label(k) for k in
                            set(PS.STOP_KINDS + PS.TRAIL_KINDS + PS.TP_KINDS
                                + ("white_line",))},
            "factors": [{"name": f.name, "label": f.label, "category": f.category,
                         "direction": f.direction, "help": f.help}
                        for f in FA.list_factors()],
            "presets": PRESETS,
            "factor_defaults": {
                "horizon": PS.get_value(cfg, "factors.evaluate.horizon", 5),
                "quantiles": PS.get_value(cfg, "factors.evaluate.quantiles", 5),
            },
        })

    @app.get("/api/exit-params/{strategy}")
    def exit_params(strategy: str):
        return [views.param(p, need_cfg()) for p in PS.exit_params_for(strategy, need_cfg())]

    # ---- 行情与账户 ----------------------------------------------------------

    @app.get("/api/health")
    def get_health(fresh: bool = False):
        return health(fresh)

    @app.get("/api/status")
    def status(book: str = "swing"):
        """侧栏的账户快照。"""
        h = health()
        out = {"data_ok": bool(h.get("ok")), "data_date": h.get("date_max"),
               "data_warn": bool(h.get("warnings")), "data_message": h.get("message", "")}
        if not h.get("ok"):
            return out
        s = store()
        try:
            positions = s.positions(book)
            prices = spot_prices() if positions else {}
            eq = s.equity(prices, book)
            held = sum(prices.get(p["code"], p["entry_price"]) * p["shares"]
                       for p in positions)
            out.update(equity=eq, cash=s.cash(book), n_positions=len(positions),
                       position_pct=held / eq if eq else 0.0)
        finally:
            s.close()
        return jsonable(out)

    @app.get("/api/positions")
    def positions(book: str = "swing"):
        s = store()
        try:
            pos = s.positions(book)
            prices = spot_prices()
            rows = []
            for p in pos:
                px = float(prices.get(p["code"], p["entry_price"]))
                rows.append({**p, "last_price": px,
                             "return_pct": px / p["entry_price"] - 1,
                             "market_value": px * p["shares"],
                             "pnl": (px - p["entry_price"]) * p["shares"]})
            trades = s.trades(book)
            if not trades.empty:
                trades = trades.drop(columns=[c for c in ("id", "book") if c in trades])
            return jsonable({"cash": s.cash(book), "equity": s.equity(prices, book),
                             "stats": s.stats(book), "positions": rows,
                             "trades": frame(trades)})
        finally:
            s.close()

    @app.post("/api/positions/open")
    def open_position(p: Dict[str, Any] = Body(...)):
        s = store()
        try:
            code = str(p.get("code", "")).strip().zfill(6)
            s.open_position(code, int(p["shares"]), float(p["price"]),
                            float(p["stop_loss"]), book=p.get("book", "swing"),
                            name=p.get("name", ""), strategy=p.get("strategy", ""),
                            take_profit=p.get("take_profit"),
                            entry_date=p.get("entry_date"))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        finally:
            s.close()
        return {"ok": True, "code": code}

    @app.post("/api/positions/close")
    def close_position(p: Dict[str, Any] = Body(...)):
        s = store()
        try:
            r = s.close_position(str(p["code"]), float(p["price"]),
                                 book=p.get("book", "swing"), reason=p.get("reason", "手工"))
        finally:
            s.close()
        if not r:
            raise HTTPException(404, "没找到这笔持仓")
        return jsonable(r)

    @app.post("/api/cash")
    def set_cash(p: Dict[str, Any] = Body(...)):
        s = store()
        try:
            s.set_cash(float(p["cash"]), book=p.get("book", "swing"))
        finally:
            s.close()
        return {"ok": True}

    # ---- 个股 ----------------------------------------------------------------

    @app.get("/api/stock/{code}")
    def stock(code: str, days: int = 160):
        from zhixing_quant.data.tdx_loader import load_daily
        from zhixing_quant.indicators.pipeline import defense_coverage, run_pipeline

        code = code.strip()
        code = code if code[:2] in ("sh", "sz", "bj") else code.zfill(6)
        df = load_daily(code, adjust="qfq")
        if df.empty:
            raise HTTPException(404, "库里没有这只股票。确认代码，或到「数据」页同步。")
        r = run_pipeline(df, need_cfg(), "full")
        # 只数布尔列：sig_*_stop 是止损价、sig_distribution_type 是形态名，
        # 转 bool 后每根K线都是 True，会被误报成「每天都触发」
        hits = {c: int(r.df[c].fillna(False).astype(bool).sum()) for c in r.df.columns
                if c.startswith("sig_") and views.is_flag(r.df[c])}
        last = r.df.iloc[-1]
        prev_close = r.df["close"].iloc[-2] if len(r.df) > 1 else last["close"]
        return jsonable({
            "code": code, "adjust": df.attrs.get("adjust"),
            "warnings": r.warnings(),
            "coverage": defense_coverage(r.df),
            "signals": sorted([[k, v] for k, v in hits.items() if v], key=lambda x: -x[1]),
            "last": {"date": r.df.index[-1], "close": last["close"],
                     "chg": last["close"] / prev_close - 1 if prev_close else None,
                     "amount": last.get("amount"),
                     "yellow": last.get("yellow_line"), "white": last.get("white_line")},
            "bars": len(r.df),
            "kline": views.kline(r.df, days),
        })

    # ---- 后台任务 ------------------------------------------------------------

    @app.post("/api/jobs/{kind}")
    def submit(kind: str, p: Dict[str, Any] = Body(default={})):
        c = need_cfg()
        if kind == "ablation":
            src = job_or_404(str(p.get("source", "")), "backtest")
            job = jobs.submit(kind, lambda j: tasks.ablation(j, c, p, src))
        elif kind in tasks.RUNNERS:
            fn = tasks.RUNNERS[kind]
            job = jobs.submit(kind, lambda j: fn(j, c, p))
            if kind == "sync":
                health_cache.clear()
        else:
            raise HTTPException(404, f"未知任务类型 {kind}")
        return job.public()

    @app.get("/api/jobs/{job_id}")
    def poll(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在或已过期，请重新运行")
        if job.kind == "sync" and job.status in ("done", "error"):
            health_cache.clear()
        return job.public()

    @app.get("/api/jobs/{job_id}/chart/{code}")
    def job_chart(job_id: str, code: str, days: int = 0):
        job = job_or_404(job_id)
        charts = job.private.get("charts") or {}
        if code not in charts:
            raise HTTPException(404, "这只标的没有图表数据")
        return views.kline(charts[code], days or None)

    @app.post("/api/jobs/{job_id}/weights")
    def weights(job_id: str, p: Dict[str, Any] = Body(default={})):
        return tasks.factor_weights(job_or_404(job_id, "factors"), p)

    # ---- 页面 ----------------------------------------------------------------

    @app.exception_handler(Exception)
    async def unhandled(_, exc: Exception):
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        # 升级后浏览器不能拿旧的 JS 配新的接口：静态文件每次都带 ETag 回源校验
        resp = await call_next(request)
        if request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app

