# Cash-Market Quant Research Engine with NautilusTrader — Detailed Implementation Plan

## 1. Purpose

Build a **security-agnostic, cash-market quantitative research engine** that scans a defined equity universe and identifies statistically favorable **intraday** and **swing** opportunities.

The system should learn generalized market behavior across many securities rather than memorizing the behavior of one specific stock.

The architecture combines:

- Historical and live cash-market OHLCV data
- Timeframe-specific feature pipelines
- Technical indicators and price-action features
- Market and sector context
- Market-regime classification
- Multiple specialized strategy models
- Meta-model based signal fusion
- NautilusTrader for event-driven backtesting, portfolio/risk simulation, and eventual live runtime
- Optional LLM layer later for explanation and unstructured research inputs

The system is designed as a **research-support platform**, not an opaque autonomous buy/sell engine.

## 1.1 Revision Additions — Analog and Regime Intelligence

This revision promotes two additional model families to first-class components:

- **K-Nearest Neighbors (KNN)** as a historical-analog engine: *Have we seen a sufficiently similar market state before, and what happened next?*
- **K-Means clustering** as the first unsupervised market-regime discovery baseline: *What kind of market state are we currently in?*

The mature regime roadmap is:

```text
K-Means baseline
      |
      v
Gaussian Mixture Model (probabilistic regimes)
      |
      v
Hidden Markov Model (regime persistence / transitions)
```

KNN and K-Means do **not** replace XGBoost/LightGBM or the specialized strategy models. They provide complementary evidence that feeds the meta-model and the explicit `NO_TRADE` decision.

---

# 2. Version 1 Scope

## In Scope

- NSE cash-market equities
- Security-agnostic modeling across a defined liquid universe
- Intraday and swing analysis
- Historical OHLCV persistence
- Separate timeframe handling
- Technical indicators
- Price-action structure
- Volume analysis
- Market context
- Sector context
- Regime classification
- Momentum strategy model
- Mean-reversion strategy model
- Breakout strategy model
- Relative-strength strategy model
- Independent strategy backtesting
- Walk-forward validation
- Meta-model
- Intraday opportunity ranking
- Swing opportunity ranking
- NautilusTrader integration for event-driven backtesting
- Risk veto and explicit `NO_TRADE` state
- Transition path toward live trading later

## Explicitly Out of Scope for Version 1

- Futures
- Options
- Open interest
- Option Greeks
- Implied volatility surfaces
- Automated live order placement
- HFT / ultra-low-latency execution
- LLM as a raw market-price predictor
- News, earnings, filings, and fundamentals
- Reinforcement learning
- Portfolio optimization across many concurrent strategies
- Cross-asset trading

These should be added only after the cash-market engine is validated.

---

# 3. Core Design Principles

## 3.1 Security Agnostic

The model should not learn:

> `RELIANCE behaves like this.`

It should learn:

> `When a liquid equity shows this combination of trend, momentum, price action, volume, relative strength, and market regime, similar securities historically behaved in this way.`

The stock symbol is metadata and an identifier, not the primary predictive feature.

---

## 3.2 Preserve Timeframe Identity

Do not flatten all timeframes into one generic representation.

A security can simultaneously be:

```text
5m      Bullish
15m     Bullish
1h      Neutral
Daily   Bearish
```

This is valuable information and should remain intact.

Recommended paths:

### Intraday

Primary context:

- 5-minute
- 15-minute
- 1-hour

Prediction horizons:

- 15 minutes
- 30 minutes
- 60 minutes
- 120 minutes

### Swing

Primary context:

- 1-hour
- Daily

Prediction horizons:

- 1 day
- 3 days
- 5 days
- 10 days

---

## 3.3 Separate Research from Execution Simulation

Use two major runtime domains.

### Offline Research Domain

Responsible for:

- Data ingestion
- Historical storage
- Feature generation
- Label generation
- Model training
- Model validation
- Model versioning

### NautilusTrader Domain

Responsible for:

- Event replay
- Strategy execution logic
- Signal consumption
- Risk checks
- Portfolio state
- Order simulation
- Fill simulation
- Backtest accounting
- Production-like strategy runtime

The quant models generate intelligence.

NautilusTrader tests whether that intelligence remains useful when converted into realistic trades.

---

# 4. Preferred Technology Stack

## Primary Language

**Python**

Python should be the main language for the first version.

Reasons:

- Strongest ecosystem for quantitative research
- Easy TA-Lib integration
- Excellent ML ecosystem
- Simple Parquet/DuckDB support
- Easy NautilusTrader strategy integration
- Easy experimentation
- Easy future LLM integration

NautilusTrader provides a high-performance Rust core under a Python control layer, which is a good fit.

## Recommended Stack

| Layer | Technology |
|---|---|
| Primary language | Python |
| Technical indicators | TA-Lib |
| Dataframes | pandas initially, Polars later if needed |
| Raw storage | Parquet |
| Analytical DB | DuckDB |
| Historical analog model | scikit-learn KNN (weighted) |
| Regime discovery baseline | scikit-learn K-Means |
| Regime evolution later | Gaussian Mixture Model / Hidden Markov Model |
| Primary structured ML | XGBoost / LightGBM |
| ML tooling | scikit-learn |
| Deep learning later | PyTorch |
| Backtest/runtime | NautilusTrader |
| API later | FastAPI |
| Experiment tracking | MLflow or lightweight local registry |
| Config | YAML / Pydantic Settings |
| Logging | Python logging + structured JSON logs |
| Local LLM later | Qwen via Ollama / MLX-LM |

---

# 5. High-Level Architecture

```text
                    CASH MARKET UNIVERSE
                            |
                            v
                    Historical / Live OHLCV
                            |
                            v
                  RAW MARKET DATA STORAGE
                    Parquet + DuckDB
                            |
          +-----------------+------------------+
          |                                    |
          v                                    v
 OFFLINE RESEARCH PIPELINE              NAUTILUS DATA CATALOG
          |                                    |
          v                                    |
 Timeframe-Specific Features                   |
          |                                    |
          v                                    |
 Regime + Strategy Models                      |
          |                                    |
          v                                    |
      Meta Model                               |
          |                                    |
          +------------------+-----------------+
                             |
                             v
                    Nautilus Strategy
                             |
                      Model Inference
                             |
                             v
                       Signal State
                  BUY / SELL / NO_TRADE
                             |
                             v
                       Risk Veto Layer
                             |
                             v
                    Simulated Execution
                             |
                             v
                         Portfolio
                             |
                             v
                    Performance Metrics
```

---

# 6. Repository Structure

Recommended structure:

```text
stock-analyzer/
|
├── app/
│   ├── config/
│   │   ├── settings.py
│   │   ├── model_config.py
│   │   └── strategy_config.py
│   │
│   ├── data/
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   └── broker_provider.py
│   │   ├── ingestion/
│   │   │   ├── historical_loader.py
│   │   │   └── live_loader.py
│   │   ├── storage/
│   │   │   ├── parquet_store.py
│   │   │   ├── duckdb_store.py
│   │   │   └── schema.py
│   │   └── catalog/
│   │       └── nautilus_catalog.py
│   │
│   ├── features/
│   │   ├── technical.py
│   │   ├── price_action.py
│   │   ├── volume.py
│   │   ├── market_context.py
│   │   ├── sector_context.py
│   │   ├── normalization.py
│   │   └── pipeline.py
│   │
│   ├── labels/
│   │   ├── intraday.py
│   │   ├── swing.py
│   │   └── mfe_mae.py
│   │
│   ├── regimes/
│   │   ├── rules.py
│   │   ├── kmeans.py
│   │   ├── gmm.py
│   │   ├── hmm.py
│   │   ├── diagnostics.py
│   │   ├── model.py
│   │   └── trainer.py
│   │
│   ├── models/
│   │   ├── analog/
│   │   │   ├── fingerprint.py
│   │   │   ├── scaler.py
│   │   │   ├── knn.py
│   │   │   ├── weighting.py
│   │   │   ├── familiarity.py
│   │   │   └── evaluator.py
│   │   ├── momentum/
│   │   ├── mean_reversion/
│   │   ├── breakout/
│   │   ├── relative_strength/
│   │   ├── meta/
│   │   ├── registry.py
│   │   └── inference.py
│   │
│   ├── backtest/
│   │   ├── nautilus/
│   │   │   ├── strategy.py
│   │   │   ├── actor.py
│   │   │   ├── risk.py
│   │   │   ├── execution.py
│   │   │   ├── config.py
│   │   │   └── runner.py
│   │   └── metrics.py
│   │
│   ├── scanner/
│   │   ├── intraday_scanner.py
│   │   ├── swing_scanner.py
│   │   └── ranking.py
│   │
│   ├── observability/
│   │   ├── logging.py
│   │   ├── metrics.py
│   │   └── audit.py
│   │
│   └── services/
│       ├── research_service.py
│       └── signal_service.py
│
├── data/
│   ├── raw/
│   │   └── spot/
│   │       ├── 5m/
│   │       ├── 15m/
│   │       ├── 1h/
│   │       └── 1d/
│   ├── features/
│   ├── labels/
│   ├── training/
│   └── nautilus_catalog/
│
├── models/
│   ├── analog/
│   ├── regime/
│   ├── momentum/
│   ├── mean_reversion/
│   ├── breakout/
│   ├── relative_strength/
│   └── meta/
│
├── notebooks/
│   ├── exploration/
│   └── validation/
│
├── scripts/
│   ├── backfill_market_data.py
│   ├── build_features.py
│   ├── build_labels.py
│   ├── train_regime.py
│   ├── build_knn_index.py
│   ├── evaluate_knn.py
│   ├── compare_regime_models.py
│   ├── train_strategies.py
│   ├── train_meta.py
│   └── run_backtest.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── leakage/
│   ├── backtest/
│   └── regression/
│
├── configs/
│   ├── universe.yaml
│   ├── features.yaml
│   ├── labels.yaml
│   ├── models.yaml
│   ├── analog.yaml
│   ├── regimes.yaml
│   ├── backtest.yaml
│   └── risk.yaml
│
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

# 7. Step 1 — Define the Cash-Market Universe

Start with a liquid universe.

Recommended first choice:

- NIFTY 100 or NIFTY 200

Possible metadata:

```text
symbol
exchange
sector
industry
market_cap_bucket
average_daily_volume
average_daily_turnover
historical_volatility
beta_to_index
listing_date
active_flag
```

Store historical membership when possible to reduce survivorship bias.

## Universe Configuration Example

```yaml
universe:
  exchange: NSE
  source: nifty_200
  min_avg_daily_turnover: 100000000
  exclude_suspended: true
  exclude_recent_ipos_days: 90
