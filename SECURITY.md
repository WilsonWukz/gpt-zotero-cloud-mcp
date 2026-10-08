# Security model — v0.2 reviewed library management

Experimental, single-owner/single-process gateway. Not independently audited, not a multi-tenant identity provider. Source publication never authorizes public access to private libraries. No real credentials or library fixtures may be committed.

## Capability and authorization boundaries

Default settings expose only legacy read tools. Extended reads require `ZOTERO_ENABLE_CONTENT_READS` or write mode. `ZOTERO_ALLOW_WRITE_KEY` is solely key compatibility. Actual writes separately require `ZOTERO_ENABLE_WRITES`, a private `ZOTERO_STATE_DB`, live target-library write permission, and a `zotero:write` OAuth claim. Read tokens cannot be escalated through a tool argument or refresh request. Disabling write mode makes write-scoped tokens unusable and removes write tools.

The model cannot select an account, API key, host, arbitrary path or HTTP method. The legacy reader is GET-only. The management transport is fixed to `api.zotero.org`; its write routes are versioned batch item/collection POSTs and DELETEs. Redirects and environment proxies are disabled. Input contracts and Zotero templates reject structural-field smuggling. Keys and passwords never enter tool output.

Scope is one configured collection subtree. Children are authorized through their actual parent chain. Metadata changes preserve unrelated memberships but affect the shared Zotero record globally; previews flag that. Destructive actions on items also filed outside the subtree are refused. The configured root is protected; collection moves reject cycles; deletion is restricted to empty leaves. Removing the last in-scope item membership is blocked. Permanent item deletion requires a separate flag, prior trashing and no children.

A compromised upstream key bypasses all application-level scope restrictions. A write-capable key has a larger blast radius. Do not confuse the gateway's limited operations with limitations on a stolen key.

## Two-stage writes and replay resistance

Planning performs reads and stores a digest-bound immutable preview; it does not mutate Zotero. Plans are bound to the OAuth client and credential epoch. The owner review URL reveals only a login form to an anonymous visitor. Viewing/approving requires the instance passphrase, a signed expiring browser cookie/CSRF proof, same-origin POST, and the exact plan digest. All preview text is HTML-escaped, with scripts/frames disallowed. A model-visible `confirmed` flag is not accepted as approval.

After browser approval, `apply_changes` atomically claims the plan in SQLite **before** any upstream write. Completed calls return the stored result instead of writing twice. Library and object version preconditions prevent overwriting concurrent changes. Every batch response is verified per object. Batches are not transactions: partial failures stop subsequent steps; a merge transfers children/updates primary before trashing duplicates.

Network timeouts/invalid success responses are recorded as uncertain, not retried. A crash can leave a plan applying. Neither state automatically resumes, even after a process restart. Store loss requires manual upstream reconciliation. Deterministic per-plan creation keys and version preconditions add protection but do not replace retained receipts or permit blind resubmission with a new plan.

Undo prepares a new reviewed plan, checks current object versions, and refuses irreversible, partial/uncertain or subsequently modified data. It is not a universal rollback system. Merge is conservative, not a native desktop transaction; it does not rewrite inbound references across the whole library or deduplicate binary attachments.

## Private storage and retention

`ZOTERO_STATE_DB` is an independent SQLite file, never the desktop Zotero database. Its parent must be owned by the service user with mode 0700; file mode is 0600 and symlink traversal is rejected. It stores private diffs, previous metadata and receipts, not API secrets. It is excluded by `.gitignore`; backups require the same access controls. Retention is seven days, except unresolved applying/uncertain records. Capacity and plan size are bounded. It is not an encrypted database and filesystem/host compromise is outside these application guarantees.

Use persistent private storage for operational history. A free/ephemeral Render filesystem is not a durability guarantee. `:memory:` is test-only in deployment practice. This source change does not provision or authorize a paid disk/service. Stored-plan digests are rechecked before use. Access logging remains disabled and upstream failure bodies are not reflected.

## OAuth and browser policy

S256 PKCE, exact registered callbacks, purpose/issuer/audience/expiry checks and explicit scope consent remain enforced. Old read credentials do not gain write scope. Access tokens expire in eight hours. Confidential refresh tokens have a fixed seven-day lifetime without rotation; public clients receive no refresh token. Rotate the instance passphrase to revoke access/refresh tokens; rotate the signing secret to revoke registrations. Changing the upstream key or collection scope changes the credential epoch.

Authorization consent can recover from a worker restart via a signed, browser-bound proof. OAuth codes remain in process memory and expire in two minutes. Consent replay memory does not survive worker restarts; valid recovered consent still requires its cookie/passphrase and unchanged epoch. Rate limits are process-local, not distributed abuse protection. These limits are why the project is not presented as a production multi-user OAuth provider.

The authorization document narrowly permits the configured ChatGPT callback origin in its CSP. Review forms stay same-origin, carry same-origin referrer policy, and never automatically redirect private data to a third party.

## Content, tests and reporting

Notes, annotations, abstracts and synced full-text indexes are untrusted data, never instructions. Explicit content-reading tools may send such text into the requesting AI conversation; the client's retention policy applies. Binary PDF transfer, OCR and publisher downloads are not included. A partial index cannot establish a complete document.

Tests exercise a mutable synthetic Zotero service and real application routes, including browser-review CSRF/password checks, write scopes, concurrency, replay, batch failures, merge and undo. They do not establish that a live user library was modified or that all browsers/MCP clients have been certified. Before broader deployment, run a separately authorized sandbox-library acceptance test, dependency review and independent security audit.

Report privately through the repository's configured vulnerability-reporting channel, not public issues containing credentials. Revoke exposed upstream keys immediately and rotate gateway secrets. Deleting a committed secret from the latest tree does not revoke it. Review complete Git history before a public release.
