# Controlled Learning and Fundamental Intelligence Execution Plan

Status: approved for incremental implementation planning

Created: 2026-09-12

Scope: Jarvis procedural learning, deterministic fundamental analysis, and
bounded fundamental evidence for Bull, Bear, and Senior Judge

## 1. Objective

Deliver two connected capabilities without weakening Jarvis's existing
evidence, provider, security, or long-entry safeguards:

1. A Hermes-inspired procedural-learning lifecycle that can identify useful
   improvements from operations and research, but cannot silently rewrite or
   promote an active skill.
2. A deterministic fundamental-intelligence pipeline that converts the atomic
   16-dataset company package into versioned, cited checks and applicable
   investment-framework assessments before an LLM explains them.

The end state is:

```text
Atomic 16-dataset package
         |
         v
Deterministic metric normalization
         |
         v
Mandatory checks
  - Financial health
  - Earnings quality
  - Forensic risks
         |
         v
Deterministic applicability assessment
         |
         v
Applicable framework skills (maximum three primary lenses by policy)
  - Buffett
  - Lynch
  - Jhunjhunwala-style
  - 100-Bagger
  - CAN SLIM
  - Valuation
         |
         v
Versioned scores + findings + exact citations
         |
         +------------------------------+
         |                              |
         v                              v
Fundamental Analyst synthesis    Bounded decision evidence
         |                              |
         +---------------+--------------+
                         v
                Bull + Bear + Senior Judge
```

The learning system surrounds, but does not control, this pipeline:

```text
Frozen operation + agent outcomes + released evidence
                         |
                         v
               Sanitized observation
                         |
                         v
                  Draft candidate
                         |
                         v
      Deterministic and forward-safe evaluation
                         |
                         v
        Exact-hash attributable human approval
                         |
                         v
       Versioned release through normal Git workflow
                         |
                         v
             Monitoring and rollback
```

## 2. Governing decisions

### 2.1 Deterministic logic owns financial conclusions

- LLMs may explain released metrics, checks, scores, conflicts, and missing
  evidence.
- LLMs must not calculate financial metrics, change weights, select the most
  favourable framework, invent a citation, or override a release gate.
- All material outputs carry a policy version, calculation version, evidence
  fingerprint, reporting basis, periods, units, availability time, and exact
  document citations.
- Re-running the same version against the same package fingerprint must produce
  byte-equivalent deterministic results after canonical serialization.

### 2.2 Frameworks are lenses, not impersonations

The Buffett, Lynch, Jhunjhunwala-style, 100-Bagger, CAN SLIM, and Valuation
skills are documented investment frameworks. They must not claim to reproduce
the private judgment, voice, endorsement, or unpublished method of any person.

### 2.3 Mandatory checks are not optional lenses

Financial health, earnings quality, and forensic risk always run when the
minimum required evidence exists. A framework cannot hide, dilute, or offset a
mandatory failure. `NA` means unavailable or inapplicable; it never means
`PASS`.

### 2.4 Fundamentals are default, refresh remains explicit

- Every normal single-company swing analysis requests fundamentals.
- A fresh committed package is reused without a Tijori call.
- A missing or expired package may trigger provider retrieval.
- An explicit user refresh retrieves the complete company package.
- Partial refresh results never replace the active committed package.
- Consolidated and standalone statements remain separate.
- Provider authentication, subscription, session ownership, and tenant scope
  remain user-owned and isolated.

### 2.5 Current fundamental data has an evidence boundary

Tijori-standardized data remains secondary research evidence under the current
source policy. It can support present-day screening and cited risk context, but
must not independently become decision-grade primary evidence.

Until point-in-time statement vintages and publication timestamps are retained:

- never apply today's financial table to a historical trade date;
- never claim a historical fundamental backtest is point-in-time safe;
- introduce new fundamental scores in shadow/advisory mode first;
- require a separately approved source-and-evaluation policy before a
  fundamental score becomes a hard `BUY`/`NO_TRADE` gate; and
- treat severe forensic findings as `REVIEW_REQUIRED`, not an allegation of
  fraud or misconduct.

Annual reports, filings, investor presentations, earnings-call transcripts,
qualitative management honesty, and durable-moat conclusions remain outside
this milestone.

### 2.6 Learning is governed release engineering

- Operations may create sanitized observations, never active instructions.
- A candidate has no runtime authority.
- Infrastructure failures such as expired API keys, rate limits, network
  failures, or provider downtime are operational incidents, not evidence that
  an investment skill should change.
- One user correction, one symbol, one trade, or one profitable outcome cannot
  promote a skill.
- Decision-changing candidates require temporal, multi-symbol, multi-regime
  evaluation where the required data exists.
- Promotion requires the exact evaluated candidate hash, attributable human
  approval, a new version, an updated manifest, and a rollback target.
- Production runtime never edits repository skill files or executes Git.

## 3. Current baseline

### 3.1 Implemented foundations

- Atomic company-wide fundamental package with 16 required slots.
- Complete structured JSON caching with tenant, company, source, fingerprint,
  retrieval, and expiry metadata.
- Atomic publication that preserves the previous active package on failure.
- Provider-neutral Tijori gateway and DuckDB-backed cache adapters.
- Static runtime skills for Bull, Bear, Senior Judge, and Fundamental Analyst.
- Exact-hash, approved, fail-closed runtime-skill manifest and loader.
- Generic cited Fundamental Analyst synthesis.
- Immutable per-agent execution outcome ledger and operation audit.
- Documented skill-improvement lifecycle, candidate JSON schema, and read-only
  deterministic candidate validator.

### 3.2 Missing capabilities

- Canonical normalized fundamental metric model.
- Deterministic metric calculation and label mapping.
- Mandatory health, quality, and forensic rules.
- Framework applicability and selection engine.
- Framework-specific deterministic scores.
- Framework subskill bundle and verified loader.
- Bounded fundamental decision evidence contract.
- Default fundamentals routing.
- Fundamental evidence in Bull, Bear, and Judge contexts.
- Durable learning observations and candidates.
- Automated but allow-listed candidate evaluation.
- Human approval and release-plan services.
- Post-release monitoring and audited rollback workflow.
- Point-in-time historical fundamental vintages.

## 4. Atomic 16-dataset input contract

The existing completeness manifest is the only eligible input package. It must
contain exactly these slots from one issuer, tenant, provider connection,
retrieval snapshot, and compatible expiry boundary:

| Dataset | Reporting basis | Count |
|---|---|---:|
| Company overview | Not applicable | 1 |
| Financial statements summary | Not applicable | 1 |
| Shareholding history | Not applicable | 1 |
| Growth table | Not applicable | 1 |
| Balance sheet | Consolidated and standalone | 2 |
| Profit and loss | Consolidated and standalone | 2 |
| Cash flow | Consolidated and standalone | 2 |
| Ratios | Consolidated and standalone | 2 |
| Quarterly results | Consolidated and standalone | 2 |
| Peer comparison | Not applicable | 1 |
| Benchmarking financials | Not applicable | 1 |
| **Total** | | **16** |

No normalization or scoring may start from uncommitted staged documents. The
input record must include package ID, manifest fingerprint, content
fingerprints, retrieval time, expiry, issuer identity, and source locations.

## 5. Normalized financial evidence model

### 5.1 Canonical metric record

Every normalized observation will contain at least:

- canonical metric ID;
- provider label and full hierarchy path;
- numeric value or explicit missing state;
- original display value;
- unit and scale;
- currency when applicable;
- annual, quarterly, TTM, or point observation type;
- period start/end and provider period label;
- reporting basis;
- issuer and package identity;
- document reference and document fingerprint;
- retrieval timestamp and, when available, source availability timestamp;
- normalization rule ID and version;
- quality flags and conflicts.

Zero, blank, dash, not-applicable, and parse failure are distinct states.
Values from consolidated and standalone statements are never summed, averaged,
or silently substituted for each other.

### 5.2 Label mapping policy

- Use a versioned allow-list of exact labels and reviewed aliases.
- Preserve provider labels even when a canonical alias is recognized.
- Reject ambiguous mappings instead of choosing by fuzzy similarity.
- Scope aliases by statement type and hierarchy where necessary.
- Detect duplicates for the same canonical metric, basis, and period.
- Emit an explicit conflict rather than selecting a value silently.
- Unit conversion must be deterministic and reversible to the original value.

### 5.3 Derived metrics

Initial deterministic derived families should include, only when inputs exist:

- revenue, EBITDA, EBIT, PAT, EPS, and margin growth/direction;
- annual CAGR and quarterly YoY comparisons;
- margin level, change, and multi-period stability;
- debt, cash, net debt, working capital, and liquidity direction;
- ROE, ROCE, interest coverage, and leverage;
- operating cash flow to PAT and EBITDA;
- free cash flow and free-cash-flow conversion;
- receivable, inventory, and payable intensity and direction;
- capex intensity and reinvestment proxies;
- share-count/dilution and promoter/institutional ownership changes when
  explicitly available;
- peer-relative percentile or robust rank for comparable, same-unit metrics;
- valuation ratios relative to peer distributions when comparable.

Every formula declares its required inputs, minimum periods, denominator-zero
behaviour, missing-value behaviour, and version. No interpolation or forecast
is permitted in the initial release.

## 6. Mandatory assessment policy

Every finding has `PASS`, `WARN`, `FAIL`, or `NA`, plus severity, observed
value, threshold/formula version, supporting citations, limitations, and a
stable finding ID.

### 6.1 Financial health

Initial families:

- liquidity and short-term obligations;
- gross debt, net debt, and leverage direction;
- interest coverage;
- debt growth relative to earnings and operating cash flow;
- ROE/ROCE level and persistence;
- working-capital consumption;
- balance-sheet expansion relative to revenue/profit growth; and
- consolidated-versus-standalone divergence.

BFSI and other sector-specific balance sheets must not be evaluated with
industrial-company leverage thresholds. Until an approved sector policy
exists, affected rules return `NA` with a limitation.

### 6.2 Earnings quality

Initial families:

- PAT versus operating cash flow over multiple periods;
- cumulative cash conversion, not only the latest year;
- free-cash-flow consistency;
- EBITDA, PAT, and operating-cash-flow divergence;
- receivables/inventory growth relative to revenue;
- margin improvement without corresponding cash conversion;
- annual-versus-quarterly inconsistency; and
- dilution or exceptional dependence when explicitly represented.

### 6.3 Forensic risk

Initial deterministic screens:

- positive reported profit with persistently negative operating cash flow;
- receivables or debtor days materially outgrowing revenue;
- leverage growth combined with weak interest coverage;
- promoter pledge above the approved threshold;
- repeated promoter-holding erosion;
- unexplained consolidated/standalone divergence detectable from the supplied
  records; and
- internally inconsistent totals or duplicate/conflicting provider values.

These are screening flags, not findings of wrongdoing. Auditor qualifications,
related-party transactions, contingent liabilities, governance conduct, and
management representations remain `UNAVAILABLE` without primary documents.

## 7. Framework applicability and scoring

### 7.1 Applicability output

Every framework receives:

- `ELIGIBLE`, `PARTIAL`, `INELIGIBLE`, or `UNAVAILABLE`;
- evidence coverage percentage;
- required and missing metric families;
- reasons for the status;
- any sector or lifecycle limitation;
- applicability-policy version; and
- deterministic priority.

All frameworks may be assessed, but no more than three primary explanatory
lenses are selected by the initial policy. Selection is deterministic and
recorded; the LLM cannot substitute a more favourable lens.

### 7.2 Initial framework requirements

| Framework | Minimum quantitative evidence | Important limitation |
|---|---|---|
| Buffett | Multi-year profitability, ROE/ROCE, leverage, cash conversion, reinvestment | Durable moat, pricing power, and management integrity cannot be concluded yet |
| Lynch | EPS/revenue growth, earnings consistency, balance sheet, valuation ratios | Narrative growth classification requires explicit deterministic rules |
| Jhunjhunwala-style | India-focused scalable growth, operating leverage, capital efficiency, balance-sheet risk, valuation | A documented public-principles lens, not impersonation or endorsement |
| 100-Bagger | Long-period compounding, reinvestment, ROCE, growth persistence, dilution, leverage | Runway and moat remain preliminary without primary qualitative evidence |
| CAN SLIM | Current quarterly growth, annual growth, institutional sponsorship, relative leadership, market regime | The `N` catalyst is unavailable without approved news/filing evidence |
| Valuation | Comparable P/E, P/B, EV/EBITDA or other applicable ratios and peer distribution | No reverse DCF; intrinsic valuation requires explicit assumptions and inputs |

### 7.3 Double-counting protection

- Metrics are assigned to evidence families.
- A family receives a capped contribution even when several correlated ratios
  support it.
- Mandatory checks cannot be counted again as independent framework evidence.
- Peer ranks use robust comparison and minimum-peer requirements.
- Missing evidence reduces coverage; it never produces a neutral midpoint that
  can inflate a score.
- A framework score without minimum coverage is withheld.

### 7.4 Versioned score result

The deterministic result will contain:

- mandatory-check summary;
- per-framework applicability;
- selected primary frameworks;
- per-framework score and coverage, when eligible;
- positive, negative, conflicting, and unavailable findings;
- exact metric and document citations;
- score-policy and threshold versions;
- package and manifest fingerprints;
- preliminary fundamental stance: `HEALTHY`, `CAUTION`, `WEAK`, or
  `INSUFFICIENT_EVIDENCE`;
- a separate `REVIEW_REQUIRED` flag for critical screening findings; and
- explicit decision influence mode: `SHADOW`, `ADVISORY`, or `APPROVED_GATE`.

The result is not a `BUY` or `SELL` recommendation.

## 8. Fundamental Analyst and debate integration

### 8.1 Fundamental Analyst

The agent receives the deterministic result plus only the referenced bounded
supporting evidence required for explanation. It must:

- preserve every deterministic score and status unchanged;
- explain selected framework applicability;
- distinguish fact, calculation, limitation, and interpretation;
- cite the exact released references;
- avoid qualitative moat or management claims outside the evidence boundary;
- abstain when the deterministic package is unavailable or invalid; and
- return a versioned schema linked to the deterministic assessment fingerprint.

### 8.2 Bounded debate evidence

Bull, Bear, and Senior Judge must not receive the raw 16-document JSON package.
They receive one immutable `FundamentalDecisionEvidence` projection containing:

- package and assessment fingerprints;
- freshness and coverage;
- preliminary stance;
- mandatory failures/warnings;
- selected framework scores and limitations;
- exact citation IDs; and
- the Fundamental Analyst synthesis reference, if successfully produced.

The deterministic projection remains authoritative. The Analyst narrative is
explanatory and cannot override it.

### 8.3 Decision interaction

- A healthy company does not turn a technically invalid setup into `BUY`.
- A bullish technical trend does not conceal material financial risk.
- During `SHADOW`, fundamentals are displayed but do not alter the trade gate.
- During `ADVISORY`, fundamentals can reduce confidence or require review, but
  do not independently release or veto a trade.
- `APPROVED_GATE` requires separate evaluation and human approval. Only then
  may declared critical conditions force `NO_TRADE`.
- Missing fundamental evidence is an operational/research limitation, not
  bearish evidence.
- If a required combined result cannot be produced, Jarvis returns
  `NO_CONCLUSION`; it must not mislabel a system failure as `NO_TRADE`.

### 8.4 Execution dependencies

After verified instrument resolution:

- market, daily, weekly, and fundamental work may execute concurrently;
- the Fundamental Analyst waits for the committed package and deterministic
  assessment;
- Bull and Bear wait for released technical evidence and bounded fundamental
  evidence;
- Bull and Bear may execute concurrently;
- Senior Judge waits for both Bull and Bear plus the same immutable bounded
  fundamental evidence;
- Jarvis briefing waits for the Judge when a conclusion exists;
- every agent records an independent success, failure, or skipped-dependency
  outcome in the operation ledger.

## 9. Controlled procedural-learning architecture

### 9.1 Observation record

A durable observation contains:

- observation ID, type, status, creation time, and actor;
- operation, agent, active skill, model, package, and evidence fingerprints;
- sanitized summary and reproducible references;
- classification as policy, deterministic logic, data quality, provider,
  orchestration, presentation, or operational failure;
- point-in-time and outcome-horizon metadata when market results are involved;
- suggested owning skill/domain; and
- explicit confirmation that no secret, session state, raw licensed payload,
  or private transcript is stored.

Eligible sources include reproducible validation failures, citation/schema
violations, repeated attributable corrections, data-quality incidents, and
completed point-in-time-safe evaluations. Provider authentication failures are
recorded operationally but cannot propose an investment-policy change.

### 9.2 Candidate record

The existing candidate schema remains authoritative. Candidate creation must:

- reference one active base skill version and content hash;
- propose the smallest decision-changing edit;
- declare non-goals and permission impact;
- define success and failure before evaluation;
- pin a candidate version and hash;
- remain `DRAFT` until independently validated; and
- never alter the active manifest.

