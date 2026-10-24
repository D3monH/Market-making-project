# 预测时间口径说明

这份文档定义 `forecast.py` 与 `dashboard.py` 的时间规则。后续修改预测系统时，必须保持这些规则一致。

## 1. 模型永远只使用已经完整收盘的 K 线

模型基准价不是实时 ticker，而是**最后一根完整 K 线的 close**。

例如北京时间 `20:29`，预测周期为 `15m`：

```text
20:00 ---------------- 20:15
                         ↑
                     模型基准价
                     已经确定的 close

20:15 ---------------- 20:30
             ↑
       正在形成，不能作为输入
                         ↑
                   模型预测目标
```

因此：

- `20:15` 的已知收盘价是模型基准；
- 模型预测 `20:30` 的下一根收盘价；
- `20:29` 的实时 BTC 价格只允许展示，不能替代基准价；
- 在 `20:30` 之前不能提前验真。

## 2. OHLCV index 表示开盘时间，不是收盘时间

CCXT 返回的 K 线 timestamp 表示该 K 线的**开盘时间**。

例如一根 index 为 `20:00` 的 `15m` K 线实际范围为：

```text
open:  20:00
close: 20:15
```

因此预测窗口为：

```text
as_of/base open = 20:00
base close       = 20:15
target open      = 20:15
target close     = 20:30
```

仪表盘图表会把历史 close 画在真正的收盘时间，而不是 K 线开盘时间。

## 3. Gate 1d 使用交易所原生日 K

Gate 的新 `1d` 预测不再自己重建北京时间 `00:00 -> 24:00` 日线，而是直接使用 Gate / CCXT 返回的原生 `1d` K 线。

当前项目将 Gate 原生日线定义为：

```text
UTC 00:00 -> 次日 UTC 00:00
```

页面统一按 `Asia/Shanghai`（UTC+8）显示，因此同一根 Gate 日 K 在北京时间看起来就是：

```text
北京时间 08:00 -> 次日 08:00
```

也就是说，一根日 K 的**收盘时刻是北京时间每天早上 08:00**。

例如北京时间 `2026-10-24 20:00` 进行 `1d` 预测：

```text
上一根完整日 K：
2026-10-23 08:00 -> 2026-10-24 08:00
                         ↑
                       基准 close

正在形成 / 预测目标：
2026-10-24 08:00 -> 2026-10-25 08:00
                         ↑
                       目标 close
```

所以：

```text
base close   = 2026-10-24 08:00
目标 close    = 2026-10-25 08:00
```

## 4. 旧版北京时间自然日只用于兼容历史记录

旧版本曾经把 `1d` 定义为：

```text
北京时间 00:00 -> 次日 00:00
```

并使用 6 根 `4h` K 聚合。

这套口径已经不再用于**新预测**，但不能直接删除，因为旧 SQLite 里已经存在按这个口径生成的预测记录。

因此：

- 新 Gate `1d`：直接使用交易所原生 `1d`；
- 旧 `natural_day:Asia/Shanghai`：只用于历史兼容验真；
- `resample_daily_ohlcv()` 继续保留，但不再作为新 Gate 1d 的数据源；
- 旧记录仍按它原先的 24 小时窗口验真，不能突然拿新 Gate 原生日 K 去评分。

核心原则仍然是：

```text
某一条预测的训练口径 == 该条预测的目标口径 == 该条预测的验真口径
```

## 5. Gate 1d 验真

新 Gate 原生 `1d` 记录的 `target_open_at` 应落在 UTC `00:00`。

验真时直接抓对应 Gate 原生 `1d` K：

```text
target open  = UTC 00:00
目标 close    = 次日 UTC 00:00
```

换成北京时间显示就是：

```text
target open  = 08:00
目标 close    = 次日 08:00
```

旧版北京时间自然日记录的 `target_open_at` 通常为 UTC `16:00`，系统会识别为 legacy daily，并继续使用 4h 聚合完成旧记录验真。

## 6. 重复点击不会产生重复预测

预测唯一键：

```text
exchange + symbol + timeframe + as_of
```

例如北京时间：

```text
20:16 点击 BTC 15m
20:20 再点击
20:25 再点击
20:29 再点击
```

只要最后一根完整 K 线仍然是 `20:00-20:15`，这四次操作都属于同一个目标：

```text
base close   = 20:15
target close = 20:30
```

数据库只保留一条正式预测记录。

当 `20:30` K 线完成后，再次预测才会形成新目标：

```text
base close   = 20:30
target close = 20:45
```

## 7. 数据过期保护

预测数据会做 freshness 校验。

如果最新已收盘 K 线明显落后于当前市场超过一个完整周期，系统直接报错并拒绝生成预测，而不是继续使用旧数据。

这是为了防止分页错误、网络问题或交易所数据异常导致“拿几小时前/几天前的价格当当前基准”。

## 8. 数据库 candle_basis

SQLite 中保存每条预测使用的 K 线口径。

新口径建议语义：

```text
5m / 15m / 1h / 4h ...
exchange_native_closed

Gate 1d
gate_native_utc_day
```

历史兼容口径包括：

```text
natural_day:Asia/Shanghai
legacy_exchange_native
```

准确率统计必须按 `symbol + timeframe + candle_basis` 分组，不能把不同日线定义混在一起。

## 9. 时间配置

`config_forecast.yaml` 当前：

```yaml
time:
  daily_candle_timezone: UTC
  display_timezone: Asia/Shanghai
```

这里要区分两个概念：

- `daily_candle_timezone: UTC`：兼容当前 UI / cohort 标签，表示 Gate 日 K 使用 UTC 日界线；
- `display_timezone: Asia/Shanghai`：页面显示北京时间，因此 UTC 00:00 会显示成北京时间 08:00。

新的 Gate `1d` 数据本身直接来自交易所原生 K 线，不再由 `daily_candle_timezone` 触发重采样。

---

核心原则：**先确定时间窗口，再谈模型准确率。只要预测输入、目标和验真不是同一根 K 线定义，再高级的模型统计也没有意义。**
