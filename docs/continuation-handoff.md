# Jarvis Implementation Continuation Handoff

Last updated: **2026-08-27 (Asia/Kolkata)**

Use this file to resume implementation without relying on chat history. The
authoritative architectural detail remains in `docs/current-baseline.md`; this
document captures the current decisions, exact executable path, validation
evidence, local artifacts, known gaps, and recommended next work.

## 1. Current Verified State

- Offline regression baseline: **1,685 passing Python unit/integration tests**.
- Browser baseline: production build plus **36 passing frontend tests**.
- The production browser build reports a non-fatal Plotly chunk-size advisory.
- Live Reliance run authenticated with Angel One and completed the default
  daily/weekly Bull/Bear/Judge workflow.
- Live result: **bearish, 62% confidence, deterministic `NO_TRADE`**.
- Jarvis CEO presentation completed live with a persona budget of 5,000 tokens.
- The hard no-trade numeric-hallucination guard was added after that run and
  validated offline; the post-guard build has not yet had another live run.

Work added since the `29a4183` (`Build Jarvis browser research console`) commit,
covered offline but **not yet exercised live**:

- Conversational ticker-resolution fallback: deterministic ranked shortlist ->
  shortlist-constrained `TICKER_RESOLVER` LLM proposal -> mandatory yes/no
  confirmation -> resume; catalog-refresh offer after
  `JARVIS_RESOLUTION_FAILURE_THRESHOLD` consecutive failures. Opt-in.
- Composed `NseInstrumentCatalog`: Angel identity + AMFI SEBI cap-class + NSE
  sector/industry, JSON-cached under `data/cache/`.
- New shared conversation state `AWAITING_CONFIRMATION` and outcome
  `CONFIRMATION_REQUESTED`, plus a `FAILED -> PROCESSING` transition in the
  browser coordinator so a confirmed guess can resume.
- Angel instrument-master cache TTL removed; the cache is now sticky and only
  replaced by `.refresh()`.
- Provider-neutral speech synthesis (`app/tts/`) and transcription (`app/stt/`)
  with `google` and `elevenlabs` adapters, metadata-only prompt audit,
  `TTSError`/`STTError` hierarchies, and optional `/speech` and `/transcribe`
  HTTP routes. Browser voice-reply and microphone-VAD toggles.
- New env: `JARVIS_TICKER_RESOLVER_LLM_*`, `JARVIS_RESOLUTION_FAILURE_THRESHOLD`,
  `JARVIS_TTS_*`, `JARVIS_STT_*`. New deps: `openpyxl`,
  `google-cloud-texttospeech`, `google-cloud-speech`, `elevenlabs`.

Inspect `git status` and `git diff` before staging documentation or
future work. Never stage `.env`, `logs/`, generated instrument/catalog caches
(`data/cache/`), `.duckdb`, `.duckdb.wal`, frontend build output, or local
Wrangler state.

## 2. Frozen Product Decisions

These decisions were explicitly agreed and should not be changed accidentally:

1. Jarvis is for swing and long-term research, not intraday trading or a live
   ticker/feed UI.
2. The user-facing decision vocabulary is **`BUY` or `NO_TRADE`**. An
   actionable plan is long; bearish, neutral, conflicted, insufficient, or
   risk-blocked evidence must produce `NO_TRADE`, never a short recommendation.
3. Minimum reward/risk is **1:2 (2R)**, with **1:3 (3R)** shown as a preferred
   extension when structurally feasible.
4. One hourly source series is fetched. Completed daily candles are aggregated
   from hourly candles, then completed weekly candles from daily candles.
5. Daily and weekly technical analyses run concurrently as two distinct agent
   assignments.
6. There is one existing Judge workflow, not a separate “technical judge.” It
   validates the paired technical release, later judges Bull/Bear, and services
   grounded follow-up questions through the same provider-neutral abstraction.
7. Weekly evidence governs structural regime/eligibility; daily evidence governs
   tactical entry, stop, and target construction. The Judge must explain
   alignment or conflict rather than averaging it away.
