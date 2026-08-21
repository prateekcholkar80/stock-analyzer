# Jarvis: Current Baseline

This document replaces the old Phase-1-only baseline. It describes the system
as it actually exists today, across market data, technical analysis,
backtesting, storage, and the Bull/Bear debate layer. Treat this file, not
`README.md`, as the authoritative "what's built" reference — `README.md`
still describes only the Phase-1 market-data foundation and needs a separate
refresh.

Automated test count as of this writing: **1049 tests** (`tests/unit` +
`tests/integration`), all offline, no network access or real credentials
required.

## 1. Overview

Jarvis is an agentic equity-research platform built around one governing
principle: **every conclusion must be reproducible and grounded in
evidence that existed at the time it was reached.** Concretely, that shows
up as:

- **Point-in-time correctness** — no component may see data from beyond the
  candle it is currently evaluating (enforced by explicit boundary checks,
  not just convention).
- **Determinism first** — technical indicators, signal evidence, and
  candle aggregation are pure functions with no ML/embeddings anywhere;
  the only place an LLM is used is the Bull/Bear/Judge debate layer, and
  even there every claim must cite a real, pre-computed evidence id.
- **"Jarvis judge" gates** — every agent-produced submission (technical
  evidence, a trade plan, a historical execution, a debate transcript) is
  checked by a paired *structural* judge that verifies schema, identity,
  configuration fingerprints, and chain-of-custody before the result is
  accepted. This is a correctness/audit gate, separate from whatever
  *substantive* judgment (e.g. the debate verdict) an LLM makes.
- **Immutable, content-addressed storage** — nothing is ever mutated in
  place; every stored aggregate is fingerprinted (SHA-256 of its validated
  payload) and a changed input produces a new record, not an overwrite.

The system today covers the full path from raw OHLC ingestion through a
single-perspective technical/trade-planning pipeline, a walk-forward
backtesting engine, and — the newest layer — a bounded, evidence-grounded
Bull vs. Bear debate with an LLM judge, wired end-to-end from a resumable
Angel One data pull to a final verdict.

## 2. Project Structure

