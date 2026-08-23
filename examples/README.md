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
source interval. Jarvis pulls/resumes hourly candles, aggregates completed
daily and weekly candles, runs the two technical agents in parallel, releases
their paired evidence through the existing Judge, completes the full
Bull/Bear/Judge debate, applies the long-only 2R policy, and asks the Jarvis
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

Expected baseline at this handoff: **1,295 tests, OK**. The last live Reliance
run produced a bearish verdict at 62% confidence and the deterministic
long-only result `NO_TRADE`. It did not validate a naturally bullish live
actionable plan; that path is covered by offline tests.
