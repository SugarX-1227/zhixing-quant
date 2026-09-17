# 知行量化

盘后选股 + 回测，行情数据全部来自**本地通达信文件**，零 API 费用、零调用次数限制。

## 数据方案

通达信客户端每天盘后会把行情下载到安装目录的 `vipdoc` 下。这些 `.day` 文件不是加密的，
只是私有二进制格式，而且是 **32 字节定长记录**，所以可以按字节偏移做增量读取。

```
通达信客户端（你每天本来就要开）
        │  盘后自动下载，追加写入
        ▼
vipdoc/sh/lday/sh600000.day     32 字节/条定长
        │  记住上次读到第几条，只 seek 读新增部分
        ▼
data/market.db (SQLite)          全市场日线，主键 (code, trade_date)
        │
        ├─→ 选股扫描器 (砖型图 / B1 / B2)
        ├─→ 回测引擎
        └─→ Web 界面
```

每天的增量同步只读几百字节，秒级完成。**没有任何网络请求**（除非你主动跑 `--names` / `--xdxr`）。

## 快速开始

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytdx        # 可选，见下方「关于复权」
```

在 `config/settings.yaml` 里指向你的通达信目录：

```yaml
data:
  tdx_install_dir: C:/new_tdx      # 或 /Users/you/tdx
  db_path: data/market.db
```

打开通达信，让它下载完历史行情，然后建库：

```bash
.venv/bin/python -m zhixing_quant.data.sync --names --xdxr
```

首次全量约 5000 只股票，几分钟。之后每天盘后只需要：

```bash
.venv/bin/python -m zhixing_quant.data.sync
```

启动 Web 服务：

```bash
.venv/bin/streamlit run app.py
```

浏览器打开 http://localhost:8501，四个页面：选股 / 回测 / K线 / 数据。

## 关于复权（重要）

通达信 `.day` 文件里存的是**不复权原始价格**。遇到送股、派息，价格会凭空跳一个缺口，
均线、KDJ、砖型图这类指标在除权日附近全部失真，会大量产生假信号。

除权除息事件本地文件里没有，需要 `pytdx` 去通达信自家的免费行情服务器拉一次
（`--xdxr`，几分钟，之后增量更新很快）。

- **装了 pytdx**：`load_daily(..., adjust="qfq")` 返回前复权价格，`df.attrs["adjust"] == "qfq"`
- **没装**：自动退化为不复权，`df.attrs["adjust"] == "none"`，Web 界面会显式提示

不复权数据做选股在多数情况下能用，但做回测会明显失真，建议装上。

## 每天的流程

1. 收盘后打开通达信，等它下载完盘后数据（这一步你本来就在做）
2. `python -m zhixing_quant.data.sync`
3. `streamlit run app.py` → 选股页 → 开始扫描

第 2 步可以自动化，`scripts/daily_sync.sh` 已经写好：

```bash
# crontab -e，周一到周五 16:30
30 16 * * 1-5 /path/to/zhixing-quant/scripts/daily_sync.sh >> /path/to/sync.log 2>&1
```

它每天做增量同步，周一额外跑一次 `--names --xdxr`（新股上市、ST 变更、分红派息都是低频事件，一周一次够了）。

命令行也能直接出选股结果，不用开 Web：

```bash
python -m zhixing_quant.scanner.daily_brick
python -m zhixing_quant.scanner.daily_b1 --date 20250115 --limit-universe 300
```

## 增量同步是怎么保证不出错的

`sync_state` 表记录每个文件上次读到第几条记录。同步时：

1. 文件大小没变 且 mtime 没变 → 跳过
2. 文件变小 → 说明被重写了，整只全量重读
3. 文件变大 → 先回读第 `n-1` 条记录，比对日期和库里记的是否一致
   - 一致：从 `n * 32` 字节处 seek，只读新增部分
   - 不一致：通达信重写过文件（改数据/换口径），整只全量重读

写入用 `INSERT ... ON CONFLICT DO UPDATE`，同一天重复跑不会产生脏数据。

## 常见问题

**扫描结果是空的**
先看 Web 的「数据」页。多半是数据没同步上，或者最新交易日落后了好几天。

**ST 股没被过滤掉**
说明股票名称表是空的。跑 `--names`，它会依次尝试 `data/stock_names.csv`、pytdx、
本地 `.tnf` 文件。三条都失败时会明确告警，此时 ST 过滤静默失效。

**某只股票查不到**
停牌股在最新交易日没有 K 线，不会进入快照，这是预期行为。北交所默认不同步，
需要的话在配置里把 `include_bj` 改成 `true` 并把 `bj` 加进 `markets`。

**同步很慢**
第一次是全量，正常。如果每天都慢，检查是不是误加了 `--full`。

## 目录结构

```
app.py                      Web 服务入口（取代旧的 HTML 报告）
config/settings.yaml        全部参数
zhixing_quant/
  data/                     ★ 本地行情数据层
    tdx_reader.py           通达信二进制文件解析
    store.py                SQLite 仓库 + 增量状态
    sync.py                 增量同步 CLI
    xdxr.py                 除权除息 / 前复权
    names.py                股票名称解析
    tdx_loader.py           对上层的统一接口
  indicators/               砖型图 / B1 / B2 / MACD / KDJ ...
  scanner/                  每日选股扫描器
  backtest/                 回测引擎
  signals/ portfolio/ timing/ strategies/ executor/
