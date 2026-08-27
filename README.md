# Jarvis AI Investment Research Assistant

Jarvis is an agentic financial-intelligence platform for explainable,
evidence-grounded equity research. It combines validated market data,
deterministic technical analysis, look-ahead-safe backtesting, database-neutral
storage, a provider-neutral Bull/Bear/Judge LLM debate, and a wake-activated
conversation layer.

Jarvis is an investment-research assistant, not a price-prediction script. A
result is released only when its evidence and execution lineage satisfy the
domain contracts. If the mandatory LLM debate cannot run, Jarvis returns a
safe failure instead of silently substituting a deterministic recommendation.

## Current status

The market-data, Phase 2 technical-analysis/backtesting, storage, full debate,
natural-language routing, and conversation-foundation layers are implemented.

Automated baseline: **1,685 passing Python tests** (unit and integration) and
**36 passing browser tests**. The browser command also completes a production
build. These checks run without live broker or LLM credentials.

Implemented:

- Angel One SmartAPI behind an injectable market-data gateway
- One-shot Angel session re-authentication and request replay when a quote or
  history request reports an expired session
- Validated quotes, OHLCV candles, historical series, and IST-aware workflow
  events
- Broker LTP retained as an independently timestamped observation rather than
  being presented as the latest completed analysis candle
- Resumable, chunked historical pulls with a configurable correction overlap,
  duplicate removal, corrected-candle accounting, immutable dataset reuse, and
  definite same-session intraday-gap reporting
- Local hourly-to-daily-to-weekly aggregation that accepts Angel's start-stamped
  15:15 closing candle only after the 15:30 IST session boundary and excludes
  incomplete final periods by default
- Parallel daily and weekly technical agents with a Judge-controlled evidence
  release gate
- TA-Lib indicators plus deterministic price-action analysis
- SMA, EMA, RSI, MACD, Bollinger Bands, ATR, ADX, Stochastic, and OBV
- Swing pivots, HH/HL/LH/LL structure, BOS, CHOCH, fair value gaps, and
  support/resistance lifecycle evidence
- Timeframe-aware accumulation zones with forming, confirmed, breakout,
  retest/holding, failed-break, invalidated, and expired lifecycle states
- Close-confirmed buy-side and sell-side liquidity sweeps; a wick breach without
  a reclaim is never promoted to a sweep
- Complete deterministic bullish and bearish setup matrices for daily and
  weekly analysis, including structure, BOS/CHOCH, volume, FVG/support,
  EMA alignment, RSI, swing progression, exhaustion, and liquidity-sweep steps
- Deterministic multi-timeframe interpretation separating weekly structural
  direction, daily tactical readiness, 2R/3R feasibility, and the final
  `BUY`/`NO_TRADE` decision
- Unified weighted swing evaluation and structurally validated trade planning
- Directional swing backtesting with next-eligible-open execution and
  configurable 1:2 through 1:3 reward/risk targets
- Walk-forward replay, transaction costs, equity/drawdown curves, performance
  metrics, and point-in-time evidence capture
- Database-neutral repository protocols with in-memory and DuckDB adapters
- Immutable, fingerprinted storage plus normalized dashboard-oriented tables
- Provider-neutral structured LLM gateway and role-based model configuration
- Mandatory Bull/Bear/Judge debate with citation and chain-of-custody checks
- Bull/Bear prompts grounded in separately qualified `daily:` and `weekly:`
  evidence, including explicit timeframe agreement or conflict
- Conservative natural-language swing-intent recognition
- Cached Angel instrument-master resolution by company name or symbol; the
  downloaded cache is now sticky (no time-based expiry) and only replaced by an
  explicit `.refresh()`
- Conversational ticker-resolution fallback for partial or fuzzy company names:
  a deterministic, ranked shortlist of real NSE entries, a constrained
  `TICKER_RESOLVER` LLM proposal that structurally cannot invent a symbol, and a
  mandatory yes/no user confirmation before the guessed symbol is used
- A composed NSE cash-equity catalog joining Angel identity with AMFI
  SEBI large/mid/small-cap classification and NSE sector/industry, each carrying
  its snapshot provenance
- After a configurable number of consecutive failed resolutions, Jarvis offers
  (again with a yes/no confirmation) to refresh its company list and retry
- Provider-neutral speech synthesis (text-to-speech) and speech-to-text ports
  with `google` and `elevenlabs` adapters selected purely by configuration,
  metadata-only prompt-audit records that never store audio or transcript text,
  and optional `/speech` and `/transcribe` HTTP routes that only exist when the
  capability is composed
