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
