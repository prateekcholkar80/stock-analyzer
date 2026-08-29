# Jarvis: Current Baseline

This document describes the system as it exists today across market data,
technical analysis, backtesting, storage, provider-neutral LLM debate,
natural-language routing, instrument resolution, workflow observability, and
wake-activated conversation. It is the authoritative implementation baseline;
`README.md` provides the shorter project-level view.

Automated test count as of this writing: **1,874 Python tests** (`tests/unit` +
`tests/integration`) plus **36 browser tests**, all passing without live broker
or LLM credentials. The browser validation includes a production build.

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
    ticker_resolution_agent.py  resolve_via_llm() -- shortlist-constrained ticker proposal (TICKER_RESOLVER role)
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
    accumulation.py            Daily/weekly accumulation lifecycle and close-confirmed liquidity sweeps
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
    config.py                   Shared/per-role model, temperature, and token settings (roles now include TICKER_RESOLVER)
    factory.py                  Role-bound gateway construction and caching
    audited_gateway.py          Provider-neutral prompt/response audit decorator
    preflight.py                Local full-panel provider/credential readiness check
    client.py                   Older direct structured-output client retained for compatibility

  tts/                          Provider-neutral text-to-speech (spoken replies)
    gateway.py                  TextToSpeechSynthesizer Protocol + SpeechSynthesis model
    config.py                   TextToSpeechSettings (JARVIS_TTS_*, one swappable provider, no per-role dimension)
    factory.py                  Provider-keyed synthesizer construction/caching (google, elevenlabs)
    audited_gateway.py          Metadata-only prompt-audit decorator (never records audio or text)
    adapters/google_tts.py, adapters/elevenlabs_tts.py

  stt/                          Provider-neutral speech-to-text (voice input)
    gateway.py                  SpeechToTextTranscriber Protocol + Transcription model (empty transcript is valid data)
    config.py                   SpeechToTextSettings (JARVIS_STT_*, language independent of TTS by design)
    factory.py                  Provider-keyed transcriber construction/caching (google, elevenlabs)
    audited_gateway.py          Metadata-only prompt-audit decorator (never records audio or transcript text)
    adapters/google_stt.py, adapters/elevenlabs_stt.py

  conversation/
    config.py                   Configured user name, wake phrase, and consecutive-resolution-failure refresh threshold
    wake_word.py                Anchored detector shared by text and voice transcripts
    session.py                  Dormant/listening/processing/awaiting_confirmation conversation state machine
    pending_confirmation.py     PendingConfirmation carrier + deterministic non-LLM classify_yes_no()
    events.py                   ConversationEventSink and ordered IST event emitter

  audit/
    prompt_audit.py             Opt-in redacted JSONL conversation/prompt trail

  intents/
    swing_analysis.py           Conservative local swing-request interpreter

  instruments/
    in_memory.py                Deterministic exact/normalized instrument matching (+ list_instruments())
    angel_master.py             Bounded download, validation, and sticky cache (TTL removed; refresh() only)
    classification.py           MarketCapClass, ClassifiedInstrument, AMFI name->symbol match + MatchReport
    amfi_market_cap.py          AmfiMarketCapCatalog -- AMFI biannual large/mid/small-cap xlsx, JSON cache
    nse_sector_master.py        NseSectorMasterCatalog -- NSE EQUITY_L.csv sector/industry by exact symbol, JSON cache
    catalog.py                  NseInstrumentCatalog + build_nse_instrument_catalog() (identity + cap-class + sector)
    candidate_shortlist.py      shortlist_candidates() -- deterministic, ranked, non-LLM candidate list

  commands/swing_analysis.py    UI-safe command boundary and LLM failure envelope
  facades/swing_research.py     Natural language -> resolution -> complete workflow (+ ticker_resolution_executor)
  composition/                  Lazy wiring for debate, research, conversation, browser runner, and speech
    speech.py                   compose_jarvis_speech_synthesis()/compose_jarvis_speech_transcription() (lazy, credential-on-first-use)
    browser.py                  Now returns JarvisBrowserApplication(runner, conversation)
  api/                          FastAPI DTOs, capability authorization, and HTTP routes (+ optional /speech and /transcribe)
  conversation/browser.py      Wake-aware asynchronous browser coordinator
  conversation/follow_up.py    Shared conservative follow-up classifier
  workflow/events.py            Research progress events for UI/voice consumers
  workflow/operations.py        Browser operation registry port and in-memory adapter
  workflow/browser_runner.py    Bounded runner, result/context ports, and Jarvis operation handler
  presentation/dashboard.py     Bounded evidence-preserving dashboard projector
  presentation/technical_chart.py  Server-owned chart geometry, patterns, CPR, and overlays

  gateways/fundamentals.py        Five-capability, read-only fundamental
                                  request/result and runtime Protocol boundary

  fundamentals/
    tijori_mcp_contracts.py       Offline-safe five-tool transport envelopes and
                                  strict synthetic provider-response contracts
    adapters/tijori_mcp.py        Network-free Tijori-to-domain normalization,
                                  capability mapping, and safe failure translation
    transports/stdio_mcp.py       Pinned local MCP JSON-RPC process boundary,
                                  session isolation, timeouts, and cleanup

  models/                    Pydantic domain models (all frozen/validated)
    fundamentals.py             Tenant-scoped, provider-neutral fundamental
                                source/fact/lineage/conflict/snapshot contracts
    fundamental_storage.py      Semantic cache keys, ten-day retention,
                                stored snapshots, queries, and summaries
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
    browser_operations.py        Browser session/operation/cancellation/replay contracts
    browser_conversation.py      Browser wake state, turns, and event replay contracts
    analysis_timeframe.py        Shared daily/weekly identity and interval mapping
    market_refresh.py            Definite same-session intraday-gap contract
    accumulation.py              Accumulation-zone metrics/events and liquidity-sweep contracts
    technical_setup.py           Canonical bullish/bearish setup-step matrices
    timeframe_interpretation.py  Alignment, readiness, risk, and 2R/3R interpretation
    trade_decision.py            BUY/NO_TRADE condition and blocker vocabulary
    dashboard.py                 Versioned quote/refresh/chart browser read model
    ticker_resolution.py         TickerResolutionChoice -- shortlist-groundable resolution draft (0..1 symbols)
    conversation.py              Input, state (+ AWAITING_CONFIRMATION), outcome (+ CONFIRMATION_REQUESTED), turn, transition
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
    fundamental_repositories.py Database-neutral fundamental cache Protocol
    adapters/fundamental_in_memory.py Thread-safe tenant-isolated fundamental cache
    repositories.py              MarketSeriesRepository/BacktestRunRepository/DebateRunRepository Protocols -> JarvisStorageAdapter
    adapters/in_memory.py         InMemoryJarvisStorage (test double, full Protocol conformance)
    adapters/duckdb.py            DuckDBJarvisStorage (schema v4: immutable JSON envelopes + normalized tables)

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
    build_multi_timeframe_swing_interpretation.py  Deterministic setup/alignment/decision explanation
    run_end_to_end_multi_timeframe_swing_analysis.py  Default hourly -> two timeframes -> debate -> plan path
    ask_jarvis_judge_follow_up.py  Evidence-locked follow-up questions to the same Judge
    resolve_ticker_conversationally.py  ResolveTickerConversationally -- shortlist + constrained LLM + catalog refresh

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
  continuation-handoff.md        Resume-without-chat-history handoff
  quant-model.md                 NautilusTrader/KNN/regime quant-engine implementation plan (not yet built)

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
  TOTP. `get_ltp()` and `get_historical_candles()` remain one-shot data calls,
  but an expired-session response now triggers one bounded re-authentication
  and one replay. A second rejection stops; it cannot loop indefinitely. The
  third-party SDK request logger is disabled because it previously exposed the
  API key in an upstream request log. Jarvis lifecycle logs contain fixed,
  redacted metadata and never raw vendor messages.
- `MarketDataService` (`app/services/market_data.py`): converts raw Angel
  One dict responses into validated `MarketQuote`/`HistoricalCandleSeries`
  models; external dictionaries never cross this boundary unconverted.
- A live `MarketQuote` is not a candle and is never substituted into technical
  history. It carries its own broker observation time. The dashboard therefore
  labels `Broker LTP` separately from each timeframe's last completed
  `analysis close`; a difference is expected whenever the broker quote is newer
  than the completed daily/weekly candle.

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

`app/analytics/accumulation.py` adds deterministic daily and weekly
price-volume base detection. It measures range width, close containment,
boundary touches/rejections, normalized slope, ATR compression, non-zero
volume coverage, bullish-volume share, down-volume contraction, OBV slope,
breakout-volume multiple, and a bounded confidence score. Daily and weekly use
separate configurable window/range profiles. Overlapping candidates are
suppressed deterministically, identifiers are content-derived, and future
candles beyond `as_of` cannot change an earlier result.