- Browser voice toggles: spoken Jarvis replies via the `/speech` route and
  microphone capture with a local amplitude-threshold VAD that uploads a
  finished utterance to `/transcribe`, muting capture while Jarvis is speaking
- Text and voice-transcript activation using the same configurable wake phrase
- Retained approved analysis context for grounded follow-up questions routed
  back to the same Judge abstraction
- Typed conversation and workflow events suitable for a future dynamic UI
- Extensible workflow-event v2 activities with namespaced participants, generic
  analyst categories, and timeframe metadata for current and future agents
- Transport-neutral asynchronous browser-operation contracts with idempotency,
  cooperative cancellation, one-active-operation sessions, and event replay
- Bounded background runner that executes research, CEO presentation, and
  grounded Judge follow-ups under one operation sequence with separate,
  immutable result storage
- Versioned FastAPI boundary for browser sessions, idempotent submission,
  status/result polling, cancellation, and reconnect-safe event replay
- Wake-aware browser conversation coordinator for typed text and voice
  transcripts, asynchronous dispatch, response acknowledgement, sleep, and
  grounded Judge follow-ups
- Refresh-scoped browser sessions: leaving or refreshing the page closes the
  old backend session, and a browser back/forward-cache restoration forces a
  fresh session instead of reviving stale operation state
- Plotly daily and weekly candlestick charts with compressed non-trading gaps,
  separate Broker LTP and analysis-close lines, visible immediate
  support/resistance when confirmed, EMA 20/50 defaults, and user-selectable
  indicator, pattern, CPR, accumulation, liquidity-sweep, and price-action
  overlays
- A collapsed technical-evidence ledger and indicator drawer keep the default
  decision view readable while every deterministic calculation remains in the
  backend result
- Opt-in, redacted JSONL review trail for Jarvis and LLM agent exchanges
- Structured JSON logging, correlation IDs, and secret-safe failures

Intentionally not implemented yet:

- Acoustic/always-listening wake-word detection; speech synthesis and
  transcription now exist as server-side capabilities and an opt-in browser
  push-to-listen toggle, but there is no hotword engine and no audio persistence
- WebSocket alternative transport and a full 3D/audio Jarvis experience
- Financial-statement and earnings-call document ingestion/RAG
- Fundamental-analysis agent and document-grounded financial conclusions
- News discovery, sentiment, and macro-analysis agents
- Portfolio construction or live order placement
- Genuine order-flow ingestion or analysis; current OHLCV/OBV/volume evidence
  is price-volume context, not bid/ask aggressor flow

News and document processing remain parked while the market-analysis and
interaction foundations are completed.

## End-to-end flow

```text
Typed input -----------------------------+
                                         |
Voice -> speech-to-text transcript ------+-> Wake detector
                                                |
                                                v
                                      Conversation session
                                     dormant -> listening
                                                |
                                                v
                                  Pattern swing-intent interpreter
                                                |
                                                v
                                   Angel instrument-master resolver
                                                |
                                                v
                              Resolved SwingAnalysisCommand + operation ID
                                                |
                                                v
                               Resumable historical market-data pull
                                                |
                                                v
                      Hourly -> daily + weekly candle aggregation
                                      |              |
                                      v              v
                              Daily agent      Weekly agent
                                      \              /
                                       v            v
                                  Judge evidence release gate
                                                |
                                                v
                       Bull <-> Bear (both timeframes) -> same Judge verdict
                                                |
                                                v
                         Long-only deterministic 1:2 trade policy
                                                |
                                                v
                 CEO briefing + retained Judge follow-up context
```

The Bull, Bear, and Judge depend only on `StructuredLLMGateway`; they do not
know which model provider is active. The composition layer selects and
preflights the configured gateways.

## Wake activation

Both text and voice transcripts use the same anchored, case-insensitive wake
detector. The default phrase is `Hey Jarvis`.

```python
turn = session.handle_text(
    "Hey Jarvis, how is Reliance looking for a swing trade?"
)

turn = session.handle_voice_transcript(
    "Hey Jarvis, analyze TCS for a swing trade"
)
```

A wake-only message moves the session from dormant to listening and returns:

```text
Hello <configured name>. How can I help you today?
```

While listening, the next typed message or voice transcript can contain the
request without repeating the wake phrase. Text without the wake phrase is
ignored while dormant. A combined wake phrase and command executes immediately.

After a completed multi-timeframe analysis, Jarvis retains only the approved
technical-review and debate chain. A later activated question such as
`Hey Jarvis, where is weekly support?` is routed to the same configured Judge
through a provider-neutral abstraction. The Judge may cite only the retained
daily/weekly evidence. A new company-analysis request runs the complete workflow
again and replaces the retained context.