An LLM may draft candidate wording from sanitized observations, but the result
must pass the deterministic schema and policy validator and has no release
authority.

### 9.3 Evaluation

Evaluation commands are selected from a repository-owned allow-list. Candidate
JSON cannot provide executable shell commands.

Evaluation tiers:

1. Contract and schema fixtures.
2. Prior regression cases, including failure and abstention paths.
3. Shadow comparison between the active and candidate skill.
4. Multi-symbol and multi-regime evaluation for decision-changing research.
5. Walk-forward or forward evaluation when market outcomes are involved.
6. Operational checks for latency, token use, failure rate, and evidence
   completeness.

Outcome evaluation freezes the original decision, evidence, timestamps, skill
version, and declared horizon. It must not rewrite the original context with
future data.

### 9.4 Approval and release

- Approval is attributable and bound to the exact candidate SHA-256.
- Editing an approved candidate invalidates approval.
- Promotion verifies the active base version/hash has not changed.
- Runtime services do not edit source or call Git.
- Promotion produces a verified release plan for the normal branch, review,
  test, commit, PR, merge, and deployment process.
- Runtime skill loading remains all-or-nothing and fail-closed.
- Hot promotion inside an active operation is prohibited.

### 9.5 Monitoring and rollback

Each promoted skill declares monitored metrics and failure triggers. Monitoring
separates:

- schema/citation compliance;
- abstention correctness;
- deterministic decision changes;
- response quality;
- provider reliability;
- token and latency impact; and
- point-in-time-safe market outcomes.

Rollback restores a declared prior release through the same approved repository
workflow. The failed version, evidence, and audit history remain preserved.

### 9.6 Procedural memory retrieval

Only approved, promoted skill content has runtime authority. Sanitized
observations and draft candidates are searchable for engineering review but
are not injected into agent prompts. Relevant historical operation summaries
may be retrieved for diagnosis, never as uncited financial evidence.

## 10. Failure matrix

| Failure | Preserve | Runtime behaviour | Learning treatment |
|---|---|---|---|
| Fresh cache hit | Active package | No provider call | No observation |
| Partial provider refresh | Previous active package | Refresh fails atomically | Operational/data observation if reproducible |
| Package fingerprint conflict | Previous active package | Fail closed | Data-integrity observation |
| Normalization ambiguity | Raw committed package | Metric marked conflict/unavailable | Mapping candidate only after reviewed examples |
| Missing metric | Other valid metrics | Rule/lens becomes `NA` or partial | Coverage observation, never fabricated value |
| Fundamental LLM failure | Deterministic assessment | Show deterministic research; Analyst marked failed | Operational/model observation, not score change |
| Bull or Bear LLM failure | All completed research | Failed agent and dependent skip recorded | Operational/model observation |
| Judge failure | Research and debate artifacts | No trade conclusion | Operational/model observation |
| Provider credential failure | Existing unexpired cache | Reuse cache if eligible; otherwise no fundamental conclusion | Operational incident only |
| One poor realised trade | Frozen operation | No skill change | Insufficient learning evidence |
| Approved candidate hash mismatch | Active release | Promotion rejected | Security/audit incident |
| Post-release trigger breached | Prior and current releases | Stop promotion and prepare rollback | Audited monitoring evidence |

## 11. Execution plan

Every implementation substep below is intentionally limited to no more than
two files. If a substep expands, stop and split it before editing.

### Phase F0 — Freeze contracts and baseline

#### F0.1 Confirm the package boundary

Files:

- `app/models/fundamental_package.py`
- `tests/unit/test_fundamental_package.py`

Action: verify exact 16-slot completeness, one-snapshot identity, basis rules,
expiry, and fingerprints. Add tests only if a stated invariant is not already
covered.

Exit gate: incomplete, mixed-issuer, mixed-scope, mixed-snapshot, duplicate,
or expired input cannot reach normalization.

#### F0.2 Introduce normalized evidence contracts

Files:

- `app/models/fundamental_metrics.py`
- `tests/unit/test_fundamental_metrics.py`

Action: add immutable canonical metric, explicit missing/conflict states,
lineage, unit, period, basis, formula, and package-binding contracts.

Exit gate: models reject naive datetimes, unknown units, missing citations,
basis mixing, invalid periods, duplicate canonical keys, and non-finite values.

### Phase F1 — Deterministic normalization

#### F1.1 Implement the label and value normalizer

Files:

- `app/analytics/fundamental_metrics.py`
- `tests/unit/test_fundamental_metrics.py`

Action: map reviewed aliases, parse Indian numeric formatting, preserve original
values, distinguish missing states, and reject ambiguity.

Exit gate: repeated normalization is deterministic and every value is
reversible to its source reference.

#### F1.2 Add derived metrics

Files:

- `app/analytics/fundamental_metrics.py`
- `tests/unit/test_fundamental_metrics.py`

Action: implement versioned growth, margin, leverage, cash-conversion,
working-capital, ownership, peer-rank, and valuation calculations.

Exit gate: formula fixtures cover zero denominators, negative values, missing
periods, restated labels, short histories, and consolidated/standalone
divergence.

### Phase F2 — Mandatory checks

#### F2.1 Add assessment contracts

Files:

- `app/models/fundamental_assessment.py`
- `tests/unit/test_fundamental_assessment.py`

Action: define findings, severity, status, citations, policy versions,
coverage, stance, and review-required state.

#### F2.2 Implement financial-health checks

Files:

- `app/analytics/fundamental_checks.py`
- `tests/unit/test_fundamental_checks.py`

Action: add liquidity, leverage, coverage, capital-efficiency, working-capital,
and reporting-basis conflict rules with sector applicability.

#### F2.3 Implement earnings-quality checks

Files:

- `app/analytics/fundamental_checks.py`
- `tests/unit/test_fundamental_checks.py`

Action: add cumulative cash conversion, FCF consistency, accrual proxies,
margin/cash divergence, and working-capital quality.

#### F2.4 Implement forensic screens

Files:

- `app/analytics/fundamental_checks.py`
- `tests/unit/test_fundamental_checks.py`

Action: add deterministic red-flag screens while explicitly withholding
fraud, governance, auditor, and related-party conclusions.

Exit gate for F2: every result is reproducible, cited, unit-safe, sector-safe,
and returns `NA` rather than guessing.

### Phase F3 — Framework applicability

#### F3.1 Add applicability contracts

Files:

- `app/models/fundamental_frameworks.py`
- `tests/unit/test_fundamental_frameworks.py`

#### F3.2 Implement deterministic selection

Files:

- `app/analytics/fundamental_applicability.py`
- `tests/unit/test_fundamental_applicability.py`

Action: evaluate all six frameworks, calculate coverage, record exclusions,
rank deterministic priority, and select no more than three primary lenses.

Exit gate: selection is identical for identical evidence, insensitive to LLM
wording, and cannot select an ineligible framework.

### Phase F4 — Versioned scorecards

#### F4.1 Add scoring-policy contracts

Files:

- `app/models/fundamental_scoring.py`
- `tests/unit/test_fundamental_scoring.py`

#### F4.2 Implement framework scoring

Files:

- `app/analytics/fundamental_scorecard.py`
- `tests/unit/test_fundamental_scorecard.py`

Action: calculate capped evidence-family contributions, coverage penalties,
mandatory-check interaction, framework scores, stance, and decision influence
mode.

Exit gate: golden fixtures prove no double counting, no missing-data inflation,
no mandatory-failure offset, and stable canonical fingerprints.

### Phase F5 — Verified framework skill bundle

#### F5.1 Create Buffett and Lynch framework skills

Status: completed. Both version `1.0.0` skills are explanatory and shadow-only.
They preserve released deterministic scores, mandatory constraints, coverage,
limitations and exact citations without calculating evidence or making trade
decisions. Runtime contracts, loading and manifest registration remain scoped
to F5.4-F5.6.

Files:

- `runtime-skills/fundamental-frameworks/buffett/SKILL.md`
- `runtime-skills/fundamental-frameworks/lynch/SKILL.md`

#### F5.2 Create Jhunjhunwala-style and 100-Bagger skills

Files:

- `runtime-skills/fundamental-frameworks/jhunjhunwala-style/SKILL.md`
- `runtime-skills/fundamental-frameworks/one-hundred-bagger/SKILL.md`

#### F5.3 Create CAN SLIM and Valuation skills

Files:

- `runtime-skills/fundamental-frameworks/can-slim/SKILL.md`
- `runtime-skills/fundamental-frameworks/valuation/SKILL.md`

#### F5.4 Add framework-skill contracts

Files:

