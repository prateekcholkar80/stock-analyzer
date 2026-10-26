---
name: jarvis-lynch-explanatory-framework
description: Explain an already-computed Lynch-style fundamental score using released deterministic evidence and exact citations. Use only for the Jarvis Lynch framework lens; it cannot calculate scores, invent a company lifecycle, or make trade decisions.
metadata:
  framework_skill_id: jarvis.fundamental_framework.lynch.v1
  framework: lynch
  version: 1.0.0
  contract_schema: jarvis.fundamental_framework_skill.v1
  output_schema_id: jarvis.fundamental_framework_explanation.v1
  shared_policy: ../../shared/evidence-grounding.md
---

# Jarvis Lynch Explanatory Framework

Read and obey the shared [runtime evidence-grounding
policy](../../shared/evidence-grounding.md) before applying this framework.

## Mandate

Explain the released deterministic Lynch-style assessment for the current
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
- Lynch framework score, stance, policy ID, policy version, coverage,
  evidence-family contributions and metric findings; and
- exact evidence citations and explicit limitations attached to those results.

Do not read raw provider data, call a provider, calculate a missing metric, or
substitute another framework's evidence. If the Lynch framework was not
selected, is inapplicable, or is unavailable, report that state without
creating a Lynch conclusion.

## Explanation procedure

1. Verify that the result belongs to the active package and identifies the
   Lynch policy and version expected by the invocation.
2. State mandatory failures, warnings and coverage limitations before any
   favourable framework observations. A mandatory failure is non-offsettable.
3. Explain the released evidence families in this order: profitability,
   earnings and revenue growth, balance-sheet leverage, and valuation.
4. Distinguish observed growth magnitude from growth consistency. Describe
   consistency only when supported by the released periods, observation count
   and aggregation.
5. Use adjusted EPS before unadjusted EPS only when the deterministic finding
   explicitly identifies that selected metric. Preserve that selection and do
   not blend the two series.
6. Describe valuation as the released absolute P/E observation only. Do not
   relabel it as PEG, growth-adjusted value, sector-relative value, or intrinsic
   value.
7. Preserve the deterministic score, family contributions, coverage status,
   stance and decision-influence mode exactly. Do not recompute, round into a
   different band, override or promote a shadow result.
8. Close with the strongest supported growth observation, the most material
   balance-sheet or valuation risk, and the evidence needed to resolve any
   withheld company-lifecycle question.

## Interpretation boundaries

- Do not classify the company as a slow grower, stalwart, fast grower,
  cyclical, turnaround or asset play unless a separately approved,
  deterministic lifecycle result is released in the invocation.
- Revenue or EPS growth does not establish business quality, durability or a
  future growth rate.
- A low or high absolute P/E does not by itself establish cheapness,
  expensiveness, mispricing or a margin of safety.
- Do not infer PEG, normalized earnings, cyclicality, catalysts, addressable
  market, management quality or a business narrative from general knowledge.
- Annual reports, filings, investor presentations and earnings-call transcripts
  remain outside this framework unless the invocation explicitly releases
  them through an approved contract.
- Keep company growth, financial health, valuation, technical swing quality
  and trade eligibility as separate conclusions.

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
  Peter Lynch or Fidelity.
- Do not authenticate, fetch, refresh, store, publish or promote data, scores or
  skill versions.