The voice method receives an already-transcribed string. Audio capture and
speech adapters are deliberately outside the current domain layer.

## Conversation and workflow states

Conversation states exposed to UI clients:

```text
dormant -> greeting -> listening -> processing -> responding -> dormant
                                      |
                                      +-> failed -> dormant
                                      +-> listening (clarification required)
```

Research workflow events separately expose:

```text
request received -> instrument resolved -> market data loading
-> daily/weekly preparation -> parallel daily/weekly analysis
-> evidence review -> Bull debating -> Bear debating -> Judge reviewing
-> long-only trade planning -> CEO presentation -> completed/failed
```

Events are typed, ordered per session/operation, timestamped in IST, and contain
fixed secret-safe messages. Event-sink failures are logged but do not terminate
the research operation.

## Browser HTTP API

The versioned FastAPI adapter exposes:

```text
POST   /api/v1/sessions
DELETE /api/v1/sessions/{session_id}
POST   /api/v1/sessions/{session_id}/operations
GET    /api/v1/sessions/{session_id}/operations/{operation_id}
GET    /api/v1/sessions/{session_id}/operations/{operation_id}/result
POST   /api/v1/sessions/{session_id}/operations/{operation_id}/cancel
GET    /api/v1/sessions/{session_id}/operations/{operation_id}/events
GET    /api/v1/sessions/{session_id}/operations/{operation_id}/events/stream
GET    /api/v1/sessions/{session_id}/operations/{operation_id}/dashboard
GET    /api/v1/sessions/{session_id}/conversation
POST   /api/v1/sessions/{session_id}/conversation/turns
POST   /api/v1/sessions/{session_id}/conversation/sleep
GET    /api/v1/sessions/{session_id}/conversation/events
GET    /api/v1/sessions/{session_id}/conversation/events/stream
POST   /api/v1/sessions/{session_id}/speech       (optional; when TTS is composed)
POST   /api/v1/sessions/{session_id}/transcribe   (optional; when STT is composed)
```

`/speech` takes a JSON `{ "text": ... }` body (≤2000 chars) and returns binary
audio in the configured encoding. `/transcribe` takes a raw audio body (≤10 MiB;
empty bodies are `400`, oversized are `413`) and returns
`jarvis.http_transcription.v1` with a `transcript` that is `null`, and still
`200`, when no speech was detected. Both routes require the session capability
token, run inside the prompt-audit session context, and only exist when a
speech synthesis / transcription application is passed to
`create_jarvis_http_app`. A `TTSError`/`STTError` maps to `503` for a
configuration problem and `502` for a provider/auth/synthesis failure, with a
generic client-safe message.

The session-creation response supplies a capability token. Send it on all
session-scoped requests as `X-Jarvis-Session-Token`. Only token digests are
stored by the in-memory authorization adapter. Operation IDs are server-owned;
the browser supplies an idempotency key. Result polling returns `202` while
work is pending, `200` only after official completion, and `409` for failed or
cancelled operations. OpenAPI is available at `/api/docs`.

The completed-analysis dashboard route returns `jarvis.dashboard.v1`: bounded
daily/weekly OHLCV series, analyst stance and score, qualified evidence,
confirmed pivots, immediate support/resistance, Bull/Bear rounds, the Judge
verdict, an exact long-only 2R/3R plan or explicit no-trade fields, workflow
activities, Jarvis presentation, broker-quote provenance, and historical
refresh provenance. Refresh provenance identifies an initial, incremental, or
unchanged refresh; reports the requested and stored ranges, resumed timestamp,
new/corrected/deduplicated candle counts, chunk count, dataset reuse, and only
definite same-session gaps. It is a projection only and never recalculates or
rewrites domain evidence.

The first browser client lives in `frontend/`. It creates an authenticated
session, accepts the text wake phrase and commands, consumes conversation and
operation SSE with the capability header, animates the real workflow activity
matrix, and renders the completed dashboard projection. Daily and weekly
Plotly charts are separate, scroll naturally with the result, and never
recalculate financial evidence in JavaScript. Immediate support and resistance
remain visible; optional overlays live in a collapsed drawer and selections are
stored per timeframe in device-local storage. Missing or legacy chart fields
are normalized to empty arrays so an older result degrades safely instead of
crashing. Configure the API URL with `NEXT_PUBLIC_JARVIS_API_URL`; the local
default is `http://127.0.0.1:8000`.