An accumulation zone moves only through the allowed lifecycle graph:

```text
forming -> confirmed -> breakout -> retesting -> holding_as_support
   |           |           |            |
   +-----------+-----------+------------+-> invalidated / expired
                           +--------------> failed_breakout
```

Liquidity sweeps are attached to a known accumulation boundary. A sell-side
sweep must trade below the boundary and close back at/above it; a buy-side
sweep must trade above and close back at/below it. The breach and close-based
reclaim must occur within the configured bounded window. A wick breach alone,
an invalid geometry, a reversed timestamp, or an event owned by another
timeframe is rejected rather than described as a sweep.

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
`DuckDBJarvisStorage`. The separate `FundamentalSnapshotRepository` is
implemented by `InMemoryFundamentalSnapshotRepository` and the same
`DuckDBJarvisStorage` (**schema version 4**).

Storage design, consistent across the persisted aggregate repositories:
- Immutable JSON envelope (the exact validated aggregate, for replay/audit)
  **plus** normalized relational tables (for dashboard queries without
  re-decoding JSON).
- Every identifier is content-addressed (SHA-256 of the validated payload);
  saving identical content twice is idempotent, saving different content
  under an existing identifier raises `StorageConflictError`.
- No `REFERENCES` foreign-key constraints anywhere (a deliberate,
  repo-wide convention) — dependent-row deletes are handled explicitly by
  adapter transaction code, not hidden database cascades.

DuckDB tables as of schema v4: `jarvis_storage_metadata`,
`jarvis_market_series`, `jarvis_market_candles`, `jarvis_instruments`,
`jarvis_instrument_symbols`, `jarvis_strategy_configurations`,
`jarvis_strategy_weights`, `jarvis_backtest_runs` (+ 8 evaluation/trade/
equity/performance detail tables), `jarvis_debate_runs` +
`jarvis_debate_signal_signature`, and six fundamental-cache relations:
`jarvis_fundamental_snapshots`, request statements, request period types,
sources, facts, and conflicts. Full backtest-table detail and example dashboard
queries: `docs/backtest-storage-schema.md`.

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
  stored. A resume re-fetches a configurable overlap (default seven calendar
  days) so broker corrections to recent candles can be detected. Splits the
  needed range into `max_days_per_chunk`-day windows
  (default 30) with `inter_request_delay_seconds` (default 1.0) between
  Angel One calls, merges newly-fetched candles with whatever was already
  stored (dedup by timestamp, new data wins), and archives the merged series as
  a new immutable dataset only when at least one candle is new or corrected.
  An unchanged refresh reuses the existing dataset identity. The receipt
  reports new, corrected, and duplicate fetched counts; chunk count; requested,
  stored, resumed, and checked timestamps; adapter identity; and whether the
  dataset was reused. **`max_days_per_chunk` and
  `inter_request_delay_seconds` are conservative estimates, not verified
  against Angel One's current SmartAPI rate limits/range caps — tune
  before relying on this for large historical pulls.** Built for hourly
  (`ONE_HOUR`) as the single source of truth, specifically so daily/
  weekly views can be derived locally rather than pulled as separate,
  potentially-drifting datasets.

- Gap detection is deliberately narrow. A missing cadence interval is reported
  only when two adjacent candles are on the same IST calendar date. Overnight,
  weekend, and exchange-holiday gaps are therefore not mislabelled as missing
  broker data. Conversely, because there is not yet an exchange-session
  calendar, the receipt calls these `intraday_gaps`, not a proof that the
  exchange actually published a candle at that timestamp.
- Resume identity includes the exact normalized display symbol in addition to
  exchange, symbol token, and interval. A stored dataset for a renamed or
  mismatched symbol is not silently merged. Chunks with a different instrument,
  interval, or source are rejected.

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

Workflow events now use `jarvis.workflow_event.v2`. The stable `stage` remains
the coarse lifecycle state, while `WorkflowActivityDescriptor` supplies a
namespaced activity ID, participant ID, generic participant kind, display label,
and optional timeframe. The UI can therefore specialize known participants
such as `technical.daily_analyst` and `debate.bull`, while safely rendering an
unknown future participant as a generic analyst/service/judge card. Adding
`fundamentals.financial_statement_analyst` or `research.news_analyst` does not
require a new event envelope.

The live multi-timeframe path emits real events for hourly loading, completed
daily and weekly aggregation, parallel daily/weekly analysis, Judge evidence
release, Bull/Bear rounds, Judge verdict review, and deterministic trade
planning. Start/completed/failed states surround the actual operation; failed
validation cannot produce a false completed animation. The asynchronous browser
handler now extends that same sequence through `PRESENTATION`, terminal
completion, and later `FOLLOW_UP` operations.

The transport-neutral browser lifecycle is represented by frozen models in
`app/models/browser_operations.py` and the `BrowserOperationRegistry` port.
`InMemoryBrowserOperationRegistry` provides the first local adapter. It enforces
one active operation per open session, idempotent submission keys, immutable
request identity, queued/running/cancellation/terminal transitions, monotonic
IST timestamps, secret-safe failures, and cooperative cancellation. It also
implements `WorkflowEventSink`, stores only contiguous events for known active
operations, and serves bounded cursor pages for reconnect/replay. A completion
may legitimately win a race with a cancellation request, but the cancellation
timestamp remains in the terminal snapshot.

`AsyncBrowserOperationRunner` in `app/workflow/browser_runner.py` is the
application-service owner of execution. It submits validated requests to a
bounded thread pool, drives the registry lifecycle, supports idempotent submit
and cooperative cancellation, stores the immutable `BrowserOperationOutput`
separately from progress events, and emits exactly one outer terminal event.
The existing synchronous research façade accepts an externally owned operation
ID and emitter, so all underlying analyst events remain in one contiguous
sequence and existing synchronous callers remain compatible.

`JarvisBrowserOperationHandler` invokes the existing swing-research workflow,
then the evidence-locked CEO presenter. A presentation-provider failure keeps
the validated raw analysis available and returns a separate safe presentation
failure. Only approved multi-timeframe review/debate context is retained per
browser session; a later `JUDGE_FOLLOW_UP` operation receives that exact context
and cannot silently reuse a failed replacement analysis. Expected LLM and
follow-up failures are converted to browser-safe codes/messages. Unexpected
exceptions produce no unverified result and expose no provider detail.

The registry, result store, context store, and capability-token authorizer
currently have thread-safe in-memory adapters. The FastAPI adapter provides
session create/close, idempotent operation submit, status/result polling,
cancellation, and bounded cursor replay. It exposes results only after the
registry is officially complete, closing the brief result-save/terminal-state
race. Session routes require `X-Jarvis-Session-Token`; only SHA-256 token
digests are retained. Cross-session operation access is not disclosed.

The runner itself remains independent of FastAPI. `examples/browser_api.py`
provides live composition with Angel One, DuckDB market archival, lazy LLM
roles, bounded workers, a configurable exact-origin CORS allowlist, and orderly
runner/database shutdown. A WebSocket alternative remains optional rather than
required for the first browser client.

Authenticated SSE is implemented for both operation progress and conversation
state. Each stream replays durable in-memory cursor history before waiting for
new events, supports qualified `Last-Event-ID`, sends bounded heartbeats, and
ends with an authoritative terminal snapshot. The token stays in the request
header, so browser clients must use `fetch()` streaming rather than native
`EventSource` or a query-string credential.

`BrowserConversationCoordinator` is deliberately separate from the original
synchronous `JarvisConversationSession`. While dormant it ignores text lacking
the configured wake phrase. Text and voice transcripts share one wake detector,
and activation emits greeting/listening events plus the configured-name greeting.
Commands are submitted to the bounded operation runner rather than executed in
the HTTP request. Terminal operation state is reconciled into responding,
failed, or cancelled language; an explicit sleep acknowledgement returns to
dormant. Only completion carrying approved multi-timeframe review and debate
sets follow-up context. New company research clears that flag before execution;
qualified support/resistance/pivot/evidence questions can then route to the
existing Judge follow-up operation without inventing or replacing evidence.

`JarvisDashboardProjector` now converts a completed multi-timeframe operation
into the stable `jarvis.dashboard.v1` browser contract. It exposes bounded
daily/weekly candle windows while retaining source counts, analyst metrics,
qualified evidence with decisive markers, confirmed pivots, nearest zone
lifecycle, the unchanged Bull/Bear transcript and Judge verdict, exact 2R/3R
trade fields, workflow activity cards, and the Jarvis presentation. No-trade
results contain no invented entry, stop, or target. The authenticated endpoint
is `GET /api/v1/sessions/{session_id}/operations/{operation_id}/dashboard` and
returns `409` until a compatible swing analysis has completed.

