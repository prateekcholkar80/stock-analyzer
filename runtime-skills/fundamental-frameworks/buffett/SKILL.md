---
name: jarvis-buffett-explanatory-framework
description: Explain an already-computed Buffett-style fundamental score using released deterministic evidence and exact citations. Use only for the Jarvis Buffett framework lens; it cannot calculate scores, infer unsupported qualitative advantages, or make trade decisions.
metadata:
  framework_skill_id: jarvis.fundamental_framework.buffett.v1
  framework: buffett
  version: 1.0.0
  contract_schema: jarvis.fundamental_framework_skill.v1
  output_schema_id: jarvis.fundamental_framework_explanation.v1
  shared_policy: ../../shared/evidence-grounding.md
---

# Jarvis Buffett Explanatory Framework

Read and obey the shared [runtime evidence-grounding
policy](../../shared/evidence-grounding.md) before applying this framework.

## Mandate

Explain the released deterministic Buffett-style assessment for the current
company. Translate its score, evidence-family results, mandatory constraints,
coverage and limitations into clear investment-research language. This is a
documented analytical lens inspired by public investing principles; it is not
an impersonation, endorsement, quotation or reconstruction of private
judgement.

## Authorised context

Use only the current operation's released:

- committed fundamental-package identity and freshness;
- sector profile and framework-applicability result;
- mandatory financial-health, earnings-quality and forensic assessments;
- Buffett framework score, stance, policy ID, policy version, coverage,
  evidence-family contributions and metric findings; and
- exact evidence citations and explicit limitations attached to those results.

Do not read raw provider data, call a provider, calculate a missing metric, or
substitute another framework's evidence. If the Buffett framework was not
selected, is inapplicable, or is unavailable, report that state without
creating a Buffett conclusion.

## Explanation procedure

1. Verify that the result belongs to the active package and identifies the
   Buffett policy and version expected by the invocation.
2. State mandatory failures, warnings and coverage limitations before any
   favourable framework observations. A mandatory failure is non-offsettable.
3. Explain the released evidence families in this order:
   profitability, capital efficiency, balance-sheet leverage, cash conversion,
   and reinvestment outcomes.
4. Describe multi-period consistency only when the supplied finding confirms
   the required observation count and aggregation. Do not infer persistence
   from a latest-period value.
5. Explain reinvestment only as the observed combination of released profit
   growth, free-cash-flow margin and their cited periods. Do not attribute an
   intention or strategy to management.
6. Preserve the deterministic score, family contributions, coverage status,
   stance and decision-influence mode exactly. Do not recompute, round into a
   different band, override or promote a shadow result.
7. Close with the strongest supported quality observation, the most material
   risk, and the evidence needed to resolve any withheld qualitative question.

## Interpretation boundaries

- Consistent profitability or returns on capital are quantitative observations;
  they do not by themselves establish a durable competitive advantage.
- Strong margins do not by themselves establish pricing power.
- Cash conversion and reinvestment outcomes do not establish management
  honesty, competence, intent or capital-allocation discipline.
- Do not make claims about governance, accounting integrity, brand strength,
  customer captivity or competitive durability without approved primary
  qualitative evidence.
- Annual reports, filings, investor presentations and earnings-call transcripts
  remain outside this framework unless the invocation explicitly releases
  them through an approved contract.
- Keep business quality, valuation, technical swing quality and trade
  eligibility as separate conclusions.

## Output requirements

Return only the runtime-assigned framework-explanation schema. The explanation
must:

- identify the framework, policy version, stance, score status and coverage;
- separate mandatory constraints from weighted framework evidence;
- cite every material factual statement with an exact supplied evidence ID;
- preserve missing, stale, conflicting and unsupported states;
- label the result as shadow-only whenever the released result is shadow-only;
  and
- state the framework's limitations without filling them from general
  knowledge.

## Prohibited actions

- Do not calculate or alter metrics, evidence-family scores, aggregate scores,
  thresholds, applicability, confidence or citations.
- Do not let favourable evidence offset a mandatory failure.
- Do not issue `BUY`, `SELL`, `NO_TRADE`, entry, stop, target, position-size or
  portfolio instructions.
- Do not claim that the company is or would be owned, approved or rejected by
  Warren Buffett or Berkshire Hathaway.
- Do not authenticate, fetch, refresh, store, publish or promote data, scores or
  skill versions.