- `app/models/fundamental_framework_skill.py`
- `tests/unit/test_fundamental_framework_skill.py`

#### F5.5 Add the fail-closed framework loader

Files:

- `app/fundamental_framework_skill_loader.py`
- `tests/unit/test_fundamental_framework_skill_loader.py`

#### F5.6 Register and verify the bundle

Files:

- `runtime-skills/fundamental-frameworks/manifest.yaml`
- `tests/unit/test_fundamental_framework_skill_loader.py`

Exit gate: exact hashes, approved versions, expected framework set, path
ownership, duplicate rejection, and all-or-nothing loading pass. Framework
skills can explain released scores but cannot calculate or modify them.

### Phase F6 — Fundamental Analyst synthesis

#### F6.1 Extend the analysis result contract

Files:

- `app/models/fundamental_analysis.py`
- `tests/unit/test_fundamental_analyst_agent.py`

#### F6.2 Bind deterministic results and selected skills

Files:

- `app/agents/fundamental_analyst_agent.py`
- `tests/unit/test_fundamental_analyst_agent.py`

#### F6.3 Wire composition

Files:

- `app/composition/fundamentals.py`
- `tests/unit/test_fundamental_composition.py`

Exit gate: the agent cannot change scores/statuses, cite outside the released
assessment, load an unapproved framework, or claim unavailable qualitative
facts.

### Phase F7 — Default cache-first fundamentals

#### F7.1 Change default intent policy

Files:

- `app/intents/fundamentals.py`
- `tests/unit/test_fundamental_refresh_intent.py`

Action: normal equity analysis requests fundamentals by default; explicit
language controls full refresh, not participation.

#### F7.2 Preserve request semantics through conversation

Files:

- `app/conversation/browser.py`
- `tests/unit/test_browser_conversation.py`

#### F7.3 Enforce cache/provider behaviour

Files:

- `app/workflow/browser_runner.py`
- `tests/unit/test_browser_operation_runner.py`

Exit gate: fresh cache makes zero provider calls; miss/expiry may retrieve;
explicit refresh replaces all 16 slots atomically; technical research still
retains its independent outcome.

### Phase F8 — Bounded evidence for debate

#### F8.1 Add the decision-evidence contract

Files:

- `app/models/fundamental_decision_evidence.py`
- `tests/unit/test_fundamental_decision_evidence.py`

#### F8.2 Project the deterministic assessment

Files:

- `app/services/fundamental_decision_evidence.py`
- `tests/unit/test_fundamental_decision_evidence.py`

#### F8.3 Extend runtime evidence boundaries

Files:

- `app/models/runtime_skill.py`
- `tests/unit/test_runtime_skill.py`

#### F8.4 Update Bull skill and manifest atomically

Files:

- `runtime-skills/jarvis-bull-runtime/SKILL.md`
- `runtime-skills/manifest.yaml`

#### F8.5 Integrate Bull

Files:

- `app/agents/bull_agent.py`
- `tests/unit/test_bull_agent.py`

#### F8.6 Update Bear skill and manifest atomically

Files:

- `runtime-skills/jarvis-bear-runtime/SKILL.md`
- `runtime-skills/manifest.yaml`

#### F8.7 Integrate Bear

Files:

- `app/agents/bear_agent.py`
- `tests/unit/test_bear_agent.py`

#### F8.8 Update Judge skill and manifest atomically

Files:

- `runtime-skills/jarvis-judge-runtime/SKILL.md`
- `runtime-skills/manifest.yaml`

#### F8.9 Integrate Senior Judge

Files:

- `app/agents/debate_judge_agent.py`
- `tests/unit/test_debate_judge_agent.py`

#### F8.10 Enforce dependency orchestration

Files:

- `app/workflow/browser_runner.py`
- `tests/unit/test_browser_operation_runner.py`

Exit gate: Bull, Bear, and Judge receive the same assessment fingerprint;
Bull/Bear run concurrently after dependencies; Judge waits for both; no raw
fundamental JSON enters debate prompts; failures produce exact ledger states.

### Phase F9 — API and UI traceability

#### F9.1 Expose the bounded result contract

Files:

- `app/models/browser_operations.py`
- `tests/unit/test_browser_operations.py`

#### F9.2 Project through the API

Files:

- `app/api/http.py`
- `tests/unit/test_http_api.py`

#### F9.3 Add the frontend parser

Files:

- `frontend/lib/fundamental-decision-evidence.ts`
- `frontend/tests/fundamental-decision-evidence.test.mjs`

#### F9.4 Render compact traceability

Files:

- `frontend/components/FundamentalAnalystBriefing.tsx`
- `frontend/tests/rendered-html.test.mjs`

Exit gate: UI shows stance, coverage, selected frameworks, mandatory risks,
influence mode, freshness, and fingerprints without exposing licensed raw JSON.

### Phase L0 — Learning-domain contracts

#### L0.1 Add immutable observation models

Files:

- `app/models/skill_learning.py`
- `tests/unit/test_skill_learning.py`

#### L0.2 Add database-neutral observation repository

Files:

- `app/storage/skill_learning_repositories.py`
- `tests/unit/test_skill_learning_repositories.py`

#### L0.3 Implement in-memory observation storage

Files:

- `app/storage/adapters/skill_learning_in_memory.py`
- `tests/unit/test_skill_learning_in_memory.py`

#### L0.4 Implement DuckDB observation storage

Files:

- `app/storage/adapters/duckdb.py`
- `tests/unit/test_duckdb_storage.py`

Exit gate: idempotency, conflicts, tenant isolation, defensive copies,
sanitization flags, and immutable evidence references pass.

### Phase L1 — Observation collection

#### L1.1 Add deterministic collection policy

Files:

- `app/services/skill_observation_collector.py`
- `tests/unit/test_skill_observation_collector.py`

#### L1.2 Connect completed operation audits

Files:

- `app/workflow/browser_runner.py`
- `tests/unit/test_browser_operation_runner.py`

Exit gate: eligible failures create sanitized observations; credentials,
provider outages, raw payloads, and ordinary successful operations cannot
generate an investment-policy candidate.

### Phase L2 — Candidate persistence and drafting

#### L2.1 Add candidate domain contracts

Files:

- `app/models/skill_change.py`
- `tests/unit/test_skill_change.py`

#### L2.2 Add candidate repository port

Files:

- `app/storage/skill_change_repositories.py`
- `tests/unit/test_skill_change_repositories.py`

#### L2.3 Implement in-memory candidate storage

Files:

- `app/storage/adapters/skill_change_in_memory.py`
- `tests/unit/test_skill_change_in_memory.py`

#### L2.4 Implement DuckDB candidate storage

Files:

- `app/storage/adapters/duckdb.py`
- `tests/unit/test_duckdb_storage.py`

#### L2.5 Add draft-candidate builder

Files:

- `app/services/skill_candidate_builder.py`
- `tests/unit/test_skill_candidate_builder.py`

Exit gate: only reviewed observations can create a `DRAFT`; the existing JSON
schema and validator pass; no manifest or skill is mutated.

### Phase L3 — Safe evaluation

#### L3.1 Add evaluation contracts

Files:

- `app/models/skill_evaluation.py`
- `tests/unit/test_skill_evaluation.py`

#### L3.2 Add allow-listed evaluation registry

Files:

- `app/services/skill_evaluation_registry.py`
- `tests/unit/test_skill_evaluation_registry.py`

#### L3.3 Add evaluation coordinator

Files:

- `app/services/skill_evaluation_coordinator.py`
- `tests/unit/test_skill_evaluation_coordinator.py`

Exit gate: candidate-provided commands cannot execute; failures are preserved;
decision-changing candidates cannot pass without the required temporal
evidence; live validation remains explicitly authorized.

### Phase L4 — Outcome evaluation

#### L4.1 Add outcome-horizon contracts

Files:

- `app/models/research_outcome.py`
- `tests/unit/test_research_outcome.py`

#### L4.2 Add point-in-time-safe outcome attachment

Files:

- `app/services/research_outcome_evaluator.py`
- `tests/unit/test_research_outcome_evaluator.py`

Exit gate: outcomes attach only after the declared horizon; original evidence
and decision remain frozen; no current financial data enters historical
decisions.

### Phase L5 — Human approval and release preparation

#### L5.1 Add approval service

Files:

- `app/services/skill_candidate_approval.py`
- `tests/unit/test_skill_candidate_approval.py`

#### L5.2 Add release-plan verifier

Files:

- `app/services/skill_release_plan.py`
- `tests/unit/test_skill_release_plan.py`

Exit gate: approval binds the exact candidate hash; stale base hashes fail;
the service creates a release plan but never edits source, invokes Git, merges,
or deploys.