The first responsive browser client now lives in `frontend/`. It creates a
capability-authenticated session, supports the text wake phrase, streams
conversation and operation events with authenticated `fetch()`, renders the
reactor and agent-state matrix from actual workflow events, and presents the
completed daily/weekly evidence, price levels, debate, Judge verdict, and exact
trade/no-trade result. Its charts are bounded client-side renderings of the
dashboard contract; no indicator is recalculated in JavaScript. Audio capture,
speech recognition, and speech synthesis are deliberately not part of this
increment.

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

### 3.20 Deterministic Setup and Multi-Timeframe Interpretation

The completed result now contains an additive
`MultiTimeframeSwingInterpretation`. This layer answers two different
questions without asking the LLM to invent a scoring policy:

- **Weekly:** what is the broader structural condition and structural risk?
- **Daily:** is the long setup tactically ready, developing, blocked, or
  unsupported?

Each timeframe carries the complete canonical setup matrices below. A step is
never omitted merely because it is absent; it is explicitly `confirmed`,
`developing`, `pending`, `contradicted`, `invalidated`, or `unavailable`.

```text
Bullish matrix (9 steps)                Bearish matrix (8 steps)
1. Prior downtrend                      1. Uptrend exhaustion
2. Bullish CHOCH                        2. Buy-side liquidity sweep
3. BOS above resistance                3. Bearish CHOCH
4. Volume expansion                    4. BOS below support
5. Pullback into FVG or support        5. Volume expansion
6. EMA20 above EMA50                   6. Bearish FVG retest
7. RSI above 50                        7. EMA20 below EMA50
8. Confirmed higher low                8. Bearish RSI divergence
9. Confirmed next higher high
```

Every evidenced step contains timeframe-qualified evidence IDs, observed and
available timestamps, thresholds, measured values, and an explanation. A
confirmed state requires evidence that was available by the evaluation time.
Steps with no qualifying source remain unavailable/pending; the builder does
not turn an absent signal into a negative or positive finding.

The combined interpretation reports:

- alignment: `aligned_bullish`, `aligned_bearish`, `aligned_neutral`, `mixed`,
  `conflicted`, or `insufficient`;
- tactical readiness: `ready`, `developing`, `blocked`, `not_applicable`, or
  `insufficient`;
- structural risk: `low`, `moderate`, `high`, `prohibitive`, or `unknown`;
- exact 2R and 3R feasibility, including the blocking resistance evidence when
  structure prevents the target;
- market condition separately from action; and
- deterministic conditions that would need to change before reconsideration.

The public action vocabulary is exactly `BUY` or `NO_TRADE`. The system is not
named “long-only” in user-facing language, but its actionable side is long:
bearish, neutral, conflicted, or insufficient conditions can only produce
`NO_TRADE`. A bullish Judge verdict is necessary but not sufficient; daily
readiness, safe weekly structure, an available structural stop, and feasible
minimum 2R space are also required. A bullish bias blocked by resistance is
displayed as **Bullish bias / No Trade**, never coloured or phrased as an
approved buy. The lower-level research/backtest engine still supports shorts,
but that capability is not reachable through this conversational policy.

The Judge cannot rewrite the daily or weekly deterministic character. The
interpretation is built from the released technical chain plus the accepted
verdict and planner output, and model validators reject evidence from another
timeframe, operation, review chain, or future timestamp. A `NO_TRADE` result
cannot expose hypothetical entry, stop, target, or reward/risk numbers.

### 3.21 Historical Refresh, Quote and Chart Provenance

Repeated analysis of the same instrument does not normally download the full
year again:

1. Jarvis finds the latest immutable stored hourly series for the exact
   exchange/token/symbol/interval identity.
2. It re-fetches a seven-calendar-day correction overlap and then the range to
   the requested cutoff, using bounded chunks.
3. Duplicate timestamps inside the broker response are collapsed.
4. A newly returned candle replaces the stored candle at the same timestamp;
   an actual OHLCV change increments `corrected_candle_count`.
5. New timestamps increment `new_candle_count`.
6. If neither new nor corrected candles exist, the prior dataset is reused and
   no duplicate immutable dataset is written.

`jarvis.dashboard_refresh.v1` exposes this provenance as `initial`,
`incremental`, or `unchanged`. Its validator reconciles the existing/new/final
counts and rejects impossible combinations such as an initial fetch claiming a
resume point or an unchanged fetch claiming new candles.

The current broker quote and technical reference close are intentionally
different concepts:

- `Broker LTP` is a point-in-time quote observed for this operation.
- `daily analysis close` is the most recent completed daily candle used by the
  deterministic daily agent.
- `weekly analysis close` is the most recent completed weekly candle used by
  the weekly agent.

The live quote is never injected into a completed candle. This avoids corrupting
OHLCV history and explains why the chart can legitimately show an LTP different
from the daily/weekly close. The result contract keeps `latest_quote` optional
for compatibility with collaborators that do not implement quote loading. In
the currently composed live path, however, a quote-provider failure occurs
inside market-data loading and safely fails the operation; it is not silently
ignored. Whenever a compatible completed result has no quote, the UI says it is
unavailable instead of relabelling a candle close as LTP.

Daily charts visually compress Saturdays, Sundays, and weekday dates absent
from the returned series, so a holiday or missing-date interval does not leave
an artificial horizontal gap. Weekly charts are not given daily range breaks.
This changes only x-axis display; no candle is fabricated, shifted, or
interpolated. The refresh receipt separately reports definite gaps only between
same-day intraday candles.

### 3.22 Plotly Technical Charts and Browser Decision View

The browser renders daily and weekly charts separately with Plotly. Chart
geometry is calculated in Python from the exact assigned series and projected
through `jarvis.dashboard.v1`; JavaScript only selects and renders supplied
data. Default payload limits are 260 daily and 104 weekly candles while
`source_candle_count` preserves the full source count.

Always-visible decision context:

- OHLC candlesticks;
- EMA 20 and EMA 50 (the only default selectable overlays);
- separate horizontal `Broker LTP` and `analysis close` lines;
- immediate confirmed multi-touch support in green when one exists; and
- immediate confirmed multi-touch resistance in red when one exists.

An unavailable immediate level is not guessed from the all-time high/low or a
single visual touch. The UI states `No confirmed multi-touch support below
price` or the resistance equivalent. Accumulation zones are additional
evidence; they are not silently promoted to support/resistance because their
confirmation and lifecycle contracts differ.

The collapsed **Indicators & evidence** drawer offers `Clean`, `Trend`, `Price
action`, `Patterns`, `Decision`, and `Everything` presets plus individual
switches. Calculations remain present even when hidden. Selectable overlays
include:

- EMA 20, EMA 50, Bollinger Bands, RSI 14, volume, and look-ahead-safe CPR;
- FVG lifecycle, confirmed pivots, complete support/resistance lifecycle,
  HH/HL/LH/LL, BOS/CHOCH, accumulation zones, and liquidity sweeps; and
- Doji, Doji Star, Dragonfly/Gravestone/Long-legged Doji, Engulfing, Hammer,
  Hanging Man, Harami/Harami Cross, Morning/Evening Star and Doji variants,
  Piercing, Dark Cloud Cover, Shooting Star, Three Black Crows, Three White
  Soldiers, Marubozu/Closing Marubozu, and Hikkake/Modified Hikkake.

Daily CPR uses the exact previous completed week; weekly CPR uses the exact
previous completed month. It cannot use the current incomplete source period.
Support/resistance zones show price bands, lifecycle state, touches, breaks,
retests, role reversals, and failed breaks. Accumulation bands show lifecycle
and confidence; liquidity markers show side, implication, breach, reclaim,
availability, and volume multiple when available.

Overlay preferences are stored per timeframe in browser `localStorage`.
Corrupt or unavailable local storage is ignored and does not block rendering.
Legacy dashboard results missing the newer chart arrays are normalized to safe
empty arrays and display a compatibility notice; users must run a fresh
analysis to obtain the overlays. A Plotly import/render failure produces a
chart-specific error without changing the underlying research result.

The Judge header distinguishes conclusion from confidence:

- bearish -> red `Bearish / No Trade`, confidence in the bearish verdict;
- neutral -> neutral `Neutral / No Trade`;
- bullish but risk-blocked -> caution `Bullish bias / No Trade`; and
- bullish plus approved risk policy -> green `Buy Setup`.

