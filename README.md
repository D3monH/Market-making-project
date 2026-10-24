# Market-making-project

一个面向加密货币的**线性回归辅助做市研究项目**。

当前包含两条链路：

- **V0.1**：历史 OHLCV + Walk-Forward Linear Regression + candle-level 回测。
- **V0.2**：Gate 实时公开盘口/成交数据 + 在线线性回归 + 实时 Paper Market Making。

> **默认不会下真实订单。回测或 Paper Trading 盈利不代表实盘盈利。**

---

## V0.1：历史回测

```text
CCXT OHLCV
   ↓
特征工程
   ├─ 收益率 / 动量
   ├─ 波动率
   ├─ 成交量 Z-Score
   ├─ K线振幅
   ├─ EMA 距离
   └─ RSI
   ↓
Walk-Forward Linear Regression
   ↓
预测未来 N 根累计收益率
   ↓
Reservation Price 偏移
   ↓
Inventory Skew + Volatility Spread
   ↓
Bid / Ask
   ↓
下一根 K 线 High/Low 模拟成交
```

运行：

```bash
python main.py
```

只跑 BTC：

```bash
python main.py --symbol BTC/USDT
```

结果写到：

```text
results/BTC_USDT/
├── features_predictions.csv
├── equity_curve.csv
├── trades.csv
└── summary.json
```

---

# V0.2：Gate 实时 Paper Market Making

V0.2 不再只依赖 K 线，而是直接监听 Gate 的公开现货 WebSocket：

```text
Gate spot.book_ticker
        +
Gate spot.trades
        ↓
Order Book Imbalance
Trade Flow Imbalance
Spread
Short-term Return
Volatility
        ↓
Online Linear Regression
        ↓
预测未来约 5 秒 mid-price return
        ↓
Reservation Price
        ↓
Inventory Skew + Dynamic Spread
        ↓
Paper Bid / Ask
        ↓
真实市场 trade-through 模拟成交
        ↓
Paper Equity / Drawdown / Fills
```

## 在线线性模型

`quant_mm/online_model.py` 使用实时数据进行因果训练。

一个时间点的特征不会立刻成为训练样本，而是必须等待 `horizon_ms` 到期，未来 mid-price 已经真实发生以后，才生成 label：

```text
盘口特征 @ t
    ↓
等待 5 秒
    ↓
真实 mid @ t+5s
    ↓
生成未来收益率 label
    ↓
加入滚动训练集
```

因此在线训练不会把未来价格提前泄漏给模型。

默认特征：

- Order Book Imbalance
- Aggressive Buy / Sell Trade Flow
- Bid-Ask Spread
- 最近一次 mid return
- 短期波动率

在模型还没有积累足够训练样本之前，会使用一个透明的 `book imbalance + trade flow` warm-up 信号；模型拟合完成后，以在线 Linear Regression 为主要 alpha。

## Gate 实时 Paper 运行

安装：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Windows：

```bash
python gate_paper.py --symbol BTC/USDT
```

ETH：

```bash
python gate_paper.py --symbol ETH/USDT
```

默认配置在：

```text
config_gate.yaml
```

实时结果会写入：

```text
data/live/BTC_USDT/
├── books.jsonl
├── trades_market.jsonl
├── fills_paper.jsonl
└── snapshots.jsonl
```

这些原始数据后面可以继续用来训练更高级的微观结构模型。

---

## V0.2 Paper 撮合规则

为了避免 Paper 结果过分乐观，默认使用 `strict_crossing_fill: true`。

例如我们的 Paper Bid 是：

```text
65000.00
```

只有真实主动卖单成交价格**穿过** 65000，才认为这张 Paper Bid 成交；仅仅刚好触碰价格不算成交。

这仍然不能完整还原真实 maker queue，因为我们不知道真实挂单排队位置，但比只看 K 线 high/low 更严格。

---

## Gate 现货库存模型

V0.2 默认按 **Spot** 逻辑处理，而不是允许凭空做空。

例如初始虚拟权益：

```yaml
paper:
  initial_equity_usdt: 10000.0
  initial_base_inventory_usdt: 500.0
```

