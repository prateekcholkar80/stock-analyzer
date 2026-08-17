# Current Implementation Baseline

This document records the baseline before Phase 1 foundation hardening.

## Implemented execution path

```text
MarketAgent
  -> market_tools.get_current_price
  -> MarketDataService.get_ltp
  -> AngelOneClient / Angel One SmartAPI
```

The repository also contains a `Candle` domain model, raw historical-candle
retrieval, and conversion from Angel One candle arrays to `Candle` objects.

## Automated baseline

The automated suite uses Python's standard-library `unittest` runner and must
remain offline:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The initial tests cover the existing `Candle` model. Service and agent tests
will be added after the Angel One dependency can be injected and replaced with
a fake implementation.

## Known baseline limitations

- Configuration values are loaded without validation.
- Application imports are coupled to the SmartAPI SDK.
- Constructing the market service constructs the real Angel One client.
- Market methods return unvalidated vendor response dictionaries.
- Historical data conversion is not part of the historical retrieval method.
- The market agent supports only a current-price operation.
- The orchestrator, RAG, memory, and indicator implementations are empty.
- No API, dashboard, voice interface, research agent, or risk agent exists yet.

The manual scripts in `examples/` preserve the original live checks and are not
part of automated test discovery.
