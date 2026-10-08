"""Fixed-origin GET-only Zotero client; complete, version-consistent snapshots."""
import asyncio
import hashlib
import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from .config import Settings

ORIGIN = "https://api.zotero.org"
KEY = re.compile(r"[A-Z0-9]{8}\Z")

class DataError(Exception):
    def __init__(self, code, message, retry_after=None):
        self.code, self.message, self.retry_after = code, message, retry_after

    def result(self):
        result = {"error": self.code, "message": self.message, "complete": False}
        if self.retry_after is not None:
            result["retry_after_seconds"] = self.retry_after
        return result

def has_write_access(access):
    return isinstance(access, dict) and (access.get("write") is True or any(has_write_access(v) for v in access.values()))

def normalize_title(value):
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", value).casefold()).split())

def normalize_doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I).casefold()

def summarize(row):
    d = row["data"]
    creators = [c.get("name") or " ".join(filter(None, (c.get("firstName"), c.get("lastName")))) for c in d.get("creators", [])]
    date = d.get("date", "")
    year = re.search(r"\b(\d{4})\b", date)
    return {"key": row["key"], "title": d.get("title", ""), "creators": creators,
            "year": year.group(1) if year else None, "date": date, "item_type": d.get("itemType"),
            "doi": d.get("DOI", ""), "publication": d.get("publicationTitle", d.get("proceedingsTitle", "")),
            "abstract": d.get("abstractNote", ""), "tags": [t.get("tag", "") for t in d.get("tags", [])],
            "collections": d.get("collections", [])}

