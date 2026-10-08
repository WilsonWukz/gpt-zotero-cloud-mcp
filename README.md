# Zotero Cloud MCP — read and reviewed write access

**v0.2.0 (experimental): actual library-management operations, not just acceptance of a write-capable API key.** A self-hosted, single-owner MCP service for Zotero cloud libraries. Reading works without a running Zotero Desktop. Write mode can create collections and references, edit metadata/tags/memberships, manage notes/annotations, merge duplicate candidates, trash/restore records, and optionally permanently delete eligible objects.

[中文说明](docs/README.zh-CN.md) · [Action examples](docs/MANAGEMENT.md) · [Security model](SECURITY.md) · [Release checklist](docs/RELEASING.md)

**Safe default:** existing installations keep their five read-only tools until additional capabilities are explicitly enabled. Implementing writes does not automatically authorize them in your live library. This is independent software, not an official OpenAI/Zotero product, and has not received an independent security audit. No real library was modified by the automated tests.

## Capability matrix

| Capability | Implementation and boundaries |
| --- | --- |
| Collections | Create subcollections; rename/move descendants with cycle detection; delete empty leaf collections. The configured root is protected. |
| References | Import Zotero JSON records (up to 50 per import action); update valid bibliographic fields. Creator roles such as author/editor remain distinct. |
| Filing and tags | Add/remove memberships, add/remove/rename tags on specified items. Preserve memberships outside the configured subtree; prevent accidental scope escape. |
| Duplicates | Find exact normalized DOI/title candidates. Reviewed conservative merge transfers children, unions tags/memberships/relations, keeps chosen metadata and trashes duplicates last. |
| Trash and deletion | Move records to trash and restore them. Permanent deletion needs a separate host flag, already-trashed records, and no remaining children. |
| Notes and annotations | Read child metadata/content; create/update plain-text notes; create/update annotations using explicit geometry/fields from the actual attachment. |
| Full text and exports | Read Zotero-synced attachment text indexes with character pagination and partial-index reporting. Export selected references as BibTeX, RIS or CSL-JSON. |
| Review/history/undo | Immutable plans, owner-only browser review, one-time application, private receipts. Prepare a separately reviewed inverse for eligible completed changes. |

**Not implemented:** binary PDF upload/download, automatic publisher PDF acquisition, OCR, BibTeX/RIS *import* parsing (import accepts Zotero JSON), native-desktop-equivalent transactional merge, account/group administration, global arbitrary API access, or universal rollback of permanent/partial/uncertain writes. A synced text index is not the original PDF. These are not implied by “read/write”.

## Three different permissions

1. `ZOTERO_API_KEY`: upstream Zotero permissions. It must grant access to the selected personal/group library. A stolen write-capable key can modify the library outside this service.
2. Server configuration: `ZOTERO_ALLOW_WRITE_KEY` only accepts such a key. **Actual write tools require `ZOTERO_ENABLE_WRITES=true` and a private change-store path.**
3. OAuth/owner approval: mutation tools require `zotero:write`, not an old `zotero:read` token. Every exact plan additionally requires approval on the instance's owner-only review page. A model cannot bypass this with `confirmed: true`.

```
ChatGPT / MCP client -> OAuth + per-tool scopes -> scoped library manager
                                      |
                          immutable preview + owner review
                                      |
                  version-guarded POST / DELETE -> Zotero Web API
                                      |
                      private change receipts and backups
```

No API key, instance password, signing secret, or arbitrary URL/method argument is exposed to the model. The transport contacts only `api.zotero.org` and does not follow redirects.

## Tools

The original five tools remain available: `zotero_status`, `list_collections`, `list_items`, `get_item`, `check_references`.

Enabling content reads **or** write mode adds six read tools: `get_item_details`, `list_item_children`, `read_item_content`, `export_references`, `find_duplicates`, `list_trashed_items`.

Write mode adds these six management tools:

| Tool | Effect |
| --- | --- |
| `plan_changes` | Validate actions and save a preview. No Zotero mutation. Returns plan ID, digest and private review URL. |
| `get_change_plan` | Retrieve the caller's plan, state, preview and receipts. |
| `apply_changes` | Execute a browser-approved exact plan, with version preconditions. Actual write/destructive annotation. |
| `cancel_change_plan` | Cancel a pending/approved plan without changing Zotero. |
| `plan_undo` | Prepare a new inverse plan for an eligible completed operation. It requires another review. |
| `list_change_history` | Paginate the caller's private history. |

`plan_changes.actions` supports `create_collection`, `update_collection`, `delete_collection`, `import_items`, `update_items`, `set_memberships`, `edit_tags`, `rename_tag`, `trash_items`, `restore_items`, `delete_items`, `merge_items`, `create_note`, `update_note`, `create_annotation`, and `update_annotation`. See [examples](docs/MANAGEMENT.md).

## Deployment configuration

The existing `render.yaml` still defines one Free Python service, using:

```
Build: pip install -r requirements.txt && python -m unittest discover -s tests -q
Start: python -m zotero_cloud_mcp
Health check: /healthz
Workers: 1
```

Grant Render's GitHub integration access to a private repository rather than publishing it to solve a clone permission issue. This repository update does not provision a disk, change a hosting plan, or enable writes on an existing service.

Set real values only in the hosting environment, never in source, screenshots, issues, chat, or URLs. The application does **not** load `.env` automatically.

| Variable | Meaning / default |
| --- | --- |
| `APP_SECRET` | Independent random signing secret, at least 32 characters. Blueprint can generate it. |
| `MCP_LOGIN_PASSWORD` | A different random instance passphrase, at least 32 characters. Used only on the instance's authorization/review pages. |
| `ZOTERO_API_KEY` | Key with library read permission; target-library write permission is additionally checked for management. |
| `ZOTERO_COLLECTION_NAME` | Exact allowed root collection name. |
| `ZOTERO_COLLECTION_KEY` | Exact root key; overrides name and is recommended for stable configuration. |
| `ZOTERO_LIBRARY_TYPE` | `user` (default) or `group`. |
| `ZOTERO_LIBRARY_ID` | Optional personal ID checked against key owner; required group ID in group mode. |
| `ZOTERO_ALLOW_WRITE_KEY` | `false`; explicit compatibility opt-in for a key possessing any write privileges. Not a write-tool switch. |
| `ZOTERO_ENABLE_CONTENT_READS` | `false`; exposes extended metadata, notes, annotations, full-text index and export tools. Also enabled by write mode. |
| `ZOTERO_ENABLE_WRITES` | `false`; exposes actual management tools and write OAuth scope. Requires the store below. |
| `ZOTERO_STATE_DB` | Absolute path to the private SQLite change store. Required for write mode; no default. **Not** the Zotero desktop database. |
| `ZOTERO_ALLOW_PERMANENT_DELETE` | `false`; separate opt-in for permanent item deletion. Trash/restore do not require it. |
| `PUBLIC_BASE_URL` | HTTPS origin; automatically uses `RENDER_EXTERNAL_URL` on Render. |
| `OAUTH_REDIRECT_URIS` | JSON array of additional exact callbacks. Browser origin/CSP restrictions still apply. |
| `LOCAL_DEV` | `false`; loopback HTTP/cookie exceptions for local testing only. |

Create the private state directory as the service user with mode `0700`; the database is mode `0600`. Example path on an **already provisioned private persistent volume**:

```
ZOTERO_ALLOW_WRITE_KEY=true
ZOTERO_ENABLE_WRITES=true
ZOTERO_STATE_DB=/var/data/zotero-private/changes.sqlite3
ZOTERO_ALLOW_PERMANENT_DELETE=false
```