```text
app/
  agents/                  Agent roles (deterministic + LLM-backed)
    _debate_support.py       Shared prompt-building/grounding helpers for the debate agents
    bull_agent.py             BullDebateAgent (LLM, "Big Bull" conviction persona)
    bear_agent.py             BearDebateAgent (LLM, forensic/contrarian persona)
    debate_judge_agent.py     DebateJudgeAgent (LLM-as-judge, substantive verdict)
    technical_swing_agent.py  TechnicalSwingAgent (deterministic technical evaluation)
    trade_planning_agent.py   TradePlanningAgent (deterministic risk/reward planning)
    historical_execution_agent.py  HistoricalExecutionAgent (deterministic trade simulation)
    market_agent.py           MarketAgent (current-price capability, Phase 1)

  analytics/                Pure, deterministic calculation engines
    indicators.py             SMA/EMA/RSI/MACD/Bollinger/ATR/ADX/Stochastic/OBV/candlestick patterns
    candlestick_signals.py    Evidence aggregation over all 61 TA-Lib CDL* pattern functions
    swing_pivots.py            Pivot-high/low detection
    market_structure.py        Higher-high/lower-low structure classification
    structure_breaks.py        Break-of-structure / change-of-character events
    support_resistance.py      Zone clustering from pivots
    support_resistance_lifecycle.py  Zone birth/test/break lifecycle tracking
    fair_value_gaps.py         FVG detection and fill tracking
    trend_signals.py, momentum_signals.py, volatility_signals.py, volume_signals.py, price_action_signals.py
                                Per-category evidence builders consumed by the swing evaluator
    swing_evaluator.py          UnifiedSwingEvaluator -- assembles all 12 evidence items into one profile
    signal_profiles.py          Swing + long-term stance classification and category scoring
    risk_reward.py               Structural stop/target derivation for trade planning
    historical_analysis.py       Point-in-time historical signal replay
    trade_execution.py           Deterministic trade-execution simulation
    walk_forward.py              WalkForwardBacktestEngine
    candle_aggregation.py        aggregate_candles() -- hourly -> daily -> weekly OHLCV coarsening

  angel/
    client.py                  AngelOneClient -- thin wrapper over the SmartAPI SDK (login, LTP, historical candles)

  llm/
    client.py                   LLMClient -- provider-agnostic structured-output boundary over litellm

  models/                    Pydantic domain models (all frozen/validated)
    market.py                   Candle, HistoricalCandleSeries, MarketQuote
    technical.py                 TechnicalModel base class
    signals.py                   SignalCategory/Direction/Strength, evidence + profile models, stance classifiers
    price_action.py, trade_setup.py, execution.py, historical_analysis.py
                                  Price-action, trade-plan, execution, and historical-replay models
    backtest.py                  Walk-forward config/result models
    agentic.py                   AgenticSwingAnalysisResult and the three Jarvis judge decision/verdict models
    debate.py                    Bull/Bear/Judge debate models (see section 3.10)
    storage.py                   Storage-layer models: fingerprints, StoredX/XSummary/XQuery pairs, receipts

  orchestration/
    agent_orchestrator.py        AgentOrchestrator + JarvisSwingJudge/JarvisTradePlanJudge/JarvisHistoricalExecutionJudge
    debate_orchestrator.py        DebateOrchestrator + JarvisDebateJudge (bounded Bull/Bear loop, indecisive enforcement)
    debate_session.py             DebateSession -- in-process state machine for one debate's working memory

  storage/                   Ports-and-adapters persistence
    repositories.py              MarketSeriesRepository/BacktestRunRepository/DebateRunRepository Protocols -> JarvisStorageAdapter
    adapters/in_memory.py         InMemoryJarvisStorage (test double, full Protocol conformance)
    adapters/duckdb.py            DuckDBJarvisStorage (schema v3: immutable JSON envelopes + normalized tables)

  services/
    market_data.py               MarketDataService -- normalizes Angel One responses into domain models
    research_archive.py          ResearchArchiveService -- application-layer wrapper over JarvisStorageAdapter

  use_cases/                 Thin composition of already-built collaborators
    run_and_archive_backtest.py   RunAndArchiveWalkForwardBacktest
    pull_rolling_market_series.py  PullRollingMarketSeries -- resumable, chunked Angel One pull
    run_end_to_end_swing_analysis.py  RunEndToEndSwingAnalysis -- pull -> evaluate -> debate in one call

  tools/market_tools.py       Lazy MarketDataService singleton for agent tool access
  gateways/market_data.py     MarketDataGateway Protocol + MarketResponse type
  config.py                   Settings (Angel One credentials, SecretStr, cached)
  exceptions.py                Application exception hierarchy
  logging_config.py            Structured JSON logging, redaction, operation-ID tracing
  runtime.py                    run_entrypoint() -- safe executable-entry-point wrapper
  rag/                          Empty (__init__.py only) -- not yet started
  memory/                        Empty (__init__.py only) -- not yet started

docs/
  current-baseline.md           This file
  backtest-storage-schema.md     DuckDB schema reference (currently describes v2; v3 added the two debate tables -- see section 3.8)

tests/
  unit/                          68 test files, one per module/behavior area, no mocking framework anywhere
  integration/                   Cross-layer logging/operation-ID test

examples/                      Executable, credential-touching example scripts (angel_login, market_quote, historical_data, market_agent, candle_conversion)
```

## 3. Architecture Layers

High-level data flow, newest capability last:

```text
Angel One SmartAPI
  -> AngelOneClient / MarketDataService        (raw -> validated Candle/HistoricalCandleSeries)
  -> PullRollingMarketSeries                    (resume + chunk + merge, section 3.12)
  -> UnifiedSwingEvaluator / TechnicalSwingAgent (12 evidence items, section 3.4-3.5)
  -> AgentOrchestrator + JarvisSwingJudge        (structural approval gate, section 3.6)
  -> DebateOrchestrator                          (Bull vs Bear vs Judge, section 3.10)
  -> AgenticDebateResult                          (transcript + verdict, archived via ResearchArchiveService)
```

`RunEndToEndSwingAnalysis` (section 3.13) chains the last three steps into
one call.

### 3.1 Configuration & Logging

- `app/config.py`: `Settings` (pydantic, frozen) validates four required
  Angel One credentials from the environment (`ANGEL_API_KEY`,
  `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`), wrapped as
  `SecretStr`, cached via `get_settings()` (`lru_cache`), raising
  `ConfigurationError` if anything is missing or blank.
- LLM credentials are **not** part of `Settings`. `LLMClient` delegates to
  `litellm`, which reads whatever provider-specific env var the chosen
  model string implies (e.g. `model="anthropic/claude-sonnet-4-5"` reads
  `ANTHROPIC_API_KEY` from the process environment directly).
- `app/logging_config.py`: structured JSON logging, secret redaction, and
  an operation ID that stays consistent across service/gateway layers for
  one logical request.
- `app/runtime.py`: `run_entrypoint()` wraps executable scripts so expected
  `ApplicationError` failures exit status 1 without a vendor traceback.

### 3.2 Data Ingestion (Angel One)

- `AngelOneClient` (`app/angel/client.py`): thin wrapper over the
  `SmartApi.SmartConnect` SDK. `login()` authenticates with a generated
  TOTP; `get_ltp()` and `get_historical_candles()` are one-shot calls with
  no built-in pagination, retry, or rate-limit handling.
- `MarketDataService` (`app/services/market_data.py`): converts raw Angel
  One dict responses into validated `MarketQuote`/`HistoricalCandleSeries`
  models; external dictionaries never cross this boundary unconverted.

### 3.3 Domain Models

`app/models/market.py`: `Candle` (tz-aware timestamp, non-negative OHLCV,
high/low consistency) and `HistoricalCandleSeries` (exchange/symbol/token/
interval + candle list), both frozen. All later layers build on these two
types without redefining candle semantics.

### 3.4 Technical Analysis Engine

`app/analytics/indicators.py` and friends implement, as pure functions:
SMA, EMA, RSI, MACD, Bollinger Bands, ATR, ADX, Stochastic, OBV, swing
pivots, market structure (HH/HL/LH/LL), structure breaks, support/
resistance zone lifecycle, fair value gaps, and all 61 TA-Lib candlestick
(`CDL*`) patterns via `candlestick_signals.py`. Each produces typed,
validated evidence — never raw floats passed downstream unvalidated.

### 3.5 Unified Swing Evaluation & Signal Profiles

`UnifiedSwingEvaluator` (`app/analytics/swing_evaluator.py`) assembles
**12 evidence items across 6 `SignalCategory` values** from one candle
prefix:

| Category | Sources |
|---|---|
| trend | moving-average alignment, ADX directional strength |
| momentum | RSI mean-reversion, MACD, stochastic zone crossover |
| volatility | Bollinger price/bandwidth, ATR regime/risk distance |
| volume | OBV price confirmation |
| candlestick | aggregate candlestick pattern (all 61 `CDL*` functions) |
| price_action | fair value gap context, support/resistance lifecycle, market structure |

`app/analytics/signal_profiles.py` classifies a `SwingTradingStance` (swing
horizon) or `LongTermTechnicalStance` (holding horizon, requires daily/
weekly/monthly-interval evidence — see `LONG_TERM_TECHNICAL_INTERVALS`) from
weighted category scores. Default category weights favor trend (1.25) over
others (1.0), with long-term candlestick weight deliberately lower (0.4) so
it doesn't dilute coverage defaults.

### 3.6 Single-Perspective Agent Pipeline

