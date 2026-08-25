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
- Text wake phrase and research commands
- Authenticated conversation and workflow SSE
- Event-derived daily, weekly, Bull, Bear, and Judge activity states
- Daily/weekly candle, evidence, support, and resistance panels
- Evidence-preserving debate, verdict, and 1:2/1:3 trade-plan display
- Responsive and reduced-motion behavior

Microphone capture, speech-to-text, text-to-speech, and ElevenLabs are not yet
connected. Any future ElevenLabs key must remain in the Python service and must
never use a `NEXT_PUBLIC_` environment variable.
