# Public release gates

Change repository visibility or publish version tags only with explicit owner authorization. A public source repository does not make private deployment data public.

- Confirm MIT licensing, authorship and dependency notices. Preserve third-party licenses where applicable.
- Scan the **entire Git history** for secrets/private data. Do not include personal deployment URLs, emails, library exports, credentials, PDFs, screenshots or authentication logs in demos. Revoke any exposed credentials.
- Run offline tests in a clean environment. Review dependency advisories; add a reviewed transitive lock with hashes/SBOM before a public production release.
- Verify pinned CI actions and read-only permissions. Never run untrusted code with production secrets.
- Test live deployment: anonymous `/mcp` rejected; discovery/DCR/PKCE/consent work; real Zotero reads succeed; write-capable keys fail without their compatibility opt-in; out-of-scope access fails; pagination matches a known count.
- Test every advertised tool with a real ChatGPT client. Offline protocol tests are not an interoperability certification. Consider migration to an official MCP SDK for broader clients.
- Inspect application and hosting-level logs for accidental data exposure. Enable a real private vulnerability-reporting route.
- Review free-tier cold starts and one-worker limitations. A shared multi-user service requires a different identity/session/tenant architecture.
- Publish a version tag and release notes only after the relevant checks actually pass. Do not claim audited security or production readiness without evidence.

## v0.2 management acceptance gates

- Confirm write tools are disabled until host flags, private state storage, target-library permission and write-scoped OAuth are explicitly configured.
- In a separately authorized disposable library/subtree, exercise every enabled action, including batch partial failure, merge child transfer, trash/restore and undo. Do not use a research library as a test fixture without permission.
- Exercise owner review in a real browser; verify anonymous requests reveal no preview and old read tokens cannot write.
- Verify store permissions, durability, retention/export, capacity, and the operational response to an applying/uncertain receipt or store loss. Do not claim automatic retry/recovery.
- Read the explicit exclusions (PDF binaries, account administration, non-JSON import, native atomic merge, universal rollback). Do not advertise unsupported functionality.
