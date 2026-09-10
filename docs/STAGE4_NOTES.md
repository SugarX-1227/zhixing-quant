# 阶段 4：修正知行双线与绿转强红

对照 `Z哥战法-完整战法详解.md` 附录 B 的通达信原始源码，修正三处与规格不符的
实现，并补上能真正抓住这类问题的测试。

## 改了什么

| 文件 | 改动 |
|---|---|
| `indicators/tdx.py` | 新增 `zhixing_white` / `zhixing_yellow` / `attach_zhixing_lines`，双线的唯一定义 |
| `indicators/dual_line.py` | 重写。原 `ema(C,10)` / `ma(C,20)` → 附录 B.1 定义 |
| `indicators/b1.py` `b2.py` `brick.py` | 改调共享函数，消灭 `yellow_line` 列覆盖 |
| `indicators/key_kline.py` | 白线原为 `ma(C,10)`（全项目第三个定义），已统一 |
| `indicators/brick.py` | 绿转强红改为附录 B.5：`砖高>4 AND 砖高>昨砖×1.5` |
| `backtest/runner.py` | 前视偏差判据抽成纯函数；基准指数替换不再静默；不再吞异常 |
| `scanner/daily_{b1,b2,brick}.py` | `min_bars` 与字段名跟随 config 重构 |
| `ui/param_schema.py` | 删除已不存在的参数，新增砖高两档门槛 |
| `config/settings.yaml` | `dual_line` 段成为双线唯一配置源 |

## 一、双线定义错误

附录 B.1：

```
白线: EMA(EMA(C,10),10)
黄线: (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4
```

`dual_line.py` 原实现是 `ema(close,10)` 与 `ma(close,20)`，docstring 还引着一句
旧的错误出处「白线: MA10，黄线: MA20」。

后果不是精度差一点：

- 黄线含 MA114 才是中长期锚，「跌破黄线 = 清仓 + 移出股票池」这种重处置才站得住。
  实测 250 根 K 线内破线天数 **103 天（MA20）vs 73 天（规格）**，多出四成永久拉黑。
- `brick.py` / `b1.py` / `b2.py` 用的是正确公式，但四个模块都往 `yellow_line`
  这一列写值，而流水线里 `dual_line` 排在它们后面，**后写覆盖先写**。
  于是进攻端按四线均值判「站上黄线可以买」，防守端按 MA20 判「跌破黄线该清仓」。
- `pipeline.DEFENSE_REQUIRED` 检查的是「列在不在」。列一直都在，只是值是错的，
  所以覆盖检查全绿，界面上完全看不出来。这比「规则是哑的」更难发现。

修复方式是收敛到 `tdx.attach_zhixing_lines`，四个模块调同一个函数，
算出来的值逐位相同，覆盖变成幂等操作。修复后八条流水线的白黄线全部一致：

```
流水线              白线对  黄线对  是MA20
brick              True   True   False
b1                 True   True   False
b2                 True   True   False
dual_line          True   True   False
yoga_pants         True   True   False
single_needle      True   True   False
defense            True   True   False
full               True   True   False

破黄线天数（后250根）: 73    （修复前 103）
```

### 顺带修的：黄线预热

`ma()` 用 `min_periods=1`，K 线不足 114 根时不会返回 NaN，而是拿残缺窗口算出一个
看起来很正常的值——文档 3.1 提醒过次新股/刚复牌的票黄线失真。现在不足 114 根
直接置 NaN，并新增 `lines_valid` 列，让调用方能区分「没触发」和「历史不够、没法判」。

## 二、绿转强红踩中文档专门警告的误读

文档 5.6 加粗写着：课程口语「红柱覆盖前一根绿柱 2/3 以上」容易被理解成
「涨幅 ≥ 前根跌幅的 2/3」——**这是错的**。

`brick.py` 原实现正是这个误读，且 `config` 里就写着 `min_height_ratio: 0.6667`：

```python
red_height   = 砖高 - REF(砖高,1)          # 今日涨幅
green_height = REF(砖高,2) - REF(砖高,1)   # 昨日跌幅
height_ok    = 今日涨幅 >= 昨日跌幅 * 2/3
```

附录 B.5 的原始判据是 `砖高 > 4 AND 砖高 > REF(砖高,1) * 1.5`。漏掉两件事：
比较对象错了，以及「砖高 > 4」这道**绝对强度门槛完全没实现**——砖高从 0.1
涨到 0.11 也算红砖，正是作者注释里说要挡掉的「微红盘」。

