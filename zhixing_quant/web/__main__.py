"""启动 Web 服务。

    python -m zhixing_quant.web                       # http://127.0.0.1:8501
    python -m zhixing_quant.web --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn

    from zhixing_quant.web.server import create_app

    ap = argparse.ArgumentParser(description="知行量化 Web 服务")
    ap.add_argument("--host", default="127.0.0.1",
                    help="默认只监听本机。持仓和资金在里面，开 0.0.0.0 前想清楚")
    ap.add_argument("--port", type=int, default=8501)
    args = ap.parse_args()
    print(f"知行量化 → http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
