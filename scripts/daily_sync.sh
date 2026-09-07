#!/usr/bin/env bash
# 每日盘后增量同步。挂到 cron / 计划任务里即可。
#
# crontab 示例（周一到周五 16:30）：
#   30 16 * * 1-5 /path/to/zhixing-quant/scripts/daily_sync.sh >> /path/to/sync.log 2>&1
#
# 前提：通达信当天已经开过并下载完盘后数据。

set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "===== $(date '+%F %T') 开始同步 ====="
"$PY" -m zhixing_quant.data.sync

# 每周一顺带更新一次名称和除权除息（新股上市、ST 变更、分红派息）
if [ "$(date +%u)" = "1" ]; then
    echo "----- 周一：更新名称与除权除息 -----"
    "$PY" -m zhixing_quant.data.sync --names --xdxr
fi

echo "===== $(date '+%F %T') 同步结束 ====="