### Phase L6 — Monitoring and rollback

#### L6.1 Add monitoring contracts and policy

Files:

- `app/models/skill_monitoring.py`
- `tests/unit/test_skill_monitoring.py`

#### L6.2 Add rollback recommendation service

Files:

- `app/services/skill_rollback.py`
- `tests/unit/test_skill_rollback.py`

Exit gate: only predeclared triggers are evaluated; rollback is recommended and
audited but still requires the normal approved repository workflow.

### Phase L7 — Review API and UI

#### L7.1 Add authenticated review endpoints

Files:

- `app/api/http.py`
- `tests/unit/test_http_api.py`

#### L7.2 Add frontend review contracts

Files:

- `frontend/lib/skill-learning.ts`
- `frontend/tests/skill-learning.test.mjs`

#### L7.3 Add the maintainer review panel

Files:

- `frontend/components/SkillLearningReview.tsx`
- `frontend/tests/rendered-html.test.mjs`

Exit gate: tenant-scoped read/review actions work; no endpoint can promote,
modify source, expose secrets, or bypass exact-hash approval.

## 12. Validation strategy

### 12.1 Per-substep validation

- Run only the directly affected test module.
- Run changed-module import or compilation checks when relevant.
- Run `git diff --check` at the end of each logical phase.
- Do not run provider-backed tests unless explicitly authorized.

### 12.2 Fundamental fixtures

Use synthetic or sanitized fixtures covering:

- profitable cash-generative company;
- reported profit with weak operating cash flow;
- high-debt and falling-coverage company;
- loss-making growth company;
- cyclic and commodity company;
- BFSI company where generic debt rules are inapplicable;
- missing standalone statements;
- differing consolidated and standalone values;
- negative denominators and zero denominators;
- mixed units and Indian number formatting;
- restated labels and duplicate rows;
- short history and missing quarters;
- peer set too small for comparison; and
- explicit provider conflicts.

No raw paid-provider payload is committed to tests.

### 12.3 Learning-loop adversarial tests

- attempt to store secrets or session material;
- candidate path traversal;
- duplicate YAML keys and aliases;
- stale active-skill hash;
- approval hash mismatch;
- non-incrementing version;
- candidate-provided executable command;
- one-trade performance claim;
- incomplete outcome horizon;
- future data in original decision context;
- cross-tenant observation or candidate access;
- partial promotion; and
- rollback without an approved target.

### 12.4 Release validation

Before staging a completed milestone:

1. Run its subsystem suite.
2. Run complete Python regression once.
3. Run frontend tests/build only when frontend or shared contracts changed.
4. Run dependency checks only when dependencies changed or at release boundary.
5. Inspect generated/session artifacts and exclude them.
6. Inspect the staged diff before commit.
7. Keep live Tijori, broker, and LLM validation separate and opt-in.

## 13. Rollout gates

### Gate A — Deterministic shadow result

Normalization, mandatory checks, applicability, and scores are stored and shown
for inspection but do not affect the trade conclusion.

Required evidence: deterministic tests, cross-company fixtures, citations,
coverage behaviour, and no provider calls on a fresh cache.

### Gate B — Advisory debate context

Bull, Bear, and Judge receive bounded fundamental evidence. The Judge explains
financial risk separately, but technical release gates remain authoritative.

Required evidence: prompt-size bounds, same-fingerprint enforcement, citation
validation, dependency/failure tests, and live smoke test with explicit
authorization.

### Gate C — Approved decision influence

Only explicitly approved fundamental conditions may influence `NO_TRADE` or
confidence. `BUY` still requires all technical and reward-to-risk gates.

Required evidence: approved source policy, declared rules, point-in-time-safe
evaluation where predictive claims are made, multi-symbol/regime stability,
and exact-hash human approval.

### Gate D — Controlled skill learning

Observations and candidates become durable and reviewable. Promotion remains
through normal source-control workflow.

Required evidence: tenant isolation, sanitization, allow-listed evaluation,
approval binding, monitoring, and rollback rehearsal.

## 14. Definition of done

This programme is complete only when:

- every normal equity analysis includes cache-first fundamentals;
- a refresh activates only a complete valid 16-dataset package;
- normalized metrics are deterministic, versioned, basis-safe, and cited;
- mandatory checks cannot be bypassed by a framework score;
- framework applicability and maximum-three selection are deterministic;
- missing data cannot increase a score;
- Fundamental Analyst explains but cannot alter deterministic results;
- Bull, Bear, and Judge receive the same bounded assessment fingerprint;
- agent dependencies and individual outcomes are authoritative and auditable;
- system failures produce `NO_CONCLUSION`, not invented bearish evidence;
- observations and candidates are durable, tenant-safe, and sanitized;
- candidates cannot execute arbitrary code or modify active skills;
- promotion requires exact-hash human approval and normal repository workflow;
- rollback has been rehearsed against a prior approved version;
- historical performance claims use point-in-time-safe data; and
- documentation identifies remaining annual-report and primary-source limits.

## 15. Immediate next step

Begin with **F0.1 — Confirm the package boundary**. It changes at most
`app/models/fundamental_package.py` and
`tests/unit/test_fundamental_package.py`, and should add code only if the
existing 16-slot model lacks one of the stated invariants.

After F0.1 passes, implement **F0.2 — Introduce normalized evidence
contracts**. No scoring, LLM prompt, UI, or provider behaviour should change
before those contracts are reviewed and validated.

## 16. Company Filings and Jhunjhunwala-Style Evidence Handoff

Status: planned for incremental implementation

Added: 2026-09-14

Scope: authenticated Tijori discovery and download of Earnings Release and
Conference Call PDFs, immutable local artifact retention, structured and cited
document evidence, and a future versioned Jhunjhunwala-style framework
enhancement.

This section supersedes section 15 as the immediate execution sequence for
this workstream. It does not alter the completed quantitative fundamental
pipeline or authorize a live provider call.

### 16.1 Architectural decisions

1. The existing committed 16-dataset package remains the authoritative
   quantitative fundamental package.
2. Company filings form a separate atomic `FundamentalFilingPackage`. A
   missing or malformed transcript must not invalidate valid financial
   statements, and an incomplete filing refresh must not replace the active
   filing package.
3. Tijori MCP owns authenticated provider discovery and secure download into
   an owner-only staging boundary. Provider-neutral application code owns the
   durable artifact, metadata, extraction, evidence, and package contracts.
4. Complete PDFs are retained as immutable artifacts. The 10-day policy is a
   freshness/recheck interval, not a physical-deletion policy. An identical
   SHA-256 is reused; a revised document becomes a new immutable version.
5. PDF bytes are not stored as DuckDB blobs and are never returned to an LLM
   or browser response. DuckDB stores metadata, lineage, hashes, package
   pointers, structured extraction, and citations.
6. Initial document categories are `earnings_release` and
   `conference_call`. Annual reports and investor presentations may reuse the
   same contracts later but are not part of this implementation scope.
7. A company-authored exchange filing and a transcript are distinct source
   types. Tijori's generated Conference Call Summary is secondary derived
   material and cannot substitute for the source PDF.
8. Fiscal year and quarter are derived from validated document metadata or
   contents, never guessed from a filename. Unresolved periods remain
   `unclassified` and cannot enter a period-bound assessment.
9. A document-derived framework change is a new policy candidate. It cannot
   silently modify the active version-1 score or runtime skill.
10. A dedicated `CorporateFilingsAnalyst` owns the end-to-end filing research
    stage at the application level. It initiates provider-neutral discovery and
    download, waits for deterministic extraction and citation validation, and
    then invokes an LLM using only the exact verified Jhunjhunwala-style skill.
    The LLM never receives provider credentials, browser state, arbitrary
    files, or unvalidated document content.
11. The Corporate Filings Analyst is one deliberately specialised framework
    agent, not a general multi-framework analyst. Its output remains separate
    from the quantitative Fundamental Analyst result until the final
    Fundamental Analyst synthesis binds both immutable fingerprints.
12. Completed document analysis is immutable and reusable. An unchanged
    document/package, extraction policy, skill, output schema, prompt contract,
    and model configuration must reuse the stored result without another LLM
    call.

### 16.2 Durable artifact layout

The physical layout is tenant- and connection-isolated, equity-specific,
period-specific, and content-addressed:

```text
data/fundamental-artifacts/
  <tenant-hash>/
    <provider-connection-hash>/
      <verified-isin-or-exchange-symbol>/
        FY<year>/
          <Q1|Q2|Q3|Q4|annual|unclassified>/
            <earnings-release|conference-call>/
              <sha256>.pdf
```

