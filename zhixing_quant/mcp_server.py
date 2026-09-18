"""只读行情 MCP 服务：把本地 market.db 暴露给另一台机器上的 AI 客户端。

为什么需要
----------
本项目从 GitHub 拉代码就能跑测试，但行情数据（data/market.db，1600 万根
K 线）在 .gitignore 里，另一台机器读不到。这个模块在有数据的那台机器上
起一个 MCP 服务，AI 客户端就能直接查 K 线 / 除权 / 活跃市值，而不需要
把 2GB 的库文件传来传去。

安全模型（只读 + 可审计）
------------------------
三层只读，任何一层被绕过还有下面两层：

1. 连接层：SQLite 以 `file:...?mode=ro` 打开，物理只读。INSERT/UPDATE/
   DELETE/DDL 在这一层直接报错，和本模块的 SQL 检查无关。
2. 语句层：`sql` 工具只接受**单条** SELECT / WITH，拒绝一切写动词
   （含 PRAGMA / ATTACH），先剥离字符串字面量再扫关键字，防 `'...'` 绕过。
3. 表层：只允许查询白名单里的行情表。position / trade_log / account /
   watchlist / blacklist 是个人交易数据，**完全不出现在服务里**——
   `list_tables` 看不到，`sql` 查询会被拒绝。

审计：每次工具调用追加一行 JSONL 到 `data/mcp_audit.jsonl`
（时间 / 工具 / 参数 / 返回行数 / 耗时 / 错误）。查看：

    tail -f data/mcp_audit.jsonl

用法
----
    # stdio（本机，或 `ssh 主机 python -m zhixing_quant.mcp_server`，
    # 加密与鉴权由 SSH 承担）
    python -m zhixing_quant.mcp_server

    # HTTP（局域网 / Tailscale。必须设 MCP_TOKEN，否则拒绝启动：
    # 明文 token 走公网等于不设防）
    set MCP_TOKEN=一串随机字符
    python -m zhixing_quant.mcp_server --http --host 0.0.0.0 --port 8765

注意：stdio 模式下本模块绝不能往 stdout 打印任何东西（会破坏 MCP 协议），
日志一律走 stderr。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Iterator, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

log = logging.getLogger("zhixing_quant.mcp_server")

# ---------------------------------------------------------------------------
# 只读策略
# ---------------------------------------------------------------------------

# 允许客户端查询的表。个人交易数据（position / trade_log / account /
# watchlist / blacklist）和内部同步状态（sync_state）不在其中。
WHITELIST = ("daily_bar", "oamv_daily", "xdxr", "security", "security_name_hist")

# 明确拒绝的表，报错信息里说出来，省得对面以为是漏配。
BLOCKED = ("position", "trade_log", "account", "watchlist", "blacklist")

MAX_ROWS = 5000            # 任何一次调用最多返回的行数
QUERY_BUDGET_SECONDS = 10  # 单条 SQL 的执行时间预算，超时中断（防全表扫拖死机器）

_WRITE_VERBS = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|"
    r"pragma|vacuum|reindex|analyse|analyze)\b", re.IGNORECASE)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _load_cfg() -> dict:
    import yaml
    path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _db_path() -> Path:
    """和项目其他模块同一入口取库路径，别在这里再写一份。"""
    from zhixing_quant.data.sync import db_path
    return Path(db_path(_load_cfg()))


_AUDIT_PATH = _db_path().parent / "mcp_audit.jsonl"


def _audit(tool: str, args: dict, rows: Optional[int],
           ms: float, error: Optional[str]) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "tool": tool, "args": args, "rows": rows,
           "ms": round(ms), "error": error}
    try:
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:      # 审计失败不能静默，但也不能崩掉服务
        log.error("审计日志写入失败: %s", exc)


def _audited(fn):
    """工具统一包装：计时 + 审计 + 异常转成给客户端看的错误信息。"""
    @wraps(fn)
    def wrapper(**kwargs):
        t0 = time.perf_counter()
        rows, error = None, None
        try:
            out = fn(**kwargs)
            rows = out.get("rows") if isinstance(out, dict) else out
            if isinstance(rows, list):
                rows = len(rows)
            return out
        except (ValueError, sqlite3.Error) as exc:
            error = str(exc)
            return {"error": error}
        finally:
            _audit(fn.__name__, kwargs, rows,
                   (time.perf_counter() - t0) * 1000, error)
    return wrapper


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """每次调用开一条新的只读连接。

    SQLite 只读连接开销可忽略；每请求新连接避免了「上一条查询中断后
    连接状态残留」这类边角问题。busy_timeout 保证和每日增量 sync 的
    短事务并发时不报 SQLITE_BUSY。
    """
    con = sqlite3.connect(f"file:{_db_path().as_posix()}?mode=ro",
                          uri=True, timeout=5)
    deadline = time.time() + QUERY_BUDGET_SECONDS
    ticks = [0]

    def _watchdog() -> int:
        ticks[0] += 1
        if ticks[0] % 5000 == 0 and time.time() > deadline:
            return 1            # 非零 → 中断查询，sqlite3 抛 OperationalError
        return 0

    con.set_progress_handler(_watchdog, 1000)
    try:
        yield con
    finally:
        con.close()


def _check_sql(query: str) -> str:
    """语句层 + 表层校验，返回规范化后的 SQL。"""
    stripped = _STRING_LITERAL.sub("''", query).strip()
    body = stripped.rstrip(";")
    if ";" in body:
        raise ValueError("只接受单条语句（检测到分号）")
    if not body.lower().startswith(("select", "with")):
        raise ValueError("只接受 SELECT / WITH 查询")
    if _WRITE_VERBS.search(body):
        raise ValueError("查询里出现写动词，已拒绝")
    tables = {t.lower() for t in _TABLE_REF.findall(body)}
    blocked = tables & set(BLOCKED)
    if blocked:
        raise ValueError(f"个人交易数据不允许查询：{sorted(blocked)}")
    unknown = tables - set(WHITELIST)
    if unknown:
        raise ValueError(f"表不在白名单 {WHITELIST} 里：{sorted(unknown)}")
    # 执行原文（只去结尾分号）——上面那个 body 是剥掉字符串字面量的
    # 检查副本，拿来执行会让 where code = '600519' 变成 where code = ''。
    return query.strip().rstrip(";")


def _rows_to_dicts(cursor: sqlite3.Cursor, cap: int = MAX_ROWS) -> tuple:
    out = []
    for row in cursor.fetchmany(cap + 1):
        out.append({d[0]: v for d, v in zip(cursor.description, row)})
    truncated = len(out) > cap
    return out[:cap], truncated


def _norm_date(s: str, field: str) -> Optional[int]:
    """YYYYMMDD 或 YYYY-MM-DD → int；空串 → None。"""
    s = (s or "").replace("-", "").strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError(f"{field} 需要 YYYYMMDD 格式，收到：{s!r}")
    return int(s)


def _norm_code(code: str) -> str:
    """代码口径：个股 6 位数字；指数必须带 sh/sz 前缀。

    库里 '000001' 是平安银行、'sh000001' 才是上证指数——这是本项目
    踩过文档化的坑（见 AGENTS.md），所以这里对歧义代码不做任何猜测。
    """
    code = code.strip().lower()
    if re.fullmatch(r"\d{6}", code) or re.fullmatch(r"s[hz]\d{6}", code):
        return code
    raise ValueError("代码格式应为 6 位数字（个股）或 sh/sz + 6 位（指数，"
                     f"如 sh000001 上证指数）；收到：{code!r}。"
                     "不确定时先用 search_security 查。")


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------

def _local_hosts() -> list:
    """本机所有网卡地址 + localhost，作为 MCP 的 Host 头白名单。

    SDK 的 DNS 重绑定防护默认只认 localhost，用局域网 IP 直连会被 421
    （Misdirected Request）拒掉。启动时动态枚举网卡地址，路由器换网段 /
    DHCP 变 IP 都不用改配置。防住的攻击面不变：网页无法借域名重绑定
    假冒 Host 打进来，且鉴权层还压着 Bearer Token。
    """
    import socket
    hosts = ["localhost", "127.0.0.1"]
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        hosts += sorted({i[4][0] for i in infos})
    except OSError:
        pass
    return [f"{h}:*" for h in hosts] + hosts   # 带/不带端口的 Host 都放行


server = FastMCP(
    "zhixing-quant-readonly",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_local_hosts(),
        # 放行 claude.ai 网页版 custom connector 的浏览器端 Origin
        #（仅 Origin 检查；没有 token 照样过不了鉴权层）。
        allowed_origins=["https://claude.ai"],
    ),
)


@server.tool()
@_audited
def db_info() -> dict:
    """数据库概况：K 线日期范围、标的数量、最近一次同步时间。先调这个了解数据新鲜度。"""
    with _connect() as con:
        lo, hi, n_codes = con.execute(
            "select min(trade_date), max(trade_date), count(distinct code) "
            "from daily_bar").fetchone()
        oamv_hi = con.execute("select max(trade_date) from oamv_daily").fetchone()[0]
        last_sync = con.execute("select value from meta where key='last_sync'").fetchone()
    return {
        "daily_bar_range": [lo, hi],
        "daily_bar_codes": n_codes,
        "oamv_latest": oamv_hi,
        "last_sync": last_sync[0] if last_sync else None,
        "note": "个股代码 6 位数字，指数带 sh/sz 前缀；价格为不复权原始价，"
                "除权信息查 xdxr；amount 单位元，vol 单位股。",
    }


@server.tool()
@_audited
def search_security(text: str, limit: int = 20) -> dict:
    """按代码前缀或名称片段找证券，返回 code/name/market/board。指数（board=INDEX）
    的 code 带 sh/sz 前缀，个股是 6 位数字。查 K 线前先用它确认代码。"""
    text = text.strip()
    if not text:
        raise ValueError("text 不能为空")
    limit = max(1, min(int(limit), 100))
    like = f"%{text}%"
    with _connect() as con:
        # 相关性排序：名称精确 > 名称前缀 > 代码前缀 > 指数 > 短代码。
        # 不这样排的话，搜「上证」会被一堆 6 位代码的「上证XX」主题
        # 基金挤掉 sh000001 上证指数。
        cur = con.execute(
            "select code, name, market, board from security "
            "where code like ? or name like ? "
            "order by (name = ?) desc, (name like ?) desc, (code like ?) desc, "
            "(board = 'INDEX') desc, length(code), code limit ?",
            (like, like, text, f"{text}%", f"{text}%", limit))
        rows, _ = _rows_to_dicts(cur, limit)
    return {"rows": rows}


@server.tool()
@_audited
def daily_bars(code: str, start: str = "", end: str = "", limit: int = 500) -> dict:
    """查日 K 线（升序）。个股用 6 位代码如 '600519'；指数必须带前缀：
    'sh000001' 上证指数、'sz399006' 创业板指——'000001' 是平安银行。
    start/end 为 YYYYMMDD。默认最多 500 行，查长区间请分段。

    ⚠️ 价格是不复权原始价，跨除权日算收益前先查 xdxr 或改用前复权口径。
    """
    code = _norm_code(code)
    lo, hi = _norm_date(start, "start"), _norm_date(end, "end")
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = ("select code, trade_date, open, high, low, close, amount, vol "
           "from daily_bar where code = ?")
    args: list = [code]
    if lo is not None:
        sql += " and trade_date >= ?"
        args.append(lo)
    if hi is not None:
        sql += " and trade_date <= ?"
        args.append(hi)
    # 倒序取最近 limit 根再反转回升序：不传 start 时拿到的是「最近 N 根」
    # 而不是 2000 年的最早 N 根。
    sql += " order by trade_date desc limit ?"
    args.append(limit)
    with _connect() as con:
        cur = con.execute(sql, args)
        rows, _ = _rows_to_dicts(cur, limit)
        if not rows and re.fullmatch(r"\d{6}", code):
            exists = con.execute(
                "select 1 from security where code = ?", (code,)).fetchone()
            if not exists:
                return {"rows": [], "hint": f"库里没有 {code!r}。"
                        "如果要查的是指数，请带 sh/sz 前缀并用 "
                        "search_security 确认。"}
    return {"rows": list(reversed(rows))}    # 反转回升序


@server.tool()
@_audited
def oamv(start: str = "", end: str = "", limit: int = 1000) -> dict:
    """全市场活跃市值/量能日线（单序列，无代码维度）。列：trade_date/open/high/
    low/close/vol/amount。vol 为两市总成交量（股），amount 为两市总成交额（元）。"""
    lo, hi = _norm_date(start, "start"), _norm_date(end, "end")
    limit = max(1, min(int(limit), MAX_ROWS))
    sql = "select trade_date, open, high, low, close, vol, amount from oamv_daily"
    conds, args = [], []
    if lo is not None:
        conds.append("trade_date >= ?")
        args.append(lo)
    if hi is not None:
        conds.append("trade_date <= ?")
        args.append(hi)
    if conds:
        sql += " where " + " and ".join(conds)
    sql += " order by trade_date desc limit ?"
    args.append(limit)
    with _connect() as con:
        cur = con.execute(sql, args)
        rows, _ = _rows_to_dicts(cur, limit)
    return {"rows": list(reversed(rows))}   # 返回升序，最新的在末尾


@server.tool()
@_audited
def xdxr(code: str) -> dict:
    """个股除权除息记录。bonus/rights 为每 10 股送/配股数，dividend 为每 10 股
    派息（税前，元）。没有记录说明该股无除权数据（前复权会退化为不复权）。"""
    code = _norm_code(code)
    with _connect() as con:
        cur = con.execute(
            "select code, ex_date, bonus, rights, rights_px, dividend from xdxr "
            "where code = ? order by ex_date", (code,))
        rows, _ = _rows_to_dicts(cur)
    return {"rows": rows}


@server.tool()
@_audited
def sql(query: str) -> dict:
    """只读 SQL：单条 SELECT/WITH，只能查白名单表（见 list_tables）。
    最多返回 5000 行、执行超时 10 秒。跨表join、聚合、CTE 都可以。"""
    body = _check_sql(query)
    with _connect() as con:
        cur = con.execute(body)
        rows, truncated = _rows_to_dicts(cur)
    out = {"rows": rows}
    if truncated:
        out["truncated"] = True
        out["hint"] = f"结果超过 {MAX_ROWS} 行被截断，请加 WHERE / LIMIT 缩小范围。"
    return out


@server.tool()
@_audited
def list_tables() -> dict:
    """可查询表的建表语句（白名单）。个人交易表不在服务里。"""
    with _connect() as con:
        out = {}
        for t in WHITELIST:
            row = con.execute(
                "select sql from sqlite_master where type='table' and name=?",
                (t,)).fetchone()
            if row:
                out[t] = row[0]
    return {"tables": out,
            "note": "个股代码 6 位数字；指数带 sh/sz 前缀（daily_bar 里两者并存）。"}


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def _auth_wrapper(app, token: str):
    """HTTP 模式的 Bearer Token 校验：401 拒绝，审计里记下来源 IP。"""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class Auth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            got = request.headers.get("authorization", "")
            if got != f"Bearer {token}":
                _audit("http_auth", {"path": request.url.path,
                                     "client": request.client.host if request.client else None},
                       None, 0.0, "unauthorized")
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    return Auth(app)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="只读行情 MCP 服务")
    ap.add_argument("--http", action="store_true", help="HTTP 传输（默认 stdio）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    db = _db_path()
    if not db.exists():
        log.error("找不到行情库 %s，先在本机跑 python -m zhixing_quant.data.sync", db)
        sys.exit(1)

    if not args.http:
        log.info("stdio 模式启动，库：%s", db)
        server.run()                     # stdio
        return

    token = os.environ.get("MCP_TOKEN", "")
    if not token:
        log.error("HTTP 模式必须设置 MCP_TOKEN 环境变量（明文无鉴权等于把"
                  "行情库挂公网）。生成：python -c \"import secrets;print(secrets.token_urlsafe(32))\"")
        sys.exit(1)

    import uvicorn
    app = _auth_wrapper(server.streamable_http_app(), token)
    log.info("HTTP 模式启动：%s:%d，库：%s，审计：%s",
             args.host, args.port, db, _AUDIT_PATH)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
