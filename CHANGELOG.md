# Changelog

## Unreleased

- Added explicit opt-in `ZOTERO_ALLOW_WRITE_KEY=true` to use a write-privileged Zotero key. It remains denied by default.
- Verified the five MCP tools and Zotero HTTP client remain read-only and scoped; upstream requests remain GET-only.
- `zotero_status` now distinguishes upstream key privileges from gateway operation permissions.
- Updated English and Chinese documentation, deployment instructions and security caveats.

## 0.1.0 — experimental initial implementation

- Single-owner OAuth gateway with DCR, S256 PKCE, exact callback and audience binding.
- Scoped Zotero cloud reads, recursive deduplication, verified pagination and explicit-list comparison.
- Five read-only MCP tools; no notes, PDFs or library writes.
- Synthetic offline tests, pinned CI actions, Render blueprint and English/Chinese documentation.
- Not yet a public release or an independently audited implementation.
