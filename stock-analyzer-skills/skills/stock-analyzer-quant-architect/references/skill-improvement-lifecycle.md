# Skill Improvement Lifecycle

Use this lifecycle when project work reveals that a master or domain skill may
need to change. It governs engineering/research skills and future Jarvis
runtime skills; it does not permit an agent to train itself or rewrite active
instructions from conversation history.

Candidate records must conform to
[`skill-change-candidate.schema.json`](skill-change-candidate.schema.json).

## Separation of states

Keep these artifacts distinct:

1. **Observation** — a sanitized, reproducible gap or useful behaviour found
   during implementation, validation, historical replay or production review.
2. **Candidate** — a proposed change based on one active skill version and
   content hash. It has no production authority.
3. **Evaluation** — evidence from deterministic tests and, where applicable,
   independent forward or walk-forward scenarios.
4. **Approval** — an attributable human decision to accept or reject the exact
   evaluated candidate hash.
5. **Promotion** — an atomic update that gives the approved skill a new version
   and retains the prior version for rollback.
6. **Monitoring and rollback** — post-promotion review against declared failure
   triggers. Rollback is an audited release action, not deletion of history.

Allowed candidate states are:

```text
DRAFT -> READY_FOR_EVALUATION -> EVALUATING
      -> APPROVED -> PROMOTED -> ROLLED_BACK
      -> REJECTED
```

`APPROVED` requires completed evaluation and human approval. `PROMOTED`
requires the active base version and hash still to match the candidate. A stale
candidate must be rebased and evaluated again.

## What may create an observation

Useful sources include:

- a reproducible test or validation failure;
- an evidence-citation, abstention or schema violation;
- repeated user correction with a clearly identified decision error;
- a point-in-time-safe backtest or walk-forward result;
- data-quality, provider-boundary or operational incidents;
- independent review showing ambiguous, redundant or obsolete guidance.

Do not learn directly from one profitable trade, one symbol, one market regime,
one persuasive answer or an outcome that was unavailable at decision time.
Realized outcomes may be evaluation evidence only after the declared horizon
has completed, and only with the original decision, inputs and skill version
frozen.

## Privacy, licensing and safety

Candidate records contain references and hashes, not secret or bulky evidence.
Never store:

- credentials, tokens, cookies or browser/session state;
- raw licensed-provider payloads or copied paid research;
- private conversation transcripts or personal financial information;
- mutable links presented as reproducible evidence;
- hindsight labels mixed into the original decision context.

Use sanitized fixture IDs, test-run IDs, operation IDs, dataset versions,
commits and content fingerprints. Live-provider validation remains explicitly
authorized and separate from deterministic testing.

No candidate may expand permission to authenticate, call a provider, deploy,
stage, commit, push, place orders or mutate portfolios. Such actions still
require the user's explicit authorization at execution time.

## Candidate design rules

- Target one owning skill whenever possible.
- State the smallest decision-changing edit and its non-goals.
- Pin the base and candidate semantic versions and SHA-256 content hashes.
- Describe leakage, provider, deterministic-logic and permission risks.
- Define success and failure before evaluation.
- Preserve evidence hierarchy: LLM guidance cannot override deterministic
  calculations, citations or release gates.
- Treat wording-only improvement separately from changes that alter financial
  interpretation, strategy eligibility, risk or execution semantics.

## Domain evaluation matrix

| Domain | Minimum meaningful evidence |
|---|---|
| Python platform | Targeted tests, failure paths, types/import boundaries, regression check proportional to impact |
| Market data | Session/timezone fixtures, missing/duplicate/incomplete candle cases, lineage and provider-failure behaviour |
| Technical analysis | Formula fixtures, warm-up boundaries, completed-period timing, parity where applicable, no future data |
| Price action | Synthetic lifecycle scenarios, pivot availability, BOS/CHOCH/FVG/zone/sweep edge cases, no repainting |
| Quant research | Predeclared hypothesis, baseline, sample size, temporal split, regime/symbol stability, sensitivity |
| Strategy | Independent family test, incremental contribution, explicit state transitions, `BUY`/`NO_TRADE` effects |
| Backtesting | Walk-forward or equivalent temporal validation, realistic costs/fills, look-ahead audit, robustness |
| Risk and execution | Gap/slippage/cost stress, structural stop validity, available 2R/3R space, exposure controls |
| Quant ML | Leakage-safe temporal splits, calibration, interpretable baseline, economic value out of sample |
| Local MLOps | Reproducible environment/data/features, registry gate, champion comparison, rollback and drift plan |
| Enterprise architecture | Port/adapter boundaries, tenant isolation, secret safety, failure modes, compatibility and observability |

For cross-domain changes, satisfy every materially affected row. Correlated
indicator families must not be used as multiple independent confirmations of a
candidate's value.

## Promotion gate

Promotion is allowed only when all of the following are true:

- the candidate schema validates;
- target skill name, path, base version and base hash match the active bundle;
- declared deterministic tests pass;
- required forward/walk-forward evaluation is complete and reproducible;
- limitations and inconclusive results are retained;
- the exact candidate hash has explicit human approval;
- the manifest and target skill receive consistent versions;
- a rollback target and trigger are recorded;
- the promoted artifact is committed through the normal repository workflow.

Failed or inconclusive evaluation does not justify prompt tuning until a test
passes. Record the result and reject or revise the candidate without weakening
the original gate.

## Post-promotion monitoring

Monitor only metrics related to the proposed improvement. Separate:

- instruction-following and response quality;
- evidence/citation/schema correctness;
- deterministic decision changes;
- backtest or forward performance;
- provider reliability and latency.

If a rollback trigger fires, stop further automatic promotion, restore the
declared prior version through an approved repository change, and preserve the
failed version and evidence for audit.
