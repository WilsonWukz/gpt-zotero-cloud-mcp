# Security model and reporting

**Experimental personal-use gateway, not independently audited.** One owner and one process per deployment. Do not host unrelated users on one instance. Before offering a shared service, replace the gateway with a reviewed identity provider and durable tenant-isolated credential/session storage.

## Enforced boundaries

All MCP operations require authorization before tool discovery or execution. The model cannot choose account IDs, keys, hosts or arbitrary API paths. Upstream traffic uses only GET to `https://api.zotero.org`; redirects and environment proxies are disabled. Keys with any write privilege are rejected. Only a configured collection subtree is returned; unrelated collection names, notes and attachments are not returned. Public pages do not display the configured collection name.

The server temporarily reads collection metadata to resolve the permitted subtree. Zotero key permissions may still grant whole-library access: compromise of the upstream key bypasses our application-level subtree restriction. Use a dedicated, least-privileged key and keep it private.

## OAuth

Authorization code with mandatory S256 PKCE, exact registered redirects, explicit owner consent, per-request browser cookie binding, bounded request/state storage, issuer/audience/scope/purpose/expiry checks. No upstream key is placed in a token. DCR registration metadata is signed and survives ephemeral-host restarts.

Access: 8 hours. Confidential-client refresh: 7-day **absolute** lifetime, requiring the registered client's secret. No public-client refresh tokens. Confidential refresh tokens are not rotated and there is no per-token revocation database; these are explicit personal-use limitations. Rotate `MCP_LOGIN_PASSWORD` to revoke all access/refresh tokens. Changing data credentials or scope also revokes them. Rotate `APP_SECRET` to revoke registrations too.

Pending sign-ins expire after 10 minutes; codes expire after 2 minutes and are consumed once. Expired state is pruned on OAuth requests. A signed, 10-minute browser-bound consent proof can recover pending authorization after a worker restart. Consent replay is blocked only within the active worker; across restarts a still-valid consent proof can be submitted again only with its browser cookie and the owner passphrase. Already-issued authorization codes remain in memory and are invalidated by a restart; redeemed codes cannot be replayed across restarts. This is an experimental single-owner tradeoff, not a durable distributed OAuth state store. **Do not increase worker/process count.** Global in-memory rate limits reset on restart and are not distributed abuse protection. Provider-level limits are recommended.

## Secrets, data and logs

Use distinct cryptographically random signing/passphrase values, each at least 32 characters. Length is not entropy. Never reuse an account password. Put secrets only in the host's private environment or secret manager, never source, test fixtures, public issues, URLs or model-visible parameters.

Application access logging is disabled; HTTP debug logging is suppressed. Upstream error bodies are not returned. The hosting provider may retain infrastructure/request logs under its own policy. Do not enable debug logging on a live instance.

No telemetry SDK or persistent library database. Scoped metadata is cached in process and cleared after 45 seconds. Requested metadata/abstracts enter the requesting AI client's conversation and follow that client's retention policy. Library text is untrusted data and may contain prompt injection; never treat it as instructions or enable unrelated tools because of it.

HTTP request bodies, OAuth state, response bytes, snapshot size and timeouts are bounded. This does not eliminate denial-of-service risk. Full dependency locking, external protocol interoperability tests, and an independent security review remain public production-release gates.

## Reporting and exposure response

Before public release, enable GitHub private vulnerability reporting or publish a real private security contact. Do not invent a contact address or post exploit details/credentials in public issues. Revoke an exposed Zotero key immediately, rotate gateway secrets, and inspect Git history and provider logs. Removing a file from the latest commit is not credential revocation.