实测同一组数据：规格定义命中 0 次，原实现命中 19 次。瑜伽裤战法只有这一个信号。

## 三、回测执行器

- **前视偏差判据**抽成 `check_universe_as_of()` 纯函数并加测试。此前
  「建池基准日 = 回测开始日」只写在 docstring 里，零覆盖。
- **基准指数静默替换**。原候选链 `(code, "sh000001", "000001")`：请求 sh000300
  而库里没有时会静默换成上证并照常算超额收益；最后那个裸 `000001` 在本地库里是
  **平安银行**（见 `data/sync.py` 注释），最坏会拿一只银行股当大盘基准。
  现在候选链只含指数，替换时显式告警：

  ```
  ⚠ 库里没有 sh000300，已改用 sh000001 作基准。
     下面的超额收益是相对 sh000001 算的，不是 sh000300。
  ```
- **不再吞异常**。原 `except Exception: skipped += 1` 会让 200 只票全部报错时
  只显示 `skipped 200`。现在按错误类型归并后写进 warnings。

## 四、关于测试

新增 14 个，共 184 通过。但更重要的是它们**能失败**。做了变异验证：

| 把代码改回错误版本 | 结果 |
|---|---|
| 白线改回单条 EMA10 | 2 failed |
| 黄线改回 MA20 | 2 failed |
| 绿转强红去掉「砖高>4」 | 1 failed |
| 建池基准日改回最新快照 | 1 failed |
| 基准候选链塞回裸 `000001` | 1 failed |
| scanner `min_bars` 指向已删除的键 | 1 failed |

原来的 `test_dual_line_indicator.py` 只断言「列存在」，所以白线写成 `ema(C,10)`、
黄线写成 `ma(C,20)` 时它全绿。现在改成逐值比对附录 B.1。

### 一个必须记下来的实例

重构 config 时我删掉了 `brick/b1/b2` 段里重复的 `yellow_ma_windows`，
而三个 scanner 的 `min_bars` 正是 `lambda cfg: max(cfg["b1"]["yellow_ma_windows"])`。
**181 个测试没有一个发现**——因为 lambda 只在真正点「扫描」时才求值。

这是本项目第五次「测试全绿但代码是坏的」，而这次差点是我自己造的。
已补三个针对性测试：`test_scanner_min_bars_reads_a_key_that_exists`、
`test_param_schema_keys_exist_in_config`、
`test_scanner_extra_fields_columns_are_produced`。

## 五、一个反直觉但正确的行为，别去"修"它

横盘平台刚起涨的头几根，**黄线会比白线涨得快，先出一次死叉**，随后白线加速
才补上金叉。原因是白线是双重 EMA10，首根响应约 (2/11)²≈3.3%，而黄线里的
MA14 首根响应是 1/14≈7.1%。

这是二次平滑的固有代价，文档 3.1 说得很清楚：白线宁可晚一点确认，也不要被洗出去。
已写成 `test_double_ema_lags_ma14_on_the_first_bars_of_a_breakout` 锁住。

## 六、历史回测结果全部作废

此前所有回测数字都是拿 MA20 当生死线、拿 2/3 误读当超短信号跑出来的，
没有参考价值，需要重跑。

## 七、还没修的（按严重程度排序）

1. **建池的 ST 过滤不是时点正确的**。`store.py` 的快照 SQL 是
   `LEFT JOIN security s`，`security.name` 存的是**今天**的名字。
   合成库实测：一只「2023 年正常、今天叫 \*ST」的票，用 `as_of=20230601` 建池
   时被提前剔除。方向上恰好是抬高回测收益的那一侧。
   真修需要给 `security` 加一张按日期的名称历史表。
2. **前复权本身是一种前视偏差**。qfq 因子依赖回测期之后的分红，
   而文档的止损规格是「K线最低价下方 3–5 个**价位**（tick=0.01元）」——
   在复权价上「3-5 个 tick」和实盘不是一回事。
3. **`rebalance_dates` 仍是死代码**，`run_backtest` 只在 `start` 建一次池。
4. **退市股是否在本地库里**没有验证。若通达信同步只读当前市场目录，
   幸存者偏差在数据层就存在，时点正确的选股也救不回来。
5. `row.get("white_line", close)` 这类兜底默认值遍布 defense/rater/sell_s，
   列缺失时规则会静默失效而不是报错。
