# Jarvis Implementation Continuation Handoff

Last updated: **2026-09-02 (Asia/Kolkata)**

Use this file to resume implementation without relying on chat history. The
authoritative architectural detail remains in `docs/current-baseline.md`; this
document captures the current decisions, exact executable path, validation
evidence, local artifacts, known gaps, and recommended next work.

## Current Structured-Fundamentals Release Candidate

The feature branch now extends the original five-capability Tijori boundary
without widening its public read-only tool allow-list. `get_financials` can
return complete JSON-first structured documents for Growth Table, Balance
Sheet, Profit and Loss, Cash Flow, Ratios, and Quarterly Results. Balance
Sheet, Profit and Loss, Cash Flow, Ratios, and Quarterly Results are requested
independently for consolidated and standalone reporting; Growth Table retains
the provider's not-applicable reporting basis. `get_company_overview` also
supports complete Peer Comparison and Benchmarking Financials matrices.

The extractors prefer bounded embedded JSON over presentation-state scraping.
They recursively retain provider hierarchy, labels, periods, units, zeros,
explicit missing values, auxiliary cells, issuer identity, reporting basis,
source URL, retrieval time, parser/contract version, and content fingerprint.
Collapsed browser rows therefore do not truncate the stored document. Raw
HTML, screenshots, cookies, credentials, browser state, and provider exception
text do not cross the MCP boundary or enter DuckDB.

Provider-neutral Python contracts translate every accepted document into an
immutable `StructuredFinancialDocument`. Database-neutral repositories now
cover financial documents, Peer Comparison, and Benchmarking Financials, with
in-memory reference adapters and durable DuckDB implementations. Cache keys
include tenant, provider connection, issuer, document type, and reporting
basis. Unexpired results are reused; explicit refresh atomically replaces the
complete applicable company set only after successful retrieval; a provider
failure preserves the active cache; and expired documents are never returned.

The browser workflow requests the completed structured-document set only when
fundamentals are explicitly requested. It exposes bounded, operation-owned
references rather than embedding large provider documents in the operation
response. Authenticated, tenant-scoped HTTP resolution returns a referenced
cached JSON document only when it belongs to that released operation. The
frontend client understands these references for future Fundamental Analyst
reasoning and inspection; Benchmarking Financials is intentionally evidence,
not a mandatory dashboard visualization.

The command deck also includes the locked holographic agent presentation:
event-driven Market Data, Daily, Weekly, Fundamental, Bull, Bear, and Judge
identities; a licensed Bull model with its licence manifest; perception-only
packet links and kinetic cores; completion glow; reduced-motion handling; and
opt-in procedural neural audio. Animation timing never delays or changes the
backend workflow.

Release-boundary validation on 2026-09-02:

- **224 Tijori MCP tests passed** under the pinned Node 24.20.0 runtime;
- affected Python subsystem runs completed **534 passing test executions**;
- the complete Python regression suite passed all **2,171 tests**;
- the frontend production build and all **51 frontend tests passed**;
- the known non-fatal Plotly chunk-size advisory remains; and
- actual `.env` files, browser/session state, DuckDB files, build output,
  Wrangler state, dependency trees, logs, and Python caches remain ignored.

This milestone still does not implement a Fundamental Analyst, deterministic
fundamental scorecard, annual-report ingestion, or qualitative management/moat
claims. Provider-standardized data remains secondary research evidence and
cannot be promoted to primary or decision-grade evidence by itself.

## 0. Fundamental-Research Expansion Baseline

Phase 0, Step 0.1 of the evidence-grounded fundamental-research expansion was
completed before downloading or executing any Tijori MCP code. The frozen
source baseline is commit `bd83ff1e9d5080e7cf3c1433365f4bb546b068ce`
(`Add conversational ticker resolution and speech (TTS/STT) subsystems`) on
`main`.

Validation evidence captured on 2026-08-28:

- **1,685 Python tests passed** in 61.552 seconds.
- `pip check` reported no broken requirements.
- Python bytecode compilation completed for `app/` and `tests/`.
- **36 frontend tests passed**, including a production build.
- Frontend ESLint passed after correcting one pre-existing React effect issue
  in the microphone startup path. The correction also stops a late
  `getUserMedia()` result from reopening capture after cleanup.