The confidence percentage is confidence in the evidence-backed verdict, not a
probability that price will rise/fall and not permission to trade. The
executive briefing converts legacy Markdown-like headings into readable points,
adds beginner-language weekly/daily roles, and keeps the detailed evidence
ledger collapsed by default.

Browser page lifetime is also explicit. Initial mount creates a new backend
session and capability token. `pagehide` sends an authenticated, best-effort
session close; component cleanup does the same. A browser back/forward-cache
restore reloads the page so stale JavaScript state cannot revive an old
session. This is best-effort cleanup, not durable session garbage collection:
an abrupt process/network failure can still leave an in-memory server session
until the API process restarts or later lifecycle management is added.

### 3.23 Validated Corner-Case Matrix

The following cases have explicit automated coverage in the current build:

| Boundary | Accepted behavior | Rejected or conservative behavior |
|---|---|---|
| Broker authentication | Login, quote/history delegation, one expired-session refresh and one replay | Request before login; rejected login; repeated expiry after refresh; raw SDK/vendor failures leaking outward |
| Historical refresh | Initial, incremental, corrected, deduplicated, and unchanged/reused datasets | Cross-instrument/source/interval merge; symbol mismatch; invalid correction overlap |
| Candle gaps | Same-IST-session cadence gaps are reported | Overnight, weekend, and holiday-shaped gaps are not declared missing candles |
| Aggregation | Angel 15:15 start-stamped bar becomes complete only after 15:30 IST; hourly->weekly equals hourly->daily->weekly | Naive cutoff, empty input, same/coarser target, incomplete final daily/weekly bucket by default |
| Accumulation | Daily and weekly profiles; valid breakout/retest/hold; deterministic IDs | Insufficient/trending data returns empty; zero-volume range, future candle, bad prices, invalid lifecycle ordering, or post-terminal transition is rejected/not labelled |
| Liquidity sweep | Sell-side/bullish and buy-side/bearish breach plus close reclaim | Wick-only breach, no reclaim, wrong boundary ownership, reversed/future times, or duplicate evidence is rejected |
| Setup matrices | All 9 bullish and 8 bearish steps always exist with explicit states | Reordered/incomplete matrix, wrong side/timeframe/interval, future evidence, non-finite values, invalid terminal state |
| Interpretation | All alignment states, exact planner prices, explicit no-trade blockers | BUY without bullish alignment/readiness/safe risk/2R; target without feasibility evidence; non-bullish numeric plan; Judge rewriting technical character |
| Dashboard | Bounded charts with full source counts, CPR from prior periods, optional legacy interpretation | Non-completed/wrong-kind/wrong-session operation; mismatched IDs; impossible refresh counts; invalid chart limits |
| Browser operation | Idempotency, one active operation/session, replay, SSE reconnect, cancellation race, safe failure | Idempotency-key reuse with different request, event gaps, illegal/time-reversed transitions, cross-session disclosure |
| Conversation | Dormant ignore, text wake, transcript wake, named greeting, async dispatch, grounded follow-up, sleep | Partial wake phrase, concurrent replacement, stale context after new/failed analysis, follow-up without approved context |
| LLM grounding | Provider-neutral roles, structured validation, one citation-correction retry, safe failure | Invented evidence/argument IDs, malformed output after retry, missing credentials, provider/auth/rate-limit details exposed to UI |
| Presentation | Beginner-readable briefing, exact evidence and trade values, bearish/neutral/caution colour semantics | Persona changing verdict/evidence, omitting evidence, inventing no-trade prices, presenting confidence as outcome probability |
| Browser rendering | SSR build, deterministic initial IST clock placeholder, missing-date compression, optional overlay preferences | Hydration from server/client clock disagreement, missing chart arrays causing `undefined` access, corrupt local preferences blocking the chart |
| Ticker resolution | Ranked deterministic shortlist, one-symbol constrained LLM proposal, yes/no confirm, catalog-refresh offer after N failures, resume after "yes" | Symbol invented off the shortlist, LLM call on an empty shortlist, auto-accept without confirmation, "maybe" treated as "yes", refresh failure crashing the turn |
| Speech (TTS/STT) | Provider chosen by config, empty transcript returns 200, metadata-only audit, routes absent when uncomposed, credential only on first use | Audio or transcript text written to the audit trail, empty/oversized upload accepted, provider/auth detail in the client error, adapter that fails the Protocol check |
| Fundamental evidence | Tenant/provider scope, strict normalized values, source fingerprints, freshness, conflicts, lineage, immutable snapshot | Secret fields, source-rank masquerading, missing/unvalidated lineage, stale upgrades, cycles, unresolved decision-grade conflicts, or secondary-only decision evidence |
| Fundamental cache contract | Semantic per-tenant/provider/issuer/capability scope; immutable request/result/snapshot chain; ten-day maximum retention | Cross-request or cross-tenant chain, invalid scope, failed/rejected payload, stale-at-save entry, overlong retention, unscoped listing, raw payload/session storage |

### 3.24 Conversational Ticker Resolution

Exact instrument resolution is unchanged and still runs first. When it raises
`InstrumentNotFoundError` or `AmbiguousInstrumentError` **and** the
conversational fallback is composed, Jarvis attempts a bounded
deterministic-plus-LLM resolution before falling back to the plain
clarification message. The fallback is opt-in: `compose_jarvis_swing_research`
(and `compose_jarvis_browser_operations`) enable it only when given both an
`AmfiMarketCapCatalog` and an `NseSectorMasterCatalog`, or a pre-built
`ticker_resolution_executor`. With none of these set, behaviour is exactly
today's exact-match-only resolution.

Data sources, each a periodic snapshot with recorded provenance, cached as
git-ignored JSON under `data/cache/`:

- **AMFI market-cap list** (`amfi_market_cap.py`): the SEBI-mandated
  large/mid/small-cap classification, published biannually as an Excel file
  (needs `openpyxl`). The download URL changes every release and is **not**
  auto-resolved — the hardcoded constant, or `AmfiMarketCapConfig.endpoint_url`,
  must be updated each half-year. Keyed by company name, so it is joined to NSE
  symbols by normalized fuzzy name matching; unmatched names are preserved in a
  `MatchReport` rather than dropped silently.
- **NSE sector master** (`nse_sector_master.py`): `EQUITY_L.csv`, keyed by the
  exact tradable symbol, so it joins onto Angel identity directly with no fuzzy
  matching. HTTPS endpoint is required.

`build_nse_instrument_catalog()` composes these into a frozen
`NseInstrumentCatalog` (`ClassifiedInstrument` = Angel identity + `MarketCapClass`
+ sector/industry; unique symbols; timezone-aware `as_of`; carried match report).

Resolution flow (`ResolveTickerConversationally.attempt(command)`):

1. `shortlist_candidates()` builds a deterministic, ranked list of **real**
   catalog entries (symbol-prefix > display-name/alias-prefix > substring),
   capped at 8. Ranking is a hint only; it never auto-selects even a lone
   match.
2. `resolve_via_llm()` shows the shortlist to the `TICKER_RESOLVER` LLM role
   (temperature 0.0, 300 tokens by default). The result model
   `TickerResolutionChoice` holds 0–1 `chosen_symbols` as a tuple validated by
   the same grounding mechanism as debate citations, so the model is
   structurally incapable of returning a symbol that is not on the shortlist.
   An empty shortlist short-circuits to "not found" with no LLM call.
3. The outcome is `resolved_needs_confirmation`, `ambiguous`, or `not_found`.
   It is **never** a final resolution — the caller must obtain an explicit
   yes/no confirmation first.

Confirmation state machine (shared by `JarvisConversationSession` and
`BrowserConversationCoordinator` via `PendingConfirmation` + `classify_yes_no`):

- A single proposed symbol pivots the conversation to
  `AWAITING_CONFIRMATION` with outcome `CONFIRMATION_REQUESTED` and the message
  `Did you mean "<SYMBOL>"? Reply yes or no.` A "yes" resumes the original
  request as `Analyze <SYMBOL> for a swing trade` (carrying the original
  `to_date`); anything not clearly affirmative is treated as "no", increments
  the consecutive-failure counter, and returns to `LISTENING` with a
  clarification prompt.
- After `consecutive_resolution_failures_before_refresh_prompt` consecutive
  failures (default 3, env `JARVIS_RESOLUTION_FAILURE_THRESHOLD`), Jarvis
  instead offers to refresh its company list. A "yes" calls
  `refresh_catalog()` (a refresh failure is swallowed — the retry simply fails
  again through normal handling) and re-runs the original command.
- A successful completed analysis resets the failure counter to zero.
- In the browser coordinator the attempt runs inside `_reconcile_locked` when a
  `SWING_ANALYSIS` operation terminates with failure code
  `instrument.not_found` / `instrument.ambiguous`; it holds the coordinator
  lock during the LLM call, which is acceptable for the single-user local
  deployment and flagged for a future multi-session move. A new allowed
  transition `FAILED -> PROCESSING` lets a confirmed guess resume without a
  fresh wake.