8. Bull and Bear receive the exact same Judge-released evidence package and may
   cite only qualified IDs such as `daily:...` and `weekly:...`.
9. Full LLM debate is mandatory. If LLM configuration/provider access is absent,
   Jarvis fails gracefully and candidly; there is no hidden deterministic-only
   substitute for Bull/Bear/Judge.
10. Bull, Bear, Judge, and Jarvis depend on `StructuredLLMGateway`, not a named
    provider. Provider/model selection belongs only in composition/configuration.
11. Jarvis is the Chief Investment Research Assistant: calm, sharp, candid,
    slightly humorous, concise first, detailed on request, and addressing the
    configured user as CEO. It explains evidence but never overrides it.
12. Financial statements, earnings calls, document RAG, news, sentiment, and
    fundamentals are intentionally parked. When added, document conclusions
    must be citation-bound and must disclose missing documents.
13. Broker LTP and completed analysis close are different facts and must remain
    separately labelled. The current quote must never overwrite an OHLC candle.
14. Hiding an indicator in the UI affects presentation only. Backend technical,
    accumulation, setup, and debate calculations always run.
15. Current OHLCV/OBV/volume analysis is not genuine order flow. Do not claim
    bid/ask aggressor evidence until a tick/book data contract is implemented.
16. An LLM-guessed ticker is never used without an explicit user yes/no
    confirmation. The LLM proposal is structurally limited to symbols on the
    deterministic shortlist and can never invent one. The yes/no classifier is
    deterministic and non-LLM; anything not clearly affirmative is "no".
17. Speech synthesis and transcription are provider-neutral. Provider selection
    (`google` / `elevenlabs`) is configuration only. The prompt-audit trail
    records speech metadata only — never audio bytes or transcript/synthesized
    text. An empty STT transcript is valid "no speech" data, not an error.
18. The `/speech` and `/transcribe` routes are optional and only mounted when
    the capability is composed. Their absence must never break the core UI.

## 3. Default Executable Workflow

```text
typed text or transcribed voice
  -> anchored wake phrase: "Hey Jarvis"
  -> JarvisConversationSession
  -> PatternSwingIntentInterpreter
  -> AngelInstrumentMasterResolver (exact match; sticky cache, refresh() only)
       on InstrumentNotFound/Ambiguous and if composed:
         ResolveTickerConversationally
           shortlist_candidates (deterministic, ranked, real NSE entries)
           -> resolve_via_llm (TICKER_RESOLVER, constrained to the shortlist)
           -> AWAITING_CONFIRMATION ("Did you mean X?" / "Refresh the list?")
           -> yes -> resume as "Analyze X for a swing trade" (or refresh+retry)
  -> SwingAnalysisCommand (NSE, token, symbol, ONE_HOUR)
  -> PullRollingMarketSeries (resumable/chunked, archived hourly data)
       correction overlap -> new/corrected/deduplicated/unchanged receipt
  -> timestamped Broker LTP (optional presentation fact, never a candle)
  -> DeriveSwingTimeframes
       ONE_HOUR -> completed ONE_DAY -> completed ONE_WEEK
  -> ParallelTimeframeTechnicalOrchestrator
       DailyTechnicalSwingAgent || WeeklyTechnicalSwingAgent
       daily accumulation/sweeps || weekly accumulation/sweeps
  -> existing JarvisSwingJudge releases one fingerprinted evidence package
  -> Bull and Bear debate both timeframes
  -> same Debate Judge synthesizes verdict and confidence
  -> BuildMultiTimeframeLongTradePlan
       actionable long only when Judge and daily profile are bullish
       otherwise NO_TRADE
  -> BuildMultiTimeframeSwingInterpretation
       setup matrices + alignment + readiness + structural risk + 2R/3R
  -> JarvisBrowserOperationHandler
       evidence-locked CEO presentation and immutable operation output
  -> AsyncBrowserOperationRunner
       terminal status/event, replay, result retrieval, or safe failure
  -> JarvisPresentationAgent renders CEO briefing
  -> approved review/debate retained for evidence-locked Judge follow-ups
```