- DuckDB adapter schema remains **version 3**.
- Key validated dependency versions were Pydantic 2.13.4, DuckDB 1.5.5,
  LiteLLM 1.97.0, TA-Lib 0.7.1, pandas 3.0.5, Node 25.2.1, and npm 11.6.2.
- Existing sanitized technical/debate fixtures remain under
  `tests/unit/_debate_fixtures.py`; no credential-bearing live payload was
  captured for the expansion baseline.

The frontend production build still reports the already-known non-fatal
Plotly chunk-size advisory. LiteLLM also used its bundled model-cost map when
the offline test environment could not reach the remote cost-map URL; this did
not affect test results.

Phase 0, Step 0.2 is now complete. Tijori MCP commit
`64be6c49f99a3fb3355ab5a727ce05cf260a7acc` was cloned into an isolated
temporary directory and statically audited. No upstream setup/discovery script,
package installation, browser, authenticated session, or Tijori internal API
was executed. The audit decision is **conditional development use; production
blocked**. The source provides a useful 19-tool local stdio MCP foundation, but
its credential/session handling, dependency advisories, fail-open parsing,
test drift, missing Jarvis provenance, and data-use terms must be addressed
before authentication. The complete findings and acceptance gates are in
`docs/tijori-mcp-source-audit.md`.

The most important frozen controls are:

- never run upstream `setup.js` or `discover.js`;
- never store Tijori email/password, cookies, CSRF tokens, or browser session
  state in logs or DuckDB;
- use a Jarvis-owned manual auth-only browser flow with owner-only session
  permissions;
- treat Tijori as a secondary aggregator, with exchange/company filings as the
  fundamental source of truth;
- do not expose or redistribute Tijori-derived data publicly without written
  permission or a suitable data agreement;
- patch the pinned dependency lock to zero critical/high advisories before
  first authentication; and
- define provider-neutral evidence models and ports before connecting the MCP
  subprocess.

Phase 1, Step 1.1 is now complete. The provider-neutral fundamental-evidence
domain foundation is implemented in `app/models/fundamentals.py`; it does not
authenticate to Tijori, call a broker, invoke an LLM, or persist provider data.
It adds:

- a secret-resistant `ProviderConnectionScope` keyed by `tenant_id` and
  `provider_connection_id`, so each user supplies and owns their provider
  account and entitlement;
- listed-issuer identity independent of Tijori or Angel One identifiers;
- explicit source inventory records covering source type/rank, as-of,
  publication and retrieval timestamps, freshness, parser/schema versions,
  validation state, and SHA-256 content fingerprints;
- strict normalized fundamental facts with reporting period, statement and
  line-item identity, original and normalized values, `Decimal` arithmetic,
  unit/currency, availability, evidence label, confidence, conflict state, and
  tamper-evident source/calculation lineage;
- explicit missing-source and entitlement/paywall/provider-unavailable states;
- preserved conflict records with an explained working fact rather than silent
  averaging; and
- an immutable aggregate snapshot with cycle detection, source/fact identity
  checks, stale-evidence guards, stable fingerprinting, and a conservative
  decision-grade release gate.

The release gate follows Jarvis's internal fundamental evidence hierarchy:
Tijori/provider-standardized evidence may be used for normalized research and
cross-checking, but every decision-grade fact must trace directly or through
its parent lineage to a user-governing, connected-system, or primary public
source. Provider data cannot be the sole decision-grade source and cannot be
relabeled as issuer-reported evidence.

Step 1.1 validation evidence:

- **46 focused fundamental-model tests passed**;
- **1,731 full Python tests passed**;
- Python bytecode compilation and `pip check` passed; and
- the frontend production build and all **36 browser tests passed**.

Phase 1, Step 1.2 is now complete. `app/gateways/fundamentals.py` defines the
runtime-checkable, provider-neutral `FundamentalEvidenceGateway` for exactly
five approved, read-only capabilities: company search, issuer resolution,
company overview, financial statements, and shareholding history. It adds:

- a run-specific entitlement/capability manifest whose entries are always
  read-only and idempotent;
- versioned, frozen request contracts carrying the exact tenant/provider
  connection scope, operation/request identity, timestamp, capability, and a
  stable request fingerprint;
- bounded company-search and issuer-resolution models with strict `Decimal`
  match scores, explicit resolved/ambiguous/not-found states, and
  provider-record fingerprints;
- typed overview, reported-period financial-statement, and shareholding
  requests with bounded period/history controls;
