# Public release gates

Repository visibility stays private until the owner explicitly authorizes publication.

- Confirm MIT licensing, authorship and dependency notices. Preserve third-party licenses where applicable.
- Scan the **entire Git history** for secrets/private data. Do not include personal deployment URLs, emails, library exports, credentials, PDFs, screenshots or authentication logs in demos. Revoke any exposed credentials.
- Run offline tests in a clean environment. Review dependency advisories; add a reviewed transitive lock with hashes/SBOM before a public production release.
- Verify pinned CI actions and read-only permissions. Never run untrusted code with production secrets.
- Test live deployment: anonymous `/mcp` rejected; discovery/DCR/PKCE/consent work; real read-only Zotero access succeeds; write-capable key and out-of-scope access fail; pagination matches a known count.
- Test every advertised tool with a real ChatGPT client. Offline protocol tests are not an interoperability certification. Consider migration to an official MCP SDK for broader clients.
- Inspect application and hosting-level logs for accidental data exposure. Enable a real private vulnerability-reporting route.
- Review free-tier cold starts and one-worker limitations. A shared multi-user service requires a different identity/session/tenant architecture.
- Publish a version tag and release notes only after the relevant checks actually pass. Do not claim audited security or production readiness without evidence.