The `TICKER_RESOLVER` role has its own model/temperature/token settings
(`JARVIS_TICKER_RESOLVER_LLM_*`, falling back to the shared model), its own
prompt-audit actor `ticker_resolver`, and its gateway is lazily built on first
use.

### 3.25 Speech Synthesis and Transcription

Two provider-neutral capabilities, structured exactly like the LLM gateway:
one Protocol boundary, a settings model, a provider-keyed factory, a
metadata-only audit decorator, and per-provider adapters. Both are optional and
compose no eager I/O.

- **TTS** (`app/tts/`): `TextToSpeechSynthesizer.synthesize(text=...) ->
  SpeechSynthesis` (validated non-empty audio bytes + provider/voice/encoding/
  media-type/timestamp). `TextToSpeechSettings` (`JARVIS_TTS_PROVIDER`,
  `_VOICE_NAME`, `_LANGUAGE_CODE`, `_AUDIO_ENCODING`, `_SPEAKING_RATE`,
  `_MODEL_ID`). Default provider `google`, default language `en-GB`.
- **STT** (`app/stt/`): `SpeechToTextTranscriber.transcribe(audio=...,
  media_type=...) -> Transcription`. Unlike synthesis, an **empty transcript is
  valid data** ("no speech detected" — a VAD false positive, silence, a cough)
  and never raises; only a genuinely malformed provider response does.
  `SpeechToTextSettings` (`JARVIS_STT_PROVIDER`, `_LANGUAGE_CODE`,
  `_AUDIO_ENCODING`, `_SAMPLE_RATE_HERTZ`, `_MODEL_ID`). The spoken-input
  language is deliberately independent of the reply language.