- fail-closed retrieval results for completed, partial, not-found,
  not-entitled, paywalled, unavailable, and validation-rejected outcomes;
- capability-specific statement allow-lists so a financial, overview, or
  ownership response cannot release unrelated evidence;
- a response-binding guard covering request ID/fingerprint, tenant connection,
  capability, and timestamp; and
- a sanitized `FundamentalGatewayError` hierarchy for configuration,
  authentication, entitlement, unavailable capability/provider, throttling,
  and response-validation failures.

Raw provider payloads, HTML, cookies, session state, MCP transports,
`fetch_document`, screeners, order methods, and write operations are absent
from this boundary. Extra secret/raw-payload fields are rejected without
echoing their values in Pydantic validation output.

Step 1.2 validation evidence:

- **93 focused fundamental model/gateway/error tests passed**, including 47
  gateway and safe-failure scenarios added by this step;
- **1,778 full Python tests passed** in 60.491 seconds;
- Python bytecode compilation and `pip check` passed; and
- the frontend production build and all **36 browser tests passed**.

Phase 1, Step 1.3 is now complete. The database-neutral cache contracts are in
`app/models/fundamental_storage.py` and
`app/storage/fundamental_repositories.py`. They add:

- semantic cache keys scoped by tenant, provider connection, provider, issuer,
  evidence capability, as-of date, and the exact statement/period or
  shareholding request shape;
- deliberate exclusion of operation IDs and request timestamps from cache
  identity, allowing repeated analysis to reuse the same valid scoped data;
- immutable stored entries containing the validated request, bound gateway
  result, snapshot, fingerprints, retrieval time, storage time, and expiry;
- a maximum retention of ten days, with shorter retention allowed, inclusive
  expiry (`as_of >= expires_at`), and rejection of already-expired saves;
- fail-closed chain validation across the request, gateway response, tenant,
  provider connection, issuer, capability, result, and evidence snapshot;
- payload-free summaries and mandatory tenant/provider-scoped listing queries;
  and
- the runtime-checkable `FundamentalSnapshotRepository` protocol for
  idempotent immutable save, scoped non-expired get/list, exact delete, and
  startup/opportunistic expiry purge.

The protocol requires implementations never to return an expired row even when
physical deletion has not yet run. Saving different content under the same
semantic key must raise `StorageConflictError`. No unscoped `list_all`, raw
payload/session storage, or provider credential field exists.

Step 1.3 validation evidence:

- **118 focused fundamental model/gateway/storage tests passed**, including 25
  cache/repository contract scenarios added by this step;
- **1,803 full Python tests passed** in 60.445 seconds;
- Python bytecode compilation and `pip check` passed; and
- the frontend production build and all **36 browser tests passed**.

Phase 1, Step 1.4 is now complete. The thread-safe reference adapter is in
`app/storage/adapters/fundamental_in_memory.py`. It adds:

- an `RLock`-protected immutable snapshot map with defensive copies;
- atomic idempotent saves and `StorageConflictError` for different active
  content under the same semantic key;
- mandatory `FundamentalRepositoryScope` on point reads and deletes, closing
  the key-possession cross-tenant exposure left by the initial protocol;
- startup and opportunistic expiry purge, inclusive expiry, explicit purge
  counts, and rejection of future-stored or already-expired rows;
- point-in-time reads that cannot reveal a row before its `stored_at` time;
- newest-first deterministic listing with tenant/provider, symbol/exchange,
  capability, retrieval-window, offset, and limit filters;
- clean refresh after expiry, so a repeated request reuses valid data for up to
  ten days and accepts newly retrieved evidence after the old row expires; and
- concurrency coverage for 64 identical saves and competing immutable-content
  races without duplicates or corruption.

Step 1.4 validation evidence:

- **132 focused fundamental model/gateway/storage tests passed**, including 14
  in-memory adapter scenarios;
- **1,817 full Python tests passed** in 60.783 seconds;
- Python bytecode compilation, `pip check`, and `git diff --check` passed; and
- the frontend production build and all **36 browser tests passed**.

No Tijori repository was downloaded or vendored, no provider session was
opened, and no network, DuckDB, document, agent, or LLM integration was added
in this step.

Phase 1, Step 1.5 is now complete. `DuckDBJarvisStorage` implements the same
`FundamentalSnapshotRepository` contract and upgrades the database from schema
version 3 to **version 4**. The persistent design adds:

- `jarvis_fundamental_snapshots`, containing indexed scope/issuer/capability,
  content fingerprints, retrieval/storage/expiry lifecycle, payload-free
  summary metadata, and the strict immutable JSON envelope;
- normalized request-statement, request-period-type, source, fact, and conflict
  relations for transparent downstream queries;
- indexes for tenant/provider retrieval, issuer/capability retrieval, expiry,
  source authority, and statement/line-item/period lookup;
- transactional immutable saves, conflict rollback, idempotent child-table
  repair, exact scoped deletion, and parent-plus-child expiry purge;
- startup and opportunistic inclusive-expiry cleanup, automatic same-key
  refresh after expiry, future-storage rejection, and point-in-time reads;
- parameterized tenant/provider/symbol/capability/time-window filters with
  deterministic newest-first pagination;
- strict envelope and fingerprint integrity checks that fail closed on corrupt
  persisted data;
- restart persistence and offline schema-v3-to-v4 migration; and
- direct observable-contract parity against the in-memory adapter, plus
  serialized concurrency tests for identical and conflicting writes.

Step 1.5 validation evidence:

- **147 focused fundamental tests passed**, including 15 persistent-adapter
  scenarios;
- **1,832 full Python tests passed** in 63.010 seconds; and
- Python bytecode compilation, `pip check`, and `git diff --check` passed;
- the frontend production build and all **36 browser tests passed**; and
- all testing remained offline without Tijori, broker, or LLM credentials.

No Tijori source was downloaded or installed, no subprocess was launched, and
no authentication/session data or raw provider response was written to
DuckDB.

Phase 1, Step 1.6 is now complete. The offline-only adapter foundation is in
`app/fundamentals/tijori_mcp_contracts.py` and
`app/fundamentals/adapters/tijori_mcp.py`. It adds:

- an injected `TijoriMcpTransport` protocol that keeps credentials, cookies,
  browser state, process handles, and network behavior outside the adapter;
- an immutable exact allow-list for `search_company`, `resolve_company_ids`,
  `get_company_overview`, `get_financials`, and `get_shareholding`;
- strict canonical-JSON result envelopes with size/depth/node/string bounds,
  failure-with-payload rejection, deterministic fingerprints, and pinned
  provider-contract/parser versions;
- strict synthetic search, resolution, source, and fact response shapes with
  finite numeric values, unique IDs, explicit periods, bounded collections,
  and safe Tijori HTTPS source locations;
- deterministic search/resolution and evidence normalization into the existing
  provider-neutral contracts, including source/fact lineage and immutable
  content fingerprints;
- enforced `PROVIDER_STANDARDIZED` / `STANDARDIZED_PROVIDER` provenance and a
  maximum `RESEARCH_GRADE` posture, so provider data cannot become primary or
  decision-grade evidence by adapter convention;
- explicit partial, missing, not-found, not-entitled, paywalled, and unavailable
  outcomes, with authentication/rate/protocol/transport failures translated to
  the sanitized application hierarchy; and
- fail-closed checks for provider/version/tool mismatch, cross-issuer output,
  exchange/result-limit escape, capability-incompatible facts, unsafe URLs,
  schema drift, and invalid execution timestamps.

Step 1.6 validation evidence:

- **21 Tijori adapter synthetic-fixture tests passed**;
- **168 focused fundamental model/gateway/cache/adapter tests passed**;
- **1,853 full Python tests passed** in 62.281 seconds; and
- Python compilation and `git diff --check` passed.

This step made no provider call, downloaded or executed no Tijori source,
started no subprocess/browser, opened no authenticated session, and persisted
no synthetic or raw provider payload. The transport is deliberately only a
port; a hardened local fork/runtime remains blocked by the audit gates.

Phase 1, Step 1.7 is now complete. The concrete but still offline local process
boundary is implemented in `app/fundamentals/transports/stdio_mcp.py`. It adds:

- MCP newline-delimited JSON-RPC initialization, initialized notification,
  `tools/list`, and `tools/call` exchanges;
- one-operation-per-process lifecycle with a total deadline, bounded
  concurrency, deterministic stdin/stdout closure, and TERM/KILL process-group
  cleanup;
- `shell=False`, no inherited application environment, discarded subprocess
  stderr, and sanitized RPC/transport failure categories;