The SSE route first replays persisted events, then streams new progress with
periodic heartbeats and finishes with an authoritative `terminal` event. Its
event IDs use `{operation_id}:{sequence}` and support browser reconnection via
`Last-Event-ID` or `after_sequence`. Because native `EventSource` cannot attach
the required capability header, the first UI should consume this endpoint with
authenticated `fetch()` streaming rather than putting the token in a URL.

Browser conversation state follows:

```text
dormant -> greeting -> listening -> processing -> responding -> dormant
                                      |    |
                                      |    +-> awaiting_confirmation -> processing
                                      |                              -> listening
                                      +-> failed -> dormant
```

While dormant, input is ignored unless it begins with the configured wake
phrase. Both typed text and already-transcribed voice use the same detector.
Jarvis greets the configured user, dispatches research through the asynchronous
runner, and exposes conversation transitions through replay and SSE. A later
support/resistance/pivot/evidence question is sent to the Judge only when the
session retains an approved multi-timeframe analysis.

When exact instrument resolution fails and the conversational ticker-resolution
fallback is composed, Jarvis enters `awaiting_confirmation`: it either proposes
a single guessed symbol ("Did you mean …?") or, after the configured number of
consecutive failures, offers to refresh its company list. A deterministic,
non-LLM yes/no classifier reads the reply; anything not clearly affirmative is
treated as "no" and returns to `listening`. Both the HTTP conversation
coordinator and the wake session share this state machine and the
`PendingConfirmation` carrier that lets the original request resume after a
"yes".

## Requirements

- Python 3.11 or newer
- Node.js 22.13 or newer for the browser client
- Angel One credentials for live authentication and market-data operations
- An LLM model/provider credential for the mandatory full debate
- Network access for live Angel One and remote LLM operations

The automated suite uses injected fakes and runs offline.

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

For pinned versions:

```bash
python -m pip install -r requirements-lock.txt
```

Create local configuration:

```bash
cp .env.example .env
```

Core settings:

```dotenv
ANGEL_API_KEY="your_api_key"
ANGEL_CLIENT_CODE="your_client_code"
ANGEL_PIN="your_pin"
ANGEL_TOTP_SECRET="your_totp_secret"

JARVIS_USER_NAME="Prateek"
JARVIS_WAKE_PHRASE="Hey Jarvis"

JARVIS_LLM_MODEL="provider/model"
JARVIS_PERSONA_LLM_MODEL=""
JARVIS_JUDGE_LLM_MAX_TOKENS="1500"
JARVIS_PERSONA_LLM_MAX_TOKENS="5000"
```

`JARVIS_BULL_LLM_MODEL`, `JARVIS_BEAR_LLM_MODEL`,
`JARVIS_JUDGE_LLM_MODEL`, and `JARVIS_PERSONA_LLM_MODEL` optionally override
the shared model. A blank persona override inherits the Judge override and then
the shared model. Provider credentials remain provider-specific environment
variables and are never stored in Jarvis domain settings.

The Angel instrument-master URL, cache path, payload limit, timeout, and
exchange list are configurable. The former `ANGEL_INSTRUMENT_CACHE_TTL_SECONDS`
was removed: the downloaded cache is now sticky and refreshed only by an
explicit `.refresh()` call. `.env.example` documents the common overrides;
the validated configuration model defines all defaults.

Optional speech and ticker-resolver settings are also in `.env.example`:
`JARVIS_TTS_*` / `JARVIS_STT_*` (provider is `google` or `elevenlabs`; voice,
language, encoding and provider model are configurable; Google uses Application
Default Credentials, ElevenLabs uses `ELEVENLABS_API_KEY`),
`JARVIS_TICKER_RESOLVER_LLM_MODEL` / `_TEMPERATURE` / `_MAX_TOKENS` (falls back
to `JARVIS_LLM_MODEL`), and `JARVIS_RESOLUTION_FAILURE_THRESHOLD` (consecutive
failed resolutions before Jarvis offers to refresh its company list).

Never commit `.env`, downloaded instrument-master data, logs, provider
credentials, or generated DuckDB files.

## Running tests

Full offline suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Browser production build and tests:

```bash
cd frontend
npm test
```

Verified baseline on 2026-08-27: **1,685 Python tests** and **36 browser
tests**, all passing. The frontend build succeeds with a non-fatal warning that
the dynamically loaded Plotly client chunk exceeds the default 500 kB advisory
threshold.

Conversation and wake-word tests:

```bash
.venv/bin/python -m unittest tests.unit.test_jarvis_conversation -v
```

Workflow event tests:

```bash
.venv/bin/python -m unittest tests.unit.test_workflow_events -v
```

Live login and market-data operations are separate manual integration checks;
they are not part of the offline regression suite.

## Storage

Application code depends on repository protocols, not DuckDB directly. The
current adapters are:

- `InMemoryJarvisStorage` for tests and transient workflows
- `DuckDBJarvisStorage` for local analytical persistence

DuckDB schema version 3 stores market datasets, normalized candles, strategy
configuration and weights, backtest evaluations/trades/equity/performance, and
debate transcripts/verdicts with signal signatures for precedent retrieval.
Generated databases are local artifacts and must not be committed.

See [docs/backtest-storage-schema.md](docs/backtest-storage-schema.md) for the
schema and example dashboard queries.

## Logging and safety

Structured logs may contain event names, operation IDs, exchange/symbol
identifiers, request duration, counts, failure classification, and state names.
They must not contain credentials, authorization headers, tokens, raw vendor
responses, raw exception messages, prompts containing secrets, or model
credentials.

Known LLM failures are classified into configuration, authentication, provider
availability, rate limit, invalid response, and unknown failure envelopes.
Those envelopes provide separate display and spoken copy and explicitly state
that no investment conclusion was produced.

### Prompt and conversation review log

An opt-in development audit trail can record the full sanitized Jarvis
conversation and every agent-level Jarvis/Bull/Bear/Judge exchange:

```dotenv
JARVIS_PROMPT_AUDIT_ENABLED="true"
JARVIS_PROMPT_AUDIT_PATH="logs/jarvis-prompt-audit.jsonl"
```

Each JSONL record identifies the IST timestamp, event type, actor, session ID,
operation ID, and payload. LLM request records contain the exact system prompt,
dynamic messages, response-model name, and JSON schema. Successful response
records contain provider/model metadata, attempt count, and the validated
structured output. Grounding retries are separate records, making prompt
improvements reviewable round by round.

Wake, greeting, clarification, and safe failure copy remain deterministic.
After a successful debate, the provider-neutral Jarvis presentation agent uses
the approved Chief Investment Research Assistant persona to produce a concise
CEO briefing plus detailed technical, Bull, Bear, and Judge explanations. Its
exact system prompt, evidence context, correction retries, and validated
structured response are logged with actor `jarvis` when auditing is enabled.
Structural validation rejects a briefing that changes the symbol, interval,
technical stance/score, verdict, confidence, decisive evidence, argument IDs,
or any technical evidence metadata. Multi-timeframe briefings also attach an
application-owned long-only trade block. A bullish Judge plus bullish daily
profile may produce a daily-structure/ATR stop and exact 2R/3R targets; a
non-bullish verdict, non-bullish daily profile, blocked 2R target, or missing
stop evidence produces `NO TRADE`. The LLM cannot change those values. The
validated raw result remains available if the presentation provider fails.

The audit is disabled by default because it intentionally contains full user
text, technical evidence supplied to agents, debate context, and model output.
Recognized credentials and bearer tokens are redacted, the file is created with
owner-only permissions, and `logs/` is git-ignored. This is diagnostic evidence,
not a substitute for access control, retention, rotation, or secure deletion.

## Documentation

- [Current implementation baseline](docs/current-baseline.md)
- [Continuation handoff and exact next steps](docs/continuation-handoff.md)
- [Backtest and research storage schema](docs/backtest-storage-schema.md)
- [Executable examples](examples/README.md)

## Next implementation areas

1. Persist the complete multi-timeframe result through new database-neutral
   repository ports and normalized DuckDB tables.
2. Persist dashboard history; the live completed-operation read model is built,
   but it currently projects the in-memory operation result.
3. Add a database-neutral market-microstructure/order-flow port only after a
   valid source is selected. Existing candles cannot reconstruct genuine
   order flow; Angel best-five streaming would require prospective capture,
   while historical order/trade replay requires a separate dataset.
4. Speech synthesis, transcription, the Google/ElevenLabs adapters, the
   optional `/speech` and `/transcribe` routes, and the browser voice toggles
   are implemented. Remaining audio work is an acoustic wake-word engine and
   any hands-free/streaming capture, without changing the existing text and
   transcript wake contract.
6. Add user-supplied document ingestion, citation-preserving RAG, and a
   financial-analysis agent.
7. Combine strictly document-grounded fundamentals with existing technical and
   price-action evidence before expanding the Judge's final report.

## Disclaimer

This project is for research and educational use. Its output is not investment
advice, does not guarantee future performance, and must not be treated as an
instruction to place a trade.