Requirements:

- directories are owner-only and files are non-executable;
- provider filenames are metadata only and never trusted as paths;
- downloads use a temporary `.part` file followed by an atomic rename;
- retries never overwrite an existing different hash;
- refresh preserves prior versions and the active package until the new
  package passes every manifest and extraction gate; and
- physical deletion is explicit, separately authorized, and audited.

### 16.3 Required PDF metadata

Every artifact record must retain:

- tenant ID and provider-connection ID or their approved non-secret storage
  identifiers;
- exchange, symbol, ISIN, legal name, provider company ID, and provider slug;
- filing category, title, provider label, and original provider filename;
- fiscal year, fiscal quarter, period type, period start/end, and reporting
  basis when explicitly available;
- source page URL, direct artifact URL, provider, and source tier;
- discovered-at, provider-published-at, available-at, download-started-at,
  downloaded-at, and last-checked-at timestamps;
- HTTP status, content type, byte length, page count, PDF version, and SHA-256;
- immutable artifact ID, safe relative path, duplicate/supersession links,
  retrieval status, and bounded failure category;
- parser, table-extractor, OCR, schema, and evidence-policy versions;
- raw-text, table, structured-document, and citation fingerprints; and
- filing-package ID, manifest fingerprint, expiry/recheck time, and active or
  superseded state.

Credentials, cookies, browser-session state, raw provider HTML, and generated
Tijori summary text must not enter this metadata.

### 16.4 Tijori MCP operations

Introduce two bounded read-only operations:

```text
list_company_filings(
  issuer,
  categories=[earnings_release, conference_call],
  from_fiscal_year?,
  through_date?,
  max_documents?
) -> immutable filing metadata list

download_company_filing(
  issuer,
  filing_reference,
  expected_source_url,
  expected_provider_company_id
) -> staged artifact manifest
```

The list operation must parse the `#companyFilings` table, retain unavailable
period cells, reject unexpected categories and domains, bind every result to
the verified issuer, and return no page HTML. The download operation must use
the authenticated user-owned Playwright context and enforce:

- HTTPS and an explicit Tijori file-domain allow-list;
- issuer company-ID/path binding and exact discovered-URL matching;
- redirect limits and revalidation after every redirect;
- bounded timeout, byte size, page count, and concurrency;
- successful status, PDF MIME compatibility, and `%PDF` signature;
- SHA-256 calculation while streaming to an owner-only staging file;
- no credentials, cookies, bytes, or local paths in logs or LLM-visible
  responses; and
- cleanup of incomplete staging files on failure.

### 16.5 Executable implementation tasks: filing acquisition

Every substep is deliberately limited to two files. Complete and validate one
substep before beginning the next.

#### PDF-A0.1 Define provider-neutral filing contracts

Files:

- `app/models/fundamental_filings.py`
- `tests/unit/test_fundamental_filings.py`

Deliver immutable issuer-bound filing reference, artifact manifest, metadata,
failure, freshness, and atomic-package contracts. Validate timestamps, fiscal
period states, SHA-256, safe relative paths, source classes, and defensive
copy behaviour.

#### PDF-A0.2 Define artifact repository port

Files:

- `app/storage/fundamental_filing_repositories.py`
- `tests/unit/test_fundamental_filing_repositories.py`

Define save, content-hash lookup, scoped read, list versions, atomic package
publication, active-package read, and explicit audited-delete operations.

#### PDF-A1.1 Add internal MCP filing input validation

Files:

- `integrations/tijori-mcp/src/tool-inputs.js`
- `integrations/tijori-mcp/test/tool-inputs.test.js`

Add strict inputs for listing and downloading filings, category allow-lists,
date/year bounds, result limits, issuer binding, and exact reference matching.

#### PDF-A1.2 Expose the public MCP schemas

Files:

- `integrations/tijori-mcp/src/index.js`
- `integrations/tijori-mcp/test/index.test.js`

Expose both tools as read-only and bounded. Do not expose filesystem paths,
PDF bytes, cookies, or arbitrary URLs.

#### PDF-A1.3 Register tool names and result envelopes

Files:

- `integrations/tijori-mcp/src/result-envelope.js`
- `integrations/tijori-mcp/test/result-envelope.test.js`

Register success/failure envelopes with bounded payloads and sanitized failure
states.

#### PDF-A2.1 Implement Company Filings discovery

Files:

- `integrations/tijori-mcp/src/company-filings.js`
- `integrations/tijori-mcp/test/company-filings.test.js`

Parse synthetic HTML matching the supplied `#companyFilings` structure.
Validate multiple documents per year, absent links, duplicate URLs, unexpected
domains, malformed labels, revised documents, and unclassified quarters.

#### PDF-A2.2 Route and register filing discovery

Files:

- `integrations/tijori-mcp/src/provider-registry.js`
- `integrations/tijori-mcp/test/provider-registry.test.js`

Register the listing handler without changing existing financial, overview,
shareholding, search, or identity-resolution routes.

#### PDF-A3.1 Implement bounded authenticated download

Files:

- `integrations/tijori-mcp/src/company-filing-download.js`
- `integrations/tijori-mcp/test/company-filing-download.test.js`

Stream into the configured owner-only staging directory, enforce domain and
issuer binding, validate the PDF signature and limits, calculate SHA-256, and
atomically finalize the staging artifact. Tests use local synthetic responses,
never Tijori credentials.

#### PDF-A3.2 Register the download handler

Files:

- `integrations/tijori-mcp/src/provider-registry.js`
- `integrations/tijori-mcp/test/provider-registry.test.js`

Expose the handler only through an authenticated registry and verify fail-
closed behaviour when the provider session is absent or expired.

#### PDF-A4.1 Add Python MCP payload contracts

Files:

- `app/fundamentals/tijori_mcp_contracts.py`
- `tests/unit/test_tijori_company_filing_contract.py`

Validate listing and staged-artifact manifests across the stdio boundary with
strict limits, exact issuer binding, and no secret/path fields.

#### PDF-A4.2 Extend the provider-neutral gateway

Files:

- `app/gateways/fundamentals.py`
- `tests/unit/test_fundamental_filing_gateway.py`

Add provider-neutral list and acquire capabilities. No domain or coordinator
type may import a Tijori-specific payload.

#### PDF-A4.3 Translate through `TijoriMCPAdapter`

Files:

- `app/fundamentals/adapters/tijori_mcp.py`
- `tests/unit/test_tijori_mcp_adapter.py`

Translate strict MCP results into neutral contracts, import the staged file
only through the configured secure boundary, and reject hash, issuer, URL, or
size mismatches.

#### PDF-A5.1 Implement the in-memory artifact/package repository

Files:

- `app/storage/adapters/fundamental_filing_in_memory.py`
- `tests/unit/test_fundamental_filing_in_memory.py`

Validate tenant isolation, hash idempotency, immutable versions, CAS conflict,
atomic publication, rollback safety, freshness rechecks, defensive copies,
and explicit deletion.

#### PDF-A5.2 Implement durable filesystem plus DuckDB storage

Files:

- `app/storage/adapters/duckdb.py`
- `tests/unit/test_duckdb_storage.py`

Persist metadata and active package pointers transactionally while moving PDF
artifacts into the content-addressed owner-only layout. Never store PDF bytes
in DuckDB. Database failure must leave either the previous active package or a
recoverable unreferenced artifact, never a partial active package.

#### PDF-A6.1 Add page-preserving PDF extraction contracts

Files:

- `app/models/fundamental_filing_evidence.py`
- `tests/unit/test_fundamental_filing_evidence.py`

Define page blocks, tables, speaker turns, observed facts, adjusted metrics,
management claims, guidance commitments, risk disclosures, citations,
limitations, and extraction fingerprints.

#### PDF-A6.2 Implement deterministic PDF parsing

Files:

- `app/fundamentals/filing_extractor.py`
- `tests/unit/test_fundamental_filing_extractor.py`

Extract page text, tables, headings and speaker boundaries while retaining
page identity. Any OCR path must be explicit and separately versioned. Numeric
and quotation evidence must be traceable to an exact page span.

#### PDF-A6.3 Add document-evidence validation

Files:

- `app/fundamentals/filing_evidence_validator.py`
- `tests/unit/test_filing_evidence_validator.py`

Reject citations whose quote/value is absent from the cited page, future-dated
evidence, issuer/period conflicts, unsupported source upgrades, malformed
units, duplicated facts, and generated-summary claims presented as primary.

#### PDF-A7.1 Orchestrate cache-first filing refresh

Files:

- `app/services/fundamental_filing_evidence.py`
- `tests/unit/test_fundamental_filing_evidence.py`

After verified issuer resolution, list current filings, reuse known hashes,
download only missing/revised documents, extract and validate them, and publish
the complete filing package atomically. Provider failure preserves the active
package. Normal analysis uses current cache; explicit fundamental refresh
refreshes both quantitative and filing packages independently and reports each
outcome.

#### PDF-A7.2 Inject through the composition root

Files:

- `app/composition/fundamentals.py`
- `tests/unit/test_fundamental_composition.py`

Bind the existing Tijori adapter, already-open DuckDB storage, secure artifact
root, and filing coordinator without introducing provider imports into domain
services.

#### PDF-A7.3 Integrate browser operation outcomes

Files:

- `app/workflow/browser_runner.py`
- `tests/unit/test_browser_operation_runner.py`

Run filing acquisition independently after issuer resolution. Preserve its
atomic success/failure in the per-agent operation ledger and allow unrelated
technical agents to proceed. Do not return PDFs or raw document text to the
browser.

#### PDF-A8.1 Add authenticated metadata endpoints

Files:

- `app/api/http.py`
- `tests/unit/test_http_api.py`

Expose tenant-scoped artifact/package metadata and citations only. Raw PDF
download remains disabled until a separately approved authorization and
content-delivery policy exists.

#### PDF-A8.2 Run opt-in live validation

No source change is allowed in this task. With explicit authorization, run one
user-owned Tijori session against a selected equity and verify discovery,
download, deduplication, metadata, artifact placement, recheck behaviour, and
active-package rollback. Sanitize all output and record only hashes, IDs,
counts, timestamps, and bounded statuses.

### 16.6 Document evidence for the Jhunjhunwala-style framework

The new artifacts can support previously withheld principles, but only after
their content becomes released, cited evidence. The initial extension covers:

| Evidence area | Released document evidence | Safety boundary |
|---|---|---|
| Opportunity and runway | Stated markets, demand drivers, pipeline, guidance, new offerings | Management statements remain claims until independently corroborated or realized |
| Competitive ability | Client tenure, repeat wins, wallet/white-space expansion, disclosed market position | No durable-moat conclusion from company statements alone |
| Scalability | Revenue/headcount, margin bridges, operating leverage, delivery mix, utilization where reported | Separate organic, acquired, seasonal, currency, and accounting effects |
| Management execution consistency | Guidance, dated milestones, subsequent actual results and revisions | Measure delivery, not honesty, intent, or character |
| Earnings quality | Cash conversion explanations, DSO, working-capital commentary, adjusted-versus-reported reconciliation | Narrative cannot override statement-derived mandatory findings |
| Capital allocation | Debt repayment, acquisitions, integration, dividends, buybacks, dilution, capex and investment commitments | Intent is not discipline; require subsequent returns and outcomes |
| Concentration | Customer, vertical, geography and product concentration with historical comparisons | Missing disclosure is unavailable, not diversified |
| Thesis monitoring | Versioned claims, expected horizon, actual result, revision and invalidation trigger | No hindsight data may enter the original assessment |

### 16.7 Executable implementation tasks: framework enhancement

#### JH-D0.1 Define document-derived evidence contracts

Files:

- `app/models/fundamental_document_assessment.py`
- `tests/unit/test_fundamental_document_assessment.py`

Define immutable, cited runway, competitive-ability, scalability,
concentration, capital-allocation, execution-consistency, and limitation
results. Preserve `OBSERVED`, `MANAGEMENT_CLAIM`, `GUIDANCE`, `DERIVED`, and
`SECONDARY_SUMMARY` as distinct evidence classes.

#### JH-D1.1 Build the guidance and milestone ledger

Files:

- `app/analytics/fundamental_guidance.py`
- `tests/unit/test_fundamental_guidance.py`

Track every claim as `PENDING`, `MET`, `MISSED`, `REVISED`, `WITHDRAWN`, or
`UNVERIFIABLE`, using only documents available by each assessment timestamp.
Later outcomes must never rewrite the original claim or historical decision.

#### JH-D1.2 Add concentration and adjustment-quality findings

Files:

- `app/analytics/fundamental_document_checks.py`
- `tests/unit/test_fundamental_document_checks.py`

Implement versioned review findings for customer/vertical/geography
concentration, adjusted-versus-reported metric reconciliation, DSO and cash-
conversion commentary, acquisition contribution, and seasonal/non-organic
growth. These are review findings, not allegations.

#### JH-D2.1 Add deterministic document applicability

Files:

- `app/analytics/fundamental_document_applicability.py`
- `tests/unit/test_fundamental_document_applicability.py`

Determine whether each qualitative evidence family has sufficient source
quality, history, citations, comparability, and outcome maturity. One earnings
release cannot establish execution consistency or a durable advantage.

#### JH-D3.1 Define a version-2 shadow score candidate

Files:

- `app/analytics/fundamental_score_policies.py`
- `tests/unit/test_fundamental_score_policies.py`

Add a separately versioned, shadow-only Jhunjhunwala-style candidate that may
consume only applicable released document evidence. Do not mutate version 1,
renormalize missing families, score management honesty, or let favourable
narrative offset mandatory financial-health, earnings-quality, forensic, or
concentration constraints.

#### JH-D3.2 Add cross-company and adversarial scenarios

Files:

- `tests/unit/test_fundamental_scorecard.py`
- `tests/unit/test_fundamental_framework_explanation.py`

Cover seasonal growth, acquisition-led growth, high growth with weak cash
conversion, high but declining concentration, missed/revised guidance,
adjusted-profit dependence, transcript-only claims, summary-only claims,
conflicting documents, insufficient history, and apparently strong narrative
with deteriorating mandatory checks.

#### JH-D4.1 Propose the versioned skill/doctrine change

Files:

- `runtime-skills/fundamental-frameworks/jhunjhunwala-style/SKILL.md`
- `runtime-skills/fundamental-frameworks/jhunjhunwala-style/references/framework-doctrine.md`

Create a candidate version that explains newly released document families but
cannot calculate them, read PDFs, select itself, change a score, imitate a
person, or make a trade decision. This is a candidate only; do not register or
activate it in this task.

#### JH-D4.2 Validate and register the approved skill version

Files:

- `runtime-skills/fundamental-frameworks/skills-manifest.yaml`
- `tests/unit/test_fundamental_framework_skill_loader.py`

Only after exact-hash human approval and candidate validation, register the
new version and prove fail-closed loading, rollback compatibility, and policy
identity binding.

#### JH-D5.1 Supply evidence to Fundamental Analyst

Files:

- `app/agents/fundamental_analyst_agent.py`
- `tests/unit/test_fundamental_analyst_agent.py`

Supply the released filing-package fingerprint, document assessment, exact
citations, framework score, limitations, and source classes. The analyst may
explain but cannot retrieve documents, invent facts, or change findings.

#### JH-D5.2 Supply the same bounded evidence to debate

Files:

- `app/composition/debate.py`
- `tests/unit/test_debate_composition.py`

Give Bull, Bear, and Senior Judge the same immutable fundamental/document
assessment fingerprint. Preserve technical-entry release gates and keep a good
company distinct from a good swing entry.

#### CFA-A0.1 Define Corporate Filings Analyst contracts

Files:

- `app/models/corporate_filings_analysis.py`
- `tests/unit/test_corporate_filings_analysis.py`

Define immutable request, quarterly document bundle, analysis result,
grounding status, coverage, source-quality, citation, token-usage, latency,
failure, and reuse-key contracts. The result must separate observed facts,
management claims, guidance, risks, contradictions, missing evidence, and
Jhunjhunwala-style interpretations.

#### CFA-A0.2 Define the analysis repository port

Files:

- `app/storage/corporate_filings_analysis_repositories.py`
- `tests/unit/test_corporate_filings_analysis_repositories.py`

Define idempotent save, exact-key lookup, scoped history, active-result read,
atomic active-pointer replacement, and explicit audited-delete operations.
Failed or incomplete analysis cannot replace the last successful result.

#### CFA-A1.1 Implement the in-memory analysis repository

Files:

- `app/storage/adapters/corporate_filings_analysis_in_memory.py`
- `tests/unit/test_corporate_filings_analysis_in_memory.py`

Validate tenant isolation, defensive copies, immutable result history, cache
hits, key conflicts, compare-and-swap publication, failure preservation, and
explicit deletion.

#### CFA-A1.2 Implement the DuckDB analysis repository

Files:

- `app/storage/adapters/duckdb.py`
- `tests/unit/test_duckdb_storage.py`

