# Skill Routing Guide

Use one primary sub-skill and only the supporting sub-skills required by the
task. Read their `SKILL.md` files before acting. The master
`stock-analyzer-quant-architect`, repository instructions and current domain
contracts jointly define the governing boundary.

These are project engineering and research skills. They are not the deployed
Jarvis Bull, Bear, Judge or Fundamental Analyst personas and must not be
presented as runtime agents without a separate versioned runtime-skill
contract.

| Task | Primary skill | Supporting skills |
|---|---|---|
| New Python module/refactor | python-platform-engineer | enterprise-fintech-architect, relevant domain skill |
| New indicator or feature | technical-analysis-engineer | quant-researcher, python-platform-engineer |
| BOS/CHOCH/FVG/sweep logic | price-action-researcher | quant-researcher, backtesting-specialist |
| Historical strategy test | backtesting-specialist | quant-researcher, risk-and-execution-researcher |
| New data feed/storage | market-data-engineer | enterprise-fintech-architect, python-platform-engineer |
| Train classifier/regressor | quant-ml-researcher | quant-researcher, local-mlops-engineer |
| Local training pipeline | local-mlops-engineer | quant-ml-researcher, market-data-engineer |
| Service/API/platform design | enterprise-fintech-architect | python-platform-engineer, relevant domain skill |
| Slippage/fees/sizing/risk | risk-and-execution-researcher | backtesting-specialist |
| New strategy concept | strategy-researcher | quant-researcher, backtesting-specialist, relevant feature specialist |
| Hierarchical evidence-family policy | strategy-researcher | quant-researcher, price-action-researcher, risk-and-execution-researcher |
| Daily/weekly aggregation or data freshness | market-data-engineer | technical-analysis-engineer, python-platform-engineer |
| Fundamental provider/cache/document boundary | enterprise-fintech-architect | python-platform-engineer, quant-researcher |
| Fundamental normalization or mandatory checks | fundamental-researcher | quant-researcher, python-platform-engineer |
| Deterministic fundamental scorecard | fundamental-researcher | quant-researcher, enterprise-fintech-architect, python-platform-engineer |
| Runtime Bull/Bear/Judge/Fundamental skill contract | enterprise-fintech-architect | fundamental-researcher, quant-researcher, risk-and-execution-researcher, relevant evidence specialist |
| Backtest or walk-forward learning evidence | backtesting-specialist | quant-researcher, strategy-researcher, risk-and-execution-researcher |

## Current product boundaries

- Default analysis is Indian cash-market swing research using completed daily
  and weekly candles derived from the one-hour broker archive.
- Preserve long-entry versus `NO_TRADE`; do not introduce short execution.
- Keep market structure and price context primary. Correlated indicators are
  supporting evidence, not independent votes.
- Fundamental health and technical entry quality are separate conclusions.
- LLM agents explain and debate released evidence; they do not calculate or
  alter it.
- No skill grants authority to call a live provider, authenticate, stage,
  commit, push, deploy, place an order or mutate a portfolio.

## Mandatory cross-checks

For any new trading signal or ML model, apply:

1. `quant-researcher`
2. `backtesting-specialist`
3. `risk-and-execution-researcher` when strategy returns depend on fills or sizing

For any new price-action detector, also consult `price-action-researcher`.

For any training pipeline, consult `quant-ml-researcher` and `local-mlops-engineer`.

For fundamental metrics, mandatory checks, framework applicability, or
scorecards, consult `fundamental-researcher`. Keep financial health separate
from technical entry quality and preserve primary-source limitations.

For any architectural change that introduces infrastructure, vendor or
framework coupling, consult `enterprise-fintech-architect`.

## Conflict resolution

When recommendations conflict, use this priority:

1. Financial correctness
2. No leakage / no look-ahead
3. Reproducibility
4. Testability
5. Architecture quality
6. Explainability
7. Performance
8. Convenience
