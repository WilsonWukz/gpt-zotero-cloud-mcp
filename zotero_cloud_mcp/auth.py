"""Single-owner OAuth authorization-code gateway, with S256 PKCE and DCR.

Single worker only. Registrations and bearer tokens are signed; in-flight
logins/codes are bounded, short-lived and fail closed on process restart.
This is an experimental personal gateway, not a multi-tenant identity provider.
"""
import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from urllib.parse import parse_qs, unquote_plus, urlencode, urlsplit

import jwt
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from .config import Settings, SCOPE, WRITE_SCOPE

CALLBACK = re.compile(r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]+\Z")
LEGACY_CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}\Z")
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{43}\Z")
ACCESS_SECONDS, REFRESH_SECONDS = 28800, 604800

class OAuthError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status

async def bounded_body(request, limit=65536):
    chunks, length = [], 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > limit:
            raise OAuthError("invalid_request", "Request body too large", 413)
        chunks.append(chunk)
    return b"".join(chunks)

async def form_data(request):
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        raise OAuthError("invalid_request", "Use form-urlencoded", 415)
    try:
        values = parse_qs((await bounded_body(request, 16384)).decode(), keep_blank_values=True, max_num_fields=30)
    except (ValueError, UnicodeError):
        raise OAuthError("invalid_request", "Invalid form") from None
    if any(len(v) != 1 for v in values.values()):
        raise OAuthError("invalid_request", "Duplicate form fields")
    return {k: v[0] for k, v in values.items()}

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class Auth:
    def __init__(self, settings: Settings):
        self.s = settings
        self.pending, self.codes, self.limits = {}, {}, {}
        # Deny reuse of a submitted consent within the running worker.
        # Signed consent forms survive worker restarts; this replay cache does not.
        self.used_consents = {}
        self.cookie_prefix = "zotero-mcp-dev-login" if settings.local_dev else "__Host-zotero-mcp-login"
        # Changing the data scope or any secret revokes access/refresh tokens.
        epoch_data = json.dumps([settings.login_password, settings.zotero_key, settings.collection_name,
                                 settings.collection_key, settings.library_type, settings.library_id])
        self.epoch = hmac.new(settings.app_secret.encode(), epoch_data.encode(), hashlib.sha256).hexdigest()

    @property
    def supported_scopes(self):
        return [SCOPE, WRITE_SCOPE] if self.s.enable_writes else [SCOPE]

    def checked_scope(self, value):
        if not isinstance(value, str):
            raise OAuthError("invalid_scope", "Scope must be a string")
        scopes = set(value.split())
        if not scopes or not scopes <= set(self.supported_scopes):
            raise OAuthError("invalid_scope", "Requested scopes are not enabled")
        return " ".join(x for x in self.supported_scopes if x in scopes)

    def cookie_name(self, request_id):
        # Every OAuth approval page gets its own host-only cookie, so opening a
        # second authorization tab cannot overwrite the first tab's proof.
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise OAuthError("access_denied", "authorization_request_missing_or_invalid", 403)
        return self.cookie_prefix + "-" + request_id[:20]

    def require_ready(self):
        if not self.s.auth_ready:
            raise OAuthError("temporarily_unavailable", "Configure distinct random APP_SECRET and MCP_LOGIN_PASSWORD values (32+ characters) and an HTTPS origin in server environment.", 503)

    def throttle(self, bucket, maximum, window):
        now = time.monotonic()
        self.limits = {k: v for k, v in self.limits.items() if v[0] > now}
        until, count = self.limits.get(bucket, (now + window, 0))
        if count >= maximum or len(self.limits) >= 2048:
            raise OAuthError("temporarily_unavailable", "Too many attempts; retry later", 429)
        self.limits[bucket] = (until, count + 1)

    def sign(self, kind, data, ttl=None):
        now = int(time.time())
        claims = {**data, "kind": kind, "iss": self.s.base_url, "aud": self.s.resource, "iat": now}
        if ttl is not None:
            claims["exp"] = now + ttl
        return jwt.encode(claims, self.s.app_secret, algorithm="HS256")

    def decode(self, token, kind):
        self.require_ready()
        try:
            p = jwt.decode(token, self.s.app_secret, algorithms=["HS256"], issuer=self.s.base_url, audience=self.s.resource,
                           options={"require": ["iss", "aud", "iat", "kind"] + ([] if kind == "client" else ["exp"])})
            if p.get("kind") != kind:
                raise ValueError()
            return p
        except (jwt.PyJWTError, ValueError, TypeError):
            raise OAuthError("invalid_token", "Invalid or expired credential", 401) from None

    def client(self, cid):
        if not isinstance(cid, str) or len(cid) > 6000:
            raise OAuthError("invalid_client", "Unknown client", 401)
        try:
            return self.decode(cid, "client")
        except OAuthError:
            raise OAuthError("invalid_client", "Unknown client", 401) from None

    def client_secret(self, cid):
        return hmac.new(self.s.app_secret.encode(), ("client-secret:" + cid).encode(), hashlib.sha256).hexdigest()

    def check_redirect(self, uri):
        if not isinstance(uri, str) or len(uri) > 2048:
            return False
        try:
            u = urlsplit(uri)
        except ValueError:
            return False
        secure = u.scheme == "https" or (self.s.local_dev and u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1"})
        return bool(secure and u.hostname and not (u.username or u.password or u.fragment)
                    and (CALLBACK.fullmatch(uri) or uri == LEGACY_CALLBACK or uri in self.s.extra_redirects))

    async def metadata(self, request):
        b = self.s.base_url
        return JSONResponse({"issuer": b, "authorization_endpoint": b + "/oauth/authorize", "token_endpoint": b + "/oauth/token",
            "registration_endpoint": b + "/oauth/register", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": self.supported_scopes,
            "authorization_response_iss_parameter_supported": True})

    async def resource_metadata(self, request):
        return JSONResponse({"resource": self.s.resource, "authorization_servers": [self.s.base_url],
            "scopes_supported": self.supported_scopes, "bearer_methods_supported": ["header"], "resource_name": "Zotero Cloud MCP"})

    async def register(self, request):
        self.require_ready()
        self.throttle("registration", 60, 600)
        try:
            data = json.loads(await bounded_body(request, 16384))
        except (ValueError, UnicodeError):
            raise OAuthError("invalid_client_metadata", "Invalid JSON") from None
        if not isinstance(data, dict):
            raise OAuthError("invalid_client_metadata", "Expected an object")
        uris = data.get("redirect_uris", [])
        if not isinstance(uris, list) or not 1 <= len(uris) <= 3 or not all(self.check_redirect(u) for u in uris):
            raise OAuthError("invalid_redirect_uri", "Use ChatGPT callbacks or explicitly configured exact URLs")
        method, name = data.get("token_endpoint_auth_method", "client_secret_basic"), data.get("client_name", "MCP client")
        if not isinstance(method, str) or method not in {"none", "client_secret_post", "client_secret_basic"} or not isinstance(name, str) or len(name) > 100:
            raise OAuthError("invalid_client_metadata", "Unsupported authentication method or client name")
        meta = {"redirect_uris": uris, "token_endpoint_auth_method": method, "client_name": name, "nonce": secrets.token_urlsafe(12)}
        cid = self.sign("client", meta)
        if len(cid) > 6000:
            raise OAuthError("invalid_client_metadata", "Client metadata too large")
        result = {**meta, "client_id": cid, "client_id_issued_at": int(time.time()), "scope": " ".join(self.supported_scopes),
                  "grant_types": ["authorization_code"] + ([] if method == "none" else ["refresh_token"]), "response_types": ["code"]}
        result.pop("nonce")
        if method != "none":
            result.update(client_secret=self.client_secret(cid), client_secret_expires_at=0)
        return JSONResponse(result, status_code=201)

    def prune(self):
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
        self.codes = {k: v for k, v in self.codes.items() if v["expires"] > now}
        self.used_consents = {k: v for k, v in self.used_consents.items() if v > now}
        if len(self.pending) + len(self.codes) + len(self.used_consents) >= 512:
            raise OAuthError("temporarily_unavailable", "Too many pending sign-ins", 429)

    def recover_consent(self, rid, consent_token):
        # The signed, short-lived record is carried in the authorization form.
        # It allows the browser to finish after a Render worker restart without
        # accepting client-provided redirects, PKCE challenges or OAuth state.
        if not consent_token:
            raise OAuthError("access_denied", "authorization_state_expired_or_restarted", 403)
        if not isinstance(consent_token, str) or len(consent_token) > 12000:
            raise OAuthError("access_denied", "authorization_proof_invalid_or_expired", 403)
        try:
            proof = self.decode(consent_token, "consent")
            cid = proof["client_id"]
            c = self.client(cid)
            redirect = proof["redirect_uri"]
            challenge = proof["challenge"]
            state = proof["state"]
            if (proof.get("epoch") != self.epoch or proof.get("rid") != rid or not isinstance(redirect, str)
                    or redirect not in c["redirect_uris"] or not self.check_redirect(redirect)
                    or not isinstance(challenge, str) or not CHALLENGE.fullmatch(challenge)
                    or not isinstance(state, str) or len(state) > 2048):
                raise ValueError("Consent claim invalid")
            return {"client_id": cid, "redirect_uri": redirect, "state": state,
                    "challenge": challenge, "cookie_hash": proof["cookie_hash"],
                    "scope": self.checked_scope(proof.get("scope", SCOPE)), "expires": proof["exp"]}
        except (OAuthError, KeyError, TypeError, ValueError):
            raise OAuthError("access_denied", "authorization_proof_invalid_or_expired", 403) from None

    async def authorize(self, request):
        self.require_ready()
        self.throttle("authorize", 120, 600)
        self.prune()
        q = request.query_params
        if any(len(q.getlist(k)) != 1 for k in q.keys()):
            raise OAuthError("invalid_request", "Duplicate query parameters")
        cid, redirect = q.get("client_id", ""), q.get("redirect_uri", "")
        c = self.client(cid)
        if redirect not in c["redirect_uris"] or not self.check_redirect(redirect):
            raise OAuthError("invalid_request", "Redirect must exactly match registration")
        if q.get("response_type") != "code" or q.get("code_challenge_method") != "S256" or not CHALLENGE.fullmatch(q.get("code_challenge", "")):
            raise OAuthError("invalid_request", "Authorization code and S256 PKCE are required")
        if q.get("resource") != self.s.resource:
            raise OAuthError("invalid_target", "Resource must match this MCP endpoint")
        scope = self.checked_scope(q.get("scope", SCOPE))
        state = q.get("state", "")
        if len(state) > 2048:
            raise OAuthError("invalid_request", "State too long")
        rid, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.pending[rid] = {"client_id": cid, "redirect_uri": redirect, "state": state, "challenge": q["code_challenge"],
                             "cookie_hash": digest(cookie), "scope": scope, "expires": time.time() + 600}
        consent = self.sign("consent", {"rid": rid, **self.pending[rid], "epoch": self.epoch}, 600)
        name = html.escape(c["client_name"])
        collection = "the operator-configured collection / 已配置文献分类"
        write = WRITE_SCOPE in scope.split()
        access_label = "读取及受控写入 / read and reviewed write access" if write else "只读访问 / read-only access"
        notice = ("写入可修改、归类、合并或删除文献；每份计划仍需在独立复核页批准后才能执行。 "
                  "Writes may edit, file, merge, or delete records; each exact plan requires a separate owner approval."
                  if write else "不会修改或删除文献。No library writes.")
        body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Zotero Cloud MCP — Authorize</title></head>
<body style="font-family:system-ui;max-width:600px;margin:64px auto;padding:24px;line-height:1.7"><h1>Zotero Cloud MCP</h1>
<p>授权 / Authorize <strong>{name}</strong> {access_label} to <strong>{collection}</strong> 及其子分类 / and descendants.</p>
<p>{notice} 请求的数据将进入你发起的 AI 对话。Requested data is sent to your AI client.</p>
<form action="/oauth/approve" method="post"><input type="hidden" name="request_id" value="{rid}"><input type="hidden" name="consent_token" value="{consent}"><label for="password">插件专用口令 / Instance passphrase</label><br>
<input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512" style="width:100%;padding:10px;box-sizing:border-box">
<p>输入 Render 的 MCP_LOGIN_PASSWORD。不是 Google / Zotero 密码，也不是 Zotero API Key。</p>
<button name="decision" value="allow">确认授权 / Approve</button> <button name="decision" value="deny" formnovalidate>取消 / Cancel</button></form>
<p><small>Return only to {html.escape(redirect)}</small></p></body></html>'''
        response = HTMLResponse(body)
        response.set_cookie(self.cookie_name(rid), cookie, max_age=600, secure=not self.s.local_dev, httponly=True, samesite="lax", path="/")
        return response

    async def approve(self, request):
        self.require_ready()
        self.throttle("login", 30, 900)
        if request.headers.get("origin") not in {None, self.s.base_url}:
            raise OAuthError("access_denied", "Cross-origin approval denied", 403)
        self.prune()
        data = await form_data(request)
        rid = data.get("request_id", "")
        cookie_name = self.cookie_name(rid)
        if rid in self.used_consents:
            raise OAuthError("access_denied", "authorization_already_submitted", 403)
        p = self.pending.get(rid)
        if not p:
            p = self.recover_consent(rid, data.get("consent_token", ""))
        cookie = request.cookies.get(cookie_name, "")
        if not cookie:
            raise OAuthError("access_denied", "browser_cookie_missing", 403)
        if not hmac.compare_digest(p["cookie_hash"], digest(cookie)):
            raise OAuthError("access_denied", "browser_cookie_mismatch", 403)
        decision = data.get("decision")
        if decision not in {"allow", "deny"}:
            raise OAuthError("invalid_request", "Explicit allow or deny required")
        if decision == "deny":
            self.pending.pop(rid, None)
            params = {"error": "access_denied", "state": p["state"], "iss": self.s.base_url}
        else:
            password = data.get("password", "")
            if not 32 <= len(password) <= 512 or not hmac.compare_digest(digest(password), digest(self.s.login_password)):
                raise OAuthError("access_denied", "Incorrect instance passphrase", 401)
            self.pending.pop(rid, None)
            code = secrets.token_urlsafe(32)
            self.codes[digest(code)] = {**p, "expires": time.time() + 120}
            params = {"code": code, "state": p["state"], "iss": self.s.base_url}
        # Consume only after a valid browser proof and password (or deny).
        self.used_consents[rid] = p["expires"]
        response = RedirectResponse(p["redirect_uri"] + ("&" if "?" in p["redirect_uri"] else "?") + urlencode(params), status_code=303)
        response.delete_cookie(cookie_name, path="/", secure=not self.s.local_dev, httponly=True, samesite="lax")
        return response

    def authenticate_client(self, request, data):
        cid, secret = data.get("client_id", ""), data.get("client_secret", "")
        header = request.headers.get("authorization", "")
        basic = header.startswith("Basic ")
        if basic:
            if secret:
                raise OAuthError("invalid_client", "Use one authentication method", 401)
            try:
                pair = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
                parsed_cid, secret = map(unquote_plus, pair)
                if cid and cid != parsed_cid:
                    raise ValueError()
                cid = parsed_cid
            except (ValueError, UnicodeError):
                raise OAuthError("invalid_client", "Invalid Basic authentication", 401) from None
        elif header:
            raise OAuthError("invalid_client", "Unsupported authentication", 401)
        c = self.client(cid)
        method = c["token_endpoint_auth_method"]
        if method == "none":
            if basic or secret:
                raise OAuthError("invalid_client", "Authentication method mismatch", 401)
        elif (method == "client_secret_basic") != basic or not hmac.compare_digest(digest(secret), digest(self.client_secret(cid))):
            raise OAuthError("invalid_client", "Invalid client credential", 401)
        return cid, c

    def issue_tokens(self, cid, client, refresh=True, scope=SCOPE):
        claims = {"sub": "owner", "client_id": cid, "scope": self.checked_scope(scope), "epoch": self.epoch, "jti": secrets.token_urlsafe(20)}
        result = {"access_token": self.sign("access", claims, ACCESS_SECONDS), "token_type": "Bearer", "expires_in": ACCESS_SECONDS, "scope": claims["scope"]}
        # Public clients have no refresh token without a durable rotation store.
        if refresh and client["token_endpoint_auth_method"] != "none":
            result["refresh_token"] = self.sign("refresh", claims, REFRESH_SECONDS)
        return result

    async def token(self, request):
        self.require_ready()
        self.throttle("token", 180, 600)
        self.prune()
        data = await form_data(request)
        cid, c = self.authenticate_client(request, data)
        if data.get("resource") != self.s.resource:
            raise OAuthError("invalid_target", "Resource mismatch")
        if data.get("grant_type") == "authorization_code":
            key, verifier = digest(data.get("code", "")), data.get("code_verifier", "")
            p = self.codes.get(key)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not p or p["client_id"] != cid or p["redirect_uri"] != data.get("redirect_uri") or not VERIFIER.fullmatch(verifier) or not hmac.compare_digest(challenge, p["challenge"]):
                raise OAuthError("invalid_grant", "Invalid, expired or mismatched code")
            self.codes.pop(key)  # Atomic in one event loop: no await before consume.
            return JSONResponse(self.issue_tokens(cid, c, scope=p.get("scope", SCOPE)))
        if data.get("grant_type") == "refresh_token" and c["token_endpoint_auth_method"] != "none":
            try:
                p = self.decode(data.get("refresh_token", ""), "refresh")
            except OAuthError:
                raise OAuthError("invalid_grant", "Invalid refresh token") from None
            scope = self.checked_scope(data.get("scope", p.get("scope", "")))
            if p.get("client_id") != cid or p.get("epoch") != self.epoch or not set(scope.split()) <= set(p.get("scope", "").split()):
                raise OAuthError("invalid_grant", "Refresh token mismatch")
            result = self.issue_tokens(cid, c, refresh=False, scope=scope)
            result["refresh_token"] = data["refresh_token"]  # Fixed original expiry, never sliding.
            return JSONResponse(result)
        raise OAuthError("unsupported_grant_type", "Unsupported grant")

    def verify_request(self, request, required_scope=None):
        self.require_ready()
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 16384:
            raise OAuthError("invalid_token", "OAuth authorization required", 401)
        p = self.decode(header[7:], "access")
        try:
            scope = self.checked_scope(p.get("scope", ""))
        except OAuthError:
            raise OAuthError("invalid_token", "Token scopes are no longer enabled", 401) from None
        if p.get("sub") != "owner" or p.get("epoch") != self.epoch:
            raise OAuthError("invalid_token", "Token is not authorized for this resource", 401)
        if required_scope and required_scope not in scope.split():
            raise OAuthError("insufficient_scope", "Reauthorize with the required scope: " + required_scope, 403)
        self.client(p.get("client_id"))
        return p
