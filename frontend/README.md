# Jarvis Browser Console

Responsive browser client for the Jarvis Investment Research Assistant. It
uses the Python API as its only financial-data authority and never computes
indicators, debate conclusions, or trade levels in the browser.

## Local development

Start the backend from the repository root:

```bash
.venv/bin/uvicorn examples.browser_api:create_app --factory --reload
```

Then start the browser client:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. Click the central reactor or type `Hey Jarvis`,
then submit a supported swing-research request such as `Analyze TCS for me`.

The API defaults to `http://127.0.0.1:8000`. Copy `.env.example` to `.env.local`
and change `NEXT_PUBLIC_JARVIS_API_URL` when the backend uses another origin.
The local API allows the Jarvis UI on ports `3000`, `3001`, and `5173` by
default. Override `JARVIS_BROWSER_ORIGINS` for any other browser origin.

## Implemented

- Capability-authenticated browser sessions
- A fresh backend session on page load/refresh; page navigation performs a
  best-effort authenticated close and back/forward-cache restoration reloads
  instead of reviving stale operation state
- Text wake phrase and research commands
- Authenticated conversation and workflow SSE
- Event-derived daily, weekly, Bull, Bear, and Judge activity states
- A backend-bound operation matrix and live verified activity feed; cells are
  driven by namespaced workflow events, not timers or simulated animation
- Separate daily and weekly Plotly candlestick charts
- Broker LTP displayed separately from the latest completed daily/weekly
  analysis close, including the broker observation timestamp
- Immediate confirmed support and resistance shown as clearly labelled
  green/red horizontal price bands when qualifying zones exist
- Daily chart compression for weekends and absent weekday dates; this is visual
  continuity only and never fabricates market candles
- EMA20 and EMA50 as the clean default view
- A collapsed indicator/evidence drawer with `Clean`, `Trend`, `Price action`,
  `Patterns`, `Decision`, and `Everything` presets
- Selectable Bollinger Bands, RSI, volume, prior-period CPR, FVG, confirmed
  pivots, complete S/R lifecycle, HH/HL/LH/LL, BOS/CHOCH, accumulation zones,
  liquidity sweeps, and named TA-Lib candlestick-pattern markers
- Per-timeframe overlay preferences stored in browser local storage; corrupt or
  unavailable storage falls back safely
- Compatibility handling for older dashboard results without chart arrays;
  candles remain visible and a fresh-analysis notice replaces a runtime crash
- Complete compact bullish and bearish setup matrices for both timeframes with
  confirmed/developing/pending/contradicted/invalidated/unavailable states
- Weekly structural versus daily tactical interpretation, 2R/3R feasibility,
  explicit decision-change conditions, and deterministic BUY/NO_TRADE outcome
- Evidence-preserving debate, verdict, and 1:2/1:3 trade-plan display
- Verdict-aware confidence copy and colours: a bearish confidence score means
  confidence in the bearish no-trade verdict; bullish-but-risk-blocked remains
  a caution/no-trade state
- Beginner-readable executive briefing sections and a collapsed raw technical
  evidence ledger
- An `awaiting_confirmation` conversation state and `confirmation_requested`
  turn outcome for the ticker-resolution "Did you mean …?" / "Refresh the
  list?" yes/no prompt
- Opt-in "voice replies" toggle: posts each `spoken_message` to `/speech` and
  plays the returned audio; suspends microphone capture during playback
- Opt-in "voice input" toggle: `MediaRecorder` capture driven by a local
  amplitude-threshold VAD state machine (`idle -> speech -> trailing_silence`),
  uploading a finished utterance to `/transcribe` and submitting it only when
  it is non-blank and (in an active conversation state) above a confidence floor
- Both voice toggles persist per device in `localStorage`, default off, and
  fail silently — a corrupt store, a denied microphone, or an uncomposed
  `/speech` / `/transcribe` route never blocks conversation rendering
- Responsive and reduced-motion behavior

There is no acoustic wake-word engine and no hands-free/streaming audio
session. Speech synthesis and transcription run entirely in the Python service;
any ElevenLabs key must stay there and must never use a `NEXT_PUBLIC_`
environment variable.

## Financial authority and provenance

The browser never calculates indicators, zones, setup steps, Judge outcomes,
or trade prices. It renders the bounded `jarvis.dashboard.v1` projection from
the Python service. Hiding an overlay changes only presentation; all analysis
has already been calculated and validated by the backend.

`Broker LTP` is a quote observed during the completed operation. `Analysis
close` is the latest completed candle supplied to that timeframe's technical
agent. They can differ normally. The UI must never rename analysis close as LTP
or patch the quote into OHLC history.

If no confirmed multi-touch zone of the correct effective type exists below or
above price, support/resistance is displayed as unavailable. The chart does not
guess a level from a single visual turning point, and accumulation zones are not
silently converted into S/R zones.

## Browser validation

```bash
npm test
npm run lint
```

`npm test` performs a production build and then runs the Node tests. Verified
baseline on 2026-08-27: **36 passing tests**. Coverage includes SSR/hydration,
market-date compression, hidden/default overlays, selectable accumulation and
liquidity sweeps, verdict/confidence semantics, readable briefing formatting,
setup-state rendering, workflow-matrix event binding, the voice-reply
preference/gate, and the microphone VAD state machine and transcript-submit
gate.

The production build currently reports Plotly's dynamically loaded client chunk
above the default 500 kB advisory threshold. This is a performance follow-up,
not a correctness failure.

## Honest limitations

- The browser can capture microphone audio behind the opt-in "voice input"
  toggle, but only with a local amplitude VAD; there is no acoustic wake-word
  engine and no always-listening/hands-free session.
- Speech synthesis and transcription (Google / ElevenLabs adapters) run in the
  Python service and are optional; the `/speech` and `/transcribe` routes may
  not exist. There is no Three.js scene yet.
- The speech adapters and browser voice path have not been exercised against
  live provider credentials.
- Session cleanup on page exit is best-effort. The backend has no idle-session
  TTL/garbage collector for an abrupt browser or network termination.
- Results are projected from the completed in-memory browser operation. Durable
  normalized multi-timeframe/dashboard history remains pending.
- The UI has no genuine order-flow view. Existing volume/OBV/accumulation
  evidence must not be labelled as bid/ask order flow.
