# Multi-Horizon Forecast Engine V0.3

这个模块专门回答一个问题：

> 对 BTC/ETH/SOL 等币种，下一根 15m / 1h / 4h / 1d K 线更偏向上涨、下跌还是震荡？预期幅度和不确定区间是多少？

它与做市执行层分开。预测引擎只负责生成研究信号，后续 Paper / TestNet / Live 执行可以消费这些信号。

## 运行

```bash
python forecast.py --symbol BTC/USDT
```

只预测 BTC 的 15m 与 1h：

```bash
python forecast.py --symbol BTC/USDT --timeframe 15m --timeframe 1h
```

默认配置：`config_forecast.yaml`。

默认一次研究：

- BTC/USDT
- ETH/USDT
- SOL/USDT
- 15m
- 1h
- 4h
- 1d

数据通过 CCXT 的公开 OHLCV 接口获取，默认交易所为 `gateio`，不需要 API Key。

## 当前模型

回归模型：

1. Linear Regression
2. Ridge Regression
3. HistGradientBoostingRegressor

分类模型：

4. Logistic Regression，用于估计下一根 K 线收涨概率 `P(up)`。

三个回归模型先在时间顺序验证集上比较 MAE，再按验证误差的倒数自动加权，形成 ensemble forecast。

没有随机打乱 K 线。

## 当前特征

全部特征只使用当前及过去数据：

- 1/2/4/8/16/32 根动量收益
- 4/16/48 根波动率
- 20/96 根成交量 Z-Score
- K 线 range
- candle body
- EMA 8/32/96 相对距离
- RSI(14)

未来版本会加入：

- Funding Rate
- Open Interest
- Basis
- BTC/ETH/SOL 跨资产联动
- Order Book Imbalance（更适合 15m/1h）
- Trade Flow
- Market Regime

## 每个周期输出

例如：

```text
15m  trend=UP          signal=LONG_BIAS
     E[r]=+0.18%
     P(up)=61.4%
     confidence=58.2%
     expected_price=67,420
     range=[67,080, 67,760]
```

真实输出字段包括：

- `trend`
  - STRONG_UP
  - UP
  - SIDEWAYS
  - DOWN
  - STRONG_DOWN
- `expected_return_pct`
- `expected_price`
- `lower_price` / `upper_price`
- `up_probability`
- `confidence`
- `research_signal`
  - LONG_BIAS
  - SHORT_BIAS
  - WAIT
- `realized_volatility_pct`
- `validation_mae_pct`
- `validation_direction_accuracy`
- `model_agreement`
- 每个模型自己的预测值
- 每个模型的 ensemble 权重

结果保存到：

```text
results/forecast/latest_forecast.json
```

## 为什么不直接声称“下一根 BTC 会到 67,420”

金融预测应该输出概率和不确定性，而不是假装有一个确定未来价格。

因此 V0.3 的主要输出是：

```text
方向 + 期望收益 + 上涨概率 + 价格区间 + 置信度
```

其中价格区间来自时间顺序验证集上的真实预测残差分布，而不是随便加一个固定百分比。

## 不同周期使用不同模型

V0.3 把每个 timeframe 当成独立问题：

```text
15m 历史 → 预测下一根 15m
1h 历史  → 预测下一根 1h
4h 历史  → 预测下一根 4h
1d 历史  → 预测下一根 1d
```

这样 1d 模型不会被迫使用只适合超短线的参数。

未来会增加 Multi-Timeframe Fusion，例如 15m 预测同时读取 1h/4h trend context。

## 与做市系统的关系

未来推荐结构：

```text
Forecast Engine
  ├─ 15m forecast
  ├─ 1h forecast
  ├─ 4h forecast
  └─ 1d forecast
          ↓
      Regime / Signal Fusion
          ↓
  Market Making / Trend / StatArb
          ↓
       Risk Engine
          ↓
      Gate Execution
```

例如 15m 和 1h 同时偏多，而 1d 是强空时，可以减少做市库存目标，而不是简单全仓做多。

## 重要限制

- 预测是概率性的，不保证正确。
- 当前价格区间不是严格的 high/low quantile model。
- 当前还没有 Funding/OI/Basis 等衍生品数据。
- 当前没有新闻、宏观、链上数据。
- 模型验证结果必须长期做 walk-forward / paper 验证，不能只看某一次预测。

V0.3 的目标不是证明“AI 能预测币价”，而是先建立一个可以持续评估、比较和迭代的预测框架。