- absolute, non-symlink runtime and entrypoint paths with pinned SHA-256 hashes
  revalidated before every launch, write-permission and ownership checks, and
  immutable fingerprinted settings;
- tenant/connection/account-hash-derived session filenames that reveal no raw
  identity, with owner-only root and session permissions, regular-file,
  hard-link, location, and size validation;
- strict MCP request/response identity, protocol, capability, experimental
  authentication metadata, provider contract, catalog, envelope, JSON shape,
  byte, depth, node, timeout, and approved-tool checks; and
- a real synthetic stdio server fixture plus an end-to-end test through the
  existing `TijoriMcpAdapter` and provider-neutral gateway result.

The transport test suite covers successful and unauthenticated inspection,
successful and semantic-failure calls, invalid provider/tool/arguments,
missing/insecure/symlinked sessions, tampered or group-writable code, protocol
and contract drift, malformed and oversized output, server notifications,
unapproved catalogs, sanitized authentication errors, response-tool and
`isError` mismatch, timeout cleanup, environment isolation, and scoped session
identity.

Step 1.7 validation evidence:

- **21 hardened stdio transport tests passed**, including the synthetic
  transport-to-adapter-to-gateway path;
- **189 focused fundamental tests passed**;
- **1,874 full Python tests passed** in 65.175 seconds; and
- Python compilation and `git diff --check` passed.

No upstream Tijori repository or package is installed or executed. The only
spawned process is the repository-owned synthetic test server. No browser,
network, live login, credential, cookie, CSRF token, or provider data is used
or persisted.

Phase 1, Step 1.8 is now complete. The Jarvis-owned minimal local bridge is in
`integrations/tijori-mcp/`. It implements the approved five-tool MCP provider
surface without copying upstream setup/discovery, authentication capture,
cache, document, screener, market, macro, or write functionality. The exact
runtime dependencies and lockfile are pinned; direct startup accepts only Node
24. The server advertises read-only/idempotent annotations, loads only an
owner-scoped pre-existing session boundary, probes authentication, and does not
compose provider handlers until authentication succeeds.

The implemented bounded handlers are:

- `search_company`: bounded provider search followed by company-page identity
  verification;
- `resolve_company_ids`: deterministic slug or search resolution with strict
  exchange/symbol/ISIN/provider-ID matching;
- `get_company_overview`: supported market-cap, P/E, ROE, and ROCE evidence;
- `get_financials`: supported annual/quarterly income statement, balance sheet,
  cash-flow, and EPS facts with explicit INR-crore units; and
- `get_shareholding`: explicit quarterly promoter, FII, DII, public, and
  promoter-pledge percentages, including an unknown marker when requested
  pledge data is not displayed.

All evidence is provider-standardized secondary research evidence with safe
Tijori HTTPS provenance and explicit primary-source reconciliation
limitations. Historical cutoffs fail closed because provider-page observation
does not prove historical publication timing. Missing categories, totals,
financial rows, units, periods, and values are never inferred. Provider errors
and browser exceptions become typed payload-free envelopes without returning
raw HTML, credentials, cookies, session state, exception text, or unmapped
provider data.

Step 1.8 validation evidence:

- **7 focused financial handler tests passed**;
- **7 focused shareholding handler tests passed**;
- **8 focused provider-registry tests passed**;
- **13 focused MCP server/composition tests passed**; and
- all **111 local bridge tests passed** offline.

The authenticated MCP composition test uses only an injected synthetic browser
runner. No live browser was launched, no Tijori credential/session was created,
no provider request was made, and no provider data was persisted. All changes
remain unstaged.

Phase 1, Step 1.9 is now complete. `app/composition/fundamentals.py` composes
the pinned `TijoriStdioMcpTransport` into `TijoriMcpAdapter` and releases it as
the provider-neutral `FundamentalEvidenceGateway`. Construction performs no
process, browser, session inspection, authentication, network, or provider
call. The transport builder remains injectable for offline testing.

The same module loads only these non-secret environment controls:

- `JARVIS_TIJORI_RUNTIME_EXECUTABLE` and `JARVIS_TIJORI_RUNTIME_SHA256`;
- `JARVIS_TIJORI_SERVER_ENTRYPOINT` and `JARVIS_TIJORI_SERVER_SHA256`;
- `JARVIS_TIJORI_SESSION_ROOT`;
- optional protocol/provider-contract versions;
- optional timeout/termination controls; and
- optional message, concurrency, and session-size ceilings.

