# Stock Analyzer Skills Bundle

This bundle defines a reusable hierarchy of AI coding/research skills for the Stock Analyzer project.

## Purpose

The goal is to make the coding agent behave like a coordinated team of senior specialists instead of a generic software assistant. The master skill routes work to focused sub-skills while preserving one shared architecture, quant research discipline, and production standard.

## Included skills

- **stock-analyzer-quant-architect** — master orchestrator and final architecture authority.
- **python-platform-engineer** — production Python engineering and code quality.
- **quant-researcher** — hypotheses, features, labels, regime-aware research, statistical validation.
- **fundamental-researcher** — statement normalization, financial health, earnings quality, framework applicability, valuation policy and cited deterministic scorecards.
- **technical-analysis-engineer** — deterministic EMA/SMA/Bollinger/RSI/ATR/Volume/CPR implementation and validation.
- **strategy-researcher** — regime-aware momentum, mean-reversion, breakout and trend-continuation strategy design.
- **backtesting-specialist** — event-driven/vectorized backtests, leakage prevention, costs, walk-forward validation.
- **price-action-researcher** — market structure, pivots, BOS/CHOCH, zones, FVGs, liquidity sweeps.
- **market-data-engineer** — OHLCV ingestion, validation, normalization, corporate actions, storage and lineage.
- **quant-ml-researcher** — supervised/unsupervised ML, regime models, strategy models, meta-models and calibration.
- **local-mlops-engineer** — local Apple Silicon training, experiment tracking, registry, reproducibility, drift and retraining.
- **enterprise-fintech-architect** — modular product architecture, ports/adapters, APIs, observability, security and extensibility.
- **risk-and-execution-researcher** — portfolio risk, sizing, execution simulation, slippage, cost models and guardrails.

## Suggested placement

Copy the `skills/` directory into your project-level agent skills location, or point your coding agent to the master `SKILL.md`.

Recommended project shape:

```text
Stock-Analyzer/
├── app/
├── tests/
├── skills/
│   └── stock-analyzer-quant-architect/
│       ├── SKILL.md
│       ├── PROJECT_CONTEXT.md
│       ├── ROUTING.md
│       └── subskills/
│           ├── python-platform-engineer/SKILL.md
│           ├── quant-researcher/SKILL.md
│           ├── fundamental-researcher/SKILL.md
│           ├── technical-analysis-engineer/SKILL.md
│           ├── strategy-researcher/SKILL.md
│           ├── backtesting-specialist/SKILL.md
│           ├── price-action-researcher/SKILL.md
│           ├── market-data-engineer/SKILL.md
│           ├── quant-ml-researcher/SKILL.md
│           ├── local-mlops-engineer/SKILL.md
│           ├── enterprise-fintech-architect/SKILL.md
│           └── risk-and-execution-researcher/SKILL.md
└── ...
```

## How to use

1. Load `skills/stock-analyzer-quant-architect/SKILL.md` as the primary skill.
2. Allow the master skill to consult `ROUTING.md` and the relevant sub-skill(s).
3. Keep `PROJECT_CONTEXT.md` version-controlled with the project so all agents share the same architectural assumptions.
4. Update project decisions in `PROJECT_CONTEXT.md` rather than duplicating them across every skill.
5. Treat the master skill as the final authority when sub-skills make conflicting recommendations.

## Design principle

These skills intentionally separate **research evidence**, **software implementation**, **backtesting**, **ML**, **MLOps**, **risk**, and **architecture**. This reduces the chance that a coding agent creates attractive but statistically invalid trading logic.