The source request must currently use `ONE_HOUR`. The application rejects a
different source interval for this multi-timeframe use case. Aggregation uses
completed buckets only and fingerprints hourly, daily, and weekly lineage.
Angel's 15:15 IST start-stamped hourly candle is accepted as the closing
constituent only after the 15:30 session cutoff. An incomplete daily or weekly
tail is excluded by default.

## 4. Technical and Trade Contracts

Each daily and weekly agent runs the existing deterministic unified evaluator,
which covers trend, momentum, volatility, volume, candlestick evidence, swing
pivots, HH/HL/LH/LL structure, BOS, CHOCH, fair value gaps, and the complete
support/resistance lifecycle (active zones, confirmed breaks, retests, role
reversals, and failed breaks). Signal calculations use TA-Lib where suitable;
price-action lifecycle/state logic remains explicit application code.

The parallel assignments now also run the timeframe-aware accumulation
detector. A zone is supported by bounded range, containment, touch/rejection,
ATR-compression, slope, OBV, and volume metrics and follows a validated
forming/confirmed/breakout/retest/hold/failure/invalidation/expiry lifecycle.
Liquidity sweeps require a breach of an already-known boundary followed by a
close-based reclaim; a wick breach alone is not enough. No qualifying base is a
valid empty result and must not be replaced with a visually guessed zone.

Each timeframe exposes both full setup matrices. Bullish has nine ordered
steps: prior downtrend, CHOCH, BOS above resistance, volume expansion, pullback
into FVG/support, EMA20>EMA50, RSI>50, confirmed HL, and next HH. Bearish has
eight: uptrend exhaustion, buy-side liquidity sweep, bearish CHOCH, BOS below
support, volume expansion, bearish FVG retest, EMA20<EMA50, and bearish RSI
divergence. Every step remains visible as confirmed/developing/pending/
contradicted/invalidated/unavailable; absence is never fabricated as evidence.

The multi-timeframe release contains separate immediate support/resistance,
confirmed pivots, profiles, and evidence IDs for daily and weekly data. Package,
decision, debate, verdict, and presentation models validate the same identity
and fingerprints so evidence from another instrument/run cannot be mixed in.

The user-facing trade policy is `jarvis.long_only_2r_swing_policy.v1`:

- eligibility requires a bullish final Judge verdict;
- the daily profile must also be bullish enough to define a tactical long;
- reference entry is the latest completed daily close, not a claimed fill;
- stop comes from the nearest confirmed daily protective structure plus buffer,
  with ATR fallback when permitted by the deterministic planner;
- minimum target is exactly 2R and preferred extension is 3R;
- opposing resistance determines target feasibility;
- a blocked 2R target or inadequate stop evidence returns `NO_TRADE`;
- backtests/execution use the next eligible candle open unless market-on-close
  is explicitly modeled, avoiding look-ahead bias.

The lower-level research planner, backtester, schema, and historical metrics
still support short scenarios. That is intentional for research compatibility;
the default Jarvis product path exposes only `BUY` or `NO_TRADE`.

## 5. LLM and Presentation Contracts

Bull and Bear prompts explicitly require:

- separate daily and weekly reasoning;
- weekly structure versus daily timing;
- an explicit relationship of `aligned`, `conflicted`, `mixed`, or
  `insufficient`;
- citations only to the released qualified evidence IDs; and
- no invented market facts, levels, or evidence.

The Judge acts as a senior technical analyst reporting toward Jarvis/CEO. It
must preserve both cases, resolve timeframe conflicts using the supplied
evidence, and return a structured verdict. It cannot see fundamental/news facts
that have not been supplied.

Jarvis's multi-timeframe LLM schema intentionally authors only the executive,
weekly, daily, Judge, Bull, and Bear prose/citations. The application attaches
the complete evidence findings and exact trade-plan numbers deterministically.
Jarvis cannot alter entry, stop, targets, feasibility, stance, score, winner,
confidence, evidence metadata, or argument IDs. A `NO_TRADE` response is
rejected if generated prose introduces hypothetical numerical entry, stop,
target, or reward/risk claims.

