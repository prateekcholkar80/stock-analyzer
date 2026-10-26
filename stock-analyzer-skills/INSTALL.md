# Installation / Integration

## Option A — Project-local skills

Copy the included `skills/` folder to the root of your Stock Analyzer repository.

```text
Stock-Analyzer/
├── app/
├── tests/
├── skills/
│   └── stock-analyzer-quant-architect/
└── ...
```

Point your coding agent to:

```text
skills/stock-analyzer-quant-architect/SKILL.md
```

as the primary project skill.

## Option B — Agent-specific skills directory

If your coding agent supports a dedicated skills directory, copy the full `stock-analyzer-quant-architect` folder there. Preserve the relative locations of `PROJECT_CONTEXT.md`, `ROUTING.md`, `skills-manifest.yaml` and `subskills/`.

## Recommended agent instruction

Use the following short project instruction in your agent configuration:

```text
For all Stock Analyzer work, load skills/stock-analyzer-quant-architect/SKILL.md first. Treat PROJECT_CONTEXT.md as the shared architecture source of truth and use ROUTING.md to select relevant sub-skills. Do not implement trading/ML logic without applying quant, backtesting and leakage safeguards.
```

## Version control

Commit the skills folder to the Stock Analyzer repository. This is recommended because the skills encode architectural and research decisions that should evolve with the codebase.
