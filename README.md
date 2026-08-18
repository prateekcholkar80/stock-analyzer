# Jarvis AI Investment Research Assistant

Jarvis is an agentic financial-intelligence platform designed to produce
explainable, evidence-grounded investment research.

The long-term platform combines market data, quantitative analysis,
retrieval-augmented generation, specialized AI agents, and voice interaction.
It is an investment research assistant—not a stock-price prediction script.

## Project status

Phase 1 implementation is complete. Documentation and final acceptance checks
are in progress.

Implemented:

- Validated environment configuration with protected secrets
- Angel One SmartAPI integration through an injectable gateway
- Typed market quotes and historical candle series
- Market-data normalization and validation
- Application-specific exception hierarchy
- Lazy market-service construction
- Structured JSON logging with secret redaction
- Cross-layer operation/correlation IDs
- Offline unit and integration tests
- Logging-enabled example entry points

Automated baseline: **69 passing tests**.

Not implemented yet:

- Technical-indicator engine
- Historical signal analysis
- Backtesting
- Portfolio and risk analysis
- RAG and document processing
- News discovery and sentiment
- Multi-agent orchestration
- API, dashboard, and voice interface

News discovery and document processing are intentionally parked while
quantitative market analysis is developed.

## Current architecture

```text
MarketAgent
    |
    v
market_tools
    |
    v
MarketDataService
    |
    v
MarketDataGateway protocol
    |
    v
AngelOneClient
    |
    v
Angel One SmartAPI
```

The market service converts external dictionaries into validated domain models:

- `MarketQuote`
- `Candle`
- `HistoricalCandleSeries`

Structured logs emitted by the service and gateway share an operation ID,
allowing one request to be traced across layers.

## Requirements

- Python 3.11 or newer
- Angel One SmartAPI credentials for live examples
- Network access only for live Angel One operations

The automated test suite runs entirely offline.

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

For reproducible pinned versions:

```bash
python -m pip install -r requirements-lock.txt
```

Create the local environment file:

```bash
cp .env.example .env
```

Configure these values in `.env`:

```dotenv
ANGEL_API_KEY="your_api_key"
ANGEL_CLIENT_CODE="your_client_code"
ANGEL_PIN="your_pin"
ANGEL_TOTP_SECRET="your_totp_secret"
```

Never commit `.env`. It is excluded through `.gitignore`.

## Run the automated tests

Run the complete offline suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Run only unit tests:

```bash
.venv/bin/python -m unittest discover -s tests/unit -v
```

Run the cross-layer integration test:

```bash
.venv/bin/python -m unittest \
  tests.integration.test_market_operation_logging -v
```

Automated tests replace the Angel One SDK with fake implementations and do not
make network requests.

## Run examples

Examples are executable modules:

```bash
.venv/bin/python -m examples.candle_conversion
.venv/bin/python -m examples.angel_login
.venv/bin/python -m examples.market_quote
.venv/bin/python -m examples.historical_data
.venv/bin/python -m examples.market_agent
```

Except for candle conversion, these examples may authenticate with or request
data from Angel One.

## Logging and secret safety

Executable entry points configure structured JSON logging.

Logs can contain:

- Event names
- Operation IDs
- Exchange and instrument identifiers
- Request duration
- Candle counts
- Exception types

Logs must not contain:

- API keys
- Client codes
- PINs
- TOTP secrets
- Authorization headers
- Access, refresh, feed, or JWT tokens
- Raw vendor responses
- Vendor exception messages

Sensitive values are redacted as `[REDACTED]`.

Expected application failures at executable entry points exit with status 1
without rendering chained vendor tracebacks.

## Next phase

Phase 2 focuses on quantitative technical analysis:

1. Indicator contracts and typed results
2. SMA and EMA
3. RSI
4. MACD
5. Bollinger Bands
6. ATR and volatility
7. Volume-based indicators
8. Trend and support/resistance structure
9. Swing-trading signal profiles
10. Long-term technical profiles
11. Historical analysis
12. Backtesting

## Disclaimer

This project is for research and educational use. Its output is not investment
advice and should not be treated as a guarantee of future market performance.
