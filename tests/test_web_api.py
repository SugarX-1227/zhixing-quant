"""Web 接口：在合成行情库上把每个入口真正跑一遍。

界面层以前从来没有测试——Streamlit 页面只能靠手点。现在界面是 JSON 接口 +
静态前端，接口可以直接测；前端没有构建步骤，至少检查模块之间的导入/导出对得上，
拼错一个名字整页就白屏。
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "zhixing_quant" / "web" / "static"


def _bars(seed: int, n: int = 420, start: float = 10.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = start * np.exp(np.cumsum(rng.normal(0.0005, 0.022, n)))
    open_ = close * (1 + rng.normal(0, 0.008, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, n))
    vol = rng.uniform(5e6, 2e7, n)
    return pd.DataFrame({
        "trade_date": [int(d.strftime("%Y%m%d")) for d in dates],
        "open": open_, "high": high, "low": low, "close": close,
        "amount": vol * close, "vol": vol,
    })


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from zhixing_quant.config import load_config
    from zhixing_quant.data import tdx_loader
    from zhixing_quant.data.store import BarStore
    from zhixing_quant.web.server import create_app

    db = tmp_path_factory.mktemp("web") / "market.db"
    store = BarStore(db)
    codes = [f"60{i:04d}" for i in range(24)] + [f"00{i:04d}" for i in range(1, 17)]
    secs = []
    for i, code in enumerate(codes):
        store.upsert_bars(code, _bars(i, start=5 + i))
        secs.append((code, f"合成{i}", "sh" if code.startswith("6") else "sz", "MAIN"))
    for idx in ("sh000300", "sh000001", "sh000905"):
        store.upsert_bars(idx, _bars(99, start=3000))
        secs.append((idx, idx, "sh", "INDEX"))
    store.upsert_securities(secs)
    # 活跃市值：择时模块没有它就拒绝判断多空区间
    ov = _bars(7, start=60000)
    store.upsert_oamv([tuple(r) for r in ov[["trade_date", "open", "high", "low", "close",
                                             "vol", "amount"]].itertuples(index=False)])
    store.close()

    cfg = copy.deepcopy(load_config())
    cfg["data"]["db_path"] = str(db)
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c
    tdx_loader.set_config(load_config())


def _strict(resp):
    """Starlette 用 allow_nan=False；这里再确认一遍返回体是合法 JSON。"""
    assert resp.status_code == 200, resp.text
    return json.loads(resp.text, parse_constant=lambda c: pytest.fail(f"返回体含 {c}"))


def _run(client, kind, params, timeout=180):
    j = client.post(f"/api/jobs/{kind}", json=params).json()
    t0 = time.time()
    while j["status"] in ("queued", "running"):
        assert time.time() - t0 < timeout, f"{kind} 超时"
        time.sleep(0.2)
        j = _strict(client.get(f"/api/jobs/{j['id']}"))
    assert j["status"] == "done", j["error"]
    return j


# ---------------------------------------------------------------------------


def test_index_and_static_assets_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "importmap" in r.text
    for src in re.findall(r'"(/static/[^"]+)"', r.text):
        assert client.get(src).status_code == 200, src
    assert client.get("/static/js/main.js").headers["cache-control"] == "no-cache"


def test_meta_lists_everything_the_pages_need(client):
    m = _strict(client.get("/api/meta"))
    assert m["config_error"] == ""
    assert set(m["backtestable"]) <= set(m["strategies"])
    assert m["timing_only"] == "timing"
    assert m["params"]["execution"] and m["factors"] and m["calib_params"]
    p = m["params"]["strategy"]["b2"][0]
    assert {"key", "kind", "value", "lo", "hi"} <= set(p)


def test_exit_params_carry_choice_labels(client):
    ps = _strict(client.get("/api/exit-params/b2"))
    stop = next(p for p in ps if p["key"] == "exits.b2.stop.kind")
    assert stop["choice_labels"]["entry_low"] == "入场K线最低价"


def test_health_and_status(client):
    h = _strict(client.get("/api/health"))
    assert h["ok"] and h["codes"] >= 40
    s = _strict(client.get("/api/status?book=swing"))
    assert s["data_ok"] and s["data_date"]


def test_stock_kline_is_aligned_and_counts_only_boolean_signals(client):
    d = _strict(client.get("/api/stock/600003?days=120"))
    k = d["kline"]
    n = len(k["dates"])
    assert n == 120
    assert len(k["ohlc"]) == len(k["vol"]) == len(k["ma20"]) == n
    assert k["ma20"][0] is not None, "MA20 应在全长序列上算完再截尾"
    names = [s for s, _ in d["signals"]]
    # sig_*_stop 是价格、sig_distribution_type 是字符串，不是「触发次数」
    assert not [s for s in names if s.endswith("_stop") or s.endswith("_type")]
    assert all(v <= d["bars"] for _, v in d["signals"])


def test_stock_missing_returns_404(client):
    assert client.get("/api/stock/688999").status_code == 404


def test_scan_job_and_chart_endpoint(client):
    j = _run(client, "scan", {"strategy": "b2", "date": "20250801", "limit": 40,
                               "overrides": {"b2.j_threshold": 60, "not.declared": 1}})
    r = j["result"]
    assert r["strategy"] == "b2" and r["scanned"] == 40
    assert r["changed"] == {"b2.j_threshold": 60.0}, "未声明的键必须被丢掉"
    if r["rows"]:
        code = r["rows"][0]["code"]
        k = _strict(client.get(f"/api/jobs/{j['id']}/chart/{code}"))
        assert k["dates"] and len(k["ohlc"]) == len(k["dates"])
    assert client.get(f"/api/jobs/{j['id']}/chart/NOPE").status_code == 404


def test_today_job(client):
    j = _run(client, "today", {"strategy": "b2", "date": "2025-08-01", "limit": 40,
                                "book": "swing"})
    r = j["result"]
    assert r["regime"] in ("BULL", "NEUTRAL", "BEAR")
    assert isinstance(r["candidates"]["rows"], list) and isinstance(r["plans"], list)


def test_backtest_then_ablation(client, monkeypatch):
    j = _run(client, "backtest", {
        "strategy": "b2", "start": "2025-02-03", "end": "2025-08-01",
        "universe": {"boards": ["MAIN"], "size": 10, "min_amount": 0, "min_bars": 60},
        "overrides": {"backtest.max_positions": 3.0}, "core_code": "",
    })
    r = j["result"]
    assert r["strategy"] == "b2"
    assert len(r["equity"]["x"]) == len(r["equity"]["y"]) > 60
    assert len(r["drawdown"]["y"]) == len(r["equity"]["y"])
    assert all(v <= 1e-12 for v in r["drawdown"]["y"] if v is not None)
    assert r["changed"] == {"backtest.max_positions": 3}
    assert isinstance(r["changed"]["backtest.max_positions"], int), "int 参数要转回 int"
    assert {"total_return", "max_drawdown", "sharpe"} <= set(r["metrics"])

    # 消融本身（N+1 次回测）在 test_ablation_and_weights.py 里测；这里只验接线：
    # 必须拿「这次回测实际生效的配置」和同一段区间、同一个池子去跑
    from zhixing_quant.backtest import ablation as AB
    seen = {}

    def fake_ablate(cfg, strategy, start, end, spec=None, universe_as_of=None,
                    only_one=False, progress=None):
        seen.update(cfg=cfg, strategy=strategy, start=start, end=end, spec=spec,
                    only_one=only_one)
        cols = ["规则", "总收益", "最大回撤", "夏普", "胜率", "笔数",
                "Δ总收益", "Δ最大回撤", "Δ笔数", "判定"]
        return pd.DataFrame([["基线（全部启用）", 0.1, 0.05, 1.0, 0.5, 10,
                              0.0, 0.0, 0, "—"]], columns=cols)

    monkeypatch.setattr(AB, "ablate_exits", fake_ablate)
    a = _run(client, "ablation", {"source": j["id"], "only_one": True})
    assert a["result"]["table"]["columns"][0] == "规则"
    assert seen["strategy"] == "b2" and (seen["start"], seen["end"]) == ("20250203", "20250801")
    assert seen["only_one"] is True and seen["spec"].size == 10
    assert seen["cfg"]["backtest"]["max_positions"] == 3, "消融丢了界面上改过的参数"


def test_ablation_needs_a_finished_backtest(client):
    assert client.post("/api/jobs/ablation", json={"source": "nope"}).status_code == 404


def test_unknown_job_kind_is_404(client):
    assert client.post("/api/jobs/rm_rf", json={}).status_code == 404


def test_positions_round_trip(client):
    assert _strict(client.post("/api/cash", json={"book": "scalp", "cash": 100000}))["ok"]
    r = client.post("/api/positions/open", json={"book": "scalp", "code": "600001",
                                                 "shares": 100, "price": 10, "stop_loss": 0})
    assert r.status_code == 400 and "止损" in r.json()["detail"]
    assert _strict(client.post("/api/positions/open", json={
        "book": "scalp", "code": "600001", "shares": 100, "price": 10, "stop_loss": 9.5}))["ok"]
    p = _strict(client.get("/api/positions?book=scalp"))
    assert [x["code"] for x in p["positions"]] == ["600001"]
    assert p["positions"][0]["last_price"] > 0
    closed = _strict(client.post("/api/positions/close", json={
        "book": "scalp", "code": "600001", "price": 11, "reason": "止盈"}))
    assert closed["pnl"] > 0
    p = _strict(client.get("/api/positions?book=scalp"))
    assert not p["positions"] and len(p["trades"]["rows"]) == 1


# ---------------------------------------------------------------------------
# 前端静态检查：没有构建步骤，拼错的导入名只有在浏览器里才会白屏


def _exports(src: str) -> set:
    names = set(re.findall(r"export\s+(?:const|function|let|class)\s+(\w+)", src))
    for block in re.findall(r"export\s*\{([^}]*)\}", src):
        names |= {b.split(" as ")[-1].strip() for b in block.split(",") if b.strip()}
    if re.search(r"export\s+default", src):
        names.add("default")
    return names


def test_frontend_imports_resolve():
    files = list((STATIC / "js").rglob("*.js"))
    assert files
    for f in files:
        src = f.read_text(encoding="utf-8")
        for names, path in re.findall(r'import\s*\{([^}]*)\}\s*from\s*"(\.[^"]+)"', src):
            target = (f.parent / path).resolve()
            assert target.exists(), f"{f.name} 导入了不存在的 {path}"
            have = _exports(target.read_text(encoding="utf-8"))
            for n in names.split(","):
                n = n.split(" as ")[0].strip()
                if n:
                    assert n in have, f"{f.name} 从 {path} 导入了不存在的 {n}"
        for path in re.findall(r'import\s+\w+\s+from\s*"(\.[^"]+)"', src):
            target = (f.parent / path).resolve()
            assert target.exists() and "default" in _exports(target.read_text(encoding="utf-8")), \
                f"{f.name} 默认导入 {path} 失败"


def test_coerce_rejects_bad_choice():
    from zhixing_quant.ui import param_schema as PS
    from zhixing_quant.web.tasks import coerce

    ps = PS.exit_params_for("b2", {})
    with pytest.raises(ValueError):
        coerce({"exits.b2.stop.kind": "magic"}, ps)
