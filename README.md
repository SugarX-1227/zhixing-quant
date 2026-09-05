# 知行砖型图每日选股 MVP

第一版只做每日选股，不做历史回测。

## 当前规则

- 数据源：通达信 MCP（tdx-connector 连接器），行情经 MCP 落地为本地缓存后由扫描器读取。
- 股票池：沪深 A 股（通达信条件选股初筛，成交额排序）。
- 流动性过滤：当日成交额 `>= 1 亿`。
- ST 过滤：默认排除名称包含 ST 的股票。
- 市场状态：`config/settings.yaml` 中手动设置，当前为 `bear`。
- 空头区间：正常选票，不拦截信号。
- 战法：只做砖型图。
- 高开放弃：次日开盘高开 `>= 7%` 放弃。
- 每日候选：最多 `10` 只。
- 报告：只生成 HTML，不生成 CSV。

## 砖型图选股条件

来源：`通达信行情指标与选股指标(1).md`

```text
黄线 = (MA(CLOSE,14)+MA(CLOSE,28)+MA(CLOSE,57)+MA(CLOSE,114))/4

XG = 昨天绿柱
     AND 今天红柱
     AND 红柱高度 >= 昨天绿柱高度 * 2/3
     AND CLOSE > 黄线
```

## 数据流（通达信 MCP）

```
通达信 MCP (tdx_screener 条件选股 / tdx_kline 日K 前复权)
        │  (由 AI 助手执行同步)
        ▼
data/cache/spot_YYYYMMDD.csv        ← 全市场行情表 (code,name,close,amount,pct_chg,date)
data/cache/daily_{code}_*.csv       ← 个股日K (trade_date,open,high,low,close,vol,amount)
        │  (扫描器只读缓存，无网络请求)
        ▼
zhixing_quant/scanner/daily_brick.py → reports/daily/YYYYMMDD_candidates.html
```

数据访问层：`zhixing_quant/data/tdx_loader.py`（接口与旧 akshare_loader 一致）。
旧的 AKShare 数据源（akshare_loader.py / scripts/check_akshare.py）已删除，
`requirements.txt` 不再依赖 akshare。

### 每日同步数据

让 AI 助手（本工作区）通过通达信 MCP 执行：

1. `tdx_screener` 条件选股，组合两个池并合并去重为 `data/cache/spot_YYYYMMDD.csv`：
   - 池 A：「成交额大于10亿 非ST」**全量**（约 270 只，不再做"前 N 只"截断），分页拉取；
   - 池 B（砖型图/B1 形态预筛）：「昨日下跌今日上涨 且 成交额大于5亿 非ST」约 40 只。
   - 合并去重后约 290 只；全市场满足成交额≥1亿的有约 1800 只，逐只拉 K 线不现实，
     以 10 亿成交额门槛 + 形态预筛保证覆盖率（实测三策略都能出票）。
2. `tdx_kline` 对候选池逐只拉取日 K（period="4" 日线，tqFlag="1" 前复权，约 300 根），
   写入 `data/cache/daily_{code}_{start}_{end}_qfq.csv`（成交量换算为股，`vol = Volume * 100`）。

## 每天运行

```bash
.venv/bin/python -m zhixing_quant.scanner.daily_brick
```

生成报告：

```text
reports/daily/YYYYMMDD_candidates.html
```

小样本测试：

```bash
.venv/bin/python -m zhixing_quant.scanner.daily_brick --limit-universe 20
```

## 修改市场状态

编辑 `config/settings.yaml`：

```yaml
regime:
  current: bear
  block_open_in_bear: false
```

可选值建议：

- `bull`
- `neutral`
- `bear`

当前版本只把市场状态写进报告，不影响选股。

## 验证

```bash
.venv/bin/python -m pytest -q
```

## Python 环境

当前项目使用本目录下的 `.venv`，不要直接用系统 `python3` 跑。

重新安装依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## 注意

- 扫描器本身不发网络请求；若缓存缺失，会提示先通过通达信 MCP 同步数据。
- 数据准确性取决于通达信行情（前复权日线），与网络环境无关。
