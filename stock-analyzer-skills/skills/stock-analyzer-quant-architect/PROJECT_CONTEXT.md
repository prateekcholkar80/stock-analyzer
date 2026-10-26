# Stock Analyzer — Shared Project Context

This file contains architectural decisions that every skill must treat as project constraints unless explicitly changed by the user.

## Product objective

Build Jarvis as an enterprise-grade, broker-agnostic and database-agnostic
equity research platform for Indian cash-market swing analysis. The product is
research and decision support: it does not place orders, promise returns or
mutate portfolios. Futures, options and genuine order-flow feeds are outside
the current scope and must not be inferred from OHLCV data.

## Core modeling decision

Do **not** train one generic buy/sell model on all market data. Use a **regime-aware multi-strategy quant stack**.

Target architecture:

```text
Historical Broker Market Data + Current Broker Observation
        ↓
Data Validation + Normalization
        ↓
1-Hour Archive → Completed Daily and Weekly Candles
        ↓
Point-in-Time Technical and Price-Action Evidence
        ↓
Daily Analyst + Weekly Analyst
        ↓
Judge-approved Evidence Package
        ↓
Bull / Bear Evidence Debate
        ↓
Senior Judge Verdict + Deterministic Risk/Target Gate
        ↓
Jarvis Grounded CEO-Style Explanation
```

Cached structured fundamentals may independently feed a Fundamental Analyst.
They remain secondary research evidence unless their lineage satisfies the
project's decision-grade source policy.

## Timeframes

The broker archive is incrementally maintained at one-hour resolution and is
aggregated into completed daily and weekly candles in `Asia/Kolkata`. Daily
and weekly analyses execute independently and retain timeframe-qualified
evidence identifiers. Weekends, exchange holidays and incomplete periods must
not be fabricated or silently interpolated. Cross-timeframe conclusions may
use only information available at the evaluation timestamp.

## Initial indicators

- EMA
- SMA
- EMA-20 and EMA-50 / configured moving-average evidence
- Bollinger Bands
- RSI-14
- MACD, stochastic and ADX
- Volume, relative volume and OBV
- CPR
- ATR / volatility features

## Price-action concepts

- Confirmed swing highs/lows
- HH / HL / LH / LL
- BOS
- CHOCH
- Support/resistance zones
- Zone lifecycle events
- Fair Value Gaps
- Liquidity sweeps
- Failed breakouts
- Breakout/retest
- Compression/expansion

## Current platform direction

- Python-first implementation.
- Angel One currently acts as a market-data adapter, not a hard architectural dependency.
- Tijori currently acts as an optional, user-owned secondary fundamental-data
  adapter behind provider-neutral gateways. It is called only for a cache
  miss, expiry or explicit company-wide fundamental refresh.
- DuckDB is the current local adapter; storage contracts remain pluggable.
- Preserve ability to move to PostgreSQL/Timescale/data-lake infrastructure later.
- Broker, LLM, storage, speech and fundamental providers remain behind
  provider-neutral ports.
- LLMs orchestrate, debate and explain immutable evidence. They do not
  calculate indicators, alter evidence or become the raw price-prediction
  engine.
- The system is long-entry focused: bearish or neutral evidence produces
  `NO_TRADE`, not a short recommendation.

## Evidence hierarchy

Market structure and price context are primary evidence. Participation, trend,
momentum, CPR and candlesticks are correlated supporting families and must not
be counted as equal independent votes. Entry confirmation and realistic space
to structural 1:2 and preferably 1:3 reward-to-risk targets are release gates.
Being a financially healthy company, having a bullish trend and having an
actionable swing entry are separate conclusions.

## Fundamental scope

The current cache can retain complete, metadata-rich JSON documents for Growth
Table, Balance Sheet, Profit and Loss, Cash Flow, Ratios and Quarterly Results,
plus Peer Comparison and Benchmarking Financials. Consolidated and standalone
bases remain distinct where applicable. Annual-report ingestion, qualitative
management assessment and durable-moat conclusions are not yet implemented.

## Non-negotiable research safeguards

Always defend against:

- Look-ahead bias
- Data leakage
- Survivorship bias
- Selection bias
- Repainting
- Overfitting
- Parameter mining
- Unrealistic fills
- Ignoring costs and slippage
- Train/test contamination

## Definition of success

A strategy/model is not successful because it has a high historical return. It should demonstrate stability across time, regimes and instruments, survive realistic costs, and remain reproducible out of sample.

An agent or skill is not successful because one answer sounded convincing.
Skill improvements must be versioned, evidence-backed, independently tested,
human-approved and reversible. The active production skill must never learn by
silently rewriting itself from conversations or realized price outcomes.