`AgentOrchestrator` (`app/orchestration/agent_orchestrator.py`) runs a
**linear pipeline**, each stage gated by its own structural judge:

```text
TechnicalSwingAgent -> JarvisSwingJudge -> AgenticSwingAnalysisResult
TradePlanningAgent  -> JarvisTradePlanJudge
HistoricalExecutionAgent -> JarvisHistoricalExecutionJudge
```

Each judge independently re-validates: agent/evaluator identity,
configuration fingerprint, exact market-prefix fingerprint, point-in-time
boundary (no look-ahead), deterministic/synchronized evidence, complete
evidence-source coverage (all 12 sources / all 6 categories / 100%
weighted coverage), and review-chain linkage to the prior stage. This is
audit/verification, not multi-perspective synthesis — one opinion per
stage, checked for correctness.

`run_swing_analysis(market_series) -> AgenticSwingAnalysisResult` is the
entry point the debate layer consumes (section 3.10).

### 3.7 Walk-Forward Backtesting

`WalkForwardBacktestEngine` (`app/analytics/walk_forward.py`) replays the
single-perspective pipeline across scheduled historical dates, producing a
`WalkForwardBacktestResult`: per-date evaluations, trade ledger, equity/
drawdown curve, and aggregate performance (win rate, profit factor,
expectancy, exposure, streaks — including segment breakdowns by trade
direction and signal stance). `RunAndArchiveWalkForwardBacktest`
(`app/use_cases/run_and_archive_backtest.py`) runs the engine and persists
the result via a narrow `BacktestArchiveWriter` port.

### 3.8 Storage Layer

Ports-and-adapters, database-neutral. `JarvisStorageAdapter`
(`app/storage/repositories.py`) composes three Protocols —
`MarketSeriesRepository`, `BacktestRunRepository`, `DebateRunRepository` —
each implemented in full by both `InMemoryJarvisStorage` (test double) and
`DuckDBJarvisStorage` (**schema version 3**).

Storage design, consistent across all three repositories:
- Immutable JSON envelope (the exact validated aggregate, for replay/audit)
  **plus** normalized relational tables (for dashboard queries without
  re-decoding JSON).
- Every identifier is content-addressed (SHA-256 of the validated payload);
  saving identical content twice is idempotent, saving different content
  under an existing identifier raises `StorageConflictError`.
- No `REFERENCES` foreign-key constraints anywhere (a deliberate,
  repo-wide convention) — cascading deletes are handled by explicit code
  in the generic `_delete()` helper, not the database.

DuckDB tables as of schema v3: `jarvis_storage_metadata`,
`jarvis_market_series`, `jarvis_market_candles`, `jarvis_instruments`,
`jarvis_instrument_symbols`, `jarvis_strategy_configurations`,
`jarvis_strategy_weights`, `jarvis_backtest_runs` (+ 6 evaluation/trade/
equity/performance detail tables), and — new in v3 — `jarvis_debate_runs`
+ `jarvis_debate_signal_signature`. Full backtest-table detail and example
dashboard queries: `docs/backtest-storage-schema.md` (**note: that file's
prose still says "schema version 2" and doesn't mention the two v3 debate
tables — needs its own refresh**).

`ResearchArchiveService` (`app/services/research_archive.py`) is the
application-layer wrapper every use case actually depends on, rather than
talking to `JarvisStorageAdapter` directly.

### 3.9 LLM Client

`LLMClient` (`app/llm/client.py`) is the **only** place an LLM is called
anywhere in the codebase. It forces structured output via OpenAI-style
tool-calling (`response_model.model_json_schema()` as the tool's
parameter schema), validates the result against the pydantic
`response_model`, and retries once on a schema mismatch before raising
`LLMResponseValidationError`. Provider-agnostic through `litellm` — model
choice is just a string (`"anthropic/claude-sonnet-4-5"`,
`"openai/gpt-4o"`, etc.) via `LLMGenerationConfig`.

