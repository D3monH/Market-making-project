# Institutional-style research roadmap

This document maps public quantitative-investing ideas to this research project. It does **not** claim to reproduce any firm's proprietary strategy.

## Core principle

Do not search for one magic predictor. Build multiple weakly-correlated sources of edge, measure them out-of-sample, combine them conservatively, and keep execution/risk separate from forecasting.

## Public reference patterns

### Man AHL style: diversified systematic signals

Public Man AHL materials describe a systematic multi-strategy approach spanning momentum, mean reversion and fundamental models, with strong emphasis on diversification, evidence and risk control.

Project mapping:

- multi-speed momentum / trend signals
- mean-reversion signals
- regime classification
- signal diversification
- volatility targeting and risk limits

Reference:
- https://www.man.com/ahl
- https://www.man.com/capabilities/trend-following

### AQR style: factor portfolios

AQR has published extensively on momentum, value, carry and defensive factor premia across markets.

Crypto adaptation:

- Momentum: multi-horizon price trend / relative strength
- Carry: funding rate, futures basis, staking/borrow spreads where appropriate
- Defensive: volatility, liquidity, drawdown, downside-risk features
- Relative value: cross-sectional ranking among BTC/ETH/SOL/etc. rather than a naive absolute-price "value" metric

Reference:
- https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere
- https://www.aqr.com/insights/datasets/century-of-factor-premia-monthly

### Jane Street style: market mechanics + ML

Jane Street publicly emphasizes quantitative analysis, machine learning and deep understanding of market mechanics as a liquidity provider.

Project mapping:

- order-book imbalance
- aggressive trade flow
- spread / depth / short-term volatility
- queue-aware paper execution
- inventory-aware market making
- short-horizon ML only after market microstructure data quality is strong

Reference:
- https://www.janestreet.com/what-we-do/overview/

### Two Sigma style: data and research platform

Two Sigma publicly emphasizes hypothesis-driven research, data engineering, feature construction and scalable research infrastructure.

Project mapping:

- reproducible feature pipelines
- point-in-time datasets
- strict out-of-sample validation
- model registry / leaderboard
- experiment metadata
- alternative and cross-asset features

Reference:
- https://www.twosigma.com/

## Current model ladder

Current default ensemble:

1. Linear Regression — transparent baseline
2. Ridge — regularized linear baseline
3. ElasticNet — sparse/regularized linear model
4. HistGradientBoosting — nonlinear tree boosting
5. ExtraTrees — nonlinear interactions and regime-sensitive splits
6. Logistic Regression — separate probability of positive next-candle return

Weights are first estimated from chronological validation. After enough real settled forecasts exist for the exact symbol/timeframe/candle-basis cohort, recent out-of-sample model performance is blended into the weights.

## Next model candidates

Add only after the current ensemble has accumulated enough live research samples:

### Tier 1

- LightGBM / XGBoost benchmark
- Quantile regression for calibrated price intervals
- Regime-conditioned model weights
- Cross-timeframe consensus model

### Tier 2

- Hidden Markov Model for market regimes
- Kalman filter for dynamic relationships / pairs
- Cointegration model for BTC/ETH/SOL relative-value research
- Cross-sectional factor ranking across a larger liquid crypto universe

### Tier 3

Only after the dataset is large and leakage controls are mature:

- Temporal CNN / TCN
- Transformer-style time-series models
- sequence models using order-book events

Deep learning is not automatically better. It must beat simpler models on untouched out-of-sample data after costs and stability checks.

## Data expansion roadmap

Priority features:

1. Funding rate history
2. Open interest and OI change
3. Futures basis / term structure
4. Liquidation intensity
5. BTC/ETH/SOL cross-asset returns
6. Order-book imbalance / depth
7. Aggressive buy/sell flow
8. Volatility regime
9. Market breadth across liquid crypto assets

## Evaluation rules

Every model must be judged on:

- real out-of-sample direction accuracy
- return MAE
- calibration / interval coverage
- performance by market regime
- performance by symbol and timeframe
- stability across rolling windows
- turnover / fees / slippage once converted into a trading strategy

A more complex model is rejected if it cannot consistently outperform simpler baselines out-of-sample.
