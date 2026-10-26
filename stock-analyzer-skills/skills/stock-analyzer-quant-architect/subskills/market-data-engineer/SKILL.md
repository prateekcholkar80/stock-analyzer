---
name: market-data-engineer
description: Financial market data engineering specialist for Stock Analyzer. Owns OHLCV ingestion, session-aware validation, timezone normalization, timeframe integrity, corporate-action handling, storage, lineage and data quality.
metadata:
  version: 1.0.0
---

# Market Data Engineer

## Role

Treat market data as a governed product. Bad data can create false alpha, so ingestion and validation are quant-critical components.

## Canonical candle

Preserve at minimum:

- symbol;
- exchange;
- timestamp;
- timezone;
- timeframe;
- open/high/low/close;
- volume;
- provider;
- ingestion timestamp;
- adjustment status if relevant.

For Indian equities normalize analytical time handling to `Asia/Kolkata`.

## Validation

Check:

- `low <= open <= high`;
- `low <= close <= high`;
- `high >= low`;
- volume non-negative;
- timestamps ordered;
- duplicate candles;
- missing intervals;
- off-session candles;
- holidays;
- unexpected market-session gaps;
- timezone mistakes;
- schema changes;
- provider anomalies.

Do not silently interpolate missing market data.

## Timeframe integrity

Never discard the source timeframe. Store/generate timeframe-specific datasets or columns explicitly.

When resampling, define:

- session boundary;
- candle label/closed convention;
- timezone;
- treatment of incomplete final bars.

Incomplete candles must not contaminate historical features.

## Corporate actions

When conducting long-horizon equity research, explicitly decide whether data is adjusted for splits/dividends and ensure features, returns and benchmarks use a consistent convention.

## Storage

Prefer research-friendly columnar storage:

- Parquet;
- DuckDB;
- PyArrow.

Keep repository interfaces database-agnostic so PostgreSQL/Timescale/data-lake systems can be added later.

## Lineage

Every dataset should be traceable to:

- source/provider;
- ingestion time;
- query/range;
- transformation version;
- adjustment policy;
- validation result;
- dataset version/checksum when practical.

## Data contracts

Define explicit schemas for:

- raw candles;
- normalized candles;
- features;
- labels;
- regime outputs;
- backtest events;
- model predictions.

Reject unexpected breaking schema changes rather than silently coercing them.
