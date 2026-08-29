# Tijori Finance MCP Source and Integration Audit

Audit date: **2026-08-28 (Asia/Kolkata)**

## 1. Decision

The upstream Tijori Finance MCP is **conditionally suitable as a reference
implementation for isolated, personal-development use**. It is **not approved
for installation, authentication, production use, a public Jarvis deployment,
or direct inclusion in the Jarvis runtime** in its audited form.

The next engineering step must create a pinned, patched local integration with
Jarvis-owned security, evidence, caching, and validation boundaries. The
upstream setup and discovery scripts must not be executed.

This is a technical and data-governance assessment, not legal advice. Written
permission or a commercial data agreement from Tijori is required before the
integration is exposed to multiple users, deployed publicly, used
commercially, or used to redistribute Tijori-derived data.

## 2. Audited Artifact and Scope

The source was cloned into an isolated temporary directory for static review:

- Repository: `https://github.com/LaZZy0v0/tijori-finance-mcp`
- Audited commit: `64be6c49f99a3fb3355ab5a727ce05cf260a7acc`
- Commit date: `2026-07-14T17:27:44+05:30`
- Commit message: `Improve README description for MCP server`
- Package version: `1.0.0`
- Declared code license: MIT
- Transport: local MCP over stdio
- Registered tools: 19

The review covered the package and lock manifests, license, setup launchers,
authentication/discovery flow, browser/session runtime, cache, all registered
tools, parsers, tests, README, and architecture notes. All JavaScript source
and test files passed `node --check` syntax validation.

The audit deliberately did **not**:

- run `setup.js`, `discover.js`, or the MCP server;
- install npm packages or a Playwright browser;
- open an authenticated Tijori session;
- read or create Tijori credentials or cookies;
- call Tijori's authenticated pages or undocumented internal APIs;
- copy the upstream source into this repository; or
- run upstream live tests, because they require an authenticated account and
  execute against the real service.

Public Tijori policy and pricing pages were read separately to assess data-use
boundaries. No authenticated Tijori request was made.

## 3. What the Upstream Provides

The server uses one Playwright Chromium context loaded from a saved Django
session. It scrapes rendered pages and calls the same undocumented internal
endpoints used by Tijori's browser application. It exposes read-oriented tools
for:

- company search and company-id resolution;
- company overview and ratios;
- P&L, balance sheet, cash flow, ratios, and quarterly results;
- shareholding history;
- operational metrics and fund-flow analysis;
- revenue mix and market share;
- annual reports, earnings releases, investor presentations, and conference
  call document discovery;
- authenticated PDF text extraction;
- raw materials, macro indicators, market/sector indices, and constituents;
- popular/ad-hoc company screens and screener-field discovery.

The server has useful runtime safeguards: a singleton browser, a three-request
navigation semaphore, one timeout retry, resource blocking, an allow-list for
document downloads (`files.tijorifinance.com`), basic response caps for
screener output, and an in-memory TTL cache.

These are implementation conveniences, not a sufficient enterprise evidence
or security boundary.

## 4. Evidence Policy for Jarvis

Tijori must be classified as a **secondary aggregator / discovery source**.
It must not become Jarvis's sole source of truth for a financial conclusion.

Jarvis's fundamental evidence hierarchy will be:

1. exchange filings and company-issued annual reports, quarterly filings,
   investor presentations, and earnings-call documents;
2. validated structured values extracted from those primary documents;
3. Tijori structured fields as discovery, convenience, and cross-check data;
4. Jarvis-computed ratios and scores, with formulas and contributing values;
5. Bull, Bear, Financial Agent, and Judge inference, which must cite the
   released evidence and may never silently upgrade an inference into a fact.

If Tijori conflicts with a dated primary filing, the primary filing controls.
The conflict must be preserved and shown rather than averaged away. Missing,
stale, paywalled, malformed, or conflicting data must produce an explicit
`UNAVAILABLE`, `STALE`, or `CONFLICTED` state; it must never be filled by an
LLM.

## 5. Findings and Required Controls

### TJM-001 — Release blocker: data-use rights are narrower than the code license

The repository's MIT license applies to its code. It does not grant rights to
Tijori's website content or data. Tijori's current Terms of Use state that
website content may not be copied or stored except for the user's own personal,
non-commercial use, and prohibit reproduction, distribution, derivative use,
and exploitation without prior written permission.

Required controls:

- allow only an account owner to authenticate for personal, local research
  during development;
