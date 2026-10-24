# Institutional Alpha Engine

This project does **not** attempt to clone any proprietary fund strategy. It borrows public research patterns from systematic firms and turns them into auditable modules that can be tested on crypto data.

## Public ideas mapped into this project

### AQR-style multi-factor thinking

Public AQR research repeatedly emphasizes combining multiple, economically distinct factor premia such as momentum, carry and defensive characteristics instead of relying on one signal.

Crypto mapping in this repository:

- Momentum: multi-horizon price trend.
- Carry/crowding: perpetual funding rate.
- Defensive/risk: realized volatility and volatility regime.
- Relative-value/crowding proxy: perpetual premium versus index price.

### Man AHL-style multi-strategy diversification

Man AHL publicly describes systematic research across trends, mean reversion, carry and other repeatable patterns, with diversification and risk control built into the process.

Crypto mapping:

- Trend alpha.
- Mean-reversion alpha.
- Derivatives crowding alpha.
- Regime-aware factor weights.
- Risk multiplier that reduces confidence during unusual volatility.

### Jane Street-style market-data and microstructure research

Jane Street publicly describes quantitative research using large datasets, machine learning, time-series analysis, feature engineering and model building for pricing/trading financial instruments.

Crypto mapping:

- Top-of-book imbalance.
- Existing Gate real-time paper market-making engine.
- Later: full depth, trade-flow and queue-aware execution research.

### Two Sigma-style data and research platform

Two Sigma publicly emphasizes data sourcing/preparation, feature engineering, modeling, portfolio construction and execution as separate stages of a scientific investment process.

Crypto mapping:

- Forecast history SQLite.
- Automatic forecast verification.
- Model leaderboard.
- Alpha history SQLite.
- Factor leaderboard.
- Future portfolio/risk layer before live execution.

## Current architecture

```text
Closed K-line data
      |
      +--> Forecast model ensemble
      |      Linear / Ridge / ElasticNet / HistGB / ExtraTrees
      |                    |
      |             expected next close
      |
      +--> Price Alpha
      |      momentum / trend / mean reversion / volatility risk
      |
Gate public futures data
      |
      +--> Derivatives Alpha
             funding / OI / perp premium / positioning / top-book imbalance

Forecast model + independent Alpha
              |
        consensus signal
              |
          risk multiplier
              |
       long-term verification
```

## Important design rule

The Institutional Alpha layer does **not** currently rewrite the next-candle predicted closing price.

Reason: derivatives factors are new and do not yet have enough aligned out-of-sample history. Letting them modify the price forecast immediately would make the system more complicated before proving that they add value.

Instead:

1. The forecast ensemble continues predicting the next close.
2. Alpha factors generate independent directional scores.
3. The system creates a separate consensus signal and risk-adjusted confidence.
4. Every factor is persisted and graded after the target candle closes.
5. Only after enough real samples should a factor be promoted into the trained forecast/portfolio layer.

## Gate public data used

No API key is required for the current Alpha layer.

- `/futures/usdt/tickers`
- `/futures/usdt/contract_stats`
- `/futures/usdt/funding_rate`

The project uses these public fields where available:

- Funding rate
- Open interest (USD)
- Futures last / index / mark price
- Long/short account ratio
- Best bid/ask size

## Next research milestones

1. Accumulate at least 100-300 settled Alpha observations per symbol/timeframe.
2. Measure factor accuracy separately by market regime.
3. Add cross-asset BTC/ETH/SOL relative-strength and breadth features.
4. Add historical funding/OI features directly to supervised training only after alignment/leakage tests.
5. Add LightGBM/XGBoost as challengers, not replacements by default.
6. Add HMM regime model and Kalman/cointegration research as separate strategies.
7. Add cost/risk-aware portfolio construction before any Gate live execution.

## Public references

- AQR factor/style research: https://www.aqr.com/Insights/Research/Journal-Article/Understanding-Style-Premia
- Man AHL: https://www.man.com/ahl
- Jane Street Quantitative Research: https://www.janestreet.com/quantitative-research/
- Two Sigma Investment Management: https://www.twosigma.com/businesses/investment-management/
- Gate API v4 Futures: https://www.gate.com/docs/developers/apiv4/en/futures/
