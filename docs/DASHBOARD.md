# Crypto Forecast Dashboard

V0.4 提供一个本地 Streamlit 网页仪表盘，用来操作 V0.3 多周期预测引擎。

## Windows 最简单启动方式

1. 拉取最新代码。
2. 双击仓库根目录的 `start_dashboard.bat`。
3. 浏览器会打开本地仪表盘。

如果本地 `.venv` 存在，启动脚本优先使用 `.venv\Scripts\python.exe`；否则使用系统 `python`。如果缺少 Streamlit，会先安装 `requirements.txt`。

也可以手动运行：

```bash
pip install -r requirements.txt
python -m streamlit run dashboard.py
```

## 可以操作什么

左侧可以选择：

- 数据源交易所：Gate / Binance / OKX / Bybit
- 常用币种：BTC / ETH / SOL / DOGE / XRP
- 自定义交易对，例如 `ADA/USDT`
- 预测周期：5m / 15m / 30m / 1h / 4h / 12h / 1d
- 历史 K 线数量
- 结果重点：综合预测 / 趋势与概率 / 价格目标与区间 / 模型细节
- 高级模型参数

点击“开始预测”后，每个周期会独立下载历史 K 线、训练模型并预测下一根原生 K 线。

## 仪表盘输出

每个周期会展示：

- 趋势：强势上涨 / 偏上涨 / 震荡 / 偏下跌 / 强势下跌
- 研究信号：偏多 / 偏空 / 等待
- 预期收益率
- 下一根 K 线预测收盘价
- 上涨概率
- 预测置信度
- 价格预测区间
- 当前波动率
- 历史验证方向准确率
- 验证 MAE
- 模型一致度
- Linear / Ridge / HistGradientBoosting 各自预测
- 动态 Ensemble 权重
- 最近价格走势 + 下一根预测点

## 历史预测记录

每次点击预测都会写入：

```text
results/forecast/latest_forecast.json
results/forecast/forecast_history.jsonl
```

仪表盘底部可以直接查看最近历史预测。后续可以基于这些记录继续加入“到期后自动验真”和真实命中率统计。

## 安全边界

V0.4 仪表盘只调用公开行情数据和预测模型：

- 不读取 Gate API Key
- 不调用真实下单接口
- 不执行自动交易

以后接 Gate 时建议继续分成：

```text
Forecast Dashboard
        ↓
Paper Trading
        ↓
Gate TestNet
        ↓
小资金 Live
```

预测结果是概率研究输出，不代表未来价格确定，也不保证盈利。
