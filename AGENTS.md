# Jarvis Repository Instructions

## Purpose

Use these rules for implementation work in this repository. Keep responses,
tool output, file reads, and validation proportional to the current change.
Do not duplicate detailed architecture or implementation history here.

## Context Loading

- Read only the files needed for the current task.
- Use `docs/continuation-handoff.md` to resume work.
- Consult the relevant section of `docs/current-baseline.md` only when deeper
  architectural context is required.
- Do not reread entire large documentation files after every change.
- Search with `rg` first; if unavailable, use `grep` or `find`.
- Avoid printing whole large files or full successful test logs.

## Communication

- Keep progress updates concise and outcome-oriented.
- Do not repeat information already supplied during the same task.
- Summarize successful validation with counts; show detailed output only for
  failures that require diagnosis.
- Explain architecture or code in depth only when requested.
- Work one logical implementation step at a time.

## Change Control

- Make changes only when the user requests implementation.
- Before editing, identify the expected file scope. If a change is expected to
  touch more than two files, divide it into explicit implementation substeps.
- Complete and validate one substep at a time. Tell the user which files belong
  to the active substep before changing them.
- If an active substep unexpectedly expands beyond two files, pause further
  edits, revise the substep plan, and communicate the expanded scope.
- Do not stage, commit, push, deploy, authenticate, or run live provider calls
  unless the user explicitly requests that action.
- Preserve unrelated and pre-existing working-tree changes.
- Use `apply_patch` for repository file edits.
- Never place secrets, tokens, cookies, session state, or raw provider payloads
  in source files, logs, tests, documentation, or DuckDB.

## Validation Policy

Choose the smallest validation tier that provides meaningful confidence.

### Tier 1 — During implementation

- Run the directly affected test module only.
- Use non-verbose output unless investigating a failure.
- Run syntax or import checks only for changed modules.

### Tier 2 — Completed logical step

- Run the relevant subsystem suite.
- Run `git diff --check`.
- Run compilation, type, lint, or dependency checks only when the change can
  affect them.

### Tier 3 — Before staging or committing

- Run the complete Python regression suite once.
- Run the complete frontend suite only if frontend or shared API contracts
  changed.
- Run `pip check` only when dependencies changed or before a release boundary.

### Live validation

- Broker, LLM, speech, Tijori, network, and credential-backed tests are opt-in.
- Keep live smoke tests separate from deterministic regression tests.
- Never repeat a successful full or live validation unless relevant code has
  changed afterward.

## Architecture Invariants

- Keep broker, LLM, database, speech, and fundamental-data providers behind
  provider-neutral protocols and adapters.
- Keep deterministic calculations independent of LLMs.
- Preserve point-in-time and look-ahead-safe analysis.
- Preserve source identity, evidence lineage, fingerprints, and explicit
  missing/conflicting states.
- Never allow an LLM to invent market data, financial facts, citations, or
  evidence identifiers.
- Provider-standardized Tijori data is secondary, research-grade evidence; it
  cannot become primary or decision-grade evidence by itself.
- Keep tenant and provider-connection data isolated.
- Do not add order placement or portfolio mutation through research gateways.

## Documentation

- Update documentation after a completed milestone, not after every small edit.
- Record current architecture in `docs/current-baseline.md`.
- Record resumable status, validation evidence, and the next step in
  `docs/continuation-handoff.md`.
- Do not copy large code listings, test logs, or chat history into Markdown.
- Keep historical validation counts distinct from the current baseline.

## Handoff Format

At the end of an implementation step, report only:

- what changed;
- important safety or architecture decisions;
- targeted and full validation actually executed;
- whether changes are staged or unstaged; and
- the single recommended next step.