An ephemeral/free host can lose its database on redeploy. Use durable storage for reliable history; do not assume the free Blueprint provides it. `:memory:` is for synthetic tests, not an operational audit trail. In-flight/uncertain operations are never silently retried. If the store is lost, inspect Zotero before making a new plan; do not blindly repeat a creation/merge.

## Upgrade and connect

Keep the existing signing/password/API-key values unless intentionally rotating them. Deploy the new code only when ready. Choose the optional flags and provision/configure the private store for write mode. Do not enable paid infrastructure implicitly.

In the client's custom MCP settings, use `https://YOUR-HOST/mcp`, OAuth, and dynamic client registration (DCR). Refresh the tool definitions after deployment. **Existing read tokens do not gain write permissions:** reconnect and explicitly grant `zotero:write` for management. The authorization page distinguishes read-only from read/write consent. Per-plan review is still required.

Recommended sequence:

1. Call `zotero_status` and check `capabilities`, `connected` and actual upstream key privileges.
2. Call `plan_changes`; inspect the returned before/after preview.
3. Open its review URL, enter `MCP_LOGIN_PASSWORD`, inspect the exact diff, then approve or cancel. An anonymous GET shows no private plan data.
4. Call `apply_changes` with that plan's ID and digest. Check `status` and every receipt; `partial`/`uncertain` is not success.

`/healthz` proves only that the process is up; `/readyz` proves configuration, not library connectivity. Read all item pages using the same `snapshot_id`; a complete retrieval is not proof that a literature reading list is complete.

## Safety and limits

All objects must be provably in the configured collection subtree (children are checked through ancestors). Root modifications are refused. Shared items may be edited with an explicit preview warning, but trash/delete/merge of records also filed outside the subtree are blocked. Removing the last in-scope membership is rejected; choose a destination in the same action.

Plans allow 20 logical actions and 100 object writes, split into batches of at most 50. Every write pins the library version; object updates also carry individual versions. Any intervening library change requires a new preview/approval. A batch HTTP 200 is checked per object. No automatic write retry on network ambiguity. Merges move children/update the primary before trashing duplicates; they are not transactions and do not perform binary deduplication or global inbound-relation rewriting.

Plans expire after 30 minutes. The private SQLite store retains metadata previews/backups/receipts for seven days (unresolved applying/uncertain plans are retained). Never commit this database. Undo is another reviewed plan and refuses later edits, permanent deletes, partial/uncertain operations, and automatic reversal of a new collection (use reviewed empty-leaf deletion instead). Imported references are trashed, not permanently erased, when undoing a creation.

OAuth remains a single-owner experimental gateway. Access tokens last eight hours; confidential refresh tokens have a fixed seven-day lifetime and no rotation database. Consent proof recovery survives a worker restart, but already-issued OAuth authorization codes are still memory-bound. This is not a multi-tenant identity provider or a full MCP conformance certification.

## Development and verification

```
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use synthetic, versioned HTTP fixtures; no real credentials are required. They cover actual write requests, owner-review login/CSRF, scope enforcement, permissions, conflicts, replay, partial results, uncertain writes, merges, undo, and private-store restart behavior. These are not a substitute for a separately authorized live sandbox-library test or an independent security review.

Source: `management.py` (operations), `management_schema.py` (tool/action contracts), `change_store.py` (private plans/receipts), `review.py` (owner confirmation), `auth.py` (OAuth scopes), `app.py` (MCP), `zotero.py` (legacy reads).

## Protocol references and license

- [Zotero write requests and version conditions](https://www.zotero.org/support/dev/web_api/v3/write_requests)
- [Zotero synchronization/versioning](https://www.zotero.org/support/dev/web_api/v3/syncing)
- [Zotero full-text indexes](https://www.zotero.org/support/dev/web_api/v3/fulltext_content)
- [OpenAI authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI tool/action safeguards](https://developers.openai.com/plugins/plugin-guidelines)

MIT for original repository code; third-party dependencies retain their own licenses. No public release tag or security-audited status is implied by this commit.
