# Jarvis: Current Baseline

This document describes the system as it exists today across market data,
technical analysis, backtesting, storage, provider-neutral LLM debate,
natural-language routing, instrument resolution, workflow observability, and
wake-activated conversation. It is the authoritative implementation baseline;
`README.md` provides the shorter project-level view.

Automated test count as of this writing: **1,295 tests** (`tests/unit` +
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
  LLMs are used only in the Bull/Bear/Judge debate and the post-Judge Jarvis
  explanation layer, and
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

The system today covers the full path from a typed or voice-transcript wake
request through conservative intent recognition, instrument resolution, raw
OHLC ingestion, a technical/trade-planning pipeline, and a bounded,
evidence-grounded Bull vs. Bear debate with an LLM Judge. The same technical
pipeline also drives the look-ahead-safe walk-forward backtester. Conversation
and research events expose each major state to a future UI without coupling the
domain to HTTP, WebSocket, microphone, or speech vendors.

## 2. Project Structure

```text
app/
  agents/                  Agent roles (deterministic + LLM-backed)
    _debate_support.py       Shared prompt-building/grounding helpers for the debate agents
    bull_agent.py             BullDebateAgent (LLM, "Big Bull" conviction persona)
    bear_agent.py             BearDebateAgent (LLM, forensic/contrarian persona)
    debate_judge_agent.py     DebateJudgeAgent (LLM-as-judge, substantive verdict)
    jarvis_presentation_agent.py  Grounded Chief Investment Research Assistant/CEO briefing
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
    gateway.py                  StructuredLLMGateway Protocol
    adapters/litellm_gateway.py LiteLLM implementation and typed error mapping
    config.py                   Shared/per-role model, temperature, and token settings
    factory.py                  Role-bound gateway construction and caching
    audited_gateway.py          Provider-neutral prompt/response audit decorator
    preflight.py                Local full-panel provider/credential readiness check
    client.py                   Older direct structured-output client retained for compatibility

  conversation/
    config.py                   Configured user name and wake phrase
    wake_word.py                Anchored detector shared by text and voice transcripts
    session.py                  Dormant/listening/processing conversation state machine
    events.py                   ConversationEventSink and ordered IST event emitter

  audit/
    prompt_audit.py             Opt-in redacted JSONL conversation/prompt trail

  intents/
    swing_analysis.py           Conservative local swing-request interpreter

  instruments/
    in_memory.py                Deterministic exact/normalized instrument matching
    angel_master.py             Bounded download, validation, cache, and stale fallback

  commands/swing_analysis.py    UI-safe command boundary and LLM failure envelope
  facades/swing_research.py     Natural language -> resolution -> complete workflow
  composition/                  Lazy wiring for debate, research façade, and conversation
  workflow/events.py            Research progress events for UI/voice consumers

  models/                    Pydantic domain models (all frozen/validated)
    market.py                   Candle, HistoricalCandleSeries, MarketQuote
    technical.py                 TechnicalModel base class
    signals.py                   SignalCategory/Direction/Strength, evidence + profile models, stance classifiers
    price_action.py, trade_setup.py, execution.py, historical_analysis.py
                                  Price-action, trade-plan, execution, and historical-replay models
    backtest.py                  Walk-forward config/result models
    agentic.py                   AgenticSwingAnalysisResult and the three Jarvis judge decision/verdict models
    debate.py                    Bull/Bear/Judge debate models (see section 3.10)
    instruments.py               Resolved instrument identity
    interaction.py               Intent, command, and success/failure response envelope
    llm.py                       Preflight and secret-safe LLM failure models
    workflow.py                  Research workflow event contract
    conversation.py              Input, state, outcome, turn, and transition models
    timeframes.py                Hourly/daily/weekly lineage and parallel technical results
    multi_timeframe_evidence.py  Judge-released, qualified daily/weekly evidence package
    multi_timeframe_trade.py     Chain-bound long-only actionable/no-trade policy result
    presentation.py              Immutable Jarvis CEO briefing/explanation contracts
    storage.py                   Storage-layer models: fingerprints, StoredX/XSummary/XQuery pairs, receipts

  orchestration/
    agent_orchestrator.py        AgentOrchestrator + JarvisSwingJudge/JarvisTradePlanJudge/JarvisHistoricalExecutionJudge
    debate_orchestrator.py        DebateOrchestrator + JarvisDebateJudge (bounded Bull/Bear loop, indecisive enforcement)
    timeframe_technical_orchestrator.py  Parallel daily/weekly agent execution and validation
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
    derive_swing_timeframes.py    Completed hourly -> daily -> weekly aggregation and lineage
    build_multi_timeframe_evidence.py  Existing Judge's release gate for paired evidence
    build_multi_timeframe_long_trade_plan.py  Long-only 2R post-verdict policy
    run_end_to_end_multi_timeframe_swing_analysis.py  Default hourly -> two timeframes -> debate -> plan path
    ask_jarvis_judge_follow_up.py  Evidence-locked follow-up questions to the same Judge

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
  backtest-storage-schema.md     DuckDB schema-v3 reference and dashboard queries

tests/
  unit/                          Deterministic module/contract/edge-case coverage
  integration/                   Cross-layer logging/operation-ID test

examples/                      Executable, credential-touching example scripts (angel_login, market_quote, historical_data, market_agent, candle_conversion, conversation_demo)
```

## 3. Architecture Layers

High-level interactive data flow:

```text
Typed text / transcribed voice
  -> NormalizedWakePhraseDetector + JarvisConversationSession
  -> PatternSwingIntentInterpreter
  -> AngelInstrumentMasterResolver
  -> JarvisSwingResearchFacade / JarvisSwingAnalysisCommandHandler
Angel One SmartAPI
  -> AngelOneClient / MarketDataService        (raw -> validated Candle/HistoricalCandleSeries)
  -> PullRollingMarketSeries                    (resume + chunk + merge, section 3.12)
  -> DeriveSwingTimeframes                      (completed hourly -> daily -> weekly)
  -> DailyTechnicalSwingAgent || WeeklyTechnicalSwingAgent (parallel)
  -> existing JarvisSwingJudge                  (paired evidence release gate)
  -> DebateOrchestrator                          (Bull vs Bear -> same Judge)
  -> BuildMultiTimeframeLongTradePlan            (long-only, minimum 2R)
  -> JarvisPresentationAgent                     (CEO briefing; evidence immutable)
  -> JarvisSwingAnalysisResponse                 (success or safe LLM failure)
```

`RunEndToEndMultiTimeframeSwingAnalysis` is the default natural-language path.
The older `RunEndToEndSwingAnalysis` remains an explicit programmatic,
single-timeframe compatibility path. The research façade and conversation
session (sections 3.14-3.15) add the natural-language and wake-activation
boundaries. Workflow events are emitted throughout execution rather than
inferred later from logs.

### 3.1 Configuration & Logging

- `app/config.py`: `Settings` (pydantic, frozen) validates four required
  Angel One credentials from the environment (`ANGEL_API_KEY`,
  `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`), wrapped as
  `SecretStr`, cached via `get_settings()` (`lru_cache`), raising
  `ConfigurationError` if anything is missing or blank.
- LLM credentials are **not** part of `Settings` or `LLMSettings`.
  `LLMSettings` selects a shared model or role-specific Bull/Bear/Judge models,
  temperatures, and token budgets. The LiteLLM adapter obtains the credential
  implied by each provider. `LLMPreflightValidator` checks all mandatory roles,
  provider resolution, credential presence (except configured keyless local
  providers), gateway conformance, and a configuration fingerprint before the
  debate is constructed. This is a local readiness check, not a model call.
- `JarvisConversationConfig` requires `JARVIS_USER_NAME` and defaults
  `JARVIS_WAKE_PHRASE` to `Hey Jarvis`. Conversation settings do not contain
  audio-provider configuration because the current voice boundary accepts a
  completed speech-to-text transcript.
- `AngelInstrumentMasterConfig` controls the HTTPS master URL, cache path,
  cache TTL, download timeout, maximum payload size, and enabled exchanges.
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
`jarvis_strategy_weights`, `jarvis_backtest_runs` (+ 8 evaluation/trade/
equity/performance detail tables), and — new in v3 — `jarvis_debate_runs`
+ `jarvis_debate_signal_signature`. Full backtest-table detail and example
dashboard queries: `docs/backtest-storage-schema.md`.

`ResearchArchiveService` (`app/services/research_archive.py`) is the
application-layer wrapper every use case actually depends on, rather than
talking to `JarvisStorageAdapter` directly.

### 3.9 Provider-Neutral LLM Boundary

The current debate agents depend on `StructuredLLMGateway`, not LiteLLM or a
specific provider. Its `generate()` contract accepts system/user messages and
a pydantic response type, then returns a validated `StructuredGeneration`
containing the value plus provider, model, and attempt-count metadata.

`LiteLLMStructuredGateway` is the current adapter. It requests structured
output, validates it, retries the bounded schema-repair path, and translates
provider exceptions into the application hierarchy:

- `LLMConfigurationError`
- `LLMAuthenticationError`
- `LLMProviderUnavailableError`
- `LLMRateLimitError`
- `LLMResponseValidationError`

`LLMGatewayFactory` binds and caches one gateway per `LLMRole`. Model/provider
selection is therefore a composition concern: Jarvis, Bull, Bear, and Judge
know only their gateway contract. A shared `JARVIS_LLM_MODEL` may serve all
roles, or role-specific settings may select different providers/models without
changing agent code. The Jarvis presenter can use
`JARVIS_PERSONA_LLM_MODEL`; when blank it inherits the Judge override and then
the shared model.

The full debate is mandatory. `compose_full_debate()` preflights all three
roles before returning an orchestrator. If configuration or execution fails,
the command boundary produces `JarvisLLMFailureResponse`, containing stable
failure codes, separate display/spoken messages, recovery guidance, and an
explicit `analysis_available=False`. It never returns a deterministic-only
verdict disguised as a completed LLM debate.

`app/llm/client.py` is the earlier direct LiteLLM abstraction and remains in
the repository for compatibility; newly composed debate agents use the gateway
protocol/factory path.

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

**Rebuttal references are shown, not guessed** (fixed 2026-08-22):
`serialize_transcript()` (`app/agents/_debate_support.py`) now prints each
prior argument's real `argument_id` (e.g. `id=jarvis.bull_debate_agent.v1:
sub-1:1`) inline in the transcript shown to Bull/Bear. Previously it only
showed round number and side, so when an agent tried to rebut a specific
prior argument it had no way to know the real id and invented a
plausible-looking placeholder (e.g. `"round 1 bull"`) instead of copying
it — `BullBearDebateSubmission`'s strict validator (rebuttal references
must match a real prior argument id) then rejected it with an uncaught
`pydantic.ValidationError`, crashing the debate rather than failing
gracefully. This was a live, reproducible defect (see section 3.17), not
a hypothetical one — it fired on nearly every multi-round debate that
attempted a rebuttal.

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

This typed call remains available for programmatic use. The natural-language
façade resolves text into its arguments before invoking the same use case; it
does not create a parallel analysis implementation.

The default natural-language composition now uses
`RunEndToEndMultiTimeframeSwingAnalysis`. It accepts only `ONE_HOUR` source
data, derives completed `ONE_DAY` and `ONE_WEEK` candles, and invokes the daily
and weekly technical agents through `AgentOrchestrator`. Jarvis validates the
two receipts and releases a single fingerprinted evidence package. Bull and
Bear then receive that same package, cite timeframe-qualified IDs such as
`daily:...` and `weekly:...`, and explicitly classify the relationship between
the timeframes as aligned, conflicted, mixed, or insufficient. The existing
Judge validates and synthesizes the debate; there is no second technical Judge.

The result is a `MultiTimeframeEndToEndSwingAnalysisResult` containing the
market fetch, released technical review, accepted debate, and a long-only trade
planning outcome. `BuildMultiTimeframeLongTradePlan` runs only after the same
chain is accepted. A plan is eligible only when the final Judge verdict and
daily profile are both bullish. It reuses the existing deterministic daily
planner: latest completed daily close as the technical reference entry,
structure/ATR-derived invalidation stop, exact 2R minimum target, optional 3R
target, and resistance-based feasibility. Any bearish/neutral Judge outcome,
non-bullish daily profile, blocked 2R target, or insufficient stop evidence is
an explicit `NO_TRADE`; the conversation path cannot create a short. Model
validators require every part to reference the exact same dataset/evidence/
decision/verdict chain. The lower-level planner and backtester remain
bidirectional for research, and the older single-timeframe use case remains
available for explicit programmatic callers.

### 3.14 Natural-Language Intent and Instrument Resolution

`PatternSwingIntentInterpreter` is a deliberately conservative local parser.
It accepts explicit single-instrument swing requests such as:

```text
How is Reliance looking for a swing trade?
Analyze RELIANCE for a swing trade.
Give me a swing analysis for TCS.
```

It normalizes whitespace and terminal punctuation, defaults to `NSE` and
`ONE_HOUR`, and rejects blank, unsupported, or multi-instrument requests. This
is deterministic routing, not an LLM-generated interpretation, so a malformed
request cannot silently turn into a different financial task.

`AngelInstrumentMasterResolver` maps the company/symbol query to a validated
`ResolvedInstrument`. It:

- uses HTTPS and bounded timeout/payload controls;
- filters the configured cash-market exchanges;
- validates the complete downloaded JSON before accepting it;
- atomically replaces the cache after a valid refresh;
- uses a fresh validated cache without downloading;
- may use a valid stale cache only after a transient download failure;
- never falls back to stale data when the newly downloaded content itself is
  malformed;
- returns typed not-found or ambiguous-instrument errors rather than guessing.

`ResolveSwingAnalysisRequest` combines these two ports into a
`SwingAnalysisCommand`. `JarvisSwingResearchFacade` creates one operation ID,
emits request/resolution events, and delegates the resolved command to
`JarvisSwingAnalysisCommandHandler`. End-to-end LLM construction remains lazy:
unsupported text and unresolved instruments fail before any LLM gateway or
credential is touched.

### 3.15 Wake-Activated Conversation

`JarvisConversationSession` is the transport-neutral front door. It accepts:

```python
session.handle_text("Hey Jarvis, analyze Reliance for a swing trade")
session.handle_voice_transcript(
    "Hey Jarvis, how is TCS looking for a swing trade?"
)
```

The voice method receives text produced by a future speech-to-text adapter;
there is no microphone or audio vendor in the domain layer. Both channels use
the same `NormalizedWakePhraseDetector`, which is anchored to the start of the
input, case-insensitive, punctuation/whitespace tolerant, and protected from
partial matches such as `Hey Jarvisian`.

State transitions are explicit:

```text
DORMANT -> GREETING -> LISTENING -> PROCESSING -> RESPONDING -> DORMANT
                                      |
                                      +-> LISTENING     (clarification)
                                      +-> FAILED -> DORMANT
```

- A dormant session ignores input without the wake phrase.
- Wake-only input greets the configured user by name and stays listening.
- Wake plus a command executes immediately.
- While listening, a follow-up request does not need to repeat the wake phrase.
- Intent/not-found/ambiguous errors return safe clarification copy and retain
  the listening session.
- A concurrent request during processing receives `BUSY`; the active research
  operation is not replaced.
- Expected application failures are rendered without raw exception details.
- Unexpected programming defects reset the session to dormant and propagate;
  they are not disguised as ordinary application failures.
- A returned LLM failure preserves its candid, secret-safe display and spoken
  messages and returns the session to standby.
- A completed multi-timeframe result retains only its released technical review
  and accepted debate. An activated follow-up such as `Hey Jarvis, where is
  weekly support?` is sent through `AskJarvisJudgeFollowUp` to the same cached
  Judge abstraction, not through a new analysis or an ungrounded chat model.
- Follow-up answers must preserve the retained evidence/package/decision chain
  and may cite only qualified evidence IDs from that chain. The question,
  Judge prompt, structured answer, and Jarvis response remain visible in the
  prompt audit when auditing is enabled.
- A fresh analysis request (for example, `analyze TCS for a swing trade`) runs
  the full pipeline and replaces the retained context. A legacy result without
  paired approved multi-timeframe context clears it rather than risking a
  stale-company answer.

`JarvisConversationTurn` is the stable channel-neutral response envelope. It
contains outcome, before/after states, input channel, display/spoken copy, the
optional typed research response, and an optional grounded Judge follow-up.

### 3.16 UI Workflow Observability

There are two complementary event streams:

1. Conversation transitions: activation, listening, processing, responding,
   failure, and standby.
2. Research workflow stages: request received, instrument resolved, market
   loading, technical analysis, each Bull/Bear debate round, Judge review, and
   terminal completion/failure.

Both use runtime-checkable sink protocols with null and thread-safe in-memory
implementations. Events have stable schemas, contiguous per-operation/session
sequence numbers, fixed secret-safe messages, and timestamps validated at the
IST offset. Sink delivery failures are safely logged and cannot abort the
analysis. A WebSocket/SSE dashboard adapter can therefore stream actual domain
progress rather than scraping logs or simulating agent activity.

### 3.17 Verified Live Run (2026-08-22)

The full wake-to-verdict path was exercised for the first time against real
Angel One credentials and a real Anthropic model, via a new
`examples/conversation_demo.py`:

```python
session.handle_text("Hey Jarvis")
session.handle_text("How is Reliance looking for a swing trade?")
```

This authenticated with Angel One, pulled real hourly candles, ran the
technical pipeline, and ran a real multi-round Bull/Bear/Judge debate. The
run **completed successfully**: a `NEUTRAL` verdict at 35% confidence, with
distinct `bull_case_summary`/`bear_case_summary` and a rationale weighing
genuinely conflicting momentum (MACD/OBV bullish) against structural
evidence (failed resistance break, unresolved bearish FVG, weak ADX) —
exactly the kind of close call the indecisive-confidence threshold exists
to catch.

Getting there surfaced four findings, all now resolved:

1. **Model slug typo** — `anthropic/claude-sonnet-4-5` is not a real model;
   the intended model was `claude-sonnet-5`. A wrong model name manifests
   as `litellm.NotFoundError`/`BadRequestError`, mapped by
   `LiteLLMStructuredGateway._translate_provider_error()` to
   `LLMConfigurationError` — indistinguishable at the failure-code level
   from an actual missing-credential problem, so a "configuration" LLM
   failure is worth checking the model string first, not just credentials.
2. **Model-specific parameter constraints** — `claude-sonnet-5` only
   accepts `temperature=1`; the configured Bull/Bear/Judge temperatures
   (0.4/0.4/0.0 defaults) raised `litellm.UnsupportedParamsError` (a
   `BadRequestError` subclass, so again surfaces as `LLMConfigurationError`).
   Combined with that model also emitting malformed hybrid XML/JSON
   tool-call output on this litellm version (1.97.0, the latest published
   release at the time), the live run switched to
   `claude-haiku-4-5-20251001`, which produced clean tool calls at the
   default temperatures.
3. **Judge output truncation** — `JARVIS_JUDGE_LLM_MAX_TOKENS` (default
   600) was too small once the judge also had to produce
   `bull_case_summary`/`bear_case_summary` plus a full rationale;
   `finish_reason: "length"` cut the response off before the required
   `decisive_evidence_ids` field, which `generate_grounded()` correctly
   treated as a schema failure rather than accepting a truncated verdict.
   Raised to 1500 for the judge role.
4. **The rebuttal-reference bug** described in section 3.10 — found and
   fixed during this run, not before it.

None of these are code defects in the deterministic pipeline — findings
1-3 are LLM/provider/library configuration specifics that need
verifying per deployment (model name, per-model parameter constraints,
and per-role token budget all vary by provider and are not something the
codebase can infer), and finding 4 was a genuine, now-fixed prompt/context
bug. **No automated regression test was added yet for finding 4** (the
argument-id fix was verified only by re-running the live scenario and the
existing 1,224-test suite staying green) — a unit test asserting
`serialize_transcript()` includes each argument's real id, and that a
model-supplied `rebuts_argument_id` matching it round-trips through
`BullBearDebateSubmission` validation, is still worth adding.

### 3.18 Jarvis Persona and Prompt/Conversation Audit Trail

Prompt review is implemented as a dedicated opt-in audit stream, separate from
ordinary operational logs. `PromptAuditRecorder` builds a versioned record with
an IST timestamp, actor, event type, conversation session ID, research operation
ID, and redacted payload. `JsonlPromptAuditSink` appends one JSON object per line
to an owner-only file; null and thread-safe in-memory sinks support disabled and
test configurations.

When enabled, the audit records:

- each activated typed input or voice transcript;
- each deterministic Jarvis greeting, clarification, busy message, completion,
  or safe failure response;
- the exact Jarvis/Bull/Bear/Judge system prompt;
- the complete dynamic message list, including technical evidence, debate
  transcript, precedent context, and retry feedback actually supplied;
- the requested pydantic response model and JSON schema;
- the validated structured response plus provider, model, and attempt count;
- a secret-safe failure record containing the exception type only.

`PromptAuditedLLMGateway` decorates the provider-neutral gateway at composition,
so the agents remain unaware of logging and model providers. Each call made by
`generate_grounded()` is visible, including its citation-correction retry. The
conversation session propagates its session ID through a context variable while
the research façade establishes the operation ID; Bull, Bear, and Judge records
therefore correlate back to the initiating conversation.

Wake, greeting, clarification, and safe failure copy are deterministic. After
the Judge completes, `JarvisPresentationAgent` receives the immutable technical
profile and full debate trail through `StructuredLLMGateway`. Its Chief
Investment Research Assistant persona is calm, sharp, candid, slightly
humorous, concise first, and detailed in the technical explanation. It briefs
the configured user as CEO, distinguishes observed facts from bounded
inference, reports the Technical/Bull/Bear/Judge work, and never replaces their
evidence.

The structured output repeats immutable identifiers and values. A deterministic
validator requires every technical evidence item exactly once, exact evidence
metadata, exact stance/score and verdict/confidence, every Bull/Bear argument
ID, and only evidence actually cited by that side. One correction attempt is
allowed; a second mismatch raises `LLMResponseValidationError`. Missing
fundamental documents are disclosed as absent through fixed application-owned
limitations. For the multi-timeframe path, exact trade disposition, reference
entry, stop method/price, risk, 2R/3R targets, feasibility, and the next-open
execution caveat are attached deterministically after the prose is validated.
The LLM receives them as immutable context but cannot author or change them. If the
presentation LLM fails after research completed, the conversation returns a
candid message while preserving the validated raw research result.

`PromptAuditedLLMGateway` records the persona prompt, complete evidence context,
validated response, and any grounding retry as actor `jarvis`. Raw malformed
provider responses and raw provider exception messages remain excluded.

For multi-timeframe results the LLM-authored portion is deliberately smaller
than the final presentation. The model produces the executive briefing,
weekly/daily summaries, Judge explanation, and Bull/Bear summaries with their
approved citations. The application then attaches every technical finding and
the exact deterministic trade-plan fields. This prevents an eloquent response
from silently changing an indicator value, support/resistance level, stop, or
target. When the policy result is `NO_TRADE`, a hard validator also rejects
generated numerical entry, stop, target, or reward/risk proposals before the
response can reach the user.

Auditing is disabled by default because the requested review trail contains full
conversation text and evidence-rich prompts. Known credential assignments,
sensitive field names, and bearer tokens are redacted, the file uses mode
`0600`, and `logs/` is git-ignored. The current adapter has no rotation,
retention, encryption-at-rest, cross-process ordering, or secure-deletion policy.

### 3.19 Verified Multi-Timeframe Live Run (2026-08-23)

The default conversation path was exercised live for Reliance with real Angel
One hourly history and the configured provider-neutral LLM panel. The run:

1. authenticated successfully with Angel One;
2. resolved `NSE` / `RELIANCE-EQ` / token `2885`;
3. pulled/resumed hourly history and derived completed daily and weekly bars;
4. completed both technical assignments, the evidence-release gate, the full
   Bull/Bear debate, and the same Judge's verdict;
5. returned a **bearish verdict at 62% confidence**;
6. produced the deterministic long-only outcome **`NO_TRADE`**, because a
   bearish final Judge verdict cannot authorize a long setup; and
7. rendered the Jarvis CEO briefing successfully when the persona output
   budget was raised to 5,000 tokens.

Two runs at 2,500 persona tokens ended in the intended candid presentation
fallback because the provider response was truncated before satisfying the
structured schema. This was a capacity/configuration finding, not permission to
weaken evidence validation. The default is now 5,000. After the successful live
run, a hard `NO_TRADE` numeric-hallucination guard was added and verified by the
offline regression suite; that exact post-guard build has not yet been rerun
against a live provider.

The development audit for this investigation used
`logs/jarvis-multitimeframe-live-audit.jsonl`, and the example stored local
research in `data/jarvis_conversation_demo.duckdb`. Both are generated,
git-ignored local artifacts and must not be committed. A harmless SmartAPI
client-IP resolution warning fell back to localhost; authentication and data
retrieval still succeeded.

## 4. Configuration & Environment

```dotenv
ANGEL_API_KEY="your_api_key"
ANGEL_CLIENT_CODE="your_client_code"
ANGEL_PIN="your_pin"
ANGEL_TOTP_SECRET="your_totp_secret"

JARVIS_USER_NAME="Prateek"
JARVIS_WAKE_PHRASE="Hey Jarvis"

JARVIS_LLM_MODEL="provider/model"
JARVIS_BULL_LLM_MODEL=""
JARVIS_BEAR_LLM_MODEL=""
JARVIS_JUDGE_LLM_MODEL=""
JARVIS_PERSONA_LLM_MODEL=""

JARVIS_BULL_LLM_TEMPERATURE="0.4"
JARVIS_BEAR_LLM_TEMPERATURE="0.4"
JARVIS_JUDGE_LLM_TEMPERATURE="0.0"
JARVIS_PERSONA_LLM_TEMPERATURE="0.2"

JARVIS_BULL_LLM_MAX_TOKENS="800"
JARVIS_BEAR_LLM_MAX_TOKENS="800"
JARVIS_JUDGE_LLM_MAX_TOKENS="600"
JARVIS_PERSONA_LLM_MAX_TOKENS="5000"

JARVIS_PROMPT_AUDIT_ENABLED="false"
JARVIS_PROMPT_AUDIT_PATH="logs/jarvis-prompt-audit.jsonl"
```

Loaded lazily, validated with pydantic, wrapped in `SecretStr`, cached
after successful load, rejected via `ConfigurationError` when missing or
blank. `.env` is git-ignored; copy `.env.example` to start.

LLM role overrides inherit the shared model when blank. Each role also has a
validated temperature and token-budget setting in `.env.example`. Provider
credentials (for example `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) remain
external to Jarvis settings. Full-debate preflight checks their availability
before building the mandatory panel; it does not make a network generation
request -- so it cannot catch a wrong model name, a model-specific
parameter constraint, or an undersized token budget; those only surface on
the first real generation call (see section 3.17's findings). A verified
working combination as of 2026-08-22:

```dotenv
JARVIS_LLM_MODEL="anthropic/claude-haiku-4-5-20251001"
JARVIS_JUDGE_LLM_MAX_TOKENS="1500"
JARVIS_PERSONA_LLM_MAX_TOKENS="5000"
```

`claude-haiku-4-5-20251001` works cleanly at the default temperatures
(bull/bear 0.4, judge 0.0) and default bull/bear token budgets (800). The
judge's token budget needed raising, since it must fit
`bull_case_summary` + `bear_case_summary` + a full rationale in one
response. The multi-timeframe CEO presentation was also live-verified at
5,000 tokens; 2,500 repeatedly truncated the required structured response and
correctly triggered the candid presentation fallback. Other models may have
their own constraints (e.g.
`claude-sonnet-5` only accepts `temperature=1`) -- these are provider/model
facts, not something `LLMSettings` validates or the codebase can infer.

Instrument-master defaults point to Angel One's HTTPS daily master and
`data/cache/angel_instrument_master.json`, with a one-day cache TTL, 30-second
download timeout, and bounded payload. The cache is generated external data
and is git-ignored.

Prompt auditing must be explicitly enabled. It is intended for local prompt
review and should remain disabled when conversation/evidence content cannot be
stored under the file system's access and retention controls.

## 5. Testing

```bash
# full suite (unit + integration)
.venv/bin/python -m unittest discover -s tests -v

# unit only
.venv/bin/python -m unittest discover -s tests/unit -v
```

Current count: **1,295 tests**, fully offline. Conventions to preserve:

- Prefer injected stub classes and fake functions at gateway/clock/storage
  boundaries. Limited standard-library patching is used where the unit under
  test owns module-level composition or context behavior.
- Debate-layer tests share fixtures in `tests/unit/_debate_fixtures.py`
  (`build_market_series`, `build_approved_technical_result`,
  `build_precedent_summary`) rather than hand-rolling technical profiles.
- New agent/debate prompt changes should keep the
  `test_prompt_has_role_context_system_prompt_and_feedback_sections`-style
  lock-in tests that assert on prompt structure, not just output.

## 6. Known Gaps / Not Yet Implemented

- **No microphone/audio adapters.** `handle_voice_transcript()` accepts an
  already-transcribed string. Continuous microphone capture, acoustic wake-word
  detection, speech-to-text, and text-to-speech are not implemented.
- **No application transport or dashboard.** Conversation and research event
  contracts exist, but there is no HTTP/WebSocket/SSE server, browser client,
  or 3D Jarvis UI yet.
- **Prompt audit retention is local-only.** The JSONL audit is intentionally
  diagnostic and has no rotation, retention scheduler, encryption-at-rest,
  multi-process sequencing, or secure-deletion workflow yet.
- **The complete multi-timeframe result is not yet normalized as its own
  dashboard aggregate.** Hourly market series and debate-compatible research
  use the existing database-neutral archive, but the paired daily/weekly
  release, follow-up context, CEO presentation, and long-only policy result do
  not yet have dedicated normalized DuckDB tables. The validated result exists
  in memory for the conversation turn. Add repository ports before treating it
  as durable dashboard history.
- **NLU is intentionally narrow.** The pattern interpreter supports explicit
  single-instrument swing-analysis requests. General conversation, arbitrary
  intervals, comparisons, and other investment intents need additional typed
  interpreters/routers.
- **Long-only is enforced only at the user-facing multi-timeframe policy.**
  The lower-level deterministic planner, execution engine, storage schema, and
  walk-forward metrics intentionally still support both long and short research
  scenarios. The default Jarvis conversation can release only long or no-trade.
- **No fundamental, sentiment, or macro analysis** anywhere in the repo,
  not even stubs (P&L/valuation metrics, news/analyst sentiment, FII/DII
  flows, macro/rate data — all still just the target architecture,
  section 7).
- **No debate/verdict *quality* eval harness.** 1,295 tests verify the
  pipeline is *implemented correctly* (schemas, citations, determinism,
  chain-of-custody) — none of them score whether an argument was good or
  a verdict was right against what actually happened next. The
  walk-forward backtest engine does this kind of realized-outcome scoring
  for the older single-perspective pipeline but was never extended to
  grade debate verdicts.
- **Angel One rate-limit defaults are estimates** (`RollingFetchConfig`,
  section 3.12) — verify against current SmartAPI docs/account limits
  before relying on large historical pulls.
- **No Orchestrator & Consensus layer** in the target-architecture sense
  (confidence-weighted synthesis across technical + fundamental +
  sentiment + macro) — today's debate layer synthesizes only technical
  evidence, and the older `AgentOrchestrator` pipeline is single-opinion
  audit, not multi-perspective consensus.
- **No regression test for the rebuttal-reference fix** (section 3.10,
  section 3.17 finding 4) — only verified by a live re-run and the
  existing suite staying green. Worth adding a unit test asserting
  `serialize_transcript()` shows each argument's real id and that a
  matching `rebuts_argument_id` round-trips through
  `BullBearDebateSubmission` validation.
- **LLM preflight can't catch model-specific runtime failures.**
  `LLMPreflightValidator` only checks model-string resolution and
  credential presence, both local/offline — it cannot detect a wrong
  model slug, a model-specific parameter constraint (e.g. a fixed
  temperature), or an undersized token budget, since none of those
  surface without a real generation call. These are currently only
  caught by manual live testing (section 3.17), not preflight or the
  automated suite.
- **Live actionable-plan coverage is still missing.** The exact 2R/3R long
  policy is rigorously covered offline, and the live Reliance run verified the
  bearish `NO_TRADE` path. A naturally bullish live dataset has not yet been
  observed end-to-end; do not force a bullish verdict merely to test it.

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
  and catalysts — technical planning and debate verdict components exist, but
  the unified dashboard/document-grounded report is not yet built.

## 8. Document History

- **2026-08-23**: Connected the multi-timeframe result to the existing
  deterministic trade planner and Jarvis CEO presenter. Added a chain-validated
  long-only policy, exact 2R/3R price fields, structural feasibility, explicit
  no-trade outcomes, immutable multi-timeframe presentation findings, and the
  reference-close/next-open caveat. A live Reliance run authenticated with
  Angel One, completed the daily/weekly debate, returned a bearish verdict at
  62%, and rendered the CEO briefing with deterministic `NO TRADE`. Live
  validation established a 5,000-token persona budget; 2,500 caused structured
  output truncation. Added a no-trade hallucination guard preventing generated
  numerical entry/stop/target/RR claims. Verified 1,295 tests.
- **2026-08-23**: Made the hourly-to-daily/weekly workflow the default
  natural-language analysis path. Added parallel timeframe agents, the
  existing Judge's evidence-release gate, optimized timeframe-aware Bull/Bear
  prompts, same-Judge synthesis, automatic approved-context propagation into
  the conversation response, and grounded follow-up routing across wake cycles.
  Added chain-of-custody and stale-context protections. Verified 1,288 tests,
  bytecode compilation, dependency consistency, and diff hygiene.
- **2026-08-22**: Implemented the provider-neutral Jarvis Chief Investment
  Research Assistant presentation agent after the Judge. Added a structured
  CEO briefing, complete per-evidence technical explanation, Bull/Bear/Judge
  explanation, immutable-evidence validation with one correction retry,
  graceful presentation failure that preserves the research result, separate
  persona model settings, and exact persona prompt/response auditing. Verified
  the complete offline suite at 1,245 tests.
- **2026-08-22**: Added the opt-in prompt/conversation JSONL audit trail
  (section 3.18), including session/operation correlation, exact agent-level
  prompts and response schemas, validated structured outputs, credential
  redaction, failure isolation, concurrent append validation, and owner-only
  file permissions. Verified the complete offline suite at 1,238 tests after
  the final audit coverage was added.
- **2026-08-22**: First verified live run of the full wake-to-verdict path
  against real Angel One credentials and a real Anthropic model (section
  3.17). Fixed a genuine bug found during that run: `serialize_transcript()`
  never showed agents their own `argument_id`, so a rebuttal reference
  could never be produced correctly and crashed the debate with an
  uncaught validation error (section 3.10). Documented three
  model/provider/library configuration findings (wrong model slug,
  model-specific temperature constraint, undersized judge token budget)
  and a verified-working model/config combination in section 4. Added
  `examples/conversation_demo.py`. Test count unchanged at 1,224 (no new
  regression test yet for the rebuttal-reference fix -- see section 6).
- **2026-08-21**: Updated through commit `42bf9e8`. Added the provider-neutral
  mandatory debate composition, conservative natural-language request path,
  cached Angel instrument resolution, UI-safe LLM failures, ordered workflow
  events, and wake-activated text/voice-transcript conversation state machine.
  Refreshed the verified offline baseline to 1,224 tests.
- **2026-08-21**: Full rewrite. Previous version described only the
  Phase-1 market-data baseline (69 tests) and had been stale since Phase 2
  (technical analysis + execution pipeline, commit `2351798`) through the
  debate layer and end-to-end wiring (commit range ending around
  `c73dec9` and this session's work). This version reflects the codebase
  through the Bull/Bear debate layer, rolling market-data pull, and
  end-to-end wiring, at 1,049 passing tests.
