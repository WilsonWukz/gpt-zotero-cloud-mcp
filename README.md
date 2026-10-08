# Zotero Cloud MCP

A self-hosted, **read-only** bridge between Zotero's cloud library and MCP clients such as ChatGPT. Each operator deploys their own instance with their own credentials. No running Zotero Desktop, local tunnel, OpenAI API key or separate language model is required.

**Experimental v0.1.0 — not independently security-audited.** Independent project; not an official OpenAI or Zotero product. See the [Chinese guide](docs/README.zh-CN.md), [security model](SECURITY.md), and [public release checklist](docs/RELEASING.md). Keep your personal instance private even if you publish the source.

## Tools

| Tool | Purpose |
| --- | --- |
| `zotero_status` | Verify cloud access and a complete scoped snapshot; report upstream key privileges separately from read-only MCP operations. |
| `list_collections` | Return only the configured root collection and its descendants. |
| `list_items` | Search/list references with recursive deduplication and explicit pagination. |
| `get_item` | Retrieve an in-scope reference's metadata and abstract. |
| `check_references` | Compare an explicit list by normalized DOI/title; flag ambiguity and conflicts. |

There are **no import, edit, delete, PDF-download or note-reading tools**, even if you opt in to a write-capable upstream key. A complete API snapshot is not the same as a complete bibliography. PDF availability remains `not_checked` in this release.

## Design

```text
MCP client -- OAuth code + S256 PKCE --> this instance /mcp
                                            |
                                     fixed-origin GET only
                                            |
                                     api.zotero.org v3
```

This is a **single-owner, single-worker** service, not a multi-user SaaS. The MCP gateway uses a dedicated instance passphrase; a separate Zotero API key (preferably read-only) is stored in the hosting environment. Neither the upstream key nor the passphrase is inserted into OAuth tokens or tool results.

The transport implements a small stateless MCP Streamable HTTP JSON-response tool subset for versions `2025-03-26`, `2025-06-18`, and `2025-11-25`. It is not based on an official MCP SDK and does not claim full protocol conformance certification. SSE sessions, legacy HTTP+SSE, stdio, sampling and filesystem access are not implemented. Real ChatGPT interoperability must be tested separately from offline tests.

## Deploy on Render

Deploy your repository or private fork. `render.yaml` defines **one Free Python web service**, no database. The free tier may sleep when idle and uses an ephemeral filesystem; it is not an uptime guarantee.

For direct Web Service creation:

```text
Build: pip install -r requirements.txt && python -m unittest discover -s tests -q
Start: python -m zotero_cloud_mcp
Health check: /healthz
Python: 3.13.5
Instance: Free
Workers: 1
```

For a private repository, authorize Render's **GitHub integration** to clone it. Connecting Render to ChatGPT does not itself grant Render access to private GitHub code. Do not make a repository public merely to work around missing deployment credentials.

### Environment settings

Real values belong only in the host's private environment settings, not the repository, issue tracker, AI chat, URLs or logs.

| Variable | Meaning |
| --- | --- |
| `APP_SECRET` | Independently generated random signing secret, at least 32 characters. |
| `MCP_LOGIN_PASSWORD` | A different random passphrase, at least 32 characters. Enter only on your instance's consent page. |
| `ZOTERO_API_KEY` | Zotero API key with **read** permission for the selected personal/group library. Prefer a separate read-only key. |
| `ZOTERO_ALLOW_WRITE_KEY` | **Optional; defaults to `false`.** Set literal `true` in your private host environment to opt in to a write-capable Zotero API key. It does **not** enable MCP writes. |
| `ZOTERO_COLLECTION_NAME` | Exact allowed root collection name; source code has no default collection. |
| `ZOTERO_COLLECTION_KEY` | Optional exact 8-character key; overrides the name, resolving duplicate names. |
| `ZOTERO_LIBRARY_TYPE` | `user` by default; `group` for a group library. |
| `ZOTERO_LIBRARY_ID` | Personal numeric ID is optional and checked against the key owner; group ID is required in group mode. |
| `PUBLIC_BASE_URL` | HTTPS origin without a path; automatically uses `RENDER_EXTERNAL_URL` on Render. |
| `OAUTH_REDIRECT_URIS` | Optional JSON array of additional **exact** HTTPS callbacks for native MCP clients. |
| `LOCAL_DEV` | `false` in production. Enables loopback HTTP only for local testing. |

Blueprint deployment generates `APP_SECRET` and `MCP_LOGIN_PASSWORD` inside Render. Direct creation must supply them through Environment. A secret's length is not proof of entropy; use a password manager or `secrets.token_urlsafe(32)`, and use different values.

