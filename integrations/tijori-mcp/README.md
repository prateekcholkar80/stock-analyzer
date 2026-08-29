# Tijori MCP Integration Boundary

This directory records the exact upstream source reviewed for a future,
Jarvis-owned minimal fork. It does not contain the upstream implementation,
dependencies, credentials, cookies, or browser session state.

The pinned source identity and file hashes are in `provenance.json`. Any future
fork import must reproduce that commit and Git tree before modifications are
applied. A changed commit, tree, package manifest, lockfile, or licence requires
a new review rather than an automatic update.

Only these read-only tools are approved initially:

- `search_company`
- `resolve_company_ids`
- `get_company_overview`
- `get_financials`
- `get_shareholding`

The upstream `setup.js` and `discover.js` scripts must never be copied into or
executed by the Jarvis runtime. Package installation, browser startup, session
creation, and live provider requests remain separate approval-gated substeps.

Tijori-derived information remains secondary, research-grade evidence. It
cannot become primary or decision-grade evidence without reconciliation to
company or exchange disclosures.