- do not expose raw Tijori data through a public or multi-user Jarvis URL;
- do not redistribute Tijori data, documents, or cached payloads;
- do not send Tijori-derived content to an LLM until the user's terms/data
  agreement is confirmed to permit that processing;
- obtain written Tijori permission or a suitable data agreement before public,
  multi-user, or commercial deployment; and
- retain source attribution and the account/plan capability boundary with each
  snapshot.

Authoritative references:

- [Tijori Terms of Use](https://www.tijorifinance.com/terms-of-use)
- [Tijori subscription plans](https://www.tijorifinance.com/in/plans/)

### TJM-002 — Critical: the discovery/setup flow can expose credentials and session secrets

`setup.js` asks for a password through a normal terminal prompt, so the value
can be displayed while typed, then writes the email and password to a plain
`.env` file without explicitly setting owner-only permissions. Those
credentials are not used to perform the actual login; `discover.js` only
checks that they exist and then asks the user to log in manually.

`discover.js` writes Playwright `storageState` containing `sessionid` and CSRF
cookies to `output/session.json`, again without explicitly setting owner-only
permissions. More seriously, it records request headers, cookies, CSRF tokens,
POST bodies, response headers, and response samples into
`output/discovered_endpoints.json`. The directory is git-ignored, but that does
not protect local files, backups, malware, support bundles, or accidental log
attachment.

Required controls:

- never run upstream `setup.js` or `discover.js`;
- do not store `TIJORI_EMAIL` or `TIJORI_PASSWORD` for browser authentication;
- implement a Jarvis-owned, manual, auth-only Playwright flow with no traffic
  capture;
- store any unavoidable session state outside logs and DuckDB in an owner-only
  directory (`0700`) and owner-only file (`0600`);
- validate that session files and all parent directories are not group/world
  readable before launch;
- redact cookies, CSRF tokens, authorization values, passwords, and session
  identifiers from exceptions and audit logs;
- provide explicit re-authentication and secure session deletion; and
- never persist raw browser storage state in a source-controlled path.

### TJM-003 — High: the audited dependency lock contains known vulnerabilities

`npm audit --package-lock-only --omit=dev --json` reported six vulnerable
production packages: **four high, one moderate, one low, and zero critical**.
Affected transitive packages were `hono`, `undici`, `fast-uri`, `ip-address`,
`@hono/node-server`, and `body-parser`. Fixes were reported as available.

The documented Node 18 requirement is also inaccurate for the lock: Cheerio
1.2.0 and Undici 7.27.1 require Node `>=20.18.1`. The present Jarvis development
machine uses Node 25.2.1, but integration must declare and validate its own
supported runtime rather than inherit the upstream README claim.

Required controls:

- patch dependency versions and regenerate the lock in the isolated fork;
- require zero critical and zero high advisories before authentication;
- use `npm ci`, not `npm install`, in repeatable builds;
- pin both the fork commit and lockfile; never execute an unreviewed branch
  head; and
- establish a supported Node floor of at least 20.18.1, preferably a currently
  supported LTS release, and test that exact runtime in CI.

### TJM-004 — High: tests do not provide a reliable contract gate

There is no package `test` script and no CI/security configuration in the
audited tree. Most tests are credential-dependent live scripts rather than
isolated unit/fixture tests. At least two tests have drifted from the current
implementation:

- `search.test.js` requires `company_id`, while `searchCompany()` returns only
  `name` and `slug`;
- `metrics.test.js` calls `getOperationalMetrics(338, 443)`, while the current
  function accepts one company slug.

Required controls:

- do not treat the upstream tests or README's “stable” label as validation;
- create Jarvis-owned offline fixtures for every adopted tool;
- test success, empty, stale session, paywall, malformed HTML/JSON, timeout,
  403, 404, 429, 5xx, schema drift, oversized response, and conflicting-source
  cases;
- keep opt-in live smoke tests separate from the regression suite; and
- fingerprint the adopted tool contract so an upstream change cannot enter
  silently.

### TJM-005 — High: page readiness and response validity can fail open

`loadPage()` swallows `waitForSelector()` timeouts and returns the page anyway,
despite its comment promising complete data or an error. Several parsers return
empty arrays or embedded `error` fields instead of throwing, and callers can
cache those payloads for hours. `browserFetch()` explicitly handles only 403
and 404; other error statuses can become strings or plausible-looking data.
The upstream architecture notes also acknowledge that unknown internal API
parameters may be silently ignored, creating incorrect but believable results.

Required controls:

- fail closed when the required selector or schema is absent;
- reject 429 and all 5xx responses with typed retryability metadata;
- never cache an error, partial response, empty required table, or paywall
  page as valid evidence;
- require minimum row/period invariants per dataset;
- compare field/period identities against fixtures and quarantine schema drift;
  and
- release data to agents only after a deterministic evidence validator passes.

### TJM-006 — High: upstream output lacks Jarvis-grade provenance

Most tool results do not include retrieval time, source URL, provider plan,
document identity, filing/as-of date, raw-payload hash, parser version,
normalization version, or field-level lineage. Financial statement values are
often unnormalised display strings. This is insufficient for an evidence-bound
Financial Agent, Bull, Bear, or Judge.

Required controls for every accepted snapshot:

- exchange, symbol, company slug, and provider company id;
- source/provider and exact source URL or document URL;
- `retrieved_at`, provider `as_of`/period, and timezone where applicable;
- account capability (`free`, `paid`, or `unknown`) without account secrets;
- raw content hash, parser version, schema version, and normalized snapshot
  fingerprint;
- original value, normalized numeric value, currency/unit, scale, and period;
- freshness state, conflict state, and validation state; and
- immutable evidence IDs for every fact released to an agent.

### TJM-007 — High: document and response sizes are insufficiently bounded

`fetch_document` downloads the entire PDF, parses it fully in memory, returns
the complete text, and caches the complete result without a byte, page, or text
limit. The screener downloads every matching row before applying its output
page. Several free-form query/slug inputs have no practical length limit.

Required controls:

- enforce URL host, protocol, redirect-host, content-type, and final-host checks;
- reject oversized downloads before parsing and cap pages and extracted text;
- use a sandboxed document-processing boundary and scan uploaded/downloaded
  documents before indexing;
- return bounded excerpts/chunks to agents, never an unbounded full document;
- cap input lengths, timeouts, response bytes, rows, and concurrent requests;
  and
- keep raw document retrieval separate from LLM context construction.

The existing exact-host check for `files.tijorifinance.com` is useful and must
be retained, but it is not sufficient by itself because redirects and response
size also need enforcement.

### TJM-008 — Medium: raw upstream errors and protocol metadata need hardening

The MCP wrapper returns `err.message` directly. This can disclose local paths,
internal URLs, or provider details. Tools are read-oriented but do not declare
MCP read-only/idempotent annotations. Package/server metadata says version
1.0.0 while the runtime advertises 0.1.0.

Required controls:

- map upstream failures to Jarvis typed errors with safe user messages;
- log only redacted diagnostic codes and bounded metadata;
- expose an explicit allow-list of approved read-only tools;
- add read-only/idempotent annotations where supported; and
- align adapter, contract, and runtime versions.

### TJM-009 — Medium: upstream setup mutates unrelated application state

The wizard installs packages, downloads Chromium, authenticates, and rewrites
Claude Desktop configuration. If an existing Claude configuration is malformed,
the script starts from an empty object and can overwrite unrelated settings.
Jarvis does not need or want this coupling.

Required control: use a dedicated Jarvis stdio process configuration composed
by the Python backend. Do not run or embed the upstream setup wizard and do not
modify Claude Desktop configuration.

### TJM-010 — Medium: upstream maintenance and supply-chain posture is limited

The audited repository has one effective maintainer identity, no release tags,
no security policy, no CI workflow, no CODEOWNERS file, and no verifiable signed
release in the audited checkout. Dependency ranges are permissive even though
the lockfile currently pins resolved artifacts. `cheerio` and `dotenv` appear
unused by runtime source, and `src/auth.js` is used only by its test, increasing
attack surface without current runtime value.

Required controls:

- maintain a minimal Jarvis fork or vendored adapter containing only approved
  runtime code;
- remove unused packages and dead authentication code;
- review every upstream diff before rebasing;
- use lockfile integrity checks, dependency scanning, secret scanning, and
  provenance/SBOM output in CI; and
- never auto-update the pinned upstream commit.

## 6. Integration Boundary Approved for the Next Build Step

The approved target is not “let the LLM call Tijori directly.” The boundary is:

```text
Jarvis use case / Financial Agent
  -> FundamentalEvidenceGateway (Python protocol)
  -> TijoriMcpAdapter (Python MCP stdio client, approved tool allow-list)
  -> patched/pinned local Node MCP subprocess
  -> authenticated Tijori account owned by the user
  -> untrusted raw response
  -> deterministic schema/provenance validator
  -> database-agnostic FundamentalSnapshotRepository
  -> DuckDB adapter with expiry metadata and automatic purge
  -> immutable evidence release
  -> Financial Agent / Bull / Bear / Judge
```

The LLM remains provider-agnostic and never receives an MCP transport handle,
session cookie, or raw unrestricted tool catalog. Jarvis application code owns
tool selection, authorization, validation, caching, and evidence release.

## 7. Initial Approved Tool Surface

Adopt incrementally. The first contract test should cover only:

1. `search_company`
2. `resolve_company_ids`
3. `get_company_overview`
4. `get_financials`
5. `get_shareholding`

The following remain disabled until later gates:

- `fetch_document`: disabled until document-size, redirect, malware, parsing,
  citation, and data-rights controls exist;
- screeners: disabled until query validation and result-size controls exist;
- macro/markets/raw materials: useful later, but not required for the first
  company-fundamentals vertical slice;
- revenue mix, market share, operational metrics, and fund flow: enable only
  after plan entitlements, as-of semantics, field contracts, and primary-source
  reconciliation are validated.

No write operation, portfolio mutation, alert creation, or order placement is
approved.

## 8. DuckDB Cache and Retention Contract

The upstream in-memory cache is not the Jarvis cache. Jarvis will use a
database-agnostic repository with a DuckDB adapter.

For the agreed personal-research cache:

- `retrieved_at` and `expires_at` are mandatory;
- `expires_at = retrieved_at + 10 days` is the maximum retention for accepted
  normalized fundamental snapshots;
- reads must never return an expired row;
- opportunistic purge runs before reads/writes, and a startup purge removes
  expired rows after downtime;
- a request after expiry performs a full re-pull;
- raw secrets and browser session state never enter DuckDB;
- a failed refresh does not turn an expired snapshot into current evidence;
- stale data may be displayed only when explicitly labelled and never used for
  an unqualified current conclusion; and
- public/multi-user retention remains blocked until data rights are resolved.

Dataset freshness may be shorter than ten days when the provider supplies a
newer filing or event. Ten days is a retention ceiling, not permission to call
old data current.

## 9. Acceptance Gates Before First Authentication

All gates below are mandatory:

- [ ] Tijori use is confirmed as personal/local, or written permission/data
      agreement is available for the intended deployment.
- [ ] A minimal fork is pinned to a reviewed commit.
- [ ] Upstream setup/discovery scripts are excluded from the runtime path.
- [ ] Credentials are removed from the auth design; manual browser login is
      used.
- [ ] Session directory/file permission checks and secure deletion exist.
- [ ] Dependency audit has zero critical and zero high findings.
- [ ] Supported Node runtime is declared and validated.
- [x] The five-tool initial allow-list and Python gateway contract exist.
- [x] Offline synthetic fixtures cover success and failure/schema-drift paths.
- [x] Response/provenance validators fail closed.
- [ ] Logs prove secrets and document contents are not emitted.
- [x] DuckDB expiry/purge behavior is tested without storing credentials.

Until the remaining gates pass, Tijori remains **audited but not installed,
not authenticated, and not connected to a live Jarvis transport**. The
network-free adapter and normalization boundary are implemented, but cannot
open a provider session.

## 10. Audit Evidence

- All tracked runtime/setup/parser/test JavaScript passed syntax checking.
- Static network scan found Tijori web/CDN access plus documentation/setup
  links; no unrelated runtime data destination was found.
- Static process-execution scan found shell execution only in the setup wizard
  (`npm install`, Playwright Chromium installation, and `discover.js`).
- The document tool currently has an exact `files.tijorifinance.com` hostname
  check.
- Git history showed 65 commits across one effective maintainer identity and no
  tags in the audited checkout.
- `npm audit` reported 122 production dependencies and the six vulnerabilities
  described above.
- The isolated checkout remained clean after the audit.

## 11. Next Step

The Jarvis-owned models, five-capability gateway, offline `TijoriMcpAdapter`,
hardened local stdio transport, repository protocol, in-memory adapter, and
DuckDB schema-v4 cache are now complete and validated offline. The adapter uses
an exact five-tool allow-list, sanitized failure translation, bounded synthetic
response contracts, and deterministic provider-standardized evidence
normalization. The transport adds pinned runtime/entrypoint hashes, owner-only
session validation, bounded MCP JSON-RPC, environment isolation, timeout, and
process-group cleanup. It has only executed a synthetic Jarvis-owned fixture;
it contains no login or session-creation flow and has made no network call.

The next Tijori-specific step is a minimal pinned local fork compatible with
the completed transport. That step must not begin until the remaining
dependency, session, data-rights, and approval gates above are satisfied.
Authentication and live provider calls remain separately blocked and opt-in.