系统会在第一笔行情到来时，把约 500 USDT 等值转换成基础币库存，用于初始 Ask 做市。

默认：

```yaml
allow_short: false
```

因此卖单不会卖出超过当前持仓的 BTC/ETH。

---

# 风控

当前已经包括：

- 单次报价金额限制
- 最大基础币库存限制
- Inventory Target / Inventory Skew
- 最大回撤 Kill Switch
- 单日最大亏损 Kill Switch
- Maker Fee 计入
- 现货禁止负库存
- 线性预测输出限幅

默认示例：

```yaml
market_maker:
  order_notional_usdt: 50.0
  max_inventory_usdt: 1000.0

paper:
  max_drawdown_pct: 0.15
  max_daily_loss_pct: 0.03
```

---

# 为以后 Gate 自动交易准备的执行层

项目已经预留：

```text
quant_mm/gate_execution.py
```

包括：

- Gate API v4 HMAC-SHA512 签名
- TestNet / Live REST Base URL
- Spot Limit Order payload
- Place Limit Order
- Cancel Single Order
- Cancel All Orders for Symbol

但是：

```text
gate_paper.py
```

**完全不会调用私有 API，也不需要 API Key。**

执行模式预留为：

```text
paper
  ↓
testnet
  ↓
live
```

未来接 Gate 时，API Key 只从环境变量读取：

```text
GATE_API_KEY
GATE_API_SECRET
```

不要把 Key 写进 `config_gate.yaml`、代码或 GitHub。

即使以后把配置改成：

```text
mode = live
```

执行层仍然会要求额外环境确认：

```text
ALLOW_GATE_LIVE_TRADING=YES_I_UNDERSTAND
```

避免因为改错一个配置就直接打到真实账户。

Gate API Key 建议只开放 **Spot Read/Write**，关闭 Withdrawal 权限，并设置 IP whitelist。

---

# config_gate.yaml 关键参数

```yaml
symbol: BTC/USDT

online_linear:
  horizon_ms: 5000
  min_sample_interval_ms: 500
  train_window: 2000
  min_train_samples: 200
  retrain_every: 20
  max_abs_prediction_bps: 10.0

micro_alpha:
  book_weight_bps: 3.0
  trade_flow_weight_bps: 3.0
  max_micro_signal_bps: 6.0
  trade_window: 100

execution:
  mode: paper
  account: spot
```

---

# 测试

```bash
pytest -q
```

测试覆盖 V0.1 与 V0.2，包括：

- Walk-forward 历史预测
- Inventory Skew
- Gate WebSocket 消息解析
- Order Book / Trade Flow 信号方向
- 在线模型等待 horizon 后才生成 label
- Spot Paper 不会卖出超过库存
- Gate API 签名稳定性
- Live Trading 双重开关保护

---

# 当前局限

V0.2 已经比 candle 回测更接近真实做市，但仍然不是完整交易所级仿真。

还没有完整模拟：

- Maker queue priority
- Cancel / replace latency
- 网络延迟
- Partial fill queue position
- 交易所限频
- 最小下单量 / price tick / amount precision 的实时校验
- 实盘订单状态恢复
- WebSocket 私有 orders / usertrades 状态机
- 断网后的 open-order reconciliation

因此当前推荐顺序：

```text
历史回测
   ↓
Gate 实时 Paper
   ↓
连续积累真实盘口数据
   ↓
Gate TestNet
   ↓
订单状态机 + Cancel/Replace + Reconciliation
   ↓
小资金 Live
```

不要从历史回测直接跳到真实资金。

---

# 下一阶段 V0.3

优先实现：

1. Gate TestNet 自动挂单 / 撤单循环
2. `spot.orders` / `spot.usertrades` 私有 WebSocket
3. 交易所 tick-size / minimum amount 校验
4. Cancel/Replace 节流与 Rate Limit
5. Order State Machine
6. Restart Recovery / Open Order Reconciliation
7. PnL Attribution：Spread PnL / Inventory PnL / Fees
8. Funding / OI / Basis（如果后续扩展永续合约）
9. Ridge / Lasso / LightGBM 与在线 Linear Regression 对照
10. Prometheus / 日志 / Telegram 或其他告警