No username, password, token, cookie, CSRF value, account identity, or raw
session content is accepted by the composition settings. Invalid environment
values raise a sanitized configuration failure. The adapter contract version
is derived from the transport by default, and explicit contract drift fails at
composition time.

Step 1.9 validation evidence:

- **5 focused fundamental composition tests passed**;
- all **194 fundamental/Tijori subsystem tests passed**;
- Python compilation and repository-wide `git diff --check` passed; and
- no process, browser, network, login, credential, or provider call occurred.

## 1. Current Verified State

- Offline regression baseline: **2,171 passing Python unit/integration tests**.
- Browser baseline: production build plus **51 passing frontend tests**.
- Local Tijori MCP bridge baseline: **224 passing Node tests** under the pinned
  Node 24.20.0 runtime.
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
12. Fundamental provider calls, financial-document ingestion/RAG, financial
    agents, news, and sentiment remain parked. The provider-neutral fundamental
    evidence contracts now exist; any later conclusion must remain
    citation-bound and disclose missing or unavailable sources.
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

The historical full-suite baseline at the time this section was written was
`Ran 1803 tests` and `OK`, followed by `36` passing frontend tests after a
successful production build. Provider-session work was added afterward and has
not yet received a new complete staging-boundary regression count. Establish
and record that new count before staging. Do not treat a live provider call as
part of the offline regression suite. The build's Plotly chunk-size warning is
advisory; any actual build/test failure remains blocking.

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

### User-owned provider-session backend completed on 2026-08-29

The backend path for manual, user-owned Tijori authentication is now
implemented and remains provider-neutral above the local adapter. It does not
accept a Tijori username, password, cookie, CSRF token, browser storage value,
or raw authenticated payload through Python, HTTP, command-line arguments,
DuckDB, logs, or environment variables.

The implementation consists of:

- `app/fundamentals/session_provisioning.py`: frozen provider-neutral status,
  provision, and secure-revocation contracts with connection binding and
  secret-free lifecycle metadata;
- `app/fundamentals/local_session_provisioner.py`: owner-only local inspection,
  bounded interactive-process execution, independent artifact validation,
  expiry classification, and overwrite-plus-unlink revocation;
- `integrations/tijori-mcp/src/interactive-session.js`: headed Playwright login
  flow restricted to first-party Tijori HTTPS pages, positive authentication
  confirmation, and atomic no-overwrite session installation;
- `integrations/tijori-mcp/src/provision-session-cli.js`: Node 24-only command
  accepting the scoped target and timeout through controlled environment
  values, rejecting command arguments, and returning only status plus a SHA-256
  session reference;
- `app/services/provider_sessions.py`: provider-neutral application commands
  for `status`, `provision`, and `revoke`, including response rebinding and
  sanitized failures;
- `app/fundamentals/provider_connections.py`: thread-safe in-memory connection
  registry that binds a browser session to one tenant and resolves only an
  exactly matching provider connection/account reference within that tenant;
- `app/composition/fundamentals.py`: composition of the pinned runtime, fixed
  repository-local provisioning CLI, deterministic session path, local
  provisioner, and application service;
- `app/api/models.py` and `app/api/http.py`: authenticated, secret-free HTTP
  contracts and routes for provider-session status, provisioning, and
  revocation; and
- `examples/browser_api.py`: explicit opt-in local bootstrap that registers one
  user-owned Tijori connection and injects the service, registry, scope
  resolver, ownership hooks, and fixed local tenant resolver.

The HTTP routes are:

- `POST /api/v1/sessions/{session_id}/provider-session/status`;
- `POST /api/v1/sessions/{session_id}/provider-session/provision`; and
- `POST /api/v1/sessions/{session_id}/provider-session/revoke`.

All three require the existing browser-session capability token. Browser
session creation binds the session to the trusted tenant identity; normal
closure unbinds it, and access-token revocation still occurs if unbinding
fails. Resolver errors and connection mismatches return a non-disclosing 404,
invalid explicit authorization/secure-deletion inputs return 422, ownership
failures return a sanitized 503, and provider command failures return a
sanitized 502. Lifecycle responses omit tenant ID, account reference hash,
session reference hash, cookies, paths, and credentials.

Runtime enablement is deliberately explicit:

