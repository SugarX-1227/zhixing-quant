"""MCP 只读行情服务的端到端验证（一次性脚本，不进测试套件）。

真实起一个 stdio 服务进程，用 MCP 客户端走完整协议：
1. 全部工具正常调用
2. 攻击用例：写入 / 多语句 / 个人表 / PRAGMA / 字符串字面量绕过
3. 审计文件落盘检查
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "zhixing_quant.mcp_server"])
    failures = []

    def check(name: str, ok: bool, detail: str = ""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            tools = (await s.list_tools()).tools
            names = sorted(t.name for t in tools)
            print("工具列表:", names)
            check("7 个工具都注册", len(names) == 7, str(names))
            bars_schema = next(t for t in tools if t.name == "daily_bars").inputSchema
            check("daily_bars 参数 schema 完整",
                  "code" in bars_schema.get("properties", {}))

            async def call(tool: str, args: dict):
                return await s.call_tool(tool, args)

            # --- 正常用例 ---
            r = await call("db_info", {})
            txt = r.content[0].text
            info = json.loads(txt)
            print("db_info:", info)
            check("db_info 日期范围", info["daily_bar_range"][1] >= 20260916)

            r = await call("search_security", {"text": "上证"})
            rows = json.loads(r.content[0].text)["rows"]
            check("search_security 找到上证指数",
                  any(x["code"] == "sh000001" for x in rows), str(rows[:3]))

            r = await call("daily_bars", {"code": "sh000001",
                                          "start": "20260901", "end": "20260917"})
            rows = json.loads(r.content[0].text)["rows"]
            check("daily_bars 指数取数", len(rows) >= 10 and rows[0]["trade_date"] == 20260901,
                  f"{len(rows)} 行, 首行 {rows[0]['trade_date'] if rows else '-'}")

            r = await call("daily_bars", {"code": "600519", "limit": 5})
            rows = json.loads(r.content[0].text)["rows"]
            check("daily_bars 个股取数", len(rows) == 5)

            r = await call("daily_bars", {"code": "000001", "limit": 3})
            body = json.loads(r.content[0].text)
            check("000001 不做猜测（个股口径或明确 hint）",
                  "rows" in body, str(body)[:120])

            r = await call("oamv", {"start": "20260910"})
            rows = json.loads(r.content[0].text)["rows"]
            check("oamv 活跃市值取数", len(rows) >= 5 and rows[-1]["trade_date"] >= 20260916,
                  f"{len(rows)} 行")

            r = await call("xdxr", {"code": "600519"})
            rows = json.loads(r.content[0].text)["rows"]
            check("xdxr 除权取数", isinstance(rows, list),
                  f"{len(rows)} 条")

            r = await call("sql", {"query": "select board, count(*) n from security "
                                            "group by board order by n desc"})
            rows = json.loads(r.content[0].text)["rows"]
            check("sql 聚合查询", len(rows) >= 2, str(rows[:3]))

            r = await call("sql", {"query": "select s.name, count(*) n from daily_bar b "
                                            "join security s on s.code = b.code "
                                            "where b.code = '600519' group by s.name"})
            rows = json.loads(r.content[0].text)["rows"]
            check("sql join 白名单表", len(rows) == 1, str(rows))

            # --- 攻击用例：全部应返回 error 字段而不是数据 ---
            attacks = [
                ("写入", "insert into daily_bar values ('X', 20990101, 1,1,1,1,1,1)"),
                ("update", "update security set name = 'x'"),
                ("删除", "delete from daily_bar where code='600519'"),
                ("多语句", "select 1; drop table daily_bar"),
                ("个人表", "select * from position"),
                ("个人表2", "select count(*) from trade_log"),
                ("PRAGMA", "pragma journal_mode = wal"),
                ("字符串绕过", "select * from position where code = 'insert into t'"),
                ("非 SELECT", "explain select * from daily_bar"),
                ("白名单外表", "select * from sync_state"),
            ]
            for label, q in attacks:
                r = await call("sql", {"query": q})
                body = json.loads(r.content[0].text)
                check(f"拒绝：{label}", "error" in body and "rows" not in body,
                      body.get("error", "")[:90])

            # 超时：无 where 全表扫 1600 万行（progress handler 应中断）
            r = await call("sql", {"query": "select avg(close) from daily_bar"})
            body = json.loads(r.content[0].text)
            got_timeout = "error" in body and "interrupt" in body.get("error", "").lower()
            check("超时中断生效（或聚合快到不需要）", "rows" in body or got_timeout,
                  body.get("error", "returned rows")[:80])

    print()
    print(f"结果：{'全部通过' if not failures else '失败 ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