```

---

# 8. Step 2 — Historical OHLCV Persistence

Required raw schema:

```text
symbol
exchange
timestamp
timeframe
open
high
low
close
volume
source
adjusted_flag
ingested_at
```

Recommended types:

```text
symbol         string
timestamp      timestamp[ns, tz]
timeframe      category/string
open           float64
high           float64
low            float64
close          float64
volume         int64/float64
```

## Important Data Rules

- Use exchange-local timezone consistently.
- Handle corporate actions carefully.
- Document whether prices are adjusted or unadjusted.
- Do not mix adjusted and unadjusted history.
- Validate duplicate timestamps.
- Validate missing bars.
- Validate market-session boundaries.
- Validate volume anomalies.

## Storage Layout

```text
data/raw/spot/5m/year=2025/month=01/*.parquet
```

Partitioning may use:

- timeframe
- year
- month

Avoid excessive partition fragmentation by symbol unless required.

---

# 9. Step 3 — Data Quality Validation

Every ingestion run should validate:

```text
open <= high
low <= high
low <= close <= high
low <= open <= high
volume >= 0
timestamp unique per symbol/timeframe
```

Additional checks:

- Unexpected gaps
- Duplicate candles
- Zero-volume bars
- Unusual price jumps
- Market holiday alignment
- Split / bonus / dividend adjustment consistency

Produce a quality report before feature generation.

---

# 10. Step 4 — Timeframe-Specific Feature Replay

Reuse the current runtime TA engine, but apply it historically.

The historical replay must obey:

```text
features_at_T = function(data <= T)
```

Never allow future data into the feature computation.

## Technical Features

### Trend

- EMA 9
- EMA 20
- EMA 50
- EMA 200
- SMA variants
- DMA variants
- EMA slope
- EMA separation

### Momentum

- RSI 14
- ROC
- Bollinger position
- Bollinger width

### Volatility

- ATR 14
- ATR percentile
- True range
- Range expansion

### Volume

- Current volume
- Rolling average volume
- Volume ratio
- Volume z-score
- Volume breakout state

### Price Action

- Swing high
- Swing low
- HH
- HL
- LH
- LL
- BOS
- CHOCH
- FVG
- Support zones
- Resistance zones
- Zone lifecycle
- CPR

---

# 11. Step 5 — Look-Ahead Bias Prevention

This is mandatory.

Examples of leakage risk:

- Confirmed pivots that secretly use future candles
- Support/resistance zones defined with future knowledge
- BOS/CHOCH confirmation using future closes
- Daily feature values accidentally using incomplete future bars
- Labels accidentally entering feature columns

Create dedicated leakage tests.

Example test principle:

```text
Run feature pipeline on first N candles.
Run feature pipeline on first N+100 candles.
Compare features for candles <= N.
They must be identical.
```

If they differ, there is likely future leakage.

---

# 12. Step 6 — Feature Normalization

Avoid absolute-price learning.

Bad:

```text
close = 2480
ema20 = 2425
resistance = 2510
```

Preferred:

```text
distance_from_ema20_pct
ema20_slope_pct
atr_pct
volume_ratio
support_distance_atr
resistance_distance_atr
fvg_size_atr
breakout_distance_atr
```

## Example Calculations

```python
distance_from_ema20_pct = (close - ema20) / ema20
atr_pct = atr / close
volume_ratio = volume / rolling_volume_mean
support_distance_atr = (close - support) / atr
```

Normalize categorical price-action states as explicit values.

Example:

```text
market_structure = HH_HL
bos = BULLISH
choch = NONE
```

---

# 13. Step 7 — Market Context

Each stock observation should carry broader-market context.

Possible features:

```text
index_return_5m
index_return_15m
index_return_1h
index_return_1d
index_trend_5m
index_trend_1h
index_trend_daily
market_breadth
advance_decline_ratio
market_volatility_proxy
```

Optional later:

- India VIX
- Gap context
- Opening range state

---

# 14. Step 8 — Sector Context

Features:

```text
sector_return_5m
sector_return_1h
sector_return_1d
sector_trend
sector_breadth
stock_vs_sector_relative_strength
stock_vs_index_relative_strength
```

Example:

```python
stock_vs_index_rs = stock_return - index_return
stock_vs_sector_rs = stock_return - sector_return
```

---

# 15. Step 9 — Historical Feature Snapshot Schema

Example feature record:

```text
timestamp
symbol
timeframe
sector

# Trend
ema9_distance_pct
ema20_distance_pct
ema50_distance_pct
ema200_distance_pct
ema20_slope
ema50_slope

# Momentum
rsi14
roc
bb_position
bb_width

# Volatility
atr_pct
atr_percentile

# Volume
volume_ratio
volume_zscore

# Price action
structure
bos
choch
fvg_state
support_distance_atr
resistance_distance_atr

# Market context
index_trend
index_return
market_breadth

# Sector context
sector_trend
sector_return
relative_strength_sector
relative_strength_index
```

---

# 16. Step 10 — Intraday Label Generation

Prediction horizons:

```text
15 minutes
30 minutes
60 minutes
120 minutes
```

Store:

```text
future_return_15m
future_return_30m
future_return_60m
future_return_120m
```

Also store path-dependent metrics:

```text
mfe_30m
mae_30m
mfe_60m
mae_60m
mfe_120m
mae_120m
```

Where:

- MFE = Maximum Favorable Excursion
- MAE = Maximum Adverse Excursion

This avoids judging a trade only by terminal return.

---

# 17. Step 11 — Swing Label Generation

Prediction horizons:

```text
1 day
3 days
5 days
10 days
```

Store:

```text
future_return_1d
future_return_3d
future_return_5d
future_return_10d
mfe_5d
mae_5d
mfe_10d
mae_10d
```

---

# 18. Step 12 — Label Design

Start with continuous targets.

Example:

```text
future_return_30m
future_return_5d
```

Then optionally create classes.

Example:

```text
STRONG_BEARISH
BEARISH
NEUTRAL
BULLISH
STRONG_BULLISH
```

Thresholds should be volatility-aware rather than fixed absolute percentages where possible.

Example:

```text
bullish if future_return > 0.5 * ATR-normalized threshold
```

Do not finalize thresholds without empirical distribution analysis.

---

# 18A. Step 12A — KNN Historical-Analog Engine

## Purpose

KNN answers a highly interpretable research question:

> `Across the historical cash-market universe, when the market state looked most like the current state, what happened next?`

KNN is a **non-parametric / lazy-learning** method. Instead of learning a tree, linear equation, or neural-network representation during training, it stores the historical feature examples and performs similarity search at inference time.

KNN should be treated as an **evidence provider**, not as the sole trade-decision engine.

---

## 18A.1 Build a Compact Market Fingerprint

Do not feed the entire 150–300 feature research dataset directly into KNN.

KNN suffers from the **curse of dimensionality**: as feature count increases, distances become less informative and almost every observation can begin to look similarly far away.

Start with approximately **20–40 carefully selected normalized features**.

Suggested fingerprint:

### Trend

```text
ema20_distance_pct
ema50_distance_pct
ema20_slope_normalized
ema50_slope_normalized
ema_spread_pct
adx
```

### Momentum

```text
rsi_14
roc_normalized
momentum_percentile
```

### Volatility

```text
atr_pct
atr_percentile
bb_width_normalized
range_atr_ratio
```

### Volume

```text
volume_ratio
volume_zscore
relative_volume_percentile
```

### Price Action

```text
structure_code
bos_code
choch_code
fvg_distance_atr
support_distance_atr
resistance_distance_atr
breakout_distance_atr
```

### Relative Strength and Context

```text
stock_vs_index_return
stock_vs_sector_return
index_trend_score
sector_trend_score
market_breadth_score
```

The fingerprint definition must be versioned. A KNN index built using `fingerprint_v1` must never be queried using `fingerprint_v2` features.

---

## 18A.2 Scale Features Before Distance Calculation

Scaling is mandatory.

Bad example:

```text
RSI              64
volume       7,500,000
ATR_pct          1.8
EMA_distance    0.018
```

Raw volume would dominate Euclidean distance.

Recommended first scaler:

```python
RobustScaler
```

Why:

- Financial data contains outliers.
- Large volume spikes and volatility shocks are common.
- RobustScaler uses median/interquartile statistics and is less sensitive to extreme values than ordinary standardization.

Persist the scaler together with the KNN model/index.

Never fit the scaler on validation/test/future data.

Correct walk-forward behavior:

```text
TRAIN WINDOW
      |
      +--> fit RobustScaler
      |
      +--> transform training fingerprint
      |
      +--> build KNN reference set

VALIDATION / TEST
      |
      +--> transform using TRAIN scaler only
```

---

## 18A.3 Keep Intraday and Swing KNN Stores Separate

Do not create one universal neighbor database.

### Intraday Analog Store

Context:

```text
5m + 15m + 1h
```

Targets:

```text
+15m
+30m
+60m
+120m
```

### Swing Analog Store

Context:

```text
1h + Daily
```

Targets:

```text
+1d
+3d
+5d
+10d
```

The same current security may have high historical familiarity intraday but low familiarity for swing, or the reverse.

---

## 18A.4 K Is a Hyperparameter — Do Not Hard-Code 10

The social-media example of `10 nearest neighbors` is useful conceptually, but production K must be selected empirically.

Candidate values:

```text
K = 5, 10, 15, 20, 30, 50
```

Evaluate K using validation data and compare:

- classification calibration
- regression MAE
- directional hit rate
- stability across market regimes
- downstream NautilusTrader strategy performance after costs

A value that maximizes raw prediction accuracy but creates unstable turnover or excessive drawdown should not be preferred.

---

## 18A.5 Compare Distance Metrics

Initial candidates:

```text
Euclidean
Manhattan
Cosine distance
```

Do not assume Euclidean is automatically best.

Evaluate the metric using the same walk-forward research protocol.

For the first implementation, use:

```text
RobustScaler + Euclidean distance + distance-weighted KNN
```

as the baseline because it is simple and explainable.

---

## 18A.6 Weighted KNN Instead of Majority-Only Voting

Do not treat a very close neighbor and a weak neighbor equally.

Conceptual historical examples:

```text
Neighbor   Similarity   +30m Return
A             97%          +0.80%
B             95%          +1.10%
C             94%          +0.60%
D             83%          -0.20%
E             78%          -0.70%
```

The first three should contribute more evidence than the last two.

Initial distance weight can be implemented as:

```text
weight_i = 1 / (distance_i + epsilon)
```

Later extended to:

```text
final_weight_i =
    distance_weight_i
    * recency_weight_i
    * regime_weight_i
    * liquidity_quality_weight_i
```

Do not add these weighting dimensions simultaneously without ablation testing. Begin with distance weighting, then prove incremental value from each additional term.

---

## 18A.7 Classification and Regression Outputs

Use KNN in two ways.

### Direction Classification

Example:

```text
P(positive return over next 30m)
P(negative return over next 30m)
```

### Outcome Regression

Predict distributions / summary statistics such as:

```text
expected_return_30m
median_neighbor_return_30m
expected_return_60m
median_mfe_60m
median_mae_60m
```

Regression outputs are preferable to a simplistic `8/10 went up` statement because they provide magnitude and risk context.

---

## 18A.8 Analog Evidence Output Contract

Recommended output:

```json
{
  "horizon": "60m",
  "k": 20,
  "positive_neighbors": 16,
  "negative_neighbors": 4,
  "weighted_positive_probability": 0.82,
  "median_forward_return": 0.0068,
  "mean_forward_return": 0.0061,
  "median_mfe": 0.0119,
  "median_mae": -0.0027,
  "nearest_similarity": 0.97,
  "average_similarity": 0.91,
  "historical_familiarity": "HIGH"
}
```

This evidence can be shown directly to an analyst and consumed by the meta-model.

---

## 18A.9 Historical Familiarity / Out-of-Distribution Detection

One of KNN's most valuable roles is determining whether the current state resembles the historical training distribution.

Example:

```text
nearest_similarity       66%
average_similarity       57%
historical_familiarity   LOW
```

Even if another model is strongly bullish, this can reduce confidence or trigger `NO_TRADE`.

Recommended familiarity states:

```text
HIGH
MEDIUM
LOW
UNKNOWN
```

Thresholds must be derived from validation distributions rather than arbitrarily chosen.

This creates an explainable reason for sitting out:

> `The directional models are positive, but the current market fingerprint is poorly represented in historical data.`

---

## 18A.10 Regime-Conditioned Neighbor Search

Once regime detection is available, compare two KNN approaches.

### Global KNN

Search all historical examples.

### Regime-Conditioned KNN

```text
Current Market
      |
      v
Regime Engine
      |
      v
Current Regime = Trending Bullish
      |
      v
KNN searches only compatible historical regimes
```

This avoids comparing superficially similar indicators from fundamentally different environments, such as a low-volatility trend versus a panic-volatility event.

Do not assume regime-conditioned KNN is superior. Measure it against global KNN out of sample.

---

## 18A.11 Recency Weighting

Markets evolve.

A similar observation from six months ago may deserve more weight than one from ten years ago, but recency should not be applied blindly because older regimes may still be informative.

Possible decay:

```text
recency_weight = exp(-lambda * age_days)
```

Treat `lambda` as a validation parameter.

Always compare:

```text
No recency weighting
vs
Recency weighting
```

before adopting it.

---

## 18A.12 KNN Leakage Controls

This is critical.

For an observation at time `T`, candidate neighbors must come only from the permitted historical training set.

Never allow:

```text
T+1
T+2
future observations
```

to become neighbors during historical simulation.

For walk-forward tests, rebuild or logically constrain the reference set at every training boundary.

Also guard against near-duplicate leakage across timeframes or overlapping samples.

Example danger:

```text
10:30 sample
10:35 sample
10:40 sample
```

may contain nearly identical rolling information. Use embargo/purging rules where required during validation.

---

## 18A.13 KNN Scalability Path

Start simple:

```text
scikit-learn KNeighborsClassifier
scikit-learn KNeighborsRegressor
```

If millions of fingerprints make exact neighbor lookup too slow, benchmark:

```text
BallTree / KDTree where appropriate
Approximate nearest-neighbor index later
```

Do not prematurely introduce distributed vector infrastructure.

At 5-minute horizons, latency requirements are moderate enough that a well-designed local index may be sufficient initially.

---

# 18B. Step 12B — K-Means Market-Regime Discovery Baseline

## Purpose

K-Means answers:

> `What naturally recurring types of market environment exist in the historical data?`

K-Means is unsupervised. It does not know what `TRENDING`, `RANGE_BOUND`, or `HIGH_VOLATILITY` means.

It returns cluster IDs such as:

```text
Cluster 0
Cluster 1
Cluster 2
Cluster 3
```

The research process must inspect each cluster's centroid, distributions, persistence, and future strategy performance before assigning a semantic name.

---

## 18B.1 Regime Feature Set

Keep regime features compact and structurally meaningful.

Suggested baseline:

```text
adx
ema20_slope_normalized
ema50_slope_normalized
ema_spread_pct
atr_percentile
bb_width_normalized
range_atr_ratio
volume_ratio
return_autocorrelation
trend_persistence
market_breadth
index_return_normalized
```

Do not use future returns to form the cluster.

---

## 18B.2 Scale Before Clustering

As with KNN, fit the scaler on training data only.

Recommended baseline:

```text
RobustScaler
    ->
KMeans
```

Persist:

```text
scaler
cluster_centroids
feature_schema
training_window
random_seed
cluster_diagnostics
```

---

## 18B.3 Determine the Number of Clusters Empirically

Do not force six clusters simply because six regime names sound convenient.

Test candidates such as:

```text
K = 3, 4, 5, 6, 7, 8
```

Evaluate using:

- silhouette score
- cluster-size balance
- centroid interpretability
- stability across time windows
- transition stability
- downstream strategy differentiation
- out-of-sample NautilusTrader performance

The most statistically separated cluster count is not necessarily the most useful trading regime definition.

---

## 18B.4 Semantic Labeling of Clusters

Example post-hoc interpretation:

```text
Cluster 0
ADX: high
EMA slope: strongly positive
ATR: moderate
Breadth: positive

=> candidate label: TRENDING_BULLISH
```

```text
Cluster 1
ADX: low
ATR: low
BB width: narrow
Range/ATR: low

=> candidate label: COMPRESSION / LOW_VOLATILITY
```

```text
Cluster 2
ATR: extreme
BB width: wide
Volume: extreme

=> candidate label: HIGH_VOLATILITY
```

Cluster labels must be derived and documented from observed characteristics rather than assumed before fitting.

---

## 18B.5 K-Means Confidence Limitation

Plain K-Means does not naturally provide a probability such as:

```text
Trending 68%
Range 21%
High Vol 11%
```

It assigns the closest centroid.

Distance to centroid can be used as a rough confidence/familiarity measure, but it should not be presented as a calibrated probability.

If probabilistic regime membership is useful, compare against **Gaussian Mixture Models (GMM)**.

---

## 18B.6 Regime Flicker and Persistence

A naïve clustering output could jump rapidly:

```text
10:00 Cluster 1
10:05 Cluster 3
10:10 Cluster 1
10:15 Cluster 4
```

Real market regimes generally exhibit some persistence.

Initial mitigation options:

```text
minimum persistence bars
rolling majority regime
hysteresis threshold
```

Later, compare with an HMM because HMMs explicitly model regime-transition probabilities.

---

## 18B.7 Regime Model Evolution

Recommended research path:

### Phase A — K-Means

Purpose:

- discover regimes
- establish interpretable baseline
- understand feature geometry

### Phase B — Gaussian Mixture Model

Purpose:

- probabilistic cluster membership
- overlapping market states

Example:

```text
Trending       68%
Range          21%
High Vol       11%
```

### Phase C — Hidden Markov Model

Purpose:

- temporal persistence
- regime-transition probabilities

Example:

```text
Trending -> Trending     82%
Trending -> Range        12%
Trending -> High Vol      6%
```

The production regime engine should be selected based on **out-of-sample downstream strategy performance**, not algorithm sophistication.

---

# 19. Step 13 — Regime Engine: K-Means Baseline, then GMM/HMM Comparison

Initial semantic regime vocabulary:

```text
TRENDING_BULLISH
TRENDING_BEARISH
RANGE_BOUND
LOW_VOLATILITY / COMPRESSION
HIGH_VOLATILITY
BREAKOUT / EXPANSION
```

Implementation sequence:

```text
1. Build leakage-safe regime feature matrix
2. Scale with training-only RobustScaler
3. Fit K-Means over candidate cluster counts
4. Inspect cluster diagnostics and centroids
5. Assign semantic labels only after analysis
6. Measure regime persistence
7. Backtest each strategy by discovered regime
8. Compare global vs regime-conditioned KNN
9. Compare K-Means with GMM
10. Compare K-Means/GMM with HMM when enough data exists
11. Select the regime model by out-of-sample usefulness
```

A regime model is useful only if strategy behavior is meaningfully different across its states.

For example:

```text
TRENDING_BULLISH
Momentum            strong
Breakout            strong
Mean Reversion      weak
```

```text
RANGE_BOUND
Momentum            weak
Breakout            weak
Mean Reversion      strong
```

Do not evaluate a regime model only using clustering metrics. Its primary value is whether it improves strategy selection, calibration, risk control, and `NO_TRADE` decisions.

Runtime output contract should support both hard and soft regimes:

```json
{
  "regime_id": 2,
  "regime_label": "TRENDING_BULLISH",
  "model_type": "kmeans",
  "centroid_distance": 0.41,
  "regime_familiarity": "HIGH"
}
```

For a future GMM/HMM implementation:

```json
{
  "regime_label": "TRENDING_BULLISH",
  "probabilities": {
    "TRENDING_BULLISH": 0.68,
    "RANGE_BOUND": 0.21,
    "HIGH_VOLATILITY": 0.11
  }
}
```

---

# 20. Step 14 — Momentum Strategy Model

Question:

> Is current directional momentum likely to continue?

Potential features:

```text
EMA relationship
EMA slope
price/EMA distance
RSI
ROC
volume ratio
HH/HL
BOS
relative strength
market context
sector context
```

Model output:

```text
P(momentum continuation bullish)
P(momentum continuation bearish)
```

Recommended first model:

- XGBoost or LightGBM

---

# 21. Step 15 — Mean-Reversion Strategy Model

Question:

> Has price become sufficiently stretched that reversion is statistically likely?

Potential features:

```text
RSI extreme
EMA distance
Bollinger deviation
ATR-normalized extension
support/resistance distance
volume exhaustion
market regime
```

Important:

Mean reversion should not be applied blindly in strong trends.

Regime context is essential.

---

# 22. Step 16 — Breakout Strategy Model

Question:

> Is the breakout likely to sustain or fail?

Potential features:

```text
support/resistance break
BOS
range compression
Bollinger contraction
volume expansion
ATR expansion
relative strength
index confirmation
sector confirmation
```

Output:

```text
breakout_continuation_probability
breakout_failure_probability
```

---

# 23. Step 17 — Relative-Strength Strategy Model

Question:

> Is the stock outperforming its market and sector strongly enough to support continuation?

Potential features:

```text
stock vs index return
stock vs sector return
multi-timeframe relative strength
sector momentum
index momentum
volume confirmation
```

---

# 24. Step 18 — Independent Strategy Backtests

Do not combine models before testing them individually.

Metrics:

```text
number_of_signals
hit_rate
precision
recall
average_return
average_win
average_loss
expectancy
profit_factor
max_drawdown
Sharpe_ratio
mfe
mae
```

Analyze performance by:

```text
regime
timeframe
sector
liquidity_bucket
volatility_bucket
market_condition
```

---

# 25. Step 19 — Walk-Forward Validation

Do not randomly shuffle market data.

Example:

```text
Train:      2019-2022
Validation: 2023
Test:       2024
```

Then roll forward.

```text
Train: 2019-2023
Test:  2024
```

Next:

```text
Train: 2019-2024
Test:  2025
```

Track model degradation across windows.

---

# 26. Step 20 — Meta Model

Inputs:

```text
regime_state / regime_probability
regime_familiarity
knn_weighted_probability
knn_expected_return
knn_median_mfe
knn_median_mae
knn_average_similarity
knn_historical_familiarity
momentum_score
mean_reversion_score
breakout_score
relative_strength_score
primary_xgboost_or_lightgbm_score
market_strength
sector_strength
volatility
liquidity
```

Question:

> Given this market regime and strategy evidence, which strategy should receive the most trust?

Example:

```text
Regime: Trending Bullish

Momentum:          84%
Breakout:          81%
Mean Reversion:    22%
Relative Strength: 78%
KNN analog:        82%
Analog similarity: 91%
Primary ML:         81%

Final bullish probability: 82%
```

---

# 27. Step 21 — Explicit NO_TRADE State

Do not force every observation into BUY or SELL.

The signal domain should include:

```text
LONG
SHORT / BEARISH
NO_TRADE
```

For a cash-market long-only implementation, it may instead be:

```text
LONG_OPPORTUNITY
AVOID
NO_TRADE
```

`NO_TRADE` should be triggered when:

- Model confidence is weak
- Models disagree materially
- Regime is uncertain
- Liquidity is insufficient
- Risk/reward is poor
- Volatility is abnormal
- Invalidation is too far away
- Market context contradicts the stock signal
- Signal decays before execution
- KNN historical familiarity is low / current fingerprint is out-of-distribution
- Nearest historical analogs are too distant or highly contradictory
- Regime assignment is unstable or unfamiliar

No Trade is an explicit research outcome, not an error condition.

---

# 28. Step 22 — Risk Veto Layer

Risk should have final veto authority.

Example pipeline:

```text
Quant Opportunity
      |
      v
Is signal statistically strong?
      |
      v
Is liquidity acceptable?
      |
      v
Is volatility acceptable?
      |
      v
Is stop/invalidation distance acceptable?
      |
      v
Is expected reward/risk acceptable?
      |
      +------ No ------> NO_TRADE
      |
     Yes
      |
      v
Eligible for simulated execution
```

Potential risk checks:

- Max ATR percentage
- Min average turnover
- Max gap size
- Max spread proxy if available
- Maximum distance to invalidation
- Minimum expected reward/risk
- Maximum portfolio exposure
- Max concurrent positions

---

# 29. Step 23 — NautilusTrader Integration Boundary

NautilusTrader should not replace the research pipeline.

## Research Pipeline Owns

- Raw historical ingestion
- Feature engineering
- Labels
- Model training
- Model selection
- Model registry

## NautilusTrader Owns

- Market event replay
- Strategy lifecycle
- Position lifecycle
- Risk integration
- Portfolio accounting
- Order simulation
- Fill simulation
- Event ordering
- Backtest reporting
- Future live strategy runtime

---

# 30. Step 24 — Nautilus Data Catalog

Use the Parquet-based Nautilus catalog for backtest-compatible market data.

Recommended flow:

```text
Broker Historical OHLCV
        |
        v
Canonical Parquet Store
        |
        +-------------------+
        |                   |
        v                   v
DuckDB Research        Nautilus Catalog
Queries                Backtests
```

Do not maintain two inconsistent copies of truth.

Prefer one canonical data source and a deterministic conversion/catalog process.

Track:

```text
source_file_hash
catalog_version
data_version
schema_version
```

---

# 31. Step 25 — Model Registry

Each trained model should be versioned.

Metadata:

```text
model_name
model_version
strategy_type
timeframe
training_start
training_end
validation_period
feature_schema_version
label_schema_version
hyperparameters
training_code_commit
data_version
metrics
created_at
```

Example model directory:

```text
models/momentum/intraday/v1/
├── model.json
├── metadata.json
├── feature_schema.json
└── metrics.json
```

---

# 32. Step 26 — Nautilus Strategy Inference Flow

Conceptual runtime:

```text
Market Bar Event
     |
     v
Update local rolling state
     |
     v
Generate current features
     |
     +--------------------+
     |                    |
     v                    v
Regime Engine         KNN Fingerprint
K-Means/GMM/HMM       + Analog Search
     |                    |
     v                    v
Strategy Models       Analog Evidence
Momentum/MeanRev/     Probability + Similarity
Breakout/RS           + MFE/MAE + Familiarity
     |                    |
     +----------+---------+
                |
                v
       XGBoost / LightGBM
        Primary Predictor
                |
                v
            Meta-model
                |
                v
        Candidate signal
                |
                v
   Familiarity + Risk veto
                |
                +------> NO_TRADE
                |
                v
        Order simulation
```

Important:

The same feature logic used during training must be used during inference.

Avoid separate implementations.

Create one reusable feature-engine package.

---

# 33. Step 27 — Feature State Inside Nautilus

Do not recompute the full historical dataframe for every incoming bar.

Maintain rolling state.

Examples:

- Rolling EMA state
- Rolling RSI inputs
- Rolling ATR
- Rolling volume averages
- Recent swing points
- Current BOS/CHOCH state
- Current support/resistance zones

Where TA-Lib requires arrays, maintain bounded rolling windows.

Avoid O(N²) repeated full-history calculations.

---

# 34. Step 28 — Strategy Configuration

Example conceptual YAML:

```yaml
strategy:
  name: cash_market_meta_intraday
  timeframe: 5m
  min_probability: 0.72
  min_liquidity_score: 0.70
  allow_no_trade: true

risk:
  max_atr_pct: 0.04
  min_reward_risk: 1.5
  max_positions: 5
  max_position_weight: 0.05

models:
  regime: models/regime/intraday/v1
  momentum: models/momentum/intraday/v1
  breakout: models/breakout/intraday/v1
  mean_reversion: models/mean_reversion/intraday/v1
  relative_strength: models/relative_strength/intraday/v1
  meta: models/meta/intraday/v1
```

---

# 35. Step 29 — Intraday and Swing Strategies in Nautilus

Keep separate strategy instances.

## Intraday Strategy

Consumes:

- 5m bars
- 15m context
- 1h context

Outputs:

- 15m / 30m / 60m / 120m opportunity score

## Swing Strategy

Consumes:

- 1h bars
- Daily context

Outputs:

- 1d / 3d / 5d / 10d opportunity score

Do not force one strategy instance to manage both horizons initially.

---

# 36. Step 30 — Backtest Assumptions

Document all assumptions.

Examples:

- Entry timing
- Next-bar entry vs same-bar entry
- Market vs limit orders
- Slippage
- Brokerage
- Exchange charges
- STT / taxes where applicable
- Position sizing
- Stop-loss execution
- Gap behavior
- End-of-day exits
- Overnight holding rules

Backtest results without realistic costs are not decision-grade.

---

# 37. Step 31 — Transaction Cost Model

For every simulated trade, account for:

- Brokerage
- Exchange transaction charges
- STT
- GST
- SEBI charges
- Stamp duty
- Slippage assumptions

Keep cost assumptions configurable.

Example:

```yaml
costs:
  brokerage_model: configurable
  slippage_bps: 5
  include_taxes: true
```

---

# 38. Step 32 — Opportunity Ranking

Run inference across the whole universe.

Example result:

```text
INTRADAY

1. Security A
   Regime: Trending Bullish
   Strategy: Breakout
   Probability: 84%

2. Security B
   Regime: Range Bound
   Strategy: Mean Reversion
   Probability: 78%
```

Separate swing ranking:

```text
SWING

1. Security D
   Strategy: Momentum
   Probability: 79%
```

---

# 39. Step 33 — Scanner Architecture

```text
Cash Market Universe
       |
       v
Latest Feature State
       |
       v
Regime Classifier
       |
       v
Strategy Models
       |
       v
Meta Model
       |
       v
Risk Pre-Filter
       |
       v
Ranking Engine
       |
       +--------> Intraday Ranking
       |
       +--------> Swing Ranking
```

---

# 40. Step 34 — Testing Strategy

## Unit Tests

Test:

- Indicators
- Normalization
- Label generation
- MFE/MAE
- Regime logic
- Strategy scoring
- Risk rules

## Leakage Tests

Test that future data cannot alter historical features.

## Integration Tests

Test:

- Broker data -> Parquet
- Parquet -> feature pipeline
- Feature pipeline -> model
- Model -> Nautilus strategy
- Nautilus strategy -> simulated trade

## Regression Tests

Store known expected outputs for fixed historical windows.

If implementation changes, compare results.

---

# 41. Step 35 — Reproducibility

Every experiment should log:

```text
git_commit
data_version
feature_schema_version
label_schema_version
model_config
random_seed
training_period
validation_period
package_versions
```

Use lock files for Python dependencies.

Never trust a model result that cannot be reproduced.

---

# 42. Step 36 — Observability

Log every signal decision.

Example audit record:

```json
{
  "timestamp": "2026-08-25T10:30:00+05:30",
  "symbol": "XYZ",
  "timeframe": "5m",
  "regime": "TRENDING_BULLISH",
  "momentum_score": 0.82,
  "breakout_score": 0.79,
  "mean_reversion_score": 0.18,
  "relative_strength_score": 0.76,
  "meta_probability": 0.81,
  "risk_pass": true,
  "decision": "LONG_OPPORTUNITY"
}
```

For `NO_TRADE`, log the reason.

Example:

```json
{
  "decision": "NO_TRADE",
  "reason": "RISK_REWARD_BELOW_THRESHOLD"
}
```

---

# 43. Step 37 — Failure Modes to Guard Against

Key risks:

- Look-ahead bias
- Survivorship bias
- Overfitting
- Feature leakage
- Regime overfitting
- Corporate-action errors
- Missing candles
- Duplicate candles
- Stale features
- Incorrect market-session alignment
- Model drift
- Unrealistic slippage
- Unrealistic fill assumptions
- Strategy crowding in backtests
- Using future index/sector membership
- Random train/test split
- Hyperparameter tuning on the final test period

Each should have explicit tests or controls.

---

# 44. Step 38 — SEBI RA-Oriented Research Controls

For a research-support system, retain a traceable research chain.

For each signal store:

- Timestamp
- Security
- Timeframe
- Source data version
- Feature snapshot
- Regime
- Strategy scores
- Model versions
- Final probability
- Risk checks
- Decision
- No-trade reason if applicable

This makes the process explainable and auditable.

The system should not present a signal as:

> `AI says buy.`

It should present:

> `The current regime is X, momentum score is Y, breakout score is Z, historical meta-model probability is P, risk checks passed/failed.`

---

# 45. Step 39 — Live Transition Strategy

Do not move directly from offline model to live order placement.

Recommended progression:

```text
Historical Backtest
      |
      v
Walk-Forward Validation
      |
      v
Out-of-Sample Backtest
      |
      v
Paper Trading
      |
      v
Shadow Live Signals
      |
      v
Small Capital Pilot
      |
      v
Controlled Production
```

---

# 46. Step 40 — Existing Broker Integration

Do not rewrite the existing broker integration immediately.

Initial architecture:

```text
Existing Broker/Data API
        |
        v
Historical/Live Data Adapter
        |
        v
Canonical Internal Schema
        |
        +----------> Research Pipeline
        |
        +----------> Nautilus Catalog
```

Later, if required, build a formal Nautilus adapter for:

- Market data
- Live execution
- Account state
- Orders
- Positions

Only do this after the strategy is validated.

---

# 47. Step 41 — Adapter Interface Design

Create a broker-independent interface now.

Conceptual methods:

```python
class MarketDataProvider:
    def get_historical_bars(...): ...
    def stream_bars(...): ...
    def get_instruments(...): ...
```

This prevents strategy code from becoming tightly coupled to one broker.

---

# 48. Step 42 — Model Inference Interface

Create a common interface.

```python
class StrategyModel:
    def predict_proba(self, features): ...
```

Possible implementations:

```text
MomentumModel
MeanReversionModel
BreakoutModel
RelativeStrengthModel
MetaModel
```

Nautilus should depend on this interface, not on XGBoost directly.

---

# 49. Step 43 — Feature Interface

Create a single shared feature engine.

```python
class FeatureEngine:
    def update(self, bar): ...
    def snapshot(self): ...
```

Both:

- historical training
- Nautilus live/backtest inference

must use the same logic.

This minimizes training/serving skew.

---

# 50. Step 44 — Configuration-Driven Development

Do not hard-code strategy thresholds.

Store in config:

- Timeframes
- Lookback windows
- Feature switches
- Label horizons
- Model paths
- Probability thresholds
- Risk constraints
- Cost assumptions

This makes backtests reproducible.

---

# 51. Step 45 — First Development Milestone

Do not begin Nautilus strategy coding before the historical dataset is trustworthy.

First milestone:

```text
1. Define liquid cash-market universe
2. Backfill historical OHLCV
3. Persist 5m / 15m / 1h / Daily separately
4. Validate data quality
5. Replay current TA engine historically
6. Add price-action state
7. Normalize features
8. Add sector/index context
9. Generate intraday labels
10. Generate swing labels
11. Run leakage tests
```

Deliverable:

> A clean, reproducible historical training dataset.

---

# 52. Step 46 — Second Development Milestone

```text
1. Define compact KNN market fingerprint (20-40 features)
2. Fit training-only RobustScaler
3. Build intraday KNN analog reference set
4. Build swing KNN analog reference set
5. Evaluate K values and distance metrics
6. Add distance-weighted analog probability and return/MFE/MAE summaries
7. Add historical-familiarity score and OOD/NO_TRADE thresholds
8. Fit K-Means regime baseline across candidate cluster counts
9. Diagnose and semantically label discovered clusters
10. Measure regime persistence and strategy differentiation
11. Train momentum model
12. Train mean-reversion model
13. Train breakout model
14. Train relative-strength model
15. Train XGBoost/LightGBM primary structured baseline
16. Compare rule strategies vs KNN vs XGBoost/LightGBM
17. Evaluate each independently using walk-forward validation
```

Deliverable:

> Validated analog + regime + strategy-model suite with an interpretable KNN baseline and K-Means regime baseline.

---

# 53. Step 47 — Third Development Milestone

```text
1. Add KNN analog evidence to the meta-model
2. Add regime state / regime familiarity to the meta-model
3. Add XGBoost/LightGBM probability to the meta-model
4. Build meta-model
5. Add explicit NO_TRADE state
6. Add low historical-familiarity / out-of-distribution veto
7. Add risk veto
8. Produce intraday ranking
9. Produce swing ranking
10. Validate universe-wide results
11. Run ablation tests: remove KNN, remove regime, remove each strategy model
12. Keep only components that improve out-of-sample results
```

Deliverable:

> Generic opportunity discovery engine.

---

# 54. Step 48 — Fourth Development Milestone: NautilusTrader

```text
1. Install NautilusTrader
2. Create Nautilus-compatible instrument definitions
3. Populate ParquetDataCatalog
4. Create backtest configuration
5. Create strategy wrapper
6. Load trained models
7. Feed market events into shared FeatureEngine
8. Run model inference
9. Apply meta-model
10. Apply risk veto
11. Submit simulated orders
12. Capture fills
13. Capture portfolio state
14. Add transaction costs
15. Generate performance report
```

Deliverable:

> Event-driven realistic backtesting environment.

---

# 55. Step 49 — Fifth Development Milestone

```text
1. Compare vector-style research results vs Nautilus execution results
2. Analyze slippage impact
3. Analyze fill assumptions
4. Analyze signal latency
5. Analyze risk veto impact
6. Analyze no-trade impact
7. Adjust strategy thresholds only on validation data
8. Re-run out-of-sample tests
9. Compare global KNN vs regime-conditioned KNN
10. Compare K-Means vs GMM regime inference
11. Introduce HMM only if temporal regime persistence adds measurable value
12. Re-run ablation and execution-aware tests
```

Deliverable:

> Execution-aware validated strategy.

---

# 56. Step 50 — Sixth Development Milestone

```text
1. Paper trading
2. Shadow live signal generation
3. Monitor model drift
4. Monitor market regime distribution drift
5. Monitor feature drift
6. Compare expected vs realized outcomes
```

Deliverable:

> Production-readiness evidence.

---

# 57. Optional Later LLM Layer

Only after the quantitative engine works.

Possible LLM responsibilities:

- Explain why a signal was generated
- Summarize model evidence
- Explain `NO_TRADE`
- Compare intraday vs swing view
- Later ingest news/earnings/fundamentals
- Research orchestration

Do not let the LLM calculate core technicals or override risk controls.

Example:

```text
Quant Engine
     |
     v
Structured Evidence
     |
     v
Local LLM
     |
     v
Human-Readable Research Explanation
```

---

# 58. End-State Architecture

```text
                    CASH MARKET UNIVERSE
                           |
                           v
                  MARKET DATA PROVIDER
                           |
                           v
                 CANONICAL DATA SCHEMA
                           |
             +-------------+-------------+
             |                           |
             v                           v
     PARQUET / DUCKDB             NAUTILUS CATALOG
             |                           |
             v                           |
      FEATURE PIPELINE                   |
             |                           |
      +------+-------+                   |
      |              |                   |
      v              v                   |
 REGIME ENGINE    KNN ANALOG ENGINE      |
K-Means/GMM/HMM   Similarity/Familiarity |
      |              |                   |
      +------+-------+                   |
             |                           |
             v                           |
      STRATEGY MODELS                    |
   Momentum / MeanRev / Breakout / RS   |
             |                           |
             v                           |
     XGBOOST / LIGHTGBM                  |
       Primary Predictor                 |
             |                           |
             v                           |
         META MODEL                      |
             |                           |
             +-------------+-------------+
                           |
                           v
                   NAUTILUS STRATEGY
                           |
                           v
                       RISK VETO
                           |
                +----------+----------+
                |                     |
                v                     v
             TRADE                NO_TRADE
                |
                v
        EVENT-DRIVEN EXECUTION
                |
                v
             PORTFOLIO
                |
                v
        PERFORMANCE / AUDIT
```

---

# 58A. KNN + Regime Implementation Checklist

Use this as the concrete coding checklist for the newly added components.

## KNN Analog Engine

```text
[ ] Select 20-40 market-fingerprint features
[ ] Create versioned fingerprint schema
[ ] Implement training-only RobustScaler fit
[ ] Persist scaler artifact
[ ] Build separate intraday and swing reference datasets
[ ] Implement KNeighborsClassifier baseline
[ ] Implement KNeighborsRegressor baseline
[ ] Compare K = 5/10/15/20/30/50
[ ] Compare Euclidean / Manhattan / cosine distance where applicable
[ ] Implement distance-weighted neighbor voting
[ ] Calculate neighbor return distribution
[ ] Calculate neighbor MFE/MAE distribution
[ ] Calculate nearest and average similarity
[ ] Derive historical-familiarity score on validation data
[ ] Implement LOW familiarity => candidate NO_TRADE veto
[ ] Add regime-conditioned neighbor search experiment
[ ] Add recency weighting only after baseline validation
[ ] Add walk-forward neighbor-set restrictions
[ ] Add purging/embargo checks for overlapping samples
[ ] Add unit tests for distance/scaling/weighting
[ ] Add regression tests for deterministic neighbor retrieval
[ ] Measure lookup latency across full universe
```

## K-Means / Regime Engine

```text
[ ] Select compact regime feature set
[ ] Fit training-only RobustScaler
[ ] Evaluate K = 3..8 clusters
[ ] Record silhouette score
[ ] Record cluster-size distribution
[ ] Record centroid statistics
[ ] Measure cluster stability across train windows
[ ] Assign semantic labels after centroid analysis
[ ] Measure regime persistence
[ ] Backtest each strategy by regime
[ ] Compare global KNN vs regime-conditioned KNN
[ ] Add minimum-persistence/hysteresis baseline if regime flicker is excessive
[ ] Compare K-Means with GMM
[ ] Add GMM probabilities if soft regime membership helps
[ ] Evaluate HMM only after baseline is stable
[ ] Select production regime model by downstream OOS performance
```

## Meta-Model / NO_TRADE Integration

```text
[ ] Add KNN directional probability
[ ] Add KNN expected return
[ ] Add KNN median MFE/MAE
[ ] Add KNN average similarity
[ ] Add KNN familiarity category
[ ] Add regime ID / probability
[ ] Add regime familiarity
[ ] Add momentum score
[ ] Add mean-reversion score
[ ] Add breakout score
[ ] Add relative-strength score
[ ] Add XGBoost/LightGBM primary probability
[ ] Train meta-model using only training folds
[ ] Calibrate probabilities
[ ] Implement disagreement thresholds
[ ] Implement historical unfamiliarity veto
[ ] Implement risk veto after model decision
[ ] Run ablation tests for every evidence source
[ ] Validate through NautilusTrader including costs/slippage
```

---

# 59. Guiding Principle

The system should not ask:

> `Should I buy this stock?`

It should answer:

> `What market regime are we in, how familiar is the current setup compared with history, which quantitative strategy currently has the strongest historical edge, over what horizon, with what probability, and does the opportunity still pass execution and risk constraints?`

That keeps the platform:

- Security agnostic
- Cash-market focused
- Multi-timeframe
- Explainable
- Backtestable
- Auditable
- Extensible
- Suitable for intraday and swing research
- Ready for realistic event-driven validation through NautilusTrader



---

# 60. Final Model-Role Summary

Each model has one clearly defined responsibility:

| Component | Core Question | Role |
|---|---|---|
| K-Means | What kind of market state is this? | Initial unsupervised regime discovery |
| GMM | Could the market belong partly to multiple regimes? | Probabilistic regime membership later |
| HMM | How persistent is the regime and how does it transition? | Temporal regime modeling later |
| KNN | Have we seen a sufficiently similar setup before? | Historical analog + familiarity evidence |
| Momentum model | Is directional momentum likely to continue? | Specialized strategy evidence |
| Mean-reversion model | Is price statistically stretched? | Specialized strategy evidence |
| Breakout model | Is a range/level break likely to sustain? | Specialized strategy evidence |
| Relative-strength model | Is the security outperforming market/sector? | Specialized strategy evidence |
| XGBoost / LightGBM | Given all structured features, what is the likely outcome? | Primary structured prediction baseline |
| Meta-model | How should all evidence be combined? | Final probability / strategy weighting |
| Risk veto | Is the opportunity actually tradable under constraints? | Final authority before execution |
| NautilusTrader | Does the signal survive realistic event-driven execution? | Backtesting, risk, portfolio, execution simulation |
| LLM later | How should the evidence be explained/synthesized? | Research explanation, not raw price prediction |

The system should keep a component only if it demonstrates **incremental out-of-sample value** after realistic transaction costs and execution assumptions.
