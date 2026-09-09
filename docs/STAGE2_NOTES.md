# 第二阶段：六套战法接通 + 浅色界面

## 文件清单

| 文件 | 状态 |
|---|---|
| `zhixing_quant/indicators/pipeline.py` | 新增 · 指标流水线与签名适配层 |
| `zhixing_quant/scanner/strategy_scan.py` | 新增 · 战法→扫描器适配器 |
| `zhixing_quant/strategies/b1_strategy.py` | 新增 |
| `zhixing_quant/strategies/single_needle_strategy.py` | 新增 |
| `zhixing_quant/strategies/registry.py` | 重写 · 六套全注册 |
| `zhixing_quant/ui/theme.py` | 新增 · 浅色令牌 |
| `zhixing_quant/ui/components.py` | 新增 |
| `zhixing_quant/indicators/macd.py` | 修复 · NameError |
| `zhixing_quant/executor/daily_workflow.py` | 更新 · 用流水线 + 支持全部战法 |
| `app.py` | 重写 · 六页面 |
| `tests/test_pipeline_and_strategies.py` | 新增 · 14 个用例 |

## 修的两个 bug

### 1. 三个指标模块调用约定不统一，一直在崩

指标层签名不一致是历史遗留：

```python
add_brick_indicators(df, cfg)                              # 收字典
add_dual_line(df, fast=10, slow=20)                        # 收位置标量
add_macd(df, fast, slow, signal, divergence_window)        # 收位置标量
add_volume_price(df, vol_ma_window=5)                      # 收位置标量
detect_key_k(df, atr_window=14, lookback=60)               # 收位置标量
detect_distribution(df, market_cap, cfg)                   # 三参数
```

上阶段 `_enrich` 统一按 `(df, cfg)` 调用，前五个都抛异常被 try/except 吞掉。
后果是**防守八级阶梯里有三级从来没运行过**（P5 MACD死叉、P6 跌破白线、P8 放量滞涨），
而界面上完全看不出来——"没触发"和"规则根本没运行"长得一模一样。

现在 `pipeline.py` 是唯一的适配层，每一步显式按真实签名调用，并返回
`PipelineResult.failed`。`defense_coverage()` 直接告诉你哪级规则是哑的，
界面上用红色标签显示。

### 2. MACD 模块 NameError

```python
close = pd.to_numeric(out["close"], ...)
...
for i in range(divergence_window, len(close)):
    win_p = price.iloc[...]        # price 未定义
```

`price` 是另一个函数 `_find_divergence` 的参数名。而 `test_macd_indicator.py` 是过的，
因为测试数据只有 30 根 K 线，`divergence_window=60`，那段循环从没进去过。
新测试用 160 根确保循环真的执行。

## 六套战法一次全接上

原来每套战法要手写一个 `scanner/daily_*.py`，所以只有 brick/b1/b2 有扫描器。
但所有战法都实现了同一个接口：

```python
Strategy.entry_conditions(df, idx, cfg) -> EntrySignal | None
```

所以只需要一个适配器：跑指标流水线 → 在最后一根 K 线调 entry_conditions →
把 EntrySignal 摊平成扫描器需要的列。

新增战法现在是三步，不用再写扫描器：

1. `strategies/` 下实现 Strategy 子类
2. `registry.py` 里 `register_strategy`
3. `pipeline.py` 的 `PIPELINES` 里声明需要哪些指标

实跑结果（合成数据）：

```
砖型图    (scalp) 择时BULL 候选2 计划2可执行/0拦截 哑规则0
B1       (swing) 择时BULL 候选0                    哑规则0
B2       (swing) 择时BULL 候选0                    哑规则0
双线战法  (swing) 择时BULL 候选0                    哑规则0
瑜伽裤    (scalp) 择时BULL 候选0                    哑规则0
单针下30  (scalp) 择时BULL 候选0                    哑规则0
```

## 浅色配色

不是把深色反转。红绿在白底上更亮更跳，深色版的 `#F0483E` / `#14A87C`
直接搬过来会刺眼且互相打架，两个语义色都压深一档。

对比度实测（WCAG AA 正文需 ≥4.5）：

| 用途 | 色值 | 对比度 |
|---|---|---|
| 主文字 | `#1C1F23` | 15.83:1 ✓ |
| 次文字 | `#5A6470` | 5.76:1 ✓ |
| 弱文字 | `#646E7B` | 4.95:1 ✓ |
| 涨（红） | `#C62828` | 5.38:1 ✓ |
| 跌（绿） | `#00796B` | 5.09:1 ✓ |
| 强调 | `#8F6708` | 4.89:1 ✓ |

深色版用的 `#F2B01E` 在白底上只有 **1.83:1**，做文字完全不可用。
所以强调色拆成两个值：`SIGNAL = #8F6708` 用于文字和边框，
`SIGNAL_LINE = #E8A317` 用于 K 线图上的知行多空线——
线条是图形不是文字，要的是跳出来而不是可读性。**这两个不要合并。**

底色用 `#FAFAF8` 而不是纯白，大面积表格配纯白会眩光。

## 六个页面

| 页面 | 内容 |
|---|---|
| 今日 | 择时 → 防守 → 进攻 → 下单计划，就是 daily_cycle 的可视化 |
| 持仓 | 明细、平仓、手工登记、成交记录、账户资金 |
| 战法 | 六套战法各自扫描 + K线 |
| 回测 | 目前仅支持 brick/b1/b2 |
| 个股 | K线 + 全部指标 + 历史信号统计 + 防守覆盖检查 |
| 数据 | 行情库同步 |

侧栏可切换波段/超短账户（规格 01.6 `[LOCKED]` 要求分账户）。

## 验证

```
151 passed
```

用 Streamlit AppTest 逐页跑过真实交互（切页、扫描、点运行），六页均无异常。
今日页点「运行」后确认渲染出：择时、防守、进攻、下单计划、风险敞口、记录建仓按钮。

## 已知限制

1. **回测只支持 brick/b1/b2。** 另外三套的信号需要逐根 K 线求值，
   而回测引擎读的是预先算好的信号列。适配器目前只在最后一根求值（盘后选股够用）。
2. **建仓不自动扣现金**，需手工在持仓页维护。
3. **对称战法（规格 S3）没实现**，缺对应指标。
4. **流通市值缺失**，`detect_distribution` 传 0 走通用判定，
   规格 05.2 的大小盘分档没生效。
5. 参数仍是规格给的初始猜测值，`[CALIBRATE]` 项一个都没校准。

## 下一阶段建议

优先做参数校准，不要再加功能。规格 `10-params-and-backtest.md` 有完整的回测协议，
`00-INDEX.md` 明确写着整套体系"未经独立验证，胜率数字均为课程口述"。
现在界面和管线都通了，正是拿真实数据验证的时候。
