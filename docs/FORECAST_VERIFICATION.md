# 预测持久化与自动验真

从这一版开始，每次预测除了写入 `latest_forecast.json` / `forecast_history.jsonl`，还会长期写入本地 SQLite：

```text
results/forecast/forecast_history.sqlite3
```

`results/` 已被 `.gitignore` 忽略，所以数据库只保存在你的本机，不会提交到 GitHub。

## 一条预测如何定义“到期”

模型用最新一根已经收盘的 K 线生成“下一根 K 线”的预测。

例如 15m：

```text
10:00-10:15  已收盘，作为模型基准
10:15-10:30  模型要预测的目标 K 线
10:30        目标 K 线收盘后才允许验真
```

因此系统会保存：

- `as_of`：模型基准 K 线开盘时间
- `target_open_at`：被预测 K 线开盘时间
- `target_close_at`：被预测 K 线收盘时间
- `status=PENDING`：尚未到期或交易所尚未返回最终 K 线
- `status=SETTLED`：已取得真实收盘价并完成验真

不会拿仍在形成中的 K 线提前判定结果。

## 验真记录内容

目标 K 线收盘后会保存：

- 实际收盘价 `actual_close`
- 实际收益率 `actual_return_pct`
- 方向是否预测正确 `direction_correct`
- 如果当时给出 LONG/SHORT 信号，信号是否正确 `signal_correct`
- 预测收盘价百分比误差 `price_abs_error_pct`
- 预测收益率绝对误差 `return_abs_error_pct`
- 实际收盘是否落在预测区间 `interval_hit`

## 自动检查方式

### 方式 1：仪表盘

```bash
python -m streamlit run dashboard.py
```

仪表盘打开时，验真区域每 60 秒自动扫描一次已经到期的预测。

左侧也有“立即验真到期预测”按钮。

### 方式 2：长期监控进程

Windows 可以双击：

```text
start_forecast_monitor.bat
```

或手动：

```bash
python forecast_monitor.py --interval 60
```

只检查一次：

```bash
python forecast_monitor.py --once
```

这个进程只读取公开行情，不需要 API Key，也不会下单。

## 仪表盘真实验真统计

按币种 / 周期累计显示：

- 已验真次数
- 方向命中率
- 有效 LONG/SHORT 信号数
- 有效信号命中率
- 平均价格误差
- 收益预测 MAE
- 预测区间命中率

这组数据比训练阶段的验证准确率更重要，因为它统计的是预测生成之后真正发生的未来行情。

## 关于旧预测

已有的 `forecast_history.jsonl` 会在首次打开新版仪表盘/监控器时自动导入 SQLite。只要旧记录里包含足够的时间、价格和周期字段，就会进入待验真队列，并在能够从交易所取得对应历史 K 线时完成验真。