tests/                      pytest
```

## 验证

```bash
.venv/bin/python -m pytest -q
```

## 这次改造改了什么行为

除了换数据源，有几处是**行为变更**，不是纯重构：

| 位置 | 原来 | 现在 |
|---|---|---|
| `.gitignore` | `data/` 把 `zhixing_quant/data/` 源码也忽略了，数据层从未进过仓库 | 改成 `/data/`，只忽略根目录的行情库 |
| `config/settings.yaml` | `timing` / `portfolio` 各写两遍，前一份是死配置 | 合并去重 |
| `backtest/engine.py` | 没 import pandas，`run()` 必崩；买入不扣本金；用当日收盘成交（未来函数） | 重写，T+1 开盘成交，资金曲线含本金 |
| B2 扫描器 | 只要求 3 根 K 线，但指标内部要算 MA114，`min_periods=1` 会拿 3 根硬算出"114日均线" | 要求 114 根 |
| 扫描器 | 凑够 N 只就 `break`，后面的股票没看过 | 扫完再排序截断 |
| 扫描器 | 给每只扫过的股票都存 120 根 K 线 | 只给命中的候选存 |
| 指数代码 | `sh000001`(上证指数) 和 `sz000001`(平安银行) 撞车互相覆盖 | 指数用 `市场+代码` 作 key |
| `backtest/engine.py` 止损成交价 | 跳空低开穿过止损时仍按止损价记账，系统性高估收益（实测 b1 虚增 32 个百分点） | 跳空穿越按开盘价成交 |
| 出场规则 | 写死：信号日低点止损 + 15% 止盈 + 满 20 日清仓；战法的 `exit_conditions` 从未被回测调用 | 统一走 `backtest/exits.py`，配置在 `exits` 段，回测与实盘同源 |
| 回测仓位 | 剩余现金按空槽等分，与止损位无关 | 默认走 `PositionSizer` 风险头寸公式（`backtest.sizing: risk`），并套用择时总仓位上限 |
| 砖型图判据 | 「砖高>4 且 砖高>昨日砖高×1.5」，比的是水平值；2.8 年只出 1 次信号 | 按通达信原公式「红柱高度 ≥ 绿柱高度×2/3」，恢复到约 558 次/年 |
| `sell` 配置段 | 阈值硬编码在 `sell_s.py` 里，改配置无任何反应 | 已接通 |
| S1「跌破前低」 | `rolling(5).min()` 含当日，条件恒为假，分支从未触发 | 改看不含当日的前 N 日最低 |
| 「今日」页复盘历史日期 | 防守阶段不传 `end_date`，用今天的收盘价判过去的止损 | 传入决策日 |

`zhixing_quant/timing/active_value.py:35` 还有一句提到 TDX MCP 的注释，只是注释，不影响运行。

## 已知限制

- **只有日线**。本地 `.lc1`/`.lc5` 分钟数据通达信只保留最近一段，做不了长周期日内回测。
  `tdx_reader.read_minute_file` 能读，但没接进主流程。
- **不是实时数据**。这套方案只适合盘后选股，日内实盘需要另接实时行情。
- **数据完整性依赖你开通达信**。哪天没开软件联网，本地就会缺一天，
  Web 的「数据」页会提示最新交易日落后。
- **回测未考虑**：涨跌停连板期间的流动性、停复牌、退市、成分股调整。