### 3.10 Bull/Bear/Judge Debate Layer

The newest capability: a bounded, evidence-grounded debate over one
Jarvis-approved `AgenticSwingAnalysisResult`, built as a standalone stage
(`app/agents/bull_agent.py`, `bear_agent.py`, `debate_judge_agent.py`,
`app/orchestration/debate_orchestrator.py`).

**Prompt structure** — every agent's LLM call is explicitly four sections,
enforced by `app/agents/_debate_support.py`:
- `# Role` + `# System Prompt` — static, composed once via
  `build_system_prompt()` into the `system` message.
- `# Context` + `# Feedback` — dynamic, composed per-call via
  `build_user_message()` in the `user` message. Feedback always defaults
  to `"None yet -- this is the first attempt."` rather than only
  appearing ad hoc on retry.

**Personas** (archetypal, not literal impersonation of a named individual):
Bull channels a "Big Bull" conviction-investor style (aggressive but
disciplined — "buy right, sit tight"); Bear channels a forensic/
contrarian-skeptic style.

**Grounding**: every citation (`BullBearArgument.evidence_citations`,
`DebateVerdict.decisive_evidence_ids`) must reference a real
`evidence_id` from the technical profile. `generate_grounded()` enforces
this with a fast-fail-plus-one-retry pattern (retry's Feedback section
names the exact invalid ids); `JarvisDebateJudge` independently re-checks
citation validity, chain-of-custody, and chronology *after* the LLM calls
complete — grounding is checked twice, not just prompted once.

**Termination — deterministic, not LLM-decided**: `DebateOrchestrator`
runs a bounded Bull-then-Bear loop per round, capped at
`DebateOrchestratorConfig.max_rounds` (default 3), with a citation-set
stall check (`STALL_DETECTED` if a side repeats identical citations
round-over-round). `DebateTerminationReason` is one of
`MAX_ROUNDS_REACHED` / `STALL_DETECTED` / `AGENT_FAILURE`.

**Indecisive is enforced, not just prompted**: `_normalize_verdict()`
force-overrides the winner to `NEUTRAL` whenever termination was
`STALL_DETECTED`/`AGENT_FAILURE`, or whenever confidence is below
`indecisive_confidence_threshold` (default 55%) — the LLM cannot argue
its way past this structural backstop.

**Judge output** (`DebateVerdict`): `winner`, `confidence_percentage`,
`decisive_evidence_ids`, **`bull_case_summary` and `bear_case_summary`**
(the judge summarizes each side's strongest grounded argument before
rendering its verdict), and `rationale`. The Judge **never receives
precedent** (see below) — structurally impossible to pass, not just
discouraged by prompt wording (`render_verdict()` has no `precedent`
parameter at all).

### 3.11 Debate Session State Machine & Precedent Recall

- `DebateSession` (`app/orchestration/debate_session.py`): a plain mutable
  class (deliberately not frozen, unlike the rest of the codebase's
  domain models) holding the working memory of *one in-progress* debate:
  `IN_PROGRESS -> STALLED|MAX_ROUNDS_REACHED|AGENT_FAILURE -> JUDGED`,
  with a full transition history. Fully ephemeral — nothing here is
  persisted; it replaces loose local variables inside
  `DebateOrchestrator.run_debate()`.
- **Cross-debate precedent recall** (opt-in, additive): `debate_signal_signature()`
  (`app/models/storage.py`) computes a deterministic `"{category}:{direction}"`
  token signature over MODERATE+/non-neutral evidence — no ML/embeddings.
  `find_similar_debate_runs()` ranks stored runs by shared-token overlap.
  When `DebateOrchestrator` is constructed with an `archive`
  (`DebateArchive` Protocol), it fetches the most-similar past debates
  before the debate runs and forwards them **only to Bull and Bear**
  (rendered as clearly-labeled "context only, no evidentiary weight"
  prompt text) — never to the Judge, so the verdict is always grounded
  solely in the current debate's own evidence and transcript. This was an
  explicit user requirement: precedent may make the agents argue better
  over time, but must never bias the outcome.