If presentation generation fails after valid research, the application keeps
the typed raw result and returns candid, safe failure copy. It does not silently
invent a simpler recommendation.

## 6. Conversation and Follow-Up Behavior

Both APIs use the same wake detector:

```python
session.handle_text("Hey Jarvis")
session.handle_voice_transcript("Hey Jarvis, analyze Reliance for a swing trade")
```

`handle_voice_transcript()` accepts already-transcribed text. Server-side
speech synthesis (`/speech`) and transcription (`/transcribe`) now exist as
optional capabilities, and the browser has opt-in voice-reply and
microphone-VAD toggles, but there is no acoustic wake-word engine and no
hands-free session. A wake-only turn greets `JARVIS_USER_NAME` and asks how it
can help. While listening, the next request does not need to repeat the wake
phrase.

When exact instrument resolution fails and the conversational fallback is
composed, the session (or browser coordinator) enters `AWAITING_CONFIRMATION`
and asks a single yes/no question — either `Did you mean "<SYMBOL>"?` for a
shortlist-constrained LLM guess, or an offer to refresh the company list after
`JARVIS_RESOLUTION_FAILURE_THRESHOLD` consecutive failures. `classify_yes_no`
is deterministic and non-LLM; a "yes" resumes the original request (carrying
`to_date`), anything else returns to listening with a clarification prompt. A
completed analysis resets the consecutive-failure counter.

After a successful multi-timeframe analysis, the session retains the exact
approved technical review and debate orchestrator needed by
`AskJarvisJudgeFollowUp`. Questions such as `Hey Jarvis, where is weekly
support?` are answered only from that retained chain. Follow-up citations must
belong to it. Starting a new company analysis clears/replaces old context
immediately—even if the replacement analysis fails—so stale Reliance evidence
cannot answer a later TCS question.

## 7. Configuration

Required for the live example:

```dotenv
ANGEL_API_KEY="..."
ANGEL_CLIENT_CODE="..."
ANGEL_PIN="..."
ANGEL_TOTP_SECRET="..."
JARVIS_USER_NAME="Prateek"
JARVIS_WAKE_PHRASE="Hey Jarvis"
JARVIS_LLM_MODEL="provider/model"
JARVIS_JUDGE_LLM_MAX_TOKENS="1500"
JARVIS_PERSONA_LLM_MAX_TOKENS="5000"
```

Provider credentials such as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` remain
provider-specific environment variables. Blank Bull/Bear/Judge/persona model
overrides inherit according to `app/llm/config.py`. The verified Anthropic
combination is recorded in `docs/current-baseline.md`; model slugs,
temperatures, and token needs must be revalidated per provider/model.

Optional additions (all safe to leave unset):

```dotenv
JARVIS_TICKER_RESOLVER_LLM_MODEL=""        # falls back to JARVIS_LLM_MODEL
JARVIS_TICKER_RESOLVER_LLM_TEMPERATURE="0.0"
JARVIS_TICKER_RESOLVER_LLM_MAX_TOKENS="300"
JARVIS_RESOLUTION_FAILURE_THRESHOLD="3"