class Zotero:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s, self.http = settings, client
        self.not_before, self.cache, self.cache_timer = 0.0, None, None
        self.lock = asyncio.Lock()

    async def get(self, path, params=None):
        if not self.s.data_ready:
            raise DataError("SETUP_REQUIRED", "Configure a read-only ZOTERO_API_KEY and an allowed collection in server environment.")
        if not re.fullmatch(r"/(?:keys/current|(?:users|groups)/[0-9]+/collections(?:/[A-Z0-9]{8}/items/top)?)", path):
            raise DataError("INVALID_PATH", "Endpoint not permitted")
        remaining = self.not_before - time.monotonic()
        if remaining > 0:
            raise DataError("API_BACKOFF", "Zotero requested a pause", int(remaining) + 1)
        try:
            # Stream and cap decoded bytes before accumulating an upstream body.
            async with self.http.stream("GET", ORIGIN + path, params=params,
                    headers={"Zotero-API-Key": self.s.zotero_key, "Zotero-API-Version": "3", "Accept": "application/json"}) as r:
                delay = 0
                for h in ("Backoff", "Retry-After"):
                    raw = r.headers.get(h, "0")
                    try:
                        delay = max(delay, max(0, int(raw)))
                    except ValueError:
                        try:
                            delay = max(delay, max(0, int((parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())))
                        except (ValueError, TypeError, OverflowError):
                            pass
                if delay:
                    self.not_before = time.monotonic() + min(delay, 86400)
                if r.status_code in (401, 403):
                    raise DataError("ZOTERO_ACCESS_DENIED", "Invalid key or insufficient read access")
                if r.status_code == 429 or r.status_code >= 500:
                    raise DataError("ZOTERO_RETRY_LATER", "Zotero is rate-limited or unavailable", delay or 30)
                if r.status_code != 200:
                    raise DataError("ZOTERO_REQUEST_FAILED", f"Zotero returned HTTP {r.status_code}; redirects are not followed")
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > 8_000_000:
                        raise DataError("RESPONSE_TOO_LARGE", "Upstream response exceeds the safety limit")
                    chunks.append(chunk)
                import json
                try:
                    return json.loads(b"".join(chunks)), r.headers
                except (ValueError, UnicodeError):
                    raise DataError("INVALID_UPSTREAM_DATA", "Invalid JSON response") from None
        except httpx.HTTPError:
            raise DataError("UPSTREAM_UNAVAILABLE", "Zotero could not be reached; completeness is unknown") from None

    async def pages(self, path, versions):
        rows, seen, expected = [], set(), None
        for start in range(0, 5000, 100):
            batch, headers = await self.get(path, {"limit": 100, "start": start, "sort": "dateModified", "direction": "asc"})
            total, version = headers.get("Total-Results", ""), headers.get("Last-Modified-Version", "")
            if not total.isdecimal() or not version.isdecimal() or not isinstance(batch, list):
                raise DataError("UNVERIFIED_PAGINATION", "Missing pagination/version evidence")
            total = int(total)
            versions.add(version)
            if len(versions) != 1 or expected is not None and total != expected:
                raise DataError("LIBRARY_CHANGED", "Library changed during reading; restart the snapshot")
            expected = total
            if total > 5000:
                raise DataError("SCOPE_TOO_LARGE", "Limit is 5,000 records per paged resource")
            if len(batch) != min(100, max(0, total - start)):
                raise DataError("INCOMPLETE_PAGE", "Unexpected page length; no complete result will be reported")
            for row in batch:
                k = row.get("key") if isinstance(row, dict) else None
                if not isinstance(k, str) or not KEY.fullmatch(k) or k in seen or not isinstance(row.get("data"), dict):
                    raise DataError("INVALID_UPSTREAM_DATA", "Missing/duplicate record key or malformed record")
                seen.add(k)
                rows.append(row)
            if len(rows) == total:
                return rows
        raise DataError("SCOPE_TOO_LARGE", "Pagination safety limit reached")

    def clear_cache(self):
        self.cache = None
        if self.cache_timer:
            self.cache_timer.cancel()
            self.cache_timer = None

    async def snapshot(self, fresh=False):
        async with self.lock:
            if not fresh and self.cache and time.monotonic() < self.cache[0]:
                return self.cache[1]
            try:
                result = await asyncio.wait_for(self._snapshot(), timeout=35)
            except TimeoutError:
                raise DataError("SNAPSHOT_TIMEOUT", "Snapshot timed out; retry or select a smaller subtree") from None
            self.clear_cache()
            self.cache = (time.monotonic() + 45, result)
            self.cache_timer = asyncio.get_running_loop().call_later(45, self.clear_cache)
            return result

    async def _snapshot(self):
        info, _ = await self.get("/keys/current")
        if not isinstance(info, dict) or not isinstance(info.get("access"), dict):
            raise DataError("INVALID_KEY_METADATA", "Cannot verify API-key privileges")
        access = info["access"]
        if has_write_access(access):
            raise DataError("KEY_MUST_BE_READ_ONLY", "Use a dedicated key without any personal or group write permissions")
        if self.s.library_type == "user":
            uid = str(info.get("userID", ""))
            if not uid.isascii() or not uid.isdecimal() or not access.get("user", {}).get("library"):
                raise DataError("LIBRARY_ACCESS_DENIED", "Key must allow reading its owner's personal library")
            if self.s.library_id and self.s.library_id != uid:
                raise DataError("LIBRARY_MISMATCH", "Configured ID differs from key owner")
            prefix = "/users/" + uid
        else:
            gid, groups = self.s.library_id, access.get("groups", {})
            if not (groups.get(gid, groups.get("all", {})) or {}).get("library"):
                raise DataError("LIBRARY_ACCESS_DENIED", "Key cannot read the configured group")
            prefix = "/groups/" + gid
        versions = set()
        collections = await self.pages(prefix + "/collections", versions)
        roots = [r for r in collections if (r["key"] == self.s.collection_key if self.s.collection_key else r["data"].get("name") == self.s.collection_name)]
        if len(roots) != 1:
            raise DataError("COLLECTION_NOT_UNIQUE", "Allowed collection is missing or name is ambiguous; configure its exact key")
        root = roots[0]["key"]
        allowed = {root}
        while True:
            added = {r["key"] for r in collections if r["data"].get("parentCollection") in allowed} - allowed
            if not added:
                break
            allowed |= added
        if len(allowed) > 100:
            raise DataError("SCOPE_TOO_LARGE", "Limit is 100 collections per subtree")
        scoped = [{"key": r["key"], "name": r["data"].get("name", ""), "parent": r["data"].get("parentCollection") if r["key"] != root else None} for r in collections if r["key"] in allowed]
        items, memberships = {}, {}
        for key in sorted(allowed):
            rows = await self.pages(prefix + "/collections/" + key + "/items/top", versions)
            memberships[key] = []
            for row in rows:
                if row["data"].get("itemType") in {"attachment", "note", "annotation"}:
                    continue
                k = row["key"]
                memberships[key].append(k)
                item = summarize(row)
                item["collections"] = sorted(c for c in item["collections"] if c in allowed)
                item["source_url"] = ORIGIN + prefix + "/items/" + k
                if k in items and items[k] != item:
                    raise DataError("LIBRARY_CHANGED", "Conflicting copies of one item; retry")
                items[k] = item
            if len(items) > 5000:
                raise DataError("SCOPE_TOO_LARGE", "Limit is 5,000 unique references")
        version = next(iter(versions))
        return {"root": root, "collections": scoped, "items": items, "memberships": memberships,
                "library_version": version, "snapshot_id": hashlib.sha256((prefix + root + version).encode()).hexdigest()[:24],
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "complete": True}

    async def invoke(self, name, args):
        if name == "zotero_status" and not self.s.data_ready:
            return {"configured": False, "connected": False, "complete": False, "message": "Zotero credentials/collection scope not configured"}
        snap = await self.snapshot(fresh=args.get("fresh", False))
        ctx = {k: snap[k] for k in ("snapshot_id", "library_version", "retrieved_at")}
        if args.get("snapshot_id") and args["snapshot_id"] != snap["snapshot_id"]:
            raise DataError("SNAPSHOT_CHANGED", "Previous snapshot is no longer current; restart at start=0")
        if name == "zotero_status":
            return {**ctx, "configured": True, "connected": True, "read_only_key_verified": True, "scope_root": snap["root"], "unique_references": len(snap["items"]), "complete": True}
        if name == "list_collections":
            return {**ctx, "collections": snap["collections"], "complete": True}
        if name == "get_item":
            if args["item_key"] not in snap["items"]:
                raise DataError("ITEM_NOT_IN_SCOPE", "Reference is outside the allowed subtree")
            return {**ctx, "item": snap["items"][args["item_key"]], "complete": True, "pdf_availability": "not_checked"}
        root = args.get("collection_key") or snap["root"]
        if root not in snap["memberships"]:
            raise DataError("COLLECTION_NOT_IN_SCOPE", "Collection is outside the allowed subtree")
        selected = {root}
        if args.get("recursive", True):
            while True:
                added = {c["key"] for c in snap["collections"] if c["parent"] in selected} - selected
                if not added:
                    break
                selected |= added
        keys = set().union(*(set(snap["memberships"][c]) for c in selected))
        rows = [snap["items"][k] for k in keys]
        if name == "check_references":
            result = []
            for ref in args["references"]:
                doi, title = normalize_doi(ref.get("doi", "")), normalize_title(ref.get("title", ""))
                by_doi = sorted(r["key"] for r in rows if doi and normalize_doi(r["doi"]) == doi)
                by_title = sorted(r["key"] for r in rows if title and normalize_title(r["title"]) == title)
                matches = by_doi or by_title
                conflict = bool(doi and not by_doi and any(normalize_doi(snap["items"][k]["doi"]) not in {"", doi} for k in by_title))
                result.append({"reference": ref, "status": "conflict" if conflict else "matched" if len(matches) == 1 else "ambiguous" if matches else "not_found",
                               "matched_keys": matches, "match_basis": "doi" if by_doi else "normalized_title" if by_title else None})
            return {**ctx, "results": result, "complete": True, "note": "Exact normalized matching only, within this subtree; not a universal completeness verdict"}
        q = args.get("query", "").casefold().strip()
        rows = [r for r in rows if not q or q in " ".join([r["title"], r["doi"], *r["creators"]]).casefold()]
        rows.sort(key=lambda r: (r["year"] or "", r["title"].casefold(), r["key"]))
        start, limit = args.get("start", 0), args.get("limit", 50)
        page = [{k: v for k, v in r.items() if k != "abstract"} for r in rows[start:start + limit]]
        more = start + len(page) < len(rows)
        return {**ctx, "items": page, "total_unique_references": len(rows), "returned": len(page), "start": start,
                "next_start": start + len(page) if more else None, "has_more": more, "snapshot_complete": True,
                "complete": not more and start == 0, "pdf_availability": "not_checked",
                "note": "Follow next_start with snapshot_id until has_more=false. Complete retrieval is not literature completeness."}