### 3.12 Rolling Market-Data Pull & Timeframe Aggregation

- `aggregate_candles()` (`app/analytics/candle_aggregation.py`): pure,
  deterministic OHLCV coarsening (hourly -> daily -> weekly; hourly ->
  weekly directly gives identical results since OHLC aggregation is
  associative). Each output bar is timestamped at its bucket's *close*
  (never its open), avoiding look-ahead. The final bucket is dropped
  unless genuinely complete (session-close-time check for daily, plus a
  `week_end_weekday` check — default Friday — for weekly); this is a
  documented simplification that doesn't model exchange holidays and
  fails conservatively (under-includes, never fabricates). Drop-in
  replacement anywhere a native daily/weekly Angel One pull would have
  been used, since `build_long_term_technical_profile()` only checks the
  input series' `interval` string.
- `PullRollingMarketSeries` (`app/use_cases/pull_rolling_market_series.py`):
  resumes from the most-advanced previously-stored series for
  `(exchange, symbol_token, interval)` — found via existing
  `MarketSeriesSummary.last_candle_at` + `list_market_series()` filtering,
  **no new storage schema needed** — falling back to
  `RollingFetchConfig.default_lookback_days` (default 365) when nothing is
  stored. Splits the needed range into `max_days_per_chunk`-day windows
  (default 30) with `inter_request_delay_seconds` (default 1.0) between
  Angel One calls, merges newly-fetched candles with whatever was already
  stored (dedup by timestamp, new data wins), and archives the merged
  series as a new immutable dataset. **`max_days_per_chunk` and
  `inter_request_delay_seconds` are conservative estimates, not verified
  against Angel One's current SmartAPI rate limits/range caps — tune
  before relying on this for large historical pulls.** Built for hourly
  (`ONE_HOUR`) as the single source of truth, specifically so daily/
  weekly views can be derived locally rather than pulled as separate,
  potentially-drifting datasets.

### 3.13 End-to-End Use Case

`RunEndToEndSwingAnalysis` (`app/use_cases/run_end_to_end_swing_analysis.py`)
chains the whole pipeline in one call:

```python
RunEndToEndSwingAnalysis(rolling_fetch, agent_orchestrator, debate_orchestrator).execute(
    exchange, symbol_token, symbol, interval="ONE_HOUR", to_date=None,
)
```

`PullRollingMarketSeries.execute()` -> `AgentOrchestrator.run_swing_analysis()`
-> `DebateOrchestrator.run_debate()`, returning an `EndToEndSwingAnalysisResult`
(fetch receipt + technical result + debate result). A rejected technical
submission raises `AgentSubmissionRejectedError` with Jarvis's actual
reasons and the debate stage is never reached. One shared
`ResearchArchiveService` instance backs both the market-data pull and the
debate archive, so a single call leaves both the pulled series and the
full debate transcript/verdict queryable afterward.

This is a **plain Python/API call, not natural language** — there is still
no "Jarvis, pull Reliance till today" chat/NLU front door (see section 6).

## 4. Configuration & Environment

```dotenv
ANGEL_API_KEY="your_api_key"
ANGEL_CLIENT_CODE="your_client_code"
ANGEL_PIN="your_pin"
ANGEL_TOTP_SECRET="your_totp_secret"
```

Loaded lazily, validated with pydantic, wrapped in `SecretStr`, cached
after successful load, rejected via `ConfigurationError` when missing or
blank. `.env` is git-ignored; copy `.env.example` to start.

LLM provider credentials (e.g. `ANTHROPIC_API_KEY`) are read directly by
`litellm` from the process environment and are **not** validated by
`Settings` — a missing key surfaces as a `litellm`/provider error at call
time, not at startup.

## 5. Testing