Provider is a pure configuration switch: `google` and `elevenlabs` adapters are
registered in each factory's `_BUILDER_BY_PROVIDER`; adding a provider is one
registry entry plus an adapter module, never a change to `app/conversation/`,
`app/api/http.py`, or the frontend. Google adapters authenticate via
Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS`); ElevenLabs
adapters use `ELEVENLABS_API_KEY` (same key for both directions) and require an
explicit voice ID. Construction failure, or an adapter that does not satisfy
the Protocol, raises `TTSConfigurationError` / `STTConfigurationError`.

**Audit**: `PromptAuditedTextToSpeechSynthesizer` /
`PromptAuditedSpeechToTextTranscriber` record `TTS_REQUEST/RESPONSE/FAILURE`
and `STT_REQUEST/RESPONSE/FAILURE` events with actor `jarvis` — length,
provider, voice/language, confidence, byte counts, timestamps only. Raw audio
and the synthesized/transcribed **text are never written**; real turns are
already captured by `CONVERSATION_INPUT`/`CONVERSATION_OUTPUT` records, and
duplicating them here would also create orphan records for VAD false positives.

**Exceptions** (`app/exceptions.py`): `TTSError` / `STTError` extend
`ExternalServiceError` and carry a sanitized `TTSFailureContext` /
`STTFailureContext` (provider, voice or language code, operation id, retryable).
Subclasses: `*ConfigurationError`, `*AuthenticationError`,
`*ProviderUnavailableError` (retryable), `*SynthesisError` /
`*TranscriptionError`.

**HTTP** (`app/api/http.py`): `POST /api/v1/sessions/{id}/speech` and `POST
/api/v1/sessions/{id}/transcribe` are registered **only** when a speech /
transcription application is passed to `create_jarvis_http_app`. Both require
the session capability token and run inside `prompt_audit_session_context`.
`/speech` takes JSON `SpeechSynthesisRequest` (`text`, 1–2000 chars) and
returns raw audio via `Response` (a deliberate exception to the
"every route returns a pydantic model" rule). `/transcribe` takes a raw audio
body — empty is `400`, over 10 MiB (or a `Content-Length` over it) is `413` —
and returns `SpeechTranscriptionResponse` (`jarvis.http_transcription.v1`) with
`transcript: null` and status `200` for no-speech audio. `TTSError`/`STTError`
handlers map configuration failures to `503` and provider/auth/synthesis
failures to `502`, always with a generic client-safe message.

**Composition** (`app/composition/speech.py`):
`compose_jarvis_speech_synthesis()` / `compose_jarvis_speech_transcription()`
build lazily; the provider credential is only required on the first real
`/speech` or `/transcribe` call. `examples/browser_api.py` now composes both
alongside `JarvisBrowserApplication(runner, conversation)`.

**Frontend** (`frontend/lib/voice.ts`, `voice-capture.ts`, `app/page.tsx`):
two independent device-local toggles. "Voice replies" posts each
`spoken_message` to `/speech` and plays the returned audio; playback failure or
an uncomposed route never blocks the already-rendered conversation text.
"Voice input" captures the microphone through a `MediaRecorder` and drives a
local amplitude-threshold VAD hysteresis state machine (`isSpeechSegment`:
`idle -> speech -> trailing_silence`); a completed utterance is uploaded to
`/transcribe` and submitted only when `shouldSubmitVoiceTranscript` passes
(non-blank, and — in an active conversation state — above a confidence floor).
Capture is muted for the duration of TTS playback so Jarvis never hears itself.
Corrupt or unavailable `localStorage` falls back to "off" and never blocks
rendering.

### 3.26 Provider-Neutral Fundamental Evidence Foundation

Phase 1, Step 1.1 introduces a domain-only fundamental evidence boundary in
`app/models/fundamentals.py`. It is intentionally independent of Tijori,
Angel One, DuckDB, and the configured LLM. It makes the bring-your-own-service
model explicit: each external connection is scoped by `tenant_id` and
`provider_connection_id`; only a one-way account-reference hash and declared
entitlement metadata may enter the domain model. Passwords, cookies, API keys,
CSRF tokens, and session objects have no fields in these contracts.

Each source records its semantic type and hierarchy rank, period/as-of,
publication and timezone-aware retrieval timestamps, freshness, parser and
provider-schema versions, validation state, and SHA-256 content fingerprint.
The source type describes evidentiary authority, not transport: accessing a
secondary aggregator through an MCP connection does not turn it into a primary
or authoritative connected-system disclosure.

Each normalized fact preserves the original value, strict `Decimal` normalized
value, statement and standardized line-item identity, explicit reporting
period, units/currency, availability, evidence label, confidence, freshness,
validation, conflict status, source references, and parent calculation chain.
Missing, paywalled, not-entitled, malformed, and unavailable facts are explicit
states; they are never inferred into numeric values. Conflicting values remain
separate facts joined by an explained conflict record instead of being silently
averaged.

`FundamentalEvidenceSnapshot` validates the aggregate chain of custody. It
rejects duplicate IDs, unknown or fingerprint-mismatched sources, lineage
cycles, future retrievals, source/fact freshness upgrades, source-label
masquerading, and inconsistent conflicts. A decision-grade release requires
fully validated and current/period-acceptable evidence with no unresolved or
missing facts. Every decision-grade fact must also trace directly or through
parent lineage to user-governing, authoritative connected-system, or primary
public evidence. Provider-standardized data such as Tijori can normalize and
cross-check research, but cannot be the sole decision-grade source.

Phase 1, Step 1.2 adds the runtime-checkable `FundamentalEvidenceGateway` and
versioned request/result contracts in `app/gateways/fundamentals.py`. Its only
approved capabilities are company search, issuer resolution, company overview,
financial statements, and shareholding history. Every request carries the
tenant/provider connection and a content fingerprint. Every response carries
the same binding metadata, adapter/provider-contract fingerprints, ordered
timezone-aware execution timestamps, and a stable result fingerprint.

The capability manifest records run-specific availability and entitlement;
configuration is not treated as proof of readiness. Search/resolution is
bounded and explicitly resolves, abstains, or reports ambiguity. Evidence
retrieval explicitly represents completed, partial, unavailable, not-entitled,
paywalled, not-found, and validation-rejected outcomes. Capability-specific
statement allow-lists prevent unrelated evidence from leaking through a result,
and `validate_fundamental_response_binding()` rejects cross-request,
cross-connection, cross-capability, or timestamp-mismatched responses.

The safe `FundamentalGatewayError` hierarchy carries only capability, provider,
provider-connection, operation, and retryability metadata. The contracts reject
unknown secret or raw-payload fields without echoing their values. There is no
document fetch, screener, write, portfolio, alert, or order capability.

The gateway boundary itself does not implement a Tijori process,
authentication, scorecard, red-flag scan, financial agent, or Bull/Bear/Judge
integration. A separate offline normalization adapter is described below.

Phase 1, Step 1.3 adds the database-neutral fundamental cache contract.
`FundamentalSnapshotCacheKey` identifies reusable data by tenant, provider
connection, provider, issuer, capability, as-of date, and semantic query scope;
execution request IDs and timestamps are intentionally excluded. Consequently,
the same user can repeat an identical analysis without forcing a full pull,
while another tenant, provider account, period range, or requested dataset
always produces a different key.

`StoredFundamentalSnapshot` preserves the entire validated request -> gateway
result -> evidence snapshot chain and verifies every content fingerprint. Its
`retrieved_at` is the gateway completion time. `expires_at` must be later and no
more than ten days after retrieval; expiry is inclusive, so an entry is invalid
at exactly `expires_at`. Shorter retention is allowed when data sensitivity or
new disclosures require it. An already-expired result cannot be saved.

`FundamentalSnapshotQuery` requires tenant, provider connection, and provider
scope for every listing. `FundamentalSnapshotSummary` exposes bounded metadata
without facts or source payloads. The runtime-checkable
`FundamentalSnapshotRepository` requires immutable idempotent saves, scoped
non-expired reads/listings, exact deletion, and startup/opportunistic purge.
Expired rows must never be returned even if physical deletion is pending.

Phase 1, Step 1.4 implements the thread-safe reference adapter in
`app/storage/adapters/fundamental_in_memory.py`. It performs no network access
and stores only validated `StoredFundamentalSnapshot` objects. Every exact read
and delete requires an explicit `FundamentalRepositoryScope`; providing a key
from another tenant or provider connection returns no result. Scoped listing
is deterministic, newest-first, filterable, and paginated, and never exposes
the evidence payload through its summary surface.

An injected timezone-aware clock makes retention behavior deterministic.
Startup loading and every read/write path purge expired entries
opportunistically, expiry remains inclusive, and data with a future
`stored_at` timestamp is rejected. A historical read also refuses to reveal a
snapshot that had not yet been stored at that `as_of` time. Identical saves are
atomic and idempotent; different immutable content under an active semantic key
raises `StorageConflictError`. Once the old row has expired, freshly retrieved
evidence with the same semantic key can be saved, implementing the intended
"reuse for up to ten days, then re-pull" behavior.

The adapter uses an `RLock` and defensive deep copies. Concurrency tests cover
64 simultaneous identical saves and competing-content races without duplicate
rows or corruption. It remains the ephemeral reference implementation; Tijori,
authentication, document processing, provider normalization services, and
agents remain unconnected.

Phase 1, Step 1.5 extends `DuckDBJarvisStorage` to schema version 4 and makes it
conform to the same `FundamentalSnapshotRepository` protocol. The parent table
stores indexed tenant/provider/issuer/capability, fingerprint, lifecycle, and
payload-free summary columns beside an immutable validated JSON envelope.
Separate normalized relations preserve requested statements and period types,
evidence sources, normalized facts, and conflicts for future transparent
dashboard and financial-agent queries without decoding every envelope.

All writes, idempotency checks, child-table replacement, deletes, and expiry
purges are transactional under the adapter's `RLock`. Parameterized SQL applies
mandatory tenant/provider scope and filtering. Startup and opportunistic purge
remove parent and all normalized child rows at inclusive expiry. A valid cache
survives close/reopen; a schema-v3 database migrates to v4 without provider
access; malformed envelopes or fingerprint mismatches fail closed as
`StorageError`. DuckDB behavior is tested directly against the in-memory
adapter, including repeated analysis reuse and automatic replacement after the
ten-day cache ceiling.

The database contains no provider credentials, cookies, browser sessions, raw
HTML, or unrestricted MCP payloads. This step still does not download, install,
authenticate, or invoke Tijori and does not connect any financial agent.

Phase 1, Step 1.6 implements the first offline `TijoriMcpAdapter` vertical
slice in `app/fundamentals/`. The adapter conforms to the provider-neutral
gateway but depends only on an injected `TijoriMcpTransport` protocol. It has
no subprocess, browser, network, login, credential, cookie, or repository
download code. Production Tijori access therefore remains impossible in this
step while the response and evidence boundary can be tested completely.

The transport contract exposes exactly five immutable, read-only tool names:
`search_company`, `resolve_company_ids`, `get_company_overview`,
`get_financials`, and `get_shareholding`. Unknown names, duplicate capability
advertisements, a changed provider-contract version, a mismatched response
tool, and responses beyond the requested exchange/result/history scope fail
closed. Tenant, provider-connection, entitlement, and account-reference data
are passed separately as `ProviderConnectionScope`; tool arguments contain
only the bounded business query and never copy connection/account metadata.

Untrusted tool envelopes accept canonical JSON only and impose byte, nesting,
node, key, collection, and string limits. Success requires a payload; failure
states may not carry one. Synthetic provider shapes use strict field contracts,
finite decimal values, unique identities and fact/source IDs, explicit period
dates, and HTTPS Tijori-owned source URLs without credentials, query strings,
or fragments. Schema drift, unsafe URLs, cross-issuer evidence, capability-
incompatible statements, invalid timestamps, and malformed normalized values
become sanitized `FundamentalResponseValidationError` instances; raw exception
or provider values are never rendered in the safe message.

Successful provider values are released only as `PROVIDER_STANDARDIZED`
sources ranked `STANDARDIZED_PROVIDER` and
`FACT_PROVIDER_STANDARDIZED` facts. They carry deterministic source, record,
payload, evidence, snapshot, adapter, and result fingerprints plus explicit
source/fact lineage. Snapshot posture is capped at `RESEARCH_GRADE`; the
adapter cannot manufacture decision-grade evidence. Unavailable facts remain
low-confidence partial/missing markers. Not-found, not-entitled, paywalled,
and provider-unavailable evidence returns a typed snapshot-free retrieval
state, while authentication, throttling, transport, and protocol failures map
to the existing retry-aware exception hierarchy.

Twenty-one synthetic adapter tests cover protocol conformance, allow-list
immutability, canonical-payload limits, capability and authentication states,
search/resolution outcomes, all three evidence tools, research-grade lineage,
partial/missing facts, paywall/entitlement/unavailable outcomes, issuer and
statement-scope rejection, unsafe locations, schema drift, clock ordering,
secret-free arguments, and sanitized expected/unexpected failures. Together
with the existing foundation, **168 focused fundamental tests** and **1,853
full Python tests** pass. No Tijori source was downloaded or executed, no
provider session was opened, and no provider payload was persisted.

Phase 1, Step 1.7 implements `TijoriStdioMcpTransport`, the concrete local MCP
process boundary, while remaining completely disconnected from upstream
Tijori code and live authentication. Each inspection or tool invocation starts
one pinned server process, performs the MCP `initialize` handshake, sends
`notifications/initialized`, executes exactly one `tools/list` or `tools/call`
request, and terminates the entire isolated process group. This intentionally
simple lifecycle makes timeout and cleanup behavior deterministic; a future
long-lived browser process may replace it behind the unchanged transport
protocol after live-load requirements are measured.

The transport never invokes a shell and inherits no application environment.
It supplies only locale, the opaque scoped session-file path, and the pinned
provider-contract version. Runtime executable and server entrypoint must be
absolute regular files, not symlinks, must match configured SHA-256 hashes on
construction and before every launch, and cannot be group/world writable. The
entrypoint must be owned by the Jarvis process user. Session directories must
be owner-only (`0700` equivalent); scoped session files use a non-identifying
SHA-256 filename, must be regular single-link owner-owned files, have no
group/world permissions (`0600` equivalent), and remain within a configured
size ceiling. Missing or insecure session state fails as authentication rather
than being silently recreated.

Newline-delimited JSON-RPC input/output is bounded by total time, message size,
JSON depth, and node count. Response IDs, protocol version, capabilities,
Jarvis/Tijori experimental handshake metadata, provider-contract version,
catalog tool names, text envelope shape, tool identity, and MCP `isError`
semantics are validated before release. Only the five audited tools may be
called or advertised. Server error text and stderr are discarded; only a
sanitized configuration/authentication/entitlement/rate-limit/unavailable/
protocol classification crosses the boundary. Parent environment secrets are
not inherited.

The synthetic MCP subprocess fixture exercises a real stdio handshake and is
also connected end to end through `TijoriMcpAdapter` into the provider-neutral
gateway result. Tests cover runtime and entrypoint tampering, insecure roots,
files and symlinks, missing sessions, provider mismatch, unauthenticated
metadata, tool allow-list enforcement, malformed/oversized messages,
notifications, mismatched IDs, protocol and contract drift, RPC error mapping,
non-JSON arguments, response-tool mismatch, inconsistent error envelopes,
timeouts, process-group cleanup, parent-environment isolation, and deterministic
non-identifying session paths.

Step 1.7 validation evidence is **21 stdio transport tests**, **189 focused
fundamental tests**, and **1,874 full Python tests**, all passing offline. Python
compilation and `git diff --check` also pass.

This is a transport implementation, not a Tijori installation. The repository
has still not vendored or executed upstream code, created a browser, opened an
authenticated provider session, or made a Tijori request. A hardened pinned
fork and manual user-owned session creation remain separate approval-gated
steps.

Phase 1, Step 1.8 implements the Jarvis-owned minimal local Tijori bridge under
`integrations/tijori-mcp/`. It is derived only from the pinned, statically
audited upstream behavior needed by the approved five-tool surface; upstream
setup/discovery, credential capture, cache, document, screener, market, macro,
and write paths were not copied. Runtime dependencies are pinned exactly to
`@modelcontextprotocol/sdk` 1.30.0, Playwright 1.62.1, and Zod 4.4.3, with a
committed lockfile and a Node 24-only direct-start guard.

The MCP server advertises exactly `search_company`, `resolve_company_ids`,
`get_company_overview`, `get_financials`, and `get_shareholding` as read-only,
idempotent tools. Startup loads only an owner-scoped pre-existing browser
session boundary, performs a bounded authentication probe, and composes the
provider registry only after a positive probe. Missing, expired, malformed, or
unavailable authentication gates every tool without exposing browser/session
details. The local browser runner serializes access, enforces bounded timeouts,
and does not accept credentials as tool arguments.

All five provider handlers now use deterministic, bounded normalization:

- company search and identifier resolution re-read company-page metadata and
  require exchange/symbol/ID consistency;
- company overview maps only supported market-cap, P/E, ROE, and ROCE facts;
- financials maps supported annual/quarterly income statement, balance sheet,
  cash-flow, and EPS rows only when a rendered INR-crore unit and valid period
  are present;
- shareholding maps explicit quarterly promoter, FII, DII, public, and promoter
  pledge percentages without inferring missing categories or totals; and
- every evidence response carries a safe Tijori HTTPS source, observed IST
  date, explicit missing/unknown states, and limitations requiring
  reconciliation to primary company or exchange disclosures. Historical
  `as_of_date` requests fail closed because the provider pages do not establish
  point-in-time publication or restatement history.

Provider HTTP/authentication/paywall/rate-limit failures are reduced to typed,
payload-free result envelopes. Raw HTML, browser state, cookies, credentials,
provider exception text, and unmapped provider rows are never returned or
persisted. Offline MCP composition coverage exercises all five actual handlers
through an authenticated synthetic browser boundary. **111 local bridge tests
pass**. No live browser, Tijori login, provider request, or provider-data write
was performed; user-owned session creation and live validation remain separate
opt-in steps.

Phase 1, Step 1.9 adds the Python composition boundary in
`app/composition/fundamentals.py`. `compose_tijori_fundamental_gateway()`
constructs the existing hardened stdio transport behind `TijoriMcpAdapter` and
returns only the provider-neutral `FundamentalEvidenceGateway`. Composition
validates pinned files and owner-only session-root configuration but does not
start Node, launch Playwright, inspect a session, or call a provider. An
injected transport builder keeps composition deterministic in tests.

`load_tijori_stdio_settings()` reads only non-secret runtime controls:
absolute runtime/server/session-root paths, SHA-256 pins, protocol/provider
contract versions, timeouts, byte ceilings, concurrency, and session-size
limits. It does not read a Tijori username, password, cookie, token, CSRF value,
or account identity. Invalid values produce one sanitized `ConfigurationError`.
The adapter contract defaults to the transport contract; an explicit mismatch
fails during composition rather than on the first provider handshake.

Five focused composition tests and all **194 fundamental/Tijori subsystem
tests pass**. The local bridge baseline remains **111 passing Node tests**.

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

# Optional local Tijori fundamental-evidence bridge (non-secret settings only)
JARVIS_TIJORI_RUNTIME_EXECUTABLE="/absolute/path/to/pinned-node-24"
JARVIS_TIJORI_RUNTIME_SHA256="64-lowercase-hex-characters"
JARVIS_TIJORI_SERVER_ENTRYPOINT="/absolute/path/to/integrations/tijori-mcp/src/index.js"
JARVIS_TIJORI_SERVER_SHA256="64-lowercase-hex-characters"
JARVIS_TIJORI_SESSION_ROOT="/absolute/path/to/owner-only-session-directory"
JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION="tijori.local_contract.v1"

JARVIS_BULL_LLM_TEMPERATURE="0.4"
JARVIS_BEAR_LLM_TEMPERATURE="0.4"
JARVIS_JUDGE_LLM_TEMPERATURE="0.0"
JARVIS_PERSONA_LLM_TEMPERATURE="0.2"

JARVIS_BULL_LLM_MAX_TOKENS="800"
JARVIS_BEAR_LLM_MAX_TOKENS="800"
JARVIS_JUDGE_LLM_MAX_TOKENS="600"
JARVIS_PERSONA_LLM_MAX_TOKENS="5000"

# Ticker-resolver role (partial/fuzzy company names); falls back to JARVIS_LLM_MODEL
JARVIS_TICKER_RESOLVER_LLM_MODEL=""
JARVIS_TICKER_RESOLVER_LLM_TEMPERATURE="0.0"
JARVIS_TICKER_RESOLVER_LLM_MAX_TOKENS="300"
JARVIS_RESOLUTION_FAILURE_THRESHOLD="3"

# Optional text-to-speech (spoken replies). Route absent unless composed.
JARVIS_TTS_PROVIDER="google"           # or "elevenlabs"
JARVIS_TTS_VOICE_NAME="en-GB-Neural2-B"
JARVIS_TTS_LANGUAGE_CODE="en-GB"
JARVIS_TTS_AUDIO_ENCODING="MP3"
JARVIS_TTS_SPEAKING_RATE="1.0"
# JARVIS_TTS_MODEL_ID="eleven_v3"      # ElevenLabs only

# Optional speech-to-text (voice input). Route absent unless composed.
JARVIS_STT_PROVIDER="google"           # or "elevenlabs"
JARVIS_STT_LANGUAGE_CODE="en-IN"
JARVIS_STT_AUDIO_ENCODING="WEBM_OPUS"
# JARVIS_STT_SAMPLE_RATE_HERTZ="16000"
# JARVIS_STT_MODEL_ID="scribe_v1"      # ElevenLabs only

# GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"  # Google TTS/STT
# ELEVENLABS_API_KEY="your_elevenlabs_key"                            # ElevenLabs TTS/STT

JARVIS_PROMPT_AUDIT_ENABLED="false"
JARVIS_PROMPT_AUDIT_PATH="logs/jarvis-prompt-audit.jsonl"
```

