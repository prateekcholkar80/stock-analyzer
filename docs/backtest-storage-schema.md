# Backtest Storage Schema

Jarvis stores backtests in DuckDB schema version 2. The design has two layers:

- Immutable JSON envelopes preserve the exact validated domain aggregate for replay, audit, and compatibility.
- Normalized relations support dashboard queries, comparisons, charts, attribution, and model evaluation without repeatedly decoding large JSON documents.

The application continues to depend on the database-neutral storage ports. DuckDB is the current adapter, not a domain dependency.

## Data lineage

Each archived run follows this lineage:

`instrument -> market dataset -> candles -> backtest run -> strategy configuration -> evaluations -> trades/equity/performance`

Identifiers and SHA-256 fingerprints make the lineage reproducible:

- `instrument_id` identifies a provider/exchange/symbol-token combination. The display symbol is stored separately because symbols can change.
- `dataset_id` identifies the exact historical series and links every normalized candle.
- `market_fingerprint` proves which market aggregate the run consumed.
- `strategy_configuration_id` is content-addressed from all technical, planning, execution, walk-forward, and weighting parameters.
- `result_fingerprint` proves the complete stored backtest result.

## Relations

| Table | Purpose | Important attributes |
| --- | --- | --- |
| `jarvis_storage_metadata` | Adapter schema control | `schema_version` |
| `jarvis_instruments` | Stable instrument identity | source, exchange, symbol token, current symbol, first/last seen |
| `jarvis_instrument_symbols` | Symbol observations by dataset | instrument, symbol, observed time, dataset |
| `jarvis_market_series` | Immutable market dataset envelope | identity, interval, fingerprint, summary JSON, payload JSON |
| `jarvis_market_candles` | Chart-ready OHLCV history | dataset, ordered candle index, timestamp, OHLCV |
| `jarvis_strategy_configurations` | Reproducible strategy snapshot | agent/evaluator/planner/engine IDs, component fingerprints, complete configuration JSON |
| `jarvis_strategy_weights` | Queryable configured weights | strategy configuration, signal category, weight |
| `jarvis_backtest_runs` | Run header and lineage | market dataset, instrument, strategy, engine, warmup, walk-forward config, immutable result JSON |
| `jarvis_backtest_evaluations` | What Jarvis knew and concluded at each scheduled historical date | outcome, capital, technical/planning/execution decisions, signal metrics, thresholds, rationale |
| `jarvis_backtest_evaluation_categories` | Per-date attribution | category weight, raw category score, weighted score |
| `jarvis_backtest_signal_evidence` | Evidence available at each date | source, direction, strength, observed/available times, explanation, observed values and parameters |
| `jarvis_backtest_signal_contributions` | Score construction | signed contribution, category weight, weighted contribution |
| `jarvis_backtest_trades` | Complete simulated trade ledger | planning/execution lineage, entry/exit, stop/target, quantity, costs, P&L and realized R |
| `jarvis_backtest_equity_points` | Dashboard equity and drawdown curves | candle, close, equity, running peak, drawdown, active trade |
| `jarvis_backtest_performance` | Run-level metrics | returns, counts, win rate, profit factor, expectancy, costs, drawdown, exposure and streaks |
| `jarvis_backtest_performance_segments` | Comparison breakdowns | metrics by trade direction and signal stance |

## Strategy snapshot

The strategy snapshot includes every validated setting that can change the result:

- technical indicator and price-action parameters;
- trend, momentum, volatility, volume, and price-action weights;
- trade-planning and 1:2-to-1:3 reward/risk rules;
- historical execution assumptions, including capital, sizing, slippage, fees, target selection, and end-of-data behavior;
- walk-forward warmup, stride, date bounds, and execution configuration.

The combined fingerprint is recalculated during model validation. Changing a weight or parameter without changing the fingerprint is rejected.

## Temporal safety

Each evaluation stores the completed candle used for the decision and its full technical profile. Evidence has separate `observed_at` and `available_at` timestamps. This preserves the historical information boundary and makes look-ahead checks auditable. Timestamps are timezone-aware; DuckDB stores them as `TIMESTAMPTZ`, while the domain retains their original offset semantics.

## Migration and deletion

Opening a version-1 DuckDB database performs one transaction that:

1. creates the version-2 relations and columns;
2. validates each existing immutable payload;
3. backfills instruments, candles, run attributes, equity, performance, and any available evaluation detail;
4. updates the schema version only after the backfill succeeds.

The original JSON is not rewritten. A failure rolls back the migration. Explicit run and market-dataset deletion also removes their normalized dependent rows; reusable strategy configurations and instrument identities remain available for other runs.

## Example dashboard queries

Equity and drawdown chart:

```sql
SELECT point_at, equity, drawdown_percentage
FROM jarvis_backtest_equity_points
WHERE run_id = ?
ORDER BY candle_index;
```

Signal-category attribution over time:

```sql
SELECT e.candle_at, c.category, c.weight,
       c.category_score, c.weighted_score
FROM jarvis_backtest_evaluations AS e
JOIN jarvis_backtest_evaluation_categories AS c
  USING (run_id, candle_index)
WHERE e.run_id = ?
ORDER BY e.candle_at, c.category;
```

Trade outcomes with risk-adjusted result:

```sql
SELECT direction, signal_stance, outcome, entry_at, exit_at,
       entry_price, stop_loss_price, target_price,
       net_pnl, realized_r_multiple
FROM jarvis_backtest_trades
WHERE run_id = ?
ORDER BY entry_at;
```
