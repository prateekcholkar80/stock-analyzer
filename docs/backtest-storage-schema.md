# Backtest Storage Schema

Jarvis stores market, backtest, and debate research in DuckDB schema version 3.
The design has two layers:

- Immutable JSON envelopes preserve the exact validated domain aggregate for replay, audit, and compatibility.
- Normalized relations support dashboard queries, comparisons, charts, attribution, and model evaluation without repeatedly decoding large JSON documents.

The application continues to depend on the database-neutral storage ports. DuckDB is the current adapter, not a domain dependency.

## Data lineage

Each archived run follows this lineage:

```text
instrument -> market dataset -> candles
                           |-> backtest run -> strategy configuration
                           |                -> evaluations
                           |                -> trades/equity/performance
                           |
                           +-> technical result -> debate transcript/verdict
                                              -> signal signature
```

Identifiers and SHA-256 fingerprints make the lineage reproducible:

- `instrument_id` identifies a provider/exchange/symbol-token combination. The display symbol is stored separately because symbols can change.
- `dataset_id` identifies the exact historical series and links every normalized candle.
- `market_fingerprint` proves which market aggregate the run consumed.
- `strategy_configuration_id` is content-addressed from all technical, planning, execution, walk-forward, and weighting parameters.
- `result_fingerprint` proves the complete stored backtest result.
- `technical_fingerprint` binds a debate to the exact Jarvis-approved
  technical result consumed by Bull, Bear, and Judge.
- `debate_signal_signature` stores deterministic category/direction tokens used
  to retrieve comparable precedent without embeddings or semantic guesswork.

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
| `jarvis_debate_runs` | Immutable debate archive and query header | result and technical fingerprints, instrument identity, interval, winner, summary JSON, complete transcript/verdict JSON |
| `jarvis_debate_signal_signature` | Deterministic precedent lookup | debate run and normalized signal token |

## Strategy snapshot

The strategy snapshot includes every validated setting that can change the result:

- technical indicator and price-action parameters;
- trend, momentum, volatility, volume, and price-action weights;
- trade-planning and 1:2-to-1:3 reward/risk rules;
- historical execution assumptions, including capital, sizing, slippage, fees, target selection, and end-of-data behavior;
- walk-forward warmup, stride, date bounds, and execution configuration.

The combined fingerprint is recalculated during model validation. Changing a
weight or parameter without changing the fingerprint is rejected. The current
planner and execution engine can model both long and short directions, and the
normalized trade/performance tables retain that direction explicitly.

## Debate archive and precedent recall

`jarvis_debate_runs` stores the exact validated `AgenticDebateResult` as an
immutable JSON envelope alongside queryable identity and outcome columns. Its
`technical_fingerprint` proves which approved technical evidence entered the
debate. `result_fingerprint` detects any transcript or verdict change.

`jarvis_debate_signal_signature` stores tokens computed from moderate-or-
stronger, non-neutral evidence in the form `category:direction`. Similar-run
queries rank prior debates by shared-token overlap, with optional instrument and
interval filtering. Retrieved precedent is supplied only to Bull and Bear as
context with no evidentiary weight. The Judge cannot receive precedent through
its method contract and must decide solely from current evidence and the current
transcript.

Saving identical debate content is idempotent. Saving conflicting content under
an existing run identity raises `StorageConflictError`. Deleting a debate run
also deletes its signal-signature rows.

## Temporal safety

Each evaluation stores the completed candle used for the decision and its full technical profile. Evidence has separate `observed_at` and `available_at` timestamps. This preserves the historical information boundary and makes look-ahead checks auditable. Timestamps are timezone-aware; DuckDB stores them as `TIMESTAMPTZ`, while the domain retains their original offset semantics.

## Migration and deletion

Opening a supported version-1 or version-2 DuckDB database upgrades it to
version 3 in one transaction that:

1. creates all missing normalized and debate relations/columns;
2. validates each existing immutable payload;
3. backfills instruments, candles, run attributes, equity, performance, and any available evaluation detail;
4. creates debate indexes and signal-signature support;
5. updates the schema version only after the complete migration succeeds.

The original JSON is not rewritten. A failure rolls back the migration. An
unknown/newer schema version is rejected instead of being guessed at. Explicit
run and market-dataset deletion removes normalized dependent rows; reusable
strategy configurations and instrument identities remain available for other
runs.

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

Debate verdict history for an instrument:

```sql
SELECT run_id, stored_at, interval, winner,
       technical_fingerprint, result_fingerprint
FROM jarvis_debate_runs
WHERE symbol_token = ?
ORDER BY stored_at DESC;
```

Signal tokens supporting deterministic precedent lookup:

```sql
SELECT r.run_id, r.stored_at, r.winner, s.token
FROM jarvis_debate_runs AS r
JOIN jarvis_debate_signal_signature AS s
  USING (run_id)
WHERE r.symbol_token = ?
ORDER BY r.stored_at DESC, s.token;
```

## Adapter boundary and local files

Domain/application code depends on `MarketSeriesRepository`,
`BacktestRunRepository`, and `DebateRunRepository`, composed by
`JarvisStorageAdapter`. Both `InMemoryJarvisStorage` and
`DuckDBJarvisStorage` implement the same boundary. DuckDB is therefore a local
analytical adapter, not a requirement embedded in agents, evaluators, or use
cases.

Generated `.duckdb` and `.duckdb.wal` files contain local research state and
must not be committed to source control.

## Multi-Timeframe Persistence Boundary

Schema version 3 predates the combined conversational multi-timeframe result.
It persists normalized market/backtest data and immutable debate runs, but does
not yet provide a dedicated aggregate or normalized tables for:

- the paired daily/weekly technical release and its lineage;
- immediate support/resistance and confirmed pivots by timeframe;
- the user-facing long-only actionable/`NO_TRADE` policy result;
- deterministic 2R/3R plan fields attached to the CEO briefing;
- the final Jarvis presentation; or
- retained Judge follow-up questions and answers.

Those objects are typed, chain-validated, and available in memory through
`MultiTimeframeEndToEndSwingAnalysisResult`; they should not be described as
durable dashboard history yet. The next storage change should add a new
database-agnostic repository port first, then implement both in-memory and
DuckDB adapters. Avoid making agents or use cases import DuckDB directly.
