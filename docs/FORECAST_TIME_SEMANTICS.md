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

## 3. 1d 使用北京时间自然日

交易所原生 `1d` 经常按 UTC 00:00 切日，这等于北京时间 08:00 切日，不符合本项目的日线定义。

预测引擎中的 `1d` 固定使用：

```text
Asia/Shanghai
北京时间 00:00 -> 次日 00:00
```

实现方式：

```text
交易所已收盘 4h K
        ↓
按 Asia/Shanghai 转换
        ↓
每 6 根完整 4h K
        ↓
聚合成北京时间自然日 1d K
```

OHLCV 聚合规则：

- open = 当天第一根 4h K 的 open
- high = 6 根 4h K 的最高 high
- low = 6 根 4h K 的最低 low
- close = 当天最后一根 4h K 的 close
- volume = 6 根 volume 之和

只要缺少任意一根 4h K，该日就不会作为完整日 K 进入模型。

## 4. 1d 验真必须使用同一时间窗口

如果预测使用北京时间自然日，验真也不能改用交易所 UTC 日 K。

系统会按照预测记录中保存的 `target_open_at`，精确抓取目标 24 小时窗口对应的 6 根 4h K 并重新聚合，再取真实 close 验真。

这样保证：

```text
训练口径 == 预测口径 == 验真口径
```

## 5. 重复点击不会产生重复预测

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

## 6. 数据过期保护

预测数据会做 freshness 校验。

如果最新已收盘 K 线明显落后于当前市场超过一个完整周期，系统直接报错并拒绝生成预测，而不是继续使用旧数据。

这是为了防止分页错误、网络问题或交易所数据异常导致“拿几小时前/几天前的价格当当前基准”。

## 7. 数据库 candle_basis

SQLite 中保存每条预测使用的 K 线口径。

当前新记录：

```text
15m / 1h / 4h ...
exchange_native_closed

1d
natural_day:Asia/Shanghai
```

旧版本记录会标记为：

```text
legacy_exchange_native
```

准确率统计会按 `symbol + timeframe + candle_basis` 分组，避免旧 UTC 日 K 与新北京时间日 K 混在一起统计。

## 8. 时间配置

`config_forecast.yaml`：

```yaml
time:
  daily_candle_timezone: Asia/Shanghai
  display_timezone: Asia/Shanghai
```

当前建议不要随意修改 `daily_candle_timezone`，否则长期历史的日 K 口径会改变。

---

核心原则：**先确定时间窗口，再谈模型准确率。只要预测输入、目标和验真不是同一根 K 线定义，再高级的模型统计也没有意义。**
