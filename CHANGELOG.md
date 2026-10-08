# Changelog

## 0.2.0 — reviewed library management (experimental, no release tag)

- Implement actual version-guarded Zotero library writes: collections, bibliographic JSON import, metadata, creator roles, tags, memberships, notes/annotations, conservative duplicate merges, trash/restore, and separately gated permanent deletion.
- Add extended metadata/content reads, synchronized attachment text indexes, duplicate candidate detection, trash listing, and BibTeX/RIS/CSL-JSON exports.
- Introduce per-tool read/write OAuth scopes. Old read tokens cannot mutate data or escalate through refresh.
- Require immutable change previews, owner-only browser confirmation and one-time SQLite plan claims; record per-object outcomes and refuse automatic retries of uncertain writes.
- Add reviewed inverse plans for eligible completed changes, with version checks and explicit exclusions for irreversible/partial outcomes.
- Preserve safe deployment defaults and existing read-only tools; no live credentials, library mutations, paid resources, or deployment enablement are part of this code change.
- Update English/Chinese README, action examples, security model, maintainer rules and environment template.

## 0.1.0 — initial implementation and compatibility fixes

- Five collection-scoped read tools, verified pagination and explicit-list comparison.
- Single-owner OAuth gateway, DCR, PKCE, exact callback binding, browser-origin/CSP fixes and signed consent recovery.
- Explicit `ZOTERO_ALLOW_WRITE_KEY` compatibility flag; operations remained GET-only in 0.1.x.
- Synthetic tests, pinned CI actions and free-tier Render blueprint.

No version in this log implies independent security audit, universal MCP conformance, or a live-library write acceptance test.