JARVIS_TTS_PROVIDER="google"              # or "elevenlabs"
JARVIS_TTS_VOICE_NAME="en-GB-Neural2-B"
JARVIS_TTS_LANGUAGE_CODE="en-GB"
JARVIS_STT_PROVIDER="google"              # or "elevenlabs"
JARVIS_STT_LANGUAGE_CODE="en-IN"
# GOOGLE_APPLICATION_CREDENTIALS="/path/to/key.json"   # Google TTS/STT
# ELEVENLABS_API_KEY="..."                             # ElevenLabs TTS/STT
```

The conversational ticker-resolution fallback is only composed when both an
`AmfiMarketCapCatalog` and an `NseSectorMasterCatalog` (or a pre-built
`ticker_resolution_executor`) are passed to `compose_jarvis_swing_research` /
`compose_jarvis_browser_operations`. `/speech` and `/transcribe` are only
mounted when a speech / transcription application is passed to
`create_jarvis_http_app` (see `examples/browser_api.py`).

## 8. Running and Inspecting the Live Scenario

The concrete example input is currently embedded in
`examples/conversation_demo.py`:

```text
Hey Jarvis
How is Reliance looking for a swing trade?
```

It resolves to `NSE`, `RELIANCE-EQ`, token `2885`, and `ONE_HOUR`. Run it from
the repository root only when real market and LLM calls are intended:

```bash
.venv/bin/python -m examples.conversation_demo
```

The example uses `data/jarvis_conversation_demo.duckdb`. To capture agent
prompts and conversation events:

```dotenv
JARVIS_PROMPT_AUDIT_ENABLED="true"
JARVIS_PROMPT_AUDIT_PATH="logs/jarvis-prompt-audit.jsonl"
```

The latest debugging run used
`logs/jarvis-multitimeframe-live-audit.jsonl`. Audit records contain timestamp,
actor, event type, session ID, operation ID, exact redacted system/messages,
requested response schema, validated result, provider/model, and retry/failure
metadata. Credentials and bearer tokens are redacted; raw malformed provider
responses and raw provider exception text are deliberately omitted.

Prompt logs still contain private conversation and evidence content. They are
owner-mode local diagnostics, not production audit storage. Rotation,
retention, encryption-at-rest, cross-process ordering, and secure deletion are
not implemented.

## 9. Time and Persistence Semantics

- Workflow and prompt-audit events are validated at the IST offset.
- Market candles are timezone-aware; hourly aggregation is performed in the
  configured market timezone and only completed daily/weekly buckets are used.
- Repeated analysis resumes the exact stored exchange/token/symbol/interval
  series, re-fetches a seven-day correction overlap, and creates a new immutable
  dataset only for new or corrected candles. Duplicate fetched timestamps are
  counted and collapsed. A fully unchanged refresh reuses the prior dataset.
- Gap reporting is deliberately limited to missing intervals within one IST
  trading date. Overnight, weekend, and holiday-shaped gaps are not reported as
  definite missing broker candles.
- Broker LTP has its own observation timestamp. Daily/weekly analysis uses the
  latest completed candle and can legitimately differ from LTP.
- DuckDB uses `TIMESTAMPTZ`, preserving instants while the domain retains
  timezone-aware values. Do not assume every physical database rendering is an
  IST-formatted string.
- The database-neutral archive currently persists normalized market series,
  backtests, and debate-compatible research through repository protocols.
- The new combined multi-timeframe release, long-only policy result, retained
  follow-up context, and CEO presentation do **not** yet have their own durable,
  normalized dashboard tables. They are typed/validated in memory. This is the
  main persistence gap before historical UI replay.

## 10. Validation Commands

Run before staging:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app examples tests
.venv/bin/python -m pip check
cd frontend && npm test
git diff --check
git status --short
```

Expected full-suite baseline for this handoff: `Ran 1685 tests` and `OK`, then
`36` passing frontend tests after a successful production build. Do not treat a
live provider call as part of the offline regression suite. The build's Plotly
chunk-size warning is advisory; any actual build/test failure remains blocking.

### UI event foundation added after the original handoff

`JarvisWorkflowEvent` is now schema v2. Each event has an extensible
`WorkflowActivityDescriptor` containing a namespaced activity/participant,
generic participant kind, display label, and optional timeframe. Current
multi-timeframe execution emits real daily/weekly aggregation and analyst
events, Judge evidence release, debate/verdict, and deterministic trade-plan
events. Future financial-statement and news analysts can use the same envelope.
Presentation and follow-up activities are now emitted by the asynchronous
browser handler under the shared operation lifecycle, preventing duplicate
sequence IDs.

The first shared lifecycle layer is now implemented in
`app/models/browser_operations.py` and `app/workflow/operations.py`. It defines
browser sessions, idempotent operation requests, queued/running/cancellation/
terminal snapshots, safe failures, reconnect cursors, event batches, a
database-neutral registry protocol, and a thread-safe in-memory adapter.

