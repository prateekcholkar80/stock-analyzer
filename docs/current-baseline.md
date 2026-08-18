# Phase 1 Implementation Baseline

This document records the Jarvis AI Investment Research Assistant baseline at
the completion of Phase 1.

## Phase 1 outcome

Phase 1 established a secure, testable market-data foundation for later
technical analysis, backtesting, agentic workflows, and research features.

The implementation is intentionally focused on market-data access and domain
boundaries. It does not yet provide investment recommendations or predictive
signals.

## Implemented execution paths

### Current market quote

```text
MarketAgent
  -> market_tools.get_current_price
  -> lazy MarketDataService singleton
  -> MarketDataService.get_quote
  -> MarketDataGateway
  -> AngelOneClient.get_ltp
  -> Angel One SmartAPI
  -> MarketQuote
```

### Historical candles

```text
MarketDataService.get_historical_series
  -> MarketDataGateway
  -> AngelOneClient.get_historical_candles
  -> Angel One SmartAPI
  -> raw candle rows
  -> Candle models
  -> HistoricalCandleSeries
```

External response dictionaries do not cross the service boundary. They are
converted into validated domain models before being returned to application
consumers.

## Implemented components

| Component | Responsibility |
|---|---|
| `Settings` | Validate required environment configuration and protect secrets |
| `AngelOneClient` | Authenticate and communicate with Angel One SmartAPI |
| `MarketDataGateway` | Define the market-provider interface |
| `MarketDataService` | Coordinate requests and normalize external responses |
| `MarketQuote` | Represent a validated current market quote |
| `Candle` | Represent a validated timezone-aware OHLCV candle |
| `HistoricalCandleSeries` | Represent typed historical data and metadata |
| `market_tools` | Lazily create and reuse the market service |
| `MarketAgent` | Expose the current-price market capability |
| `logging_config` | Provide JSON logging, redaction, and operation tracing |

## Configuration

The application requires:

```dotenv
ANGEL_API_KEY="your_api_key"
ANGEL_CLIENT_CODE="your_client_code"
ANGEL_PIN="your_pin"
ANGEL_TOTP_SECRET="your_totp_secret"
```

Configuration is:

- Loaded lazily
- Validated with Pydantic
- Rejected when required values are missing or blank
- Represented with `SecretStr`
- Wrapped in `ConfigurationError` at the application boundary
- Cached after successful loading

The local `.env` file is ignored by Git.

## Exception boundaries

Expected application failures use an explicit hierarchy:

```text
ApplicationError
├── ConfigurationError
└── ExternalServiceError
    ├── AuthenticationError
    └── MarketDataError
        ├── ClientNotInitializedError
        ├── DataValidationError
        └── InvalidInstrumentError
```

SDK authentication failures are wrapped as `AuthenticationError`.

Market-provider request failures are wrapped as `MarketDataError`.

Malformed external response data is wrapped as `DataValidationError`, with the
original parsing or validation exception preserved as the cause.

## Domain validation

`Candle` validates:

- Timezone-aware timestamps
- Non-negative OHLC prices
- Non-negative volume
- High greater than or equal to low
- High not below open or close
- Low not above open or close

`MarketQuote` validates:

- Required instrument metadata
- Non-negative price fields
- High greater than or equal to low
- Timezone-aware observation time

`HistoricalCandleSeries` validates:

- Required market metadata
- Required interval
- Typed candle entries
- Timezone-aware retrieval time

Domain models are frozen against field reassignment.

## Logging and observability

Application entry points configure structured JSON logging.

Each log can include:

- UTC timestamp
- Log level
- Logger name
- Event name
- Operation ID
- Exchange and instrument identifiers
- Duration
- Candle count
- Exception type

A single quote or historical operation retains the same operation ID across the
service and Angel One gateway layers.

Sensitive fields and embedded values are redacted. Application logs do not
intentionally include credentials, session tokens, raw vendor responses, or
vendor exception messages.

## Automated test baseline

Run the complete offline suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Phase 1 baseline:

```text
66 tests passing
```

Coverage includes:

- Configuration validation and secret masking
- Exception hierarchy
- Angel SDK delegation and failure wrapping
- Market-service initialization and normalization
- Quote and historical response validation
- Domain-model boundaries
- Structured logging and redaction
- Operation-context behavior
- Lazy service creation and reuse
- Cross-layer operation-ID continuity
- Logger and singleton test-state restoration

The automated suite does not require network access or real Angel One
credentials.

## Manual examples

Run examples from the repository root:

```bash
.venv/bin/python -m examples.candle_conversion
.venv/bin/python -m examples.angel_login
.venv/bin/python -m examples.market_quote
.venv/bin/python -m examples.historical_data
.venv/bin/python -m examples.market_agent
```

Only candle conversion is fully offline. The remaining examples may
authenticate with or request data from Angel One.

## Known limitations

Phase 1 intentionally does not include:

- Symbol discovery or instrument-master synchronization
- Retry, timeout, rate-limit, or circuit-breaker policies
- Persistent market-data storage
- Streaming market data
- Technical indicators
- Signal generation
- Backtesting
- Portfolio and risk models
- Fundamental analysis
- RAG and document processing
- News discovery or sentiment
- Multi-agent orchestration
- API, dashboard, or voice interface

`MarketAgent` currently exposes only the current-price capability. Historical
data is available through `MarketDataService` but is not yet exposed as an
agent tool.

## Phase 2 handoff

Phase 2 will build quantitative technical analysis on the typed historical
candle foundation.

The first implementation step is to define typed indicator inputs, outputs,
validation rules, and insufficient-data behavior before implementing SMA and
EMA calculations.