- `JARVIS_TIJORI_ENABLED=true` activates composition; unset or `false` leaves
  the existing API unchanged;
- `JARVIS_LOCAL_TENANT_ID` and `JARVIS_TIJORI_CONNECTION_ID` are non-secret
  Jarvis identifiers;
- `JARVIS_TIJORI_ACCOUNT_REFERENCE_HASH` is optional and may contain only a
  64-character lowercase SHA-256 value, never the underlying account identity;
- the runtime executable and MCP server entrypoint are absolute, pinned by
  SHA-256, non-symlinked, executable/readable as applicable, and not
  group/world writable;
- the runtime must be Node 24; the provisioning CLI enforces that major
  version again at execution time;
- the session root must be canonical, current-user-owned, and mode 0700; and
- the provisioning CLI path is fixed under the repository and is not supplied
  by browser input or environment configuration.

See `.env.example` for the complete supported variable set. The default
interactive timeout is five minutes, the default local session maximum age is
twelve hours, and the MCP/session payload ceiling defaults to 5,000,000 bytes.
The authenticated artifact itself remains an owner-only local file and is
never copied into DuckDB.

Offline operator checklist before any separately approved live smoke test:

1. Install or copy a fixed Node 24 runtime to an absolute regular-file path.
   Confirm that invoking that exact file with `--version` reports major version
   24. Do not configure a moving `node`, `nvm current`, or symlink path.
2. Verify that the configured MCP server entrypoint is the reviewed
   Jarvis-owned local bridge. Calculate SHA-256 for the exact runtime and server
   files and place only those digests in `.env`; recalculate after every
   intentional upgrade.
3. Create a dedicated session directory outside public/static/frontend paths,
   make it current-user-owned, and set mode 0700. Do not reuse the DuckDB data,
   log, download, or repository directory.
4. Set `JARVIS_TIJORI_ENABLED=true`, the local tenant/connection identifiers,
   pinned file paths/digests, session root, and provider contract version.
   Leave the optional account reference blank unless a non-reversible
   64-character lowercase SHA-256 reference is already available.
5. Confirm that no `TIJORI_USERNAME`, `TIJORI_PASSWORD`, cookie, CSRF, bearer,
   or browser-storage variable has been added. Jarvis has no supported setting
   for any of them.
6. Start the Python API normally. Composition validates paths and hashes but
   does not open a Tijori browser. The headed browser opens only after an
   authenticated browser session sends an explicitly authorized provision
   command.
7. Keep live login/provider validation separate from offline tests. Do not save
   screenshots, traces, raw tool payloads, console output, or session files as
   test fixtures. Exercise secure revocation when the smoke test is complete.

Offline validation completed across the bounded substeps:

- 13 local provisioner tests, 7 Node CLI tests, and 7 interactive-browser
  fixture tests passed;
- the complete local bridge suite passed with 125 tests;
- 8 composition, 7 command-service, 6 connection-registry, 5 API-model, and 33
  HTTP API tests passed;
- 4 executable-bootstrap tests passed without the earlier LiteLLM remote
  price-map lookup, after moving live research imports inside `create_app()`;
- the combined Python provider-session path passed 85 tests; and
- the fundamental/Tijori subsystem passed 226 tests at its latest applicable
  checkpoint.

No live Tijori provider request, Angel One login, LLM call, or credential-backed
test was performed at the PR boundary. Deterministic merge-gate validation on
2026-08-30 passed **1,974 Python tests**, **45 frontend tests** plus the frontend
production build, and **140 local Tijori MCP tests**. `pip check` reported no
broken requirements and `git diff --check` passed.

### Locked Jarvis command-deck design

The command-deck visual contract is locked at this milestone. Future work may
add agents and states through the same reusable contracts, but should not change
the established composition without an explicit design-revision request:

- the Jarvis neural core remains central, with three packet-bearing reactor
  orbits and state-aware illumination;
- Research Division cards place vertically centred copy on the left and the
  holographic agent on the right, with dynamically measured links converging on
  the Jarvis core;
- Debate Chamber cards retain their left-side hologram and place agent status
  at the bottom-left, with links converging on the same core;
- Market Data, Daily, and Weekly analysts share the complete scanner-ring
  geometry while keeping their distinct candlestick/scanning instruments and
  colour identities;
- Fundamental Analyst uses an emerald radar with binary-data rain; Bull, Bear,
  and Judge retain green, red, and neutral-blue kinetic identities;
