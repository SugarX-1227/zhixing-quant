"""云端会话用：从 GitHub Release 下载行情切片，落到 data/market.db。

和 `export_slice.py` 是一对：有数据的机器导出并上传，云端会话用这个拉下来，
之后所有命令（扫描 / 回测 / 因子检验）都能直接跑，不用再让人肉转数据。

为什么是 Release 而不是 MCP 或提交进仓库
----------------------------------------

- **MCP 不通**：云端容器到局域网没有路由，公网隧道被出网白名单 403 掉。
  实测记录见 `export_slice.py` 的模块文档。
- **不提交进仓库**：切片几十 MB，进 git 会让每次 clone 都背上历史包袱，
  而且数据每天都在变，提交进去会把仓库撑爆。Release 附件不进 git 历史，
  单文件上限 2GB，换一版直接发新 tag。

用法
----

    python scripts/fetch_slice.py                 # 拉最新的 data-* release
    python scripts/fetch_slice.py --tag data-20260918
    python scripts/fetch_slice.py --out data/market.db --force

拉完直接验证：

    python -c "from zhixing_quant.data.tdx_loader import data_health; print(data_health())"
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

REPO = "SugarX-1227/zhixing-quant"
API = "https://api.github.com"


def _token() -> Optional[str]:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(key)
        if v:
            return v
    return None


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": "zhixing-quant-fetch",
        **({"Authorization": f"Bearer {_token()}"} if _token() else {}),
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def find_asset(tag: Optional[str]) -> tuple:
    """找到要下的附件，返回 (tag, 附件名, 下载地址, 字节数)。"""
    if tag:
        rel = json.loads(_get(f"{API}/repos/{REPO}/releases/tags/{tag}"))
    else:
        rels = json.loads(_get(f"{API}/repos/{REPO}/releases?per_page=30"))
        data_rels = [r for r in rels if str(r.get("tag_name", "")).startswith("data-")]
        if not data_rels:
            raise SystemExit(
                "没找到任何 data-* 的 release。\n"
                "先在有行情库的机器上跑：\n"
                "    python scripts/export_slice.py --gzip\n"
                "再按它打印的 gh release create 命令上传。")
        rel = data_rels[0]
    assets = rel.get("assets") or []
    cand = [a for a in assets if a["name"].endswith((".db", ".db.gz"))]
    if not cand:
        raise SystemExit(f"release {rel['tag_name']} 里没有 .db / .db.gz 附件。")
    # 有压缩版优先下压缩版
    cand.sort(key=lambda a: (not a["name"].endswith(".gz"), a["name"]))
    a = cand[0]
    return rel["tag_name"], a["name"], a["url"], int(a.get("size", 0))


def download(url: str, dest: Path) -> Path:
    """Release 附件要用 octet-stream 的 Accept，否则拿到的是 JSON 元数据。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = _get(url, accept="application/octet-stream")
    dest.write_bytes(blob)
    return dest


def main() -> None:
    p = argparse.ArgumentParser(description="从 GitHub Release 拉行情切片")
    p.add_argument("--tag", default=None, help="指定 release tag，默认取最新 data-*")
    p.add_argument("--out", default="data/market.db")
    p.add_argument("--force", action="store_true", help="覆盖已存在的库")
    args = p.parse_args()

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out)
    out = out if out.is_absolute() else root / out
    if out.exists() and not args.force:
        raise SystemExit(f"{out} 已存在。要覆盖请加 --force。")

    tag, name, url, size = find_asset(args.tag)
    print(f"release {tag} → {name}（{size / 1e6:.1f} MB）")

    tmp = out.parent / name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    download(url, tmp)
    print(f"  已下载 {tmp}")

    # 先算哈希再解压：发布方会公布 sha256，两边对得上才能确认传输无损。
    # 原实现解压后直接把 .gz 删了，事后想核对都没得核对。
    import hashlib

    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    print(f"  sha256 {digest}")
    print("  （和发布方公布的哈希对一下，不一致说明传输有损）")

    if name.endswith(".gz"):
        with gzip.open(tmp, "rb") as fi, open(out, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        print(f"  已解压 → {out}（{out.stat().st_size / 1e6:.1f} MB）"
              f"；压缩包保留在 {tmp.name} 供核对")
    elif tmp != out:
        tmp.replace(out)

    # 自查：拉下来的东西得真的能用
    sys.path.insert(0, str(root))
    from zhixing_quant.config import load_config
    from zhixing_quant.data import tdx_loader

    cfg = load_config()
    cfg["data"]["db_path"] = str(out)
    tdx_loader.set_config(cfg)
    h = tdx_loader.data_health(cfg)
    if not h.get("ok"):
        raise SystemExit(f"库拉下来了但读不了：{h.get('message')}")
    print(f"  自查通过：{h['codes']:,} 只 / {h['bars']:,} 根K线 / 最新 {h['date_max']}")
    for w in h.get("warnings", []):
        print(f"  ⚠ {w}")


if __name__ == "__main__":
    main()
