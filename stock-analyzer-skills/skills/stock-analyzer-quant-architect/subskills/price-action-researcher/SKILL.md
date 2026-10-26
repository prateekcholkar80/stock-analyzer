---
name: price-action-researcher
description: Systematic price-action specialist for Stock Analyzer. Translates visual market structure, swings, BOS/CHOCH, support/resistance zones, FVGs and liquidity sweeps into deterministic, timestamp-safe features suitable for backtesting and ML.
metadata:
  version: 1.0.0
---

# Price Action Researcher

## Role

Convert discretionary chart concepts into deterministic, testable event definitions. Never rely on vague visual interpretation.

## Confirmed swings

If a pivot requires `N` candles to the right, it becomes known only after those candles close.

Track at least:

- `pivot_timestamp`;
- `confirmation_timestamp`;
- `pivot_price`;
- `pivot_type`;
- `source_timeframe`.

Historical replay must not use the pivot before confirmation.

## Structure

Model:

- HH / HL / LH / LL;
- bullish/bearish continuation;
- BOS;
- CHOCH;
- failed BOS;
- structure invalidation.

BOS/CHOCH definitions must identify which confirmed structural level was broken and whether the rule uses wick, close or displacement confirmation.

## Support/resistance zones

Use zones rather than exact prices. Suggested lifecycle:

- CREATED
- ACTIVE
- TESTED
- WEAKENED
- BROKEN
- FLIPPED
- INVALIDATED

Useful attributes:

- zone_low/high;
- source_timeframe;
- creation timestamp;
- touch count;
- rejection strength;
- volume at creation;
- age;
- distance in ATR units;
- break/flip timestamps.

Do not assume repeated touches always strengthen or always weaken a zone; test both hypotheses.

## Fair Value Gaps

Use deterministic three-candle rules. Example bullish candidate:

`candle_1.high < candle_3.low`

Track lifecycle:

- CREATED
- PARTIALLY_FILLED
- FILLED
- INVALIDATED

FVG presence is a feature, not automatically a trade signal.

## Liquidity sweeps

Candidate evidence:

- prior confirmed swing high/low;
- equal highs/lows;
- prior-day/session extremes;
- wick penetration;
- close back inside;
- volume expansion;
- displacement afterward.

Distinguish:

- sweep;
- true breakout;
- failed breakout;
- random wick.

A useful bullish sweep hypothesis may require price below a prior low, close/reclaim above it, then bullish displacement within a defined number of candles.

## Multi-timeframe context

Price-action features must preserve origin timeframe. Example:

- 5m sweep of 15m swing low;
- 15m breakout into 1h resistance;
- 1h structure aligned with daily trend.

Align only using information available at the lower-timeframe timestamp.

## Required tests

Maintain synthetic/golden scenarios for:

- pivot confirmation;
- HH/HL/LH/LL transitions;
- BOS/CHOCH;
- FVG creation/fill;
- zone break/flip;
- sweep vs breakout;
- gap/open edge cases.
