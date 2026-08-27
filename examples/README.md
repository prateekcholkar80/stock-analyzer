# Examples

These scripts exercise the current implementation manually. Scripts that log in
or request market data require a configured `.env` file and network access.
They are examples and integration checks, not automated tests.

Run them from the repository root with the project virtual environment:

```bash
.venv/bin/python -m examples.candle_conversion
.venv/bin/python -m examples.angel_login
.venv/bin/python -m examples.market_quote
.venv/bin/python -m examples.historical_data
.venv/bin/python -m examples.market_agent
```

The last four commands can authenticate with or query Angel One. Do not use
them in an offline unit-test run.

## Conversation layer

The wake-activated conversation boundary is an application API and now has a
live, credential-touching example in `examples/conversation_demo.py`. It
supports both channels through:

```python
session.handle_text("Hey Jarvis, analyze Reliance for a swing trade")
session.handle_voice_transcript(
    "Hey Jarvis, how is TCS looking for a swing trade?"
)
```

`handle_voice_transcript()` expects text from a speech-to-text adapter; it does
not record audio. A composed conversation also requires a rolling market-data
fetch dependency, validated Angel/instrument settings, and a ready mandatory
Bull/Bear/Judge LLM configuration. The composition is lazy, so the LLM panel is
not constructed until a valid, resolved research request reaches execution.

Run the live text example only with valid Angel One and LLM configuration:

```bash
.venv/bin/python -m examples.conversation_demo
```

The example currently sends these two turns:

```text
Hey Jarvis
How is Reliance looking for a swing trade?
```

The request resolves to `NSE`, `RELIANCE-EQ`, token `2885`, and an hourly
source interval. Jarvis pulls/resumes hourly candles with a recent correction
overlap, records whether the dataset was initial/incremental/unchanged, fetches
a separately timestamped Broker LTP, aggregates completed daily and weekly
candles, runs the two technical and accumulation assignments in
parallel, releases their paired evidence through the existing Judge, completes
the full Bull/Bear/Judge debate, applies the BUY/NO_TRADE minimum-2R policy,
builds the deterministic setup/timeframe interpretation, and asks the Jarvis
persona to render the CEO briefing. Change the text in
`examples/conversation_demo.py` when manually exercising another supported
single-instrument swing request; instrument identity is resolved rather than
hard-coded into the workflow.

The example stores local research in:

```text
data/jarvis_conversation_demo.duckdb
```

That database and its WAL are generated local artifacts and must not be
committed. The complete multi-timeframe result/presentation is currently
validated in memory; dedicated normalized persistence for that combined result
is still the recommended next implementation step.

To capture the complete sanitized conversation and Jarvis/Bull/Bear/Judge prompt trail
for that run, configure:

```dotenv
JARVIS_PROMPT_AUDIT_ENABLED="true"
JARVIS_PROMPT_AUDIT_PATH="logs/jarvis-prompt-audit.jsonl"
```

Use `JARVIS_JUDGE_LLM_MAX_TOKENS="1500"` and
`JARVIS_PERSONA_LLM_MAX_TOKENS="5000"` for the currently verified live
configuration. The Judge needs room for both case summaries and its rationale;
a 2,500-token persona budget repeatedly truncated the structured presentation
during live Reliance testing. Provider/model requirements can differ, so these
are verified operating values rather than universal guarantees.

The audit file contains sensitive research conversation even after credential
redaction. Keep it private and never commit it. For offline validation, use:

```bash
.venv/bin/python -m unittest tests.unit.test_jarvis_conversation -v
```

For the complete offline regression baseline:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Expected backend baseline at this handoff: **1,685 Python tests, OK**. The
frontend production build and **36 browser tests** also pass. The last live
Reliance run produced a bearish verdict at 62% confidence and the deterministic
result `NO_TRADE`. It did not validate a naturally bullish live
actionable plan; that path is covered by offline tests.

## Browser API

With valid Angel One, Jarvis user, and LLM configuration, start the live API:

```bash
.venv/bin/uvicorn examples.browser_api:create_app --factory --reload
```

The factory authenticates with Angel One and stores downloaded research in
`data/jarvis_browser.duckdb` by default. Override that path with
`JARVIS_DATABASE_PATH` and the bounded worker count with
`JARVIS_BROWSER_WORKERS`. `JARVIS_BROWSER_ORIGINS` is a comma-separated CORS
allowlist and defaults to local frontend origins on ports 3000 and 5173.

`examples/browser_api.py` also composes the optional extras:
`compose_jarvis_browser_operations` now returns
`JarvisBrowserApplication(runner, conversation)` and is passed an
`AmfiMarketCapCatalog` and `NseSectorMasterCatalog` (both lazily downloaded on
first use) so the conversational ticker-resolution fallback is wired into the
coordinator. `compose_jarvis_speech_synthesis()` and
`compose_jarvis_speech_transcription()` are composed independently and passed as
`speech=` / `transcription=` to `create_jarvis_http_app`, which mounts the
`/speech` and `/transcribe` routes only then. Neither speech capability fires or
needs a credential until its route is first called.
Interactive OpenAPI documentation is served at
`http://127.0.0.1:8000/api/docs`. The API supports polling, bounded event
replay, and authenticated SSE streaming. The browser must use `fetch()`
streaming so it can attach `X-Jarvis-Session-Token`; do not place the token in
the stream URL. The same API now accepts typed or already-transcribed voice
turns, enforces the wake phrase while dormant, greets `JARVIS_USER_NAME`,
dispatches analysis asynchronously, retains approved context for Judge
follow-ups, and exposes conversation-state SSE.

The implemented browser console runs separately from `frontend/` on port 3000
and calls this API on port 8000 by default. Override its target with
`NEXT_PUBLIC_JARVIS_API_URL`. It renders daily/weekly Plotly charts, Broker LTP
versus completed analysis close, confirmed immediate S/R, optional technical
overlays, setup matrices, Judge confidence semantics, and the executive
briefing. Refreshing or leaving the page performs a best-effort close of the old
browser session before the next page creates a fresh one. It also has opt-in
"voice replies" (plays `/speech` audio for each spoken message) and "voice
input" (microphone capture with a local VAD, uploading finished utterances to
`/transcribe`) toggles. An acoustic wake-word engine and hands-free session
remain pending.