Create a Zotero key at https://www.zotero.org/settings/keys . The secure default rejects keys with **any** personal/group write privilege (`KEY_MUST_BE_READ_ONLY`). To reuse a write-capable key, explicitly set `ZOTERO_ALLOW_WRITE_KEY=true` in your **private Render Environment**. A successful `zotero_status` then reports `read_only_operations: true`, `upstream_key_has_write_access: true`, and `read_only_key_verified: false`. The same five read-only tools, fixed Zotero hostname, GET-only upstream requests, and configured collection subtree restriction remain in place. **Risk:** a leaked write-capable key can still modify or delete Zotero data through other clients or direct Zotero API access. Therefore a dedicated read-only key is strongly recommended for general deployments. Collection isolation is enforced by the gateway, not by Zotero's upstream key permissions.

The service starts safely without credentials: `/healthz` reports the process, `/readyz` reports configuration, and private access stays closed. **Only authenticated `zotero_status` verifies the real Zotero connection.**

## Connect ChatGPT

In the account's available custom-plugin/developer settings, add a remote MCP server:

```text
URL: https://YOUR-SERVICE.onrender.com/mcp
Authentication: OAuth
Registration: Dynamic client registration (DCR)
```

When DCR is selected, do not supply a fixed client ID/secret. This server does not advertise CIMD. Labels and custom-plugin availability can vary by account; consult OpenAI's current documentation.

On the consent page, enter **MCP_LOGIN_PASSWORD**, not a Google/Zotero account password, not `APP_SECRET`, and not `ZOTERO_API_KEY`. Approve read-only access. Then invoke `zotero_status`, `list_collections` and `list_items`.

Supported ChatGPT redirects are `https://chatgpt.com/connector/oauth/<callback_id>` and the documented legacy callback. A request must exactly match its client's registered callback. Additional native MCP clients require explicit callbacks in `OAUTH_REDIRECT_URIS`; arbitrary browser origins are not allowed.

When `list_items` returns `next_start`, continue with that value and the same `snapshot_id` until `has_more=false`. A version change raises an error instead of silently mixing library versions. To check coverage, provide an explicit reference list to `check_references`; inspect `ambiguous` and `conflict` results.

## Development

Python 3.12+; deployment pins Python 3.13.5.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use synthetic data and `httpx.MockTransport`; no live credentials are needed. The application does not auto-load `.env`. Inject environment values through your shell/IDE or host. For local HTTP, use `LOCAL_DEV=true` and `PUBLIC_BASE_URL=http://127.0.0.1:8000`, then `python -m zotero_cloud_mcp`. A cloud ChatGPT session cannot reach plain localhost.

### Source layout

- `config.py`: account-independent deployment settings.
- `auth.py`: bounded single-owner OAuth gateway.
- `zotero.py`: scoped cloud client, snapshot integrity and reference matching.
- `app.py`: MCP tool schemas, routes and security headers.
- `tests/test_service.py`: offline security, protocol, data-integrity and privileged-key opt-in checks.
- `tests/test_oauth_csp.py`: OAuth callback/CSP regression tests.
- `render.yaml`: repeatable free-tier deployment configuration.

## Explicit limits

At most 100 collections in the allowed subtree, 5,000 unique references, and 5,000 records per paged resource. Snapshots verify `Total-Results` and a stable `Last-Modified-Version`, and are cleared from memory after 45 seconds. Missing headers, short pages, duplicates, library changes and backoff requests produce explicit errors.

Access tokens last 8 hours. Public clients receive no refresh token. Confidential clients receive a client-bound, fixed-lifetime 7-day refresh token; it is not rotated. Rotating the passphrase revokes access/refresh tokens; rotating `APP_SECRET` also revokes client registrations. The signed, 10-minute consent form can recover an in-flight authorization after a worker restart when the browser still holds its bound cookie and the owner passphrase is entered. Once approval creates an authorization code, that code is still held in memory and will be lost on restart; restart authorization in that case. This personal deployment tradeoff is **not appropriate for shared multi-user hosting**; see SECURITY.md.

## References

- Zotero Web API: https://www.zotero.org/support/dev/web_api/v3/basics
- Zotero key metadata: https://www.zotero.org/support/dev/web_api/v3/syncing
- OpenAI authentication: https://developers.openai.com/plugins/build/auth
- MCP transport: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Render free tier: https://render.com/docs/free

## License

MIT for this repository's original code. Dependency licenses remain with their respective projects. Review the release checklist and confirm licensing before making a private repository public.
