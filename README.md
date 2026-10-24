# Market-making-project

一个面向加密货币的**线性回归辅助做市研究项目**。V0.1 支持 BTC/USDT、ETH/USDT、SOL/USDT 等 CCXT 交易对，通过历史 OHLCV 生成因子，使用 walk-forward 线性回归预测未来收益率，再用预测结果调整做市 reservation price（报价中心），同时用库存偏斜和波动率动态调整报价。

> **重要：V0.1 只做研究、回测和 candle-level paper simulation，不包含真实交易下单。回测盈利不代表实盘盈利。**

## V0.1 做了什么

```text
CCXT OHLCV
   ↓
特征工程
   ├─ 1/3/6/12 根收益率
   ├─ 12 根波动率
   ├─ 成交量 Z-Score
   ├─ K 线振幅
   ├─ EMA 距离
   └─ RSI
   ↓
Walk-Forward Linear Regression
   ↓
预测未来 N 根累计收益率
   ↓
Signal → Reservation Price 偏移
   ↓
Inventory Skew + Volatility Spread
   ↓
Bid / Ask
   ↓
下一根 K 线 High/Low 模拟被动成交
   ↓
Equity / Drawdown / Sharpe / Fees
```

### 核心思想

线性回归不是直接决定“买还是卖”，而是预测未来短期收益率。例如预测为正，则做市报价中心略微向上偏；预测为负，则向下偏。与此同时：

- 库存过多：报价中心向下移动，鼓励卖出、减少继续买入。
- 库存过空：报价中心向上移动，鼓励买回。
- 波动率升高：扩大 bid/ask spread。
- 最大库存：限制单方向库存暴露。
- 最大回撤：达到阈值后停止继续报价。

## 防前视设计

V0.1 使用 walk-forward 训练。在时间点 `t` 做预测时，只允许训练目标已经在 `t` 时刻可观察到的历史样本。不会随机打乱时间序列，也不会把未来收益泄漏给模型。

报价在 candle `t` 收盘后生成，只允许在 candle `t+1` 的 high/low 范围内模拟成交，避免使用下一根 K 线的信息来生成本根报价。

## 安装

建议 Python 3.11+：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux/macOS：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 直接运行

默认配置会依次研究 BTC/USDT、ETH/USDT、SOL/USDT：

```bash
python main.py
```

只跑 BTC：

```bash
python main.py --symbol BTC/USDT
```

只跑 ETH：

```bash
python main.py --symbol ETH/USDT
```

也可以使用自己的 OHLCV CSV：

```bash
python main.py --symbol BTC/USDT --csv your_data.csv
```

CSV 必须包含：

```text
timestamp,open,high,low,close,volume
```

## 配置

主要参数在 `config.yaml`。默认是 15m、3000 根 K 线、预测未来 4 根累计收益率：

```yaml
exchange: binance
symbols:
  - BTC/USDT
  - ETH/USDT
  - SOL/USDT
timeframe: 15m
bars: 3000

model:
  horizon_bars: 4
  train_window: 1200
  min_train_rows: 400
  retrain_every: 24
```

做市参数：

```yaml
market_maker:
  order_notional_usdt: 100.0
  max_inventory_usdt: 1000.0
  base_half_spread_bps: 4.0
  min_half_spread_bps: 2.0
  vol_spread_multiplier: 0.35
  signal_weight: 0.60
  max_signal_bps: 20.0
  inventory_skew_bps: 12.0
```

## 输出

每个交易对都会写入独立目录：

```text
results/BTC_USDT/
├── features_predictions.csv
├── equity_curve.csv
├── trades.csv
└── summary.json
```

`summary.json` 包含：

- 初始/最终权益
- 净收益率
- 最大回撤
- Sharpe Ratio
- 模拟成交次数
- 手续费
- 最终库存
- Buy & Hold 收益率参考
- 是否触发最大回撤停止

## 测试

```bash
pytest -q
```

当前测试覆盖：

1. walk-forward 预测能正确生成；
2. 多头库存会让 reservation price 下移；
3. 正向回归信号会让 reservation price 上移。

## V0.1 的现实限制

本版本使用 OHLCV 做撮合模拟，因此**不知道 K 线内部的真实成交顺序、盘口队列位置和你的限价单是否真的排到并成交**。当下一根 K 线同时穿过 bid 和 ask 时，模拟器可能比真实交易更乐观。

所以 V0.1 的目标是验证：

> “这个线性回归信号 + 库存管理 + spread 模型有没有继续研究的价值？”

而不是证明它可以直接赚钱。

## 下一阶段建议

V0.2 优先升级：

- WebSocket 实时 Order Book / Trades
- 真实 maker queue / fill model
- Funding、Open Interest、Basis 因子
- Ridge / Lasso 与 Linear Regression 对照
- Trend / Mean Reversion / StatArb 信号
- Market Regime
- 实时 Paper Trading
- PnL attribution（spread 收益、inventory PnL、fees 分拆）

在实时 Paper Trading 经过足够长时间验证之前，不建议接真实资金。