Store the complete structured LLM result and every reproducibility fingerprint
transactionally. Do not store prompts containing raw document text, hidden
reasoning, credentials, cookies, or provider session material.

#### CFA-A2.1 Create the grounded Corporate Filings Analyst

Files:

- `app/agents/corporate_filings_analyst_agent.py`
- `tests/unit/test_corporate_filings_analyst_agent.py`

Require the exact loader-verified Jhunjhunwala-style skill, strict structured
output, complete citation coverage, balanced supporting and adverse evidence,
explicit missing states, and no calculation, score mutation, provider access,
persona impersonation, or trade decision. Reject any result that cites an
unknown document/page/span or changes deterministic fields.

#### CFA-A2.2 Add the dedicated LLM role and composition

Files:

- `app/composition/fundamentals.py`
- `tests/unit/test_fundamental_composition.py`

Bind a dedicated Corporate Filings Analyst LLM role, verified framework skill,
filing coordinator, analysis repository, and bounded input/output budgets.
Production composition must fail closed if any required capability or skill
hash is absent.

#### CFA-A3.1 Orchestrate analysis and cache reuse

Files:

- `app/services/corporate_filings_analysis.py`
- `tests/unit/test_corporate_filings_analysis_service.py`

For each matched fiscal quarter, reuse an exact cached result or make one LLM
call over the validated Earnings Release plus Conference Call evidence. Build
the longitudinal company view from stored quarterly JSON, not by resending old
PDFs. Publish a new active analysis only after every required quarterly result
and the longitudinal synthesis pass validation.

#### CFA-A3.2 Integrate atomic workflow outcomes

Files:

- `app/workflow/browser_runner.py`
- `tests/unit/test_browser_operation_runner.py`

Start the Corporate Filings Analyst after verified issuer resolution and run
it concurrently with independent market, daily, weekly, and quantitative-
fundamental work. Record discovery, download, extraction, LLM analysis, cache
reuse, and publication as individually attributable outcomes. Dependent
Fundamental Analyst synthesis waits for the required filing result; unrelated
agents continue safely if it fails.

#### CFA-A3.3 Supply the cached result to Fundamental Analyst

Files:

- `app/agents/fundamental_analyst_agent.py`
- `tests/unit/test_fundamental_analyst_agent.py`

Supply only the immutable, validated Corporate Filings Analyst result and its
fingerprints. Never pass the PDFs again. Preserve the filing result as a
Jhunjhunwala-style document lens distinct from the existing deterministic
quantitative framework scores and mandatory checks.

### 16.8 Separate-agent decision

Decision: **create one dedicated Corporate Filings Analyst that strictly uses
the verified Jhunjhunwala-style runtime skill.** It is a bounded document-
research agent, not an impersonation of Rakesh Jhunjhunwala and not a general
multi-framework substitute for the Fundamental Analyst.

The application-level agent initiates download through the provider-neutral
filing coordinator. The Tijori MCP adapter performs authenticated browser work;
the LLM never downloads a document directly and never receives authentication
material. Analysis starts only after deterministic PDF extraction and citation
validation succeed:

```text
Verified issuer
  +-- quantitative fundamental refresh -------------------+
  +-- market-data / daily / weekly research ---------------+--> dependencies
  +-- filing discovery and download                         |
          -> deterministic PDF extraction                   |
          -> cited evidence validation                      |
          -> atomic filing package -------------------------+
                              |
                              v
          Corporate Filings Analyst
          verified Jhunjhunwala-style skill
                              |
                              v
              immutable cached filing analysis
                              |
                              v
              Fundamental Analyst synthesis
                              |
                              v
                    Bull + Bear -> Judge
```

The filing pipeline may run concurrently with independent market and
quantitative-fundamental retrieval after issuer resolution. Per-document
downloads and parsing use bounded concurrency. The Corporate Filings Analyst
waits for a successfully published filing package. Fundamental Analyst
synthesis waits for both required quantitative and filing results; the Judge
continues to wait for the required Bull and Bear outcomes.

The LLM is an evidence-constrained interpreter. It receives exact validated
page spans, deterministic metrics, evidence classes, and limitations under the
verified skill. It cannot release new facts, calculate a score, alter a skill,
or make a trade decision. A deterministic validator rejects unknown citations,
unsupported statements, missing counter-evidence, schema drift, and any change
to released fields. Its success/failure is recorded atomically in the operation
ledger.

### 16.8.1 Grounding contract

Every Corporate Filings Analyst invocation must:

- load the exact approved Jhunjhunwala-style skill ID, version, content hash,
  doctrine ID, and bound score-policy identity;
- use only evidence from one verified issuer and one immutable filing-package
  fingerprint;
- distinguish `OBSERVED`, `ADJUSTED_RESULT`, `MANAGEMENT_CLAIM`, `GUIDANCE`,
  `ANALYST_QUESTION`, `DERIVED`, and `SECONDARY_SUMMARY` evidence;
- cite every company-specific statement to an approved document hash, page,
  and validated text/table span;
- state material adverse and contradictory evidence before reaching a
  framework interpretation;
- preserve missing, stale, conflicting, unclassified, and unsupported states;
- treat Tijori-generated summaries only as secondary discovery aids and never
  as substitutes for source PDFs;
- assess management execution consistency through dated claim-versus-outcome
  records, never infer honesty, character, endorsement, or private judgement;
- keep observed results separate from future claims and acquisition or
  currency effects; and
- produce no `BUY`, `SELL`, `NO_TRADE`, entry, stop, target, position-size, or
  portfolio instruction.

### 16.8.2 Token and performance policy

Use a two-level immutable cache:

1. **Quarterly bundle analysis** pairs the matching Earnings Release and
   Conference Call for one fiscal quarter. It is created once per exact bundle
   and reused.
2. **Longitudinal company synthesis** consumes the stored quarterly JSON
   results and open guidance ledger. It never resends historical PDFs.

The exact reuse key includes:

- tenant and verified issuer identity;
- filing-package and quarterly-bundle fingerprints;
- ordered source PDF hashes;
- extraction, citation, and evidence-policy versions;
- Jhunjhunwala-style skill ID, version, doctrine version, and content hash;
- score-policy, prompt-contract, and output-schema versions; and
- LLM provider, model, and material model-configuration fingerprint.

When the key matches a successful result, no LLM call is permitted. A normal
fundamental refresh rechecks provider documents but reuses analysis if hashes
are unchanged. Reanalysis occurs only for a new/revised document, changed
extraction or grounding policy, newly approved skill/model contract, missing
valid result, or an explicit `reanalyze filings` instruction.

Initial bootstrap downloads every available in-scope Earnings Release and
Conference Call with bounded provider concurrency. Analysis pairs matching
documents by validated fiscal quarter and processes only previously unseen
bundle fingerprints. Input selection uses cited relevant spans and tables,
not complete PDFs; output and token limits are enforced per bundle. Store input
and output token counts, latency, model identity, retry count, and cache-hit
status without storing hidden reasoning or raw prompt text.

### 16.9 Validation and release gates

Required offline fixtures include:

- multiple earnings releases and calls in one fiscal year;
- a missing earnings release or transcript;
- a link with a Tijori label but an unapproved redirect/domain;
- HTML changes, malformed URLs, duplicate hashes, and revised documents;
- non-PDF content, oversized files, truncated files, timeout, and expired
  authentication;
- ambiguous quarter/year, future publication date, and issuer mismatch;
- transcript disclaimer/errors and generated-summary-only evidence;
- cross-tenant artifact lookup and path traversal attempts;
- database failure after file download and file failure before transaction;
- refresh failure preserving the previous active filing package; and
- identical input and policy fingerprints producing identical released
  deterministic results.

Release sequence:

1. MCP discovery and download pass synthetic offline tests.
2. Artifact metadata and atomic repositories pass in-memory and DuckDB tests.
3. PDF extraction and citation validation pass sanitized fixtures.
4. Cache-first orchestration and per-agent outcome tests pass.
5. One explicitly authorized live equity validates download and storage; no
   LLM is required.
6. Document assessments enter shadow mode across multiple companies and
   sectors.
7. The Jhunjhunwala-style skill candidate follows the controlled skill-
   improvement lifecycle, exact-hash approval, registration, and rollback
   validation.
8. Only a separate future approval may allow the new evidence to influence a
   trade conclusion.

### 16.10 Immediate executable task

Start with **PDF-A0.1 — Define provider-neutral filing contracts**. It is the
smallest safe step, changes exactly two files, performs no provider call or
filesystem mutation, and establishes the metadata and atomic-package boundary
needed by every later MCP, storage, extraction, and framework task.
