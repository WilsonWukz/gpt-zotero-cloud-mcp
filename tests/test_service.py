"""Synthetic offline fixtures. No account, usable credential, or real network."""
import base64
from dataclasses import replace
import hashlib
import os
from unittest.mock import patch
import json
import re
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from starlette.testclient import TestClient
from zotero_cloud_mcp.app import create_app
from zotero_cloud_mcp.config import Settings, SCOPE

ROOT, CHILD, OUTSIDE = "ROOT0001", "SUBC0001", "OTHR0001"
CALLBACK = "https://chatgpt.com/connector/oauth/synthetic_callback"
VERIFIER = "test-only-pkce-verifier-000000000000000000000000000000000000"
PASSWORD = "test-only-owner-passphrase-00000000000000000000000000000000"

def collection(key, name, parent=False):
    return {"key": key, "data": {"key": key, "name": name, "parentCollection": parent}}

def item(i, cols=None):
    k = f"I{i:07d}"
    return {"key": k, "data": {"key": k, "title": f"Synthetic reference {i}", "date": "2020-01-01", "itemType": "journalArticle", "DOI": f"10.0000/example.{i}",
        "creators": [{"firstName": "Test", "lastName": "Author"}], "collections": cols or [ROOT], "abstractNote": "Synthetic abstract", "tags": []}}

