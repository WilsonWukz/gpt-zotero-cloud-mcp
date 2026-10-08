# Management actions and examples

All keys below are **synthetic placeholders**. Retrieve actual scoped keys with the read tools. Examples are arguments to `plan_changes`, not immediate writes. The host flag, write-scoped OAuth token and private store are required; the owner must review the immutable returned plan before `apply_changes`.

## Metadata and tags

```json
{"actions":[
  {"action":"update_items","item_keys":["ABCD2345"],"fields":{"title":"Correct title","date":"2024","creators":[{"creatorType":"author","firstName":"Example","lastName":"Author"},{"creatorType":"editor","name":"Example Editorial Group"}]}},
  {"action":"edit_tags","item_keys":["ABCD2345"],"add":["reviewed"],"remove":["to-read"]}
]}
```

Fields are checked against Zotero's item template. Structural fields (`key`, `version`, `collections`, `parentItem`, `relations`, `deleted`, item type) cannot be smuggled through metadata updates. Creator roles are explicit. Omitted fields are preserved.

## Collections and filing

```json
{"actions":[{"action":"create_collection","name":"Intensive reading","parent_key":"BCDE3456"}]}
```

After creating a collection, read the returned key before referring to it in another plan. To rename/move: `{"action":"update_collection","collection_key":"CDEF4567","name":"Read next","parent_key":"BCDE3456"}`. Delete an empty non-root leaf with `delete_collection` and `collection_key`.

```json
{"actions":[{"action":"set_memberships","item_keys":["ABCD2345"],"add":["CDEF4567"],"remove":["BCDE3456"]}]}
```

This changes only the specified memberships. It does not remove the record from other collections. At least one membership must remain inside the scope.

## Import bibliographic JSON

```json
{"actions":[{"action":"import_items","collection_key":"BCDE3456","items":[{"itemType":"journalArticle","title":"Synthetic example reference","date":"2024","creators":[{"creatorType":"author","firstName":"Example","lastName":"Author"}],"tags":[{"tag":"new","type":0}]}]}]}
```

DOI/title duplicate candidates block import by default. `allow_duplicates: true` is an explicit, visible override, not automatic deduplication. This interface does not resolve a DOI through an external metadata service and does not parse BibTeX/RIS imports.

## Merge candidates

Use `find_duplicates`, examine `get_item_details`, then plan:

```json
{"actions":[{"action":"merge_items","primary_key":"ABCD2345","duplicate_keys":["DEFG5678"],"fields":{"title":"Chosen canonical title"}}]}
```

Up to ten duplicates, matching item types, and exact DOI/title evidence without conflicting nonempty DOIs are required. Primary metadata wins unless fields are explicitly selected. Existing duplicate data remains in trash; child notes/attachments retain keys and move to the primary. Nested PDF annotations remain attached to their PDF. Memberships/tags/relations are combined, including `dc:replaces` aliases. No native-merge equivalence or multi-request atomicity is promised. A failed transfer stops the plan before trashing duplicates.

## Trash, restore, permanently delete

```json
{"actions":[{"action":"trash_items","item_keys":["ABCD2345"]}]}
```

Use `restore_items` with the same shape to restore a provably scoped trashed record. `delete_items` is separate: requires `ZOTERO_ALLOW_PERMANENT_DELETE=true`, already-trashed records, no children and no out-of-scope memberships. There is no automatic inverse for permanent deletion. Nonempty collection deletion is not allowed.

## Notes and annotations

Create a note: `{"action":"create_note","parent_key":"ABCD2345","text":"Plain text; HTML is escaped."}`.

Update a note: `{"action":"update_note","item_key":"EFGH6789","text":"Revised note text"}`.

For annotations, `create_annotation` uses `parent_key` of the actual attachment and `fields` including `annotationType`, `annotationPosition`, `annotationSortIndex` and relevant text/color. Get geometry from the actual PDF; do not invent coordinates. `update_annotation` takes `item_key` and `fields` such as `annotationComment`. Type/fields are validated against Zotero templates. The server can refuse invalid geometry; such a response is never reported as a successful whole batch.

## Read content and export

`list_item_children` lists notes/attachments of a reference, or annotations of an attachment. `read_item_content` takes `item_key`, `start` and `limit` (maximum 20,000 characters), and reports `next_start`, `retrieval_complete` and `index_complete`. It reads Zotero's synchronized text, not a PDF binary.

`export_references` takes explicit `item_keys` and `format` (`bibtex`, `ris`, `csljson`). Exports are returned as text, not public file URLs.

## Review, execute and undo

A plan response contains `plan_id`, `digest`, `review_url`, `preview` and `status`. Open the owner-only review URL and approve. Then:

```json
{"plan_id":"COPY_RETURNED_PLAN_ID","digest":"COPY_RETURNED_DIGEST"}
```

Pass those arguments to `apply_changes`. They are identifiers, not substitutes for human approval. Repeating an already-completed apply returns the stored receipt and sends no new mutation. `applying` or `uncertain` plans must be inspected, not blindly resumed.

`get_change_plan` retrieves receipts; `cancel_change_plan` cancels a pending/approved plan; `list_change_history` paginates history. `plan_undo` prepares a separately approved inverse of eligible completed changes; later object edits or irreversible steps block it. No new signing/password/API key is needed merely to edit library data.