- every agent has two colour-matched orbital packet clusters. Packets remain
  subtle while idle and become denser/brighter through state-driven CSS only;
- connector and hologram animation is perception-only. It never delays,
  schedules, retries, or changes backend execution;
- procedural neural audio is opt-in through the explicit switchboard control,
  defaults to OFF after refresh, is rate-limited, and distinguishes activation,
  dispatch, evidence, verdict, completion, and fault cues. It does not persist
  audio or alter voice-reply behaviour; and
- reduced-motion preferences remain authoritative, and audio requires an
  explicit browser interaction before it can start.

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
- No WebSocket alternative or asset-driven full 3D environment. The browser
  console now provides a CSS-rendered holographic command deck, procedural
  opt-in neural sound cues, HTTP/SSE transport, dynamic reactor, operation
  matrix, Plotly charts, setup view, debate, and executive briefing.
- Provider-neutral fundamental evidence, the five-capability gateway, hardened
  local stdio transport, complete structured financial/peer/benchmarking JSON
  contracts, in-memory and DuckDB repositories, the pinned Jarvis-owned local
  bridge, authenticated provider sessions, browser cache orchestration, and
  scoped cached-document resolution now exist. User-owned interactive login
  and selected provider reads have been manually exercised; automated live
  provider regression remains intentionally opt-in. The deterministic
  fundamental metric engine and Fundamental Analyst are pending.
- The frontend exposes Tijori connection status, explicit headed-login connect,
  refresh/status, expiry/reconnect, and secure revoke controls. Credentials and
  provider session material are never accepted or rendered by the frontend.
- The executable bootstrap currently supports one configured local tenant and
  one user-owned Tijori connection. A hosted multi-user deployment must replace
  the fixed tenant resolver with authenticated identity and persist connection
  registrations behind a database-agnostic repository.
- The provider-connection registry is intentionally in memory. Browser close
  unbinds ownership, but abrupt process termination loses registry bindings;
  authenticated session files remain owner-only on disk until expiry or
  explicit revocation.
- Provider-session client idempotency keys produce deterministic request IDs,
  but there is no durable command-result/idempotency repository yet. A retry
  after successful provisioning observes the existing session rather than
  silently overwriting it.
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

1. Promote CPR from a chart-only prior-period overlay into a provider-neutral,
   look-ahead-safe `CPRAnalysisRecord`: width regime, price location,
   acceptance, breakout/retest/rejection/failed-break lifecycle, qualified
   evidence IDs, and daily-weekly swing confluence.
2. Add versioned project-owned runtime skills for Bull, Bear, the existing
   Judge, and the Fundamental Analyst. Enforce allowed evidence, required
   citations, output schemas, abstention, skill version/hash audit, and
   provider-neutral LLM composition.
3. Build the deterministic normalized fundamental metric engine and preliminary
   scorecard from the cached structured JSON, explicitly excluding annual
   reports and qualitative management/moat conclusions.
4. Add database-agnostic repository models/ports for the complete
   `MultiTimeframeEndToEndSwingAnalysisResult`, then implement in-memory and
   DuckDB adapters with normalized daily/weekly evidence, verdict, trade plan,
   and presentation tables.
5. Persist the completed dashboard aggregate and add historical-run queries;
   the live completed-operation read model is implemented.
6. If genuine order flow is prioritized, define a database-neutral
   `MarketMicrostructureGateway`/`OrderFlowGateway` and immutable tick/book
   contracts before selecting Angel best-five prospective capture or licensed
   historical NSE order/trade data. Never backfill “real order flow” from
   candles.
7. Browser microphone capture, speech-to-text, the provider-neutral TTS/STT
   ports, and the Google/ElevenLabs adapters are implemented. Remaining audio
   work: exercise the adapters and catalog downloads against live
   endpoints/credentials, add an acoustic wake-word engine, and add rate
   limiting to `/speech` and `/transcribe` — all while keeping transcript
   handling and financial logic vendor-neutral.
8. Annual-report ingestion, citation-preserving RAG, and qualitative
   management/moat analysis remain explicitly deferred from the current
   fundamental milestone.

For a live actionable-plan check, wait for a naturally bullish qualifying
instrument/dataset and observe it without changing thresholds or forcing the
Judge. That check is useful, but durable multi-timeframe persistence is the more
important next implementation dependency for the planned dashboard.
