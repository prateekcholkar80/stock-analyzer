# Angel Stock Analyzer

AI-assisted stock analysis platform using Angel One SmartAPI.

The target product is the Jarvis AI Investment Research Assistant: an
evidence-grounded financial research platform combining market data, RAG,
specialized agents, and voice interaction.

## Current Phase

Phase 1:
- Angel One API integration
- Market data retrieval
- Historical price analysis

Future:
- Technical indicators
- Fundamental analysis
- News sentiment
- LLM based stock analyst

## Development baseline

Activate the project virtual environment, then run the offline tests:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Manual Angel One integration scripts are stored in `examples/`. See
`docs/current-baseline.md` for the implemented execution path and known
limitations.