The former `ANGEL_INSTRUMENT_CACHE_TTL_SECONDS` was removed. The downloaded
Angel instrument-master cache (and the AMFI/NSE catalog JSON caches under
`data/cache/`, all git-ignored) are now sticky: they are only replaced by an
explicit `.refresh()`. AMFI's biannual URL does not follow a predictable
pattern and must be updated in code or via `AmfiMarketCapConfig.endpoint_url`
each release.

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

# browser production build + browser tests
cd frontend && npm test
```

Current count: **1,874 Python tests** plus **36 browser tests**, fully offline.
The frontend build currently emits a non-fatal advisory that the dynamically
loaded Plotly chunk is larger than 500 kB after minification. Conventions to
preserve:

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
- **The first browser client is implemented, but not yet a full 3D/audio
  experience.** It has a code-rendered reactor, agent matrix, live authenticated
  SSE, and result panels. There is no WebSocket alternative, microphone,
  acoustic wake-word engine, STT, TTS, or Three.js scene yet.
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
- **No fundamental, sentiment, or macro agent analysis yet.** The first
  provider-neutral fundamental evidence contracts now exist for tenant-scoped
  connections, sources, normalized facts, conflicts, lineage, and immutable
  snapshots, with in-memory and DuckDB cache repositories. The offline Tijori
  normalization adapter, hardened stdio transport, and pinned five-tool local
  bridge now exist and are covered offline. There is no user-facing session
  provisioning flow, live authenticated validation, scorecard, red-flag
  engine, financial agent, or debate integration yet.
- **No genuine order-flow pipeline.** Current hourly/daily/weekly OHLCV, OBV,
  accumulation, volume expansion, and liquidity-sweep calculations are
  price-volume evidence, not bid/ask aggressor flow. Genuine evaluation needs
  trade ticks and/or time-sequenced book snapshots. Angel best-five data would
  need a prospective headless recorder; historical replay needs a separately
  licensed order/trade dataset. No `OrderFlowGateway`, recorder, storage schema,
  analyzer, agent, or backtest has been implemented.
- **No debate/verdict *quality* eval harness.** 1,874 Python tests verify the
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
- **Refresh gap detection is not an exchange calendar.** It reports definite
  same-day cadence gaps and deliberately ignores overnight/weekend boundaries.
  It cannot prove whether an absent weekday candle is a broker omission or an
  exchange closure.
- **Current browser session cleanup is best-effort and in-memory.** Page refresh
  and navigation close the session when the browser can send the request, and
  back/forward-cache restoration reloads. Abrupt browser/network termination
  can leave state until API restart because server-side expiry/garbage
  collection is not implemented.
- **Plotly increases the client bundle.** Dynamic import keeps it out of the
  initial server render, but the production build reports the Plotly chunk over
  the default 500 kB advisory threshold. Further code splitting is a performance
  improvement, not a correctness blocker.
- **Conversational ticker resolution is opt-in and unproven live.** It is fully
  covered offline, but the AMFI/NSE catalog downloads and the `TICKER_RESOLVER`
  LLM path have not been exercised against live endpoints in this baseline. The
  AMFI xlsx URL is a hardcoded per-release constant with no auto-discovery.
- **No acoustic wake word or streaming audio.** Speech synthesis/transcription
  exist as server capabilities and an opt-in browser push-to-listen toggle with
  a local amplitude VAD; there is no hotword engine, no hands-free session, and
  no audio persistence. The Google/ElevenLabs adapters have not been run against
  live provider credentials in this baseline.
- **Speech routes hold no rate limiting or per-session quota.** `/transcribe`
  caps a single upload at 10 MiB; there is no throttling of repeated calls.

## 7. Target Architecture (North Star)

User-stated direction for where Jarvis is headed, not yet fully built:

- **Data Ingestion Layer**: Angel One + financial databases + news feeds
  -> OHLC, P&L reports, conference calls, FII/DII data.
- **Analysis Modules**: Technical (built), Fundamental evidence/gateway/cache
  contracts plus in-memory/DuckDB repositories, Tijori adapter/transport, and
  bounded local five-tool provider bridge (built; financial agent not built),
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

- **2026-08-28**: Added the tenant-scoped, provider-neutral fundamental
  evidence foundation and five-capability read gateway: source hierarchy and
  fingerprints, strict normalized facts, explicit missing/unavailable states,
  conflict preservation, calculation lineage, immutable snapshot
  fingerprinting, primary-backed decision-grade release, capability manifests,
  versioned request/result contracts, cross-scope response binding, safe
  gateway failures, semantic cache identity, ten-day repository/expiry
  contracts, the thread-safe in-memory reference adapter, and DuckDB schema v4
  persistent fundamental caching with normalized evidence tables, mandatory
  point-operation scope, opportunistic purge, deterministic queries,
  point-in-time reads, refresh-after-expiry, and atomic conflict handling.
  Added the offline five-tool Tijori normalizer, pinned local stdio MCP
  transport, and Jarvis-owned minimal local provider bridge with owner-only
  session validation, bounded JSON-RPC/browser execution, deterministic
  company/overview/financial/shareholding handlers, environment isolation,
  timeouts, process cleanup, and synthetic five-tool MCP composition coverage.
  The local bridge has **111 passing offline tests** and has not been exercised
  against a live Tijori session. Added the sanitized Python composition and
  environment-loading boundary; all **194 fundamental/Tijori subsystem tests**
  pass.
  Revalidated 1,874 Python tests and the existing 36-test frontend baseline
  without live provider credentials.
- **2026-08-27**: Documented the conversational ticker-resolution fallback
  (deterministic ranked shortlist -> shortlist-constrained `TICKER_RESOLVER`
  LLM proposal -> mandatory yes/no confirmation -> resume, with a
  catalog-refresh offer after N consecutive failures), the composed
  `NseInstrumentCatalog` (Angel identity + AMFI SEBI cap-class + NSE
  sector/industry, JSON-cached), the new `AWAITING_CONFIRMATION` state and
  `CONFIRMATION_REQUESTED` outcome shared by the wake session and browser
  coordinator, the removal of the Angel instrument-master cache TTL (now
  sticky), and the provider-neutral speech synthesis / transcription
  capabilities (`app/tts/`, `app/stt/`, `app/composition/speech.py`), their
  `google`/`elevenlabs` adapters, metadata-only audit, `TTSError`/`STTError`
  hierarchies, optional `/speech` and `/transcribe` routes, and the browser
  voice-reply and microphone-VAD toggles. New env: `JARVIS_TICKER_RESOLVER_LLM_*`,
  `JARVIS_RESOLUTION_FAILURE_THRESHOLD`, `JARVIS_TTS_*`, `JARVIS_STT_*`. New
  dependencies: `openpyxl`, `google-cloud-texttospeech`, `google-cloud-speech`,
  `elevenlabs`. Revalidated **1,685 Python tests** and **36 frontend tests**
  plus the production frontend build.
- **2026-08-25**: Reconciled the authoritative baseline with the complete
  browser-console implementation in commit `29a4183`. Documented one-retry
  Angel session refresh, correction-overlap historical updates, immutable
  unchanged-dataset reuse, gap provenance, Broker LTP versus completed analysis
  close, accumulation and close-confirmed liquidity sweeps, complete bullish/
  bearish setup matrices, deterministic weekly/daily interpretation, BUY versus
  NO_TRADE policy, Plotly chart overlays/presets, support/resistance rendering,
  market-date compression, confidence semantics, legacy chart compatibility,
  and refresh-scoped browser sessions. Revalidated **1,443 Python tests** and
  **14 frontend tests** plus the production frontend build. Recorded genuine
  order flow as deferred rather than equating it with OHLCV/OBV evidence.
- **2026-08-23**: Added the first responsive Jarvis browser console under
  `frontend/`, connected to authenticated session, conversation SSE, workflow
  SSE, and `jarvis.dashboard.v1`. Added event-driven analyst/Bull/Bear/Judge
  states, code-rendered reactor, daily/weekly candles and evidence, trade/no-trade
  result panels, backend-offline handling, and a project social-preview asset.
- **2026-08-23**: Added `jarvis.dashboard.v1` and an authenticated dashboard
  endpoint for completed multi-timeframe operations. The bounded projection
  preserves chart data, evidence IDs, pivots/zones, debate/Judge conclusions,
  trade/no-trade semantics, UI activities, and presentation without domain
  recalculation. Verified 1,344 tests.

- **2026-08-23**: Connected wake, greeting, listening, processing, response,
  failure, and sleep states to browser sessions without making HTTP requests
  execute research synchronously. Added text/voice-transcript turns,
  idempotent asynchronous dispatch, candid configured-name greeting,
  conversation replay/SSE, terminal reconciliation, sleep acknowledgement, and
  approved-context Judge follow-up routing. Verified 1,337 tests.
- **2026-08-23**: Added authenticated SSE workflow streaming over persisted
  cursor replay. Streams use qualified event IDs, honor `Last-Event-ID`, avoid
  duplicates, drain backlogs in bounded pages, send periodic heartbeats, and
  finish with the authoritative terminal snapshot. Verified 1,331 tests.
- **2026-08-23**: Added the versioned FastAPI browser boundary, hashed
  session-capability authorization, server-owned operation IDs, HTTP
  submit/status/result/cancel/event-replay routes, OpenAPI, live composition,
  and shutdown lifecycle. Validated pending/completed/cancelled semantics,
  idempotency conflicts, cross-session isolation, and request validation.
  Verified 1,329 tests.
- **2026-08-23**: Added the bounded asynchronous browser runner and Jarvis
  operation handler. Connected the existing research façade, presentation, and
  Judge follow-ups under externally owned operation IDs; added immutable result
  storage, approved per-session context, cooperative cancellation, safe partial
  presentation outcomes, and contiguous terminal events. Verified 1,323 tests.
- **2026-08-23**: Added transport-neutral browser session and asynchronous
  operation contracts plus a thread-safe in-memory registry. Covered
  idempotency, single active work, lifecycle transitions, cancellation races,
  safe failure payloads, contiguous event capture, bounded replay, concurrency,
  and typed conflicts/not-found errors. Verified 1,313 tests.
- **2026-08-23**: Added the extensible UI workflow-event v2 foundation.
  Namespaced activities and generic participant kinds allow future financial,
  news, sentiment, or other analysts without redesigning the envelope. Wired
  real daily/weekly aggregation, parallel analysis, evidence-release, debate,
  Judge, and long-only trade-planning events with failure-safe sequencing.
  Verified 1,301 tests.
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