`app/workflow/browser_runner.py` now schedules that work through a bounded
executor. It reuses the existing research façade with an external operation ID
and emitter, runs CEO presentation before the outer completion, stores typed
outputs separately from events, retains only approved multi-timeframe context
for Judge follow-ups, and handles cooperative cancellation and safe failures.
It deliberately remains transport neutral. `app/api/http.py` now adapts it to
versioned FastAPI session, submit, status, result, cancel, and cursor-replay
routes. Session capability tokens are required, only token digests are stored,
and operation ownership is enforced. Authenticated SSE now replays persisted
events, resumes through qualified `Last-Event-ID`, emits periodic heartbeats,
and terminates with the authoritative operation snapshot.

The first frontend read model is implemented in `app/models/dashboard.py` and
`app/presentation/dashboard.py`. `jarvis.dashboard.v1` is available from the
authenticated completed-operation dashboard endpoint. It contains bounded
daily/weekly chart candles, analyst cards, exact evidence IDs and decisive
markers, confirmed pivots, immediate support/resistance lifecycle, Bull/Bear
rounds, Judge verdict, long-only 2R/3R or explicit no-trade values, workflow
activities, and the existing Jarvis explanation. It performs no indicator or
trade recalculation. It currently reads the operation result in memory, so it
does not close the durable historical-dashboard gap.

The first actual browser client is now under `frontend/`. It is a responsive,
wake-aware Jarvis command center with a code-rendered reactor, extensible agent
cards, authenticated conversation/workflow SSE consumption, event-derived
progress matrix, bounded daily/weekly candlesticks, evidence and levels,
Bull/Bear/Judge synthesis, and exact trade/no-trade presentation. It contains
no ElevenLabs credential in client code. The browser defaults to the
Python API at `http://127.0.0.1:8000`; the live API example now allows local
ports 3000, 3001, and 5173.

The completed browser experience now also includes:

- separate scrollable daily and weekly Plotly candlestick charts;
- independently labelled Broker LTP and completed timeframe analysis close;
- immediate confirmed support/resistance horizontal bands in green/red;
- EMA20/EMA50 as the minimal default, with a collapsed indicator drawer;
- selectable Bollinger, RSI, volume, prior-period CPR, FVG, pivots,
  support/resistance lifecycle, HH/HL/LH/LL, BOS/CHOCH, accumulation, liquidity
  sweep, and named candlestick-pattern overlays;
- `Clean`, `Trend`, `Price action`, `Patterns`, `Decision`, and `Everything`
  presets with device-local per-timeframe preferences;
- daily x-axis compression for weekends and absent weekdays without inventing
  candles;
- complete compact bullish/bearish setup progressions and weekly/daily role
  explanations;
- verdict-aware colour and copy: bearish/neutral are no-trade, bullish but
  risk-blocked is caution/no-trade, and only an approved plan is a buy setup;
- confidence wording that identifies confidence in the Judge verdict rather
  than implying a price-direction probability;
- legacy chart compatibility: missing newer arrays become empty overlays with a
  fresh-analysis notice instead of an `undefined.indicators` crash;
- refresh-scoped sessions: `pagehide` best-effort closes the old session and a
  back/forward-cache restore forces a reload/new session;
- an `awaiting_confirmation` conversation state with a `confirmation_requested`
  turn outcome, driving the "Did you mean …?" / "Refresh the list?" yes/no
  prompt; and
- two device-local voice toggles: "voice replies" (posts each `spoken_message`
  to `/speech` and plays the audio) and "voice input" (microphone capture with
  a local amplitude-threshold VAD that uploads a finished utterance to
  `/transcribe`, muted while Jarvis is speaking). Both fail silently and never
  block conversation text; the routes may be absent server-side.

Support or resistance can legitimately be absent when no confirmed multi-touch
zone of the required effective type exists on that side of price. The UI states
that explicitly. Accumulation zones remain separate evidence and are not
silently reclassified as support/resistance.

## 11. Known Gaps and Honest Boundaries

