# Gate Event Contracts Research Handoff

## Purpose

This module researches Gate crypto event contracts as a probability problem, not as ordinary leveraged futures trading.

Current Gate crypto-event scope (verified from Gate public product documentation on 2026-10-25):

- Assets: BTC, ETH, SOL, XRP, DOGE, HYPE, BNB.
- Cycles: 5m, 15m, 1h, 4h.
- Binary result: UP/DOWN.
- Winning shares settle at 1 USDT; losing shares settle at 0.
- Event share prices live roughly in the 0.01-0.99 USDT range and can be interpreted as market-implied probabilities before fees/frictions.

## Benchmark separation

### INDEX

Gate introduced a separate INDEX benchmark market.

Current rollout:

- BTC 5m / 15m
- ETH 5m / 15m

INDEX uses Gate's futures index price as the event benchmark. The project reads official public index candles through:

```text
GET /api/v4/futures/usdt/candlesticks
contract=index_BTC_USDT (or index_ETH_USDT)
```

Gate API documents that `index_`-prefixed contracts return index-price candlesticks and require no authentication.

For INDEX research:

- OHLC price features come only from Gate futures index candles.
- Regular futures volume may be aligned to the same timestamps only as an activity feature because index candles do not contain traded volume.
- The event start price is preferably read from the actual event index candle `open`.
- Matured INDEX records are automatically verified using the exact event candle `open` and `close`.
- Result semantics: `close >= open => UP`, otherwise DOWN.

## TWAP

Gate documentation states TWAP markets use Chainlink-provided 60-second time-weighted prices. They are not Gate spot prices and not Gate futures index prices.

As of this implementation, Gate API v4 public documentation does not expose a documented event-contract TWAP history/order-book API.

Therefore:

- TWAP mode is explicitly `RESEARCH_ONLY` by default.
- The current model uses Gate cached market history as a directional proxy only.
- If the human operator knows the Gate event's displayed TWAP reference price, they may enter it for better direction alignment.
- TWAP records are not automatically declared officially settled.
- The UI allows manual UP/DOWN verification from the final Gate result.
- Never claim a Gate spot/index proxy is official Chainlink TWAP settlement data.

## Probability architecture

```text
next-close ensemble
  -> classifier P(UP)
  -> regression-implied P(UP)
  -> bounded model blend
  -> institutional Alpha bounded log-odds adjustment
  -> volatility risk shrink toward 50%
  -> final P(UP) / P(DOWN)
```

Institutional Alpha is an independent confirmation layer. It includes price/momentum/trend/mean-reversion plus available Gate derivatives context such as Funding, OI, Basis, positioning and top-of-book imbalance.

Alpha must not directly overwrite the price forecast. Its probability impact is capped.

## Market edge

Because Gate event-contract public API documentation currently does not document event order books, the first version accepts manually entered UP/DOWN prices.

For an entry price `q` and model win probability `p`:

```text
gross EV/share = p - q
gross ROI       = (p - q) / q
```

These are gross research values before Gate's dynamic trading fees and execution slippage.

## Persistence

Database:

```text
results/event_contracts/event_contract_history.sqlite3
```

Uniqueness invariant:

```text
exchange + asset + timeframe + benchmark + event_open_at
```

The first official prediction is preserved. Re-clicking the same event must not rewrite or cherry-pick a later result.

INDEX status lifecycle:

```text
PENDING -> SETTLED
```

TWAP lifecycle:

```text
RESEARCH_ONLY -> SETTLED_MANUAL
```

## Realized metrics

Event evaluation includes:

- direction accuracy
- Brier score
- log loss
- average prediction confidence
- optional realized PnL per share when a market entry price and selected side were recorded

Brier/log-loss are important because event contracts trade probabilities; direction hit rate alone is insufficient.

## Desktop behavior

The preferred `app.py` navigation includes:

```text
快速多空预测
Gate 事件合约
详细预测研究
机构 Alpha 研究
```

The existing background worker also checks matured INDEX events periodically. It only calls Gate when at least one INDEX record is actually due.

## Execution boundary

This version does not place Gate event-contract orders.

Do not reverse-engineer undocumented private endpoints into production execution. Add automatic event order-book/trading integration only when Gate exposes a documented, stable interface or another explicitly supported integration path.