```bash
# full suite (unit + integration)
.venv/bin/python -m unittest discover -s tests -v

# unit only
.venv/bin/python -m unittest discover -s tests/unit -v
```

Current count: **1049 tests**, fully offline. Conventions to preserve:
- **No mocking framework anywhere** — every test double is a plain stub
  class or fake function (`FakeClient`, `StubSideAgent`, a `sleep_fn`
  callable, etc.), matching the style of every existing test file.
- Debate-layer tests share fixtures in `tests/unit/_debate_fixtures.py`
  (`build_market_series`, `build_approved_technical_result`,
  `build_precedent_summary`) rather than hand-rolling technical profiles.
- New agent/debate prompt changes should keep the
  `test_prompt_has_role_context_system_prompt_and_feedback_sections`-style
  lock-in tests that assert on prompt structure, not just output.

## 6. Known Gaps / Not Yet Implemented

- **No NLU/chat front door.** Every capability above is a typed Python
  call. There is no CLI, API, or free-text entry point — `app/rag/` is
  still empty (`__init__.py` only).
- **No fundamental, sentiment, or macro analysis** anywhere in the repo,
  not even stubs (P&L/valuation metrics, news/analyst sentiment, FII/DII
  flows, macro/rate data — all still just the target architecture,
  section 7).
- **No debate/verdict *quality* eval harness.** ~1049 tests verify the
  pipeline is *implemented correctly* (schemas, citations, determinism,
  chain-of-custody) — none of them score whether an argument was good or
  a verdict was right against what actually happened next. The
  walk-forward backtest engine does this kind of realized-outcome scoring
  for the older single-perspective pipeline but was never extended to
  grade debate verdicts.
- **Angel One rate-limit defaults are estimates** (`RollingFetchConfig`,
  section 3.12) — verify against current SmartAPI docs/account limits
  before relying on large historical pulls.
- **`docs/backtest-storage-schema.md` is stale** — still describes schema
  v2, missing the two v3 debate tables.
- **`README.md` is stale** — still describes only the Phase-1 baseline (69
  tests, "technical indicators not implemented yet"). This file
  (`current-baseline.md`) is the accurate reference; `README.md` needs a
  separate refresh if kept.
- **No Orchestrator & Consensus layer** in the target-architecture sense
  (confidence-weighted synthesis across technical + fundamental +
  sentiment + macro) — today's debate layer synthesizes only technical
  evidence, and the older `AgentOrchestrator` pipeline is single-opinion
  audit, not multi-perspective consensus.

## 7. Target Architecture (North Star)

User-stated direction for where Jarvis is headed, not yet fully built:

- **Data Ingestion Layer**: Angel One + financial databases + news feeds
  -> OHLC, P&L reports, conference calls, FII/DII data.
- **Analysis Modules**: Technical (built), Fundamental (not built),
  Sentiment (not built), Macro (not built).
- **AI Agent Layer (Debate)**: Bull vs Bear — **built** (section 3.10),
  currently technical-evidence-only; fundamental/sentiment/macro modules
  would feed richer evidence into the same debate structure once built.
- **Orchestrator & Consensus Layer**: synthesize the debate, assign
  confidence scores weighted by technical strength and macro factors —
  partially built (the Judge's confidence + indecisive-enforcement), not
  yet multi-module-weighted.
- **Final Recommendation Report**: Rating (BUY/HOLD/SELL), entry/stop/
  target, risk-reward ratio, confidence, time horizon, key assumptions
  and catalysts — not yet built as an output format.

## 8. Document History

- **2026-08-21**: Full rewrite. Previous version described only the
  Phase-1 market-data baseline (69 tests) and had been stale since Phase 2
  (technical analysis + execution pipeline, commit `2351798`) through the
  debate layer and end-to-end wiring (commit range ending around
  `c73dec9` and this session's work). This version reflects the codebase
  through the Bull/Bear debate layer, rolling market-data pull, and
  end-to-end wiring, at 1049 passing tests.