class Upstream:
    def __init__(self):
        self.collections = [collection(ROOT, "Example Collection"), collection(CHILD, "Subcollection", ROOT), collection(OUTSIDE, "Unrelated collection")]
        self.rows = {ROOT: [item(i) for i in range(205)], CHILD: [item(0), item(900, [CHILD])]}
        self.requests = []
        self.write = self.short = self.missing = self.changed = self.duplicate = self.group = self.backoff = False
        self.library_read = True
        self.status = 200
        self.secret_echo = ""

    def __call__(self, r):
        self.requests.append(r)
        assert r.method == "GET"
        assert r.url.host == "api.zotero.org"
        assert "key=" not in str(r.url)
        assert r.headers.get("zotero-api-version") == "3"
        if self.status != 200:
            return httpx.Response(self.status, text=self.secret_echo, headers={"Retry-After": "3"})
        if r.url.path == "/keys/current":
            return httpx.Response(200, json={"userID": 12345, "access": {"user": {"library": self.library_read, "write": self.write}, "groups": {"54321": {"library": self.group, "write": False}}}})
        start = int(r.url.params.get("start", 0))
        rows = self.collections if r.url.path.endswith("/collections") else self.rows.get(r.url.path.split("/")[-3], [])
        batch = rows[start:start + 100]
        if self.short and start == 100:
            batch = batch[:-1]
        if self.duplicate and start == 100:
            batch = [rows[0]] + batch[1:]
        h = {"Total-Results": str(len(rows)), "Last-Modified-Version": "8" if self.changed and start else "7"}
        if self.missing:
            h.pop("Total-Results")
        if self.backoff:
            h["Backoff"] = "5"
        return httpx.Response(200, json=batch, headers=h)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings(base_url="https://mcp.example.test", app_secret="test-only-signing-secret-00000000000000000000000000000000", login_password=PASSWORD,
                          zotero_key="SYNTHETIC-NOT-A-REAL-ZOTERO-KEY", collection_name="Example Collection")
        self.up = Upstream()
        self.app = create_app(self.s, httpx.MockTransport(self.up))
        self.c = TestClient(self.app, base_url=self.s.base_url)
        self.c.__enter__()
        self.auth = self.app.state.auth
        self.meta = {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "client_secret_post", "client_name": "Synthetic client"}
        self.cid = self.auth.sign("client", self.meta)
        self.token = self.auth.issue_tokens(self.cid, self.meta)["access_token"]

    def tearDown(self):
        self.c.__exit__(None, None, None)

    def rpc(self, name, args=None, token=None):
        return self.c.post("/mcp", headers={"Authorization": "Bearer " + (token or self.token), "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})

    def payload(self, name, args=None):
        r = self.rpc(name, args)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["result"]["structuredContent"]

    def begin_login(self, method="client_secret_post"):
        r = self.c.post("/oauth/register", json={**self.meta, "token_endpoint_auth_method": method})
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
        params = {"client_id": d["client_id"], "response_type": "code", "redirect_uri": CALLBACK, "scope": SCOPE, "state": "test-state", "resource": self.s.resource,
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        page = self.c.get("/oauth/authorize", params=params)
        self.assertEqual(page.status_code, 200, page.text)
        # A normal browser POST from this HTML page must retain its real Origin.
        self.assertEqual(page.headers.get("referrer-policy"), "same-origin")
        rid = re.search(r'name="request_id" value="([^"]+)"', page.text)[1]
        self.last_consent_token = re.search(r'name="consent_token" value="([^"]+)"', page.text)[1]
        return d, params, rid

    def login_code(self, method="client_secret_post"):
        d, _, rid = self.begin_login(method)
        r = self.c.post("/oauth/approve", data={"request_id": rid, "password": PASSWORD, "decision": "allow"}, headers={"Origin": self.s.base_url}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        q = parse_qs(urlsplit(r.headers["location"]).query)
        self.assertEqual(q["state"], ["test-state"])
        data = {"client_id": d["client_id"], "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": CALLBACK,
                "code_verifier": VERIFIER, "resource": self.s.resource}
        if "client_secret" in d:
            data["client_secret"] = d["client_secret"]
        return d, data

    def test_anonymous_access_denied_before_upstream(self):
        for m in ("GET", "POST", "DELETE"):
            r = self.c.request(m, "/mcp")
            self.assertEqual(r.status_code, 401)
            self.assertIn("resource_metadata", r.headers["www-authenticate"])
        self.assertEqual(self.up.requests, [])

    def test_health_not_upstream_verification(self):
        r = self.c.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.s.zotero_key, r.text)
        self.assertFalse(self.c.get("/readyz").json()["upstream_verified"])
        self.assertEqual(self.up.requests, [])

    def test_unconfigured_gateway_fails_closed(self):
        with TestClient(create_app(replace(self.s, app_secret="")), base_url=self.s.base_url) as c:
            self.assertEqual(c.get("/readyz").status_code, 503)
            self.assertEqual(c.post("/oauth/register", json=self.meta).status_code, 503)
            self.assertEqual(c.get("/mcp").status_code, 503)

    def test_metadata_canonical_resource_and_pkce(self):
        self.assertEqual(self.c.get("/.well-known/oauth-protected-resource/mcp").json()["resource"], self.s.resource)
        meta = self.c.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertNotIn("client_id_metadata_document_supported", meta)

    def test_code_exchange_and_replay_rejection(self):
        _, data = self.login_code()
        first = self.c.post("/oauth/token", data=data)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertNotIn(self.s.zotero_key, first.text)
        self.assertEqual(self.c.post("/oauth/token", data=data).status_code, 400)
        self.assertFalse(self.rpc("zotero_status", token=first.json()["access_token"]).json()["result"]["isError"])

    def test_pkce_mismatch(self):
        _, d = self.login_code()
        self.assertEqual(self.c.post("/oauth/token", data={**d, "code_verifier": "B" * 64}).status_code, 400)

    def test_resource_mismatch(self):
        _, d = self.login_code()
        self.assertEqual(self.c.post("/oauth/token", data={**d, "resource": "https://other.example/mcp"}).status_code, 400)

    def test_redirect_allowlist_and_exact_binding(self):
        for uri in ("https://attacker.example/callback", "https://chatgpt.com.attacker.example/connector/oauth/x", "http://chatgpt.com/connector/oauth/x", "http://[bad"):
            self.assertEqual(self.c.post("/oauth/register", json={**self.meta, "redirect_uris": [uri]}).status_code, 400)
        _, p, _ = self.begin_login()
        self.assertEqual(self.c.get("/oauth/authorize", params={**p, "redirect_uri": "https://chatgpt.com/connector/oauth/different"}).status_code, 400)

    def test_referrer_policy_only_relaxed_on_oauth_authorize(self):
        self.assertEqual(self.c.get("/healthz").headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(self.c.get("/readyz").headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(self.c.get("/privacy").headers.get("referrer-policy"), "no-referrer")

    def test_approval_requires_password_cookie_origin(self):
        _, _, rid = self.begin_login()
        d = {"request_id": rid, "decision": "allow", "password": "wrong"}
        self.assertEqual(self.c.post("/oauth/approve", data=d).status_code, 401)
        d["password"] = PASSWORD
        self.assertEqual(self.c.post("/oauth/approve", data=d, headers={"Origin": "https://attacker.example"}).status_code, 403)
        self.assertEqual(self.c.post("/oauth/approve", data=d, headers={"Origin": "null"}).status_code, 403)
        self.c.cookies.clear()
        self.assertEqual(self.c.post("/oauth/approve", data=d).status_code, 403)

    def test_login_rejections_distinguish_session_and_cookie_failures(self):
        _, _, expired = self.begin_login()
        self.auth.pending.pop(expired)
        data = {"request_id": expired, "decision": "allow", "password": PASSWORD}
        r = self.c.post("/oauth/approve", data=data, headers={"Origin": self.s.base_url})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error_description"], "authorization_state_expired_or_restarted")

        _, _, rid = self.begin_login()
        data["request_id"] = rid
        self.c.cookies.clear()
        r = self.c.post("/oauth/approve", data=data, headers={"Origin": self.s.base_url})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error_description"], "browser_cookie_missing")
        self.c.cookies.set(self.auth.cookie_name(rid), "invalid_cookie_proof")
        r = self.c.post("/oauth/approve", data=data, headers={"Origin": self.s.base_url})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error_description"], "browser_cookie_mismatch")

    def test_parallel_login_tabs_do_not_overwrite_each_other(self):
        _, _, first = self.begin_login()
        _, _, second = self.begin_login()
        self.assertNotEqual(first, second)
        for rid in (first, second):
            r = self.c.post("/oauth/approve", data={"request_id": rid, "decision": "allow", "password": PASSWORD},
                            headers={"Origin": self.s.base_url}, follow_redirects=False)
            self.assertEqual(r.status_code, 303, r.text)

    def test_signed_consent_recovers_after_worker_restart(self):
        _, _, rid = self.begin_login()
        proof = self.last_consent_token
        cookie_name = self.auth.cookie_name(rid)
        cookie = self.c.cookies.get(cookie_name)
        self.assertTrue(cookie)
        # New app process: pending authorization state is completely absent.
        with TestClient(create_app(self.s, httpx.MockTransport(self.up)), base_url=self.s.base_url) as fresh:
            self.assertNotIn(rid, fresh.app.state.auth.pending)
            fresh.cookies.set(cookie_name, cookie)
            result = fresh.post("/oauth/approve", data={
                "request_id": rid, "consent_token": proof,
                "decision": "allow", "password": PASSWORD},
                headers={"Origin": self.s.base_url}, follow_redirects=False)
            self.assertEqual(result.status_code, 303, result.text)
            self.assertTrue(result.headers["location"].startswith(CALLBACK + "?"))
            # The same proof cannot be submitted twice to this worker.
            fresh.cookies.set(cookie_name, cookie)
            replay = fresh.post("/oauth/approve", data={
                "request_id": rid, "consent_token": proof,
                "decision": "allow", "password": PASSWORD},
                headers={"Origin": self.s.base_url}, follow_redirects=False)
            self.assertEqual(replay.status_code, 403)
            self.assertEqual(replay.json()["error_description"], "authorization_already_submitted")

    def test_signed_consent_recovery_rejects_tampering_missing_cookie_and_expiry(self):
        _, _, rid = self.begin_login()
        proof = self.last_consent_token
        self.auth.pending.pop(rid)
        valid = {"request_id": rid, "consent_token": proof, "decision": "allow", "password": PASSWORD}
        tampered = valid.copy()
        header, payload, signature = proof.split(".")
        tampered["consent_token"] = ".".join((header,
            ("A" if payload[0] != "A" else "B") + payload[1:], signature))
        bad = self.c.post("/oauth/approve", data=tampered, headers={"Origin": self.s.base_url})
        self.assertEqual(bad.status_code, 403, bad.text)
        self.assertEqual(bad.json()["error_description"], "authorization_proof_invalid_or_expired")

        self.c.cookies.clear()
        missing = self.c.post("/oauth/approve", data=valid, headers={"Origin": self.s.base_url})
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json()["error_description"], "browser_cookie_missing")

        expired_proof = self.auth.sign("consent", {"rid": rid, **{
            "client_id": self.cid, "redirect_uri": CALLBACK, "state": "test-state",
            "challenge": "A" * 43, "cookie_hash": "not-a-real-cookie"}},
            -30)
        expired = self.c.post("/oauth/approve", data={**valid, "consent_token": expired_proof},
                              headers={"Origin": self.s.base_url})
        self.assertEqual(expired.status_code, 403)
        self.assertEqual(expired.json()["error_description"], "authorization_proof_invalid_or_expired")

    def test_explicit_denial(self):
        _, _, rid = self.begin_login()
        r = self.c.post("/oauth/approve", data={"request_id": rid, "decision": "deny"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=access_denied", r.headers["location"])
        self.assertEqual(self.auth.codes, {})

    def test_public_client_no_refresh(self):
        _, d = self.login_code("none")
        r = self.c.post("/oauth/token", data=d)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("refresh_token", r.json())

    def test_refresh_is_client_bound_and_non_sliding(self):
        _, d = self.login_code()
        token = self.c.post("/oauth/token", data=d).json()["refresh_token"]
        r = {"grant_type": "refresh_token", "client_id": d["client_id"], "client_secret": d["client_secret"], "resource": self.s.resource, "refresh_token": token}
        self.assertEqual(self.c.post("/oauth/token", data=r).json()["refresh_token"], token)
        self.assertEqual(self.c.post("/oauth/token", data={**r, "client_secret": "错误"}).status_code, 401)

    def test_basic_auth_exchange(self):
        d, form = self.login_code("client_secret_basic")
        form.pop("client_secret")
        header = base64.b64encode((d["client_id"] + ":" + d["client_secret"]).encode()).decode()
        self.assertEqual(self.c.post("/oauth/token", data=form, headers={"Authorization": "Basic " + header}).status_code, 200)

    def test_password_and_scope_rotation_revoke_tokens(self):
        for changed in (replace(self.s, login_password=PASSWORD + "rotated"), replace(self.s, collection_name="Another collection")):
            with TestClient(create_app(changed), base_url=self.s.base_url) as c:
                self.assertEqual(c.post("/mcp", headers={"Authorization": "Bearer " + self.token}).status_code, 401)

    def test_restart_keeps_clients_not_pending_codes(self):
        _, d = self.login_code()
        with TestClient(create_app(self.s), base_url=self.s.base_url) as c:
            self.assertEqual(c.post("/oauth/token", data=d).status_code, 400)
            self.assertEqual(c.get("/mcp", headers={"Authorization": "Bearer " + self.token}).status_code, 405)

    def test_expiry_audience_and_purpose_enforced(self):
        claims = {"sub": "owner", "scope": SCOPE, "epoch": self.auth.epoch, "client_id": self.cid}
        expired = self.auth.sign("access", claims, -10)
        wrong = jwt.encode({**claims, "kind": "access", "iss": self.s.base_url, "aud": "https://wrong.example", "iat": int(time.time()), "exp": int(time.time()) + 30}, self.s.app_secret, algorithm="HS256")
        for token in (expired, wrong, self.cid, "not-a-token"):
            self.assertEqual(self.rpc("zotero_status", token=token).status_code, 401)

    def test_bad_registration_data(self):
        for data in ([], {**self.meta, "token_endpoint_auth_method": []}, {**self.meta, "client_name": {}}):
            self.assertEqual(self.c.post("/oauth/register", json=data).status_code, 400)

    def test_outside_collection_names_not_returned(self):
        p = self.payload("list_collections")
        self.assertEqual({c["key"] for c in p["collections"]}, {ROOT, CHILD})
        self.assertNotIn("Unrelated", json.dumps(p))

    def test_pagination_dedup_and_completeness(self):
        p = self.payload("list_items", {"limit": 100})
        self.assertEqual(p["total_unique_references"], 206)
        self.assertTrue(p["has_more"])
        self.assertFalse(p["complete"])
        p2 = self.payload("list_items", {"start": 100, "limit": 100, "snapshot_id": p["snapshot_id"]})
        p3 = self.payload("list_items", {"start": 200, "limit": 100, "snapshot_id": p["snapshot_id"]})
        self.assertEqual(len({r["key"] for q in (p, p2, p3) for r in q["items"]}), 206)
        self.assertFalse(p3["has_more"])
        self.assertTrue(any(r.url.params.get("start") == "200" for r in self.up.requests))

    def test_nonrecursive_count(self):
        self.assertEqual(self.payload("list_items", {"recursive": False})["total_unique_references"], 205)

    def test_incomplete_pagination_fails(self):
        for attr, code in (("short", "INCOMPLETE_PAGE"), ("missing", "UNVERIFIED_PAGINATION"), ("changed", "LIBRARY_CHANGED"), ("duplicate", "INVALID_UPSTREAM_DATA")):
            setattr(self.up, attr, True)
            p = self.payload("list_items", {"fresh": True})
            self.assertEqual(p["error"], code)
            self.assertFalse(p["complete"])
            setattr(self.up, attr, False)

    def test_outside_item_and_collection_rejected(self):
        self.assertEqual(self.payload("get_item", {"item_key": "I9999999"})["error"], "ITEM_NOT_IN_SCOPE")
        self.assertEqual(self.payload("list_items", {"collection_key": OUTSIDE})["error"], "COLLECTION_NOT_IN_SCOPE")

    def test_ambiguous_collection_name_fails(self):
        self.up.collections.append(collection("DUPE0001", "Example Collection"))
        self.assertEqual(self.payload("list_collections")["error"], "COLLECTION_NOT_UNIQUE")

    def test_write_privileged_key_rejected_without_explicit_opt_in(self):
        self.up.write = True
        result = self.payload("zotero_status")
        self.assertEqual(result["error"], "KEY_MUST_BE_READ_ONLY")
        self.assertFalse(result["complete"])
        self.assertEqual(len(self.up.requests), 1)

    def test_explicit_opt_in_allows_write_capable_key_without_any_write_operations(self):
        self.up.write = True
        settings = replace(self.s, allow_write_key=True)
        with TestClient(create_app(settings, httpx.MockTransport(self.up)), base_url=settings.base_url) as c:
            auth = c.app.state.auth
            cid = auth.sign("client", self.meta)
            token = auth.issue_tokens(cid, self.meta)["access_token"]
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
            calls = [("zotero_status", {}), ("list_collections", {}), ("list_items", {"limit": 10}),
                     ("get_item", {"item_key": "I0000001"}),
                     ("check_references", {"references": [{"title": "Synthetic reference 1"}]})]
            for name, args in calls:
                res = c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                             "method": "tools/call", "params": {"name": name, "arguments": args}})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertFalse(res.json()["result"]["isError"], (name, res.text))
                data = res.json()["result"]["structuredContent"]
                if name == "zotero_status":
                    self.assertTrue(data["read_only_operations"])
                    self.assertTrue(data["upstream_key_has_write_access"])
                    self.assertFalse(data["read_only_key_verified"])
                    self.assertEqual(data["unique_references"], 206)
            tools = c.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2,
                           "method": "tools/list"}).json()["result"]["tools"]
            self.assertEqual(len(tools), 5)
            self.assertTrue(all(x["annotations"]["readOnlyHint"] for x in tools))
            self.assertTrue(all(x["annotations"]["destructiveHint"] is False for x in tools))
        self.assertGreater(len(self.up.requests), 1)
        self.assertTrue(all(req.method == "GET" and req.url.host == "api.zotero.org" for req in self.up.requests))

    def test_opt_in_does_not_bypass_missing_read_permission(self):
        self.up.write = True
        self.up.library_read = False
        settings = replace(self.s, allow_write_key=True)
        with TestClient(create_app(settings, httpx.MockTransport(self.up)), base_url=settings.base_url) as c:
            auth = c.app.state.auth
            cid = auth.sign("client", self.meta)
            token = auth.issue_tokens(cid, self.meta)["access_token"]
            res = c.post("/mcp", headers={"Authorization": "Bearer " + token,
                           "Accept": "application/json, text/event-stream"}, json={
                           "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "zotero_status", "arguments": {}}})
            self.assertTrue(res.json()["result"]["isError"])
            self.assertEqual(res.json()["result"]["structuredContent"]["error"], "LIBRARY_ACCESS_DENIED")

    def test_write_key_opt_in_env_requires_literal_true(self):
        for value, expected in (("false", False), ("TRUE", True), (" true ", True),
                                ("1", False), ("yes", False), ("", False)):
            with patch.dict(os.environ, {"ZOTERO_ALLOW_WRITE_KEY": value}):
                self.assertIs(Settings.from_env().allow_write_key, expected)

    def test_upstream_errors_do_not_leak_body(self):
        self.up.status, self.up.secret_echo = 403, self.s.zotero_key
        r = self.rpc("list_items")
        self.assertNotIn(self.s.zotero_key, r.text)
        self.assertIn("ZOTERO_ACCESS_DENIED", r.text)

    def test_backoff_respected(self):
        self.up.backoff = True
        p = self.payload("list_items")
        self.assertEqual(p["error"], "API_BACKOFF")
        self.assertGreater(p["retry_after_seconds"], 0)

    def test_doi_title_matching_reports_conflicts(self):
        self.up.rows[ROOT].append(item(901))
        self.up.rows[ROOT][-1]["data"]["DOI"] = "10.0000/example.1"
        p = self.payload("check_references", {"references": [{"doi": "https://doi.org/10.0000/EXAMPLE.0"}, {"doi": "10.0000/example.1"}, {"title": "missing"}, {"title": "Synthetic reference 2", "doi": "10.0000/not-the-same"}]})
        self.assertEqual([r["status"] for r in p["results"]], ["matched", "ambiguous", "not_found", "conflict"])

    def test_notes_attachments_and_private_memberships_excluded(self):
        self.up.rows[ROOT].extend([{"key": "NOTE0001", "data": {"itemType": "note", "note": "SECRET NOTE"}}, {"key": "ATTACH01", "data": {"itemType": "attachment", "path": "/private/secret.pdf"}}])
        for row in (self.up.rows[ROOT][0], self.up.rows[CHILD][0]):
            row["data"]["collections"] = [ROOT, OUTSIDE]
        p = self.payload("get_item", {"item_key": "I0000000"})
        self.assertEqual(p["item"]["collections"], [ROOT])
        self.assertEqual(p["pdf_availability"], "not_checked")
        self.assertEqual(self.payload("list_items")["total_unique_references"], 206)

    def test_arbitrary_urls_credentials_and_bad_arguments_denied(self):
        for args in ({"limit": 101}, {"limit": True}, {"query": "x" * 201}, {"collection_key": "../items"}, {"api_key": "value"}, {"url": "https://example.com"}, {"start": -1}):
            self.assertEqual(self.payload("list_items", args)["error"], "INVALID_ARGUMENTS")

    def test_snapshot_id_mismatch(self):
        self.assertEqual(self.payload("list_items", {"snapshot_id": "stale"})["error"], "SNAPSHOT_CHANGED")

    def test_mcp_initialization_and_readonly_tools(self):
        h = {"Authorization": "Bearer " + self.token, "Accept": "application/json, text/event-stream"}
        r = self.c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(r.json()["result"]["protocolVersion"], "2025-11-25")
        r = self.c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(r.json()["result"]["tools"]), 5)
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in r.json()["result"]["tools"]))
        self.assertEqual(self.c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code, 202)

    def test_bad_origin_protocol_and_oversized_request(self):
        h = {"Authorization": "Bearer " + self.token, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.assertEqual(self.c.post("/mcp", headers={**h, "Origin": "https://attacker.example"}).status_code, 403)
        self.assertEqual(self.c.post("/mcp", headers={**h, "MCP-Protocol-Version": "bad"}).status_code, 400)
        self.assertEqual(self.c.post("/mcp", headers=h, content="x" * 70000).status_code, 413)

    def test_group_library_is_configurable(self):
        self.up.group = True
        with TestClient(create_app(replace(self.s, library_type="group", library_id="54321"), httpx.MockTransport(self.up)), base_url=self.s.base_url) as c:
            token = c.app.state.auth.issue_tokens(self.cid, self.meta)["access_token"]
            r = c.post("/mcp", headers={"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_items"}})
            self.assertFalse(r.json()["result"]["isError"], r.text)
            self.assertTrue(any("/groups/54321/" in r.url.path for r in self.up.requests))

    def test_cache_can_be_cleared(self):
        self.payload("list_items")
        self.assertIsNotNone(self.app.state.zotero.cache)
        self.app.state.zotero.clear_cache()
        self.assertIsNone(self.app.state.zotero.cache)

if __name__ == "__main__":
    unittest.main()