- No acoustic wake-word engine and no hands-free/streaming audio session.
  Server-side speech synthesis (`/speech`) and transcription (`/transcribe`),
  the `google`/`elevenlabs` adapters, and opt-in browser voice-reply +
  microphone-VAD toggles are implemented; audio is never persisted.
- The Google/ElevenLabs speech adapters and the AMFI/NSE catalog downloads have
  not been run against live endpoints/credentials in this baseline.
- Conversational ticker resolution is opt-in and covered offline only. The AMFI
  cap-class xlsx URL is a hardcoded per-release constant with no auto-discovery.
- Speech routes have no rate limiting or per-session quota; `/transcribe` only
  caps a single upload at 10 MiB.
- No WebSocket alternative or full 3D/audio Jarvis experience. The browser
  console, HTTP/SSE transport, dynamic reactor, operation matrix, Plotly charts,
  setup view, debate, and executive briefing are implemented.
- No document upload/parser/chunker/vector index/RAG or financial agent.
- No news, sentiment, macro, portfolio construction, or order placement.
- No live market ticker; the product is historical/swing research.
- No genuine order-flow feed, recorder, schema, analysis agent, or historical
  backtest. Existing OHLCV/OBV/volume/accumulation evidence is only a proxy for
  price-volume behavior, not bid/ask aggressor flow.
- Natural-language routing remains deliberately narrow and single-instrument.
- Angel rate-limit/chunk defaults are estimates and need confirmation against
  the active SmartAPI account/docs before large production pulls.
- LLM preflight validates local model resolution and credentials only. It cannot
  detect an invalid remote model slug, model-specific parameter restriction,
  provider outage, or insufficient response budget without a real call.
- Automated tests protect schema/citation/lineage correctness but do not yet
  score debate quality or verdict forecasting quality against realized returns.
- A naturally bullish live end-to-end actionable plan remains unobserved. The
  bearish live no-trade path and all actionable contracts are validated.
- Multi-timeframe result persistence and dashboard query tables remain pending.
- Browser refresh cleanup is best-effort. There is no server-side idle-session
  expiry or garbage collector for abruptly abandoned in-memory sessions.
- Intraday gap detection does not use an exchange holiday calendar; it avoids
  false weekend/overnight alerts but cannot classify every absent weekday.
- Plotly is dynamically loaded but still creates a client chunk above the
  build tool's default 500 kB advisory threshold.

## 12. Recommended Next Steps, One at a Time

1. Add database-agnostic repository models/ports for the complete
   `MultiTimeframeEndToEndSwingAnalysisResult`, then implement in-memory and
   DuckDB adapters with normalized daily/weekly evidence, verdict, trade plan,
   and presentation tables. This is the recommended immediate step.
2. Persist the completed dashboard aggregate and add historical-run queries;
   the live completed-operation read model is implemented.
3. If genuine order flow is prioritized, define a database-neutral
   `MarketMicrostructureGateway`/`OrderFlowGateway` and immutable tick/book
   contracts before selecting Angel best-five prospective capture or licensed
   historical NSE order/trade data. Never backfill “real order flow” from
   candles.
4. Browser microphone capture, speech-to-text, the provider-neutral TTS/STT
   ports, and the Google/ElevenLabs adapters are implemented. Remaining audio
   work: exercise the adapters and catalog downloads against live
   endpoints/credentials, add an acoustic wake-word engine, and add rate
   limiting to `/speech` and `/transcribe` — all while keeping transcript
   handling and financial logic vendor-neutral.
6. Add user-controlled financial-document ingestion and citation-preserving RAG,
   followed by a document-grounded fundamental agent.
7. Extend the same evidence package/debate/Judge contracts to technical plus
   document-grounded fundamentals only after the RAG evaluation suite proves
   citation completeness and abstention behavior.

For a live actionable-plan check, wait for a naturally bullish qualifying
instrument/dataset and observe it without changing thresholds or forcing the
Judge. That check is useful, but durable multi-timeframe persistence is the more
important next implementation dependency for the planned dashboard.
