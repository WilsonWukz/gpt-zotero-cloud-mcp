"""Owner-only change approval UI. A tool parameter cannot bypass this confirmation.

GET shows only a login form. The plan diff is disclosed after a valid instance
passphrase and signed, browser-bound CSRF proof. No library data or secret in URLs.
"""
import hashlib
import hmac
import html
import json
import re
import secrets

from starlette.responses import HTMLResponse, Response
from .auth import OAuthError, form_data, digest
from .zotero import DataError


class ReviewPages:
    def __init__(self, manager, auth):
        self.m, self.a = manager, auth

    def render(self, content, status=200):
        return HTMLResponse('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Zotero — Review changes</title></head>'
            '<body style="font-family:system-ui;max-width:960px;margin:36px auto;padding:20px;line-height:1.6">'
            '<h1>Zotero — 复核修改 / Review changes</h1>' + content + '</body></html>', status_code=status)

    def cookie_name(self, pid):
        return ('zotero-review-dev-' if self.a.s.local_dev else '__Host-zotero-review-') + pid[:20]

    def proof(self, pid, stage, digest_value=''):
        return self.a.sign('review', {'plan_id': pid, 'stage': stage, 'epoch': self.a.epoch,
                                    'digest': digest_value, 'nonce': secrets.token_urlsafe(32)}, 600)

    def attach(self, response, pid, proof):
        response.set_cookie(self.cookie_name(pid), proof, max_age=600, secure=not self.a.s.local_dev,
                            httponly=True, samesite='strict', path='/')
        return response

    def check(self, request, pid, data, stage):
        if request.headers.get('origin') != self.a.s.base_url:
            raise OAuthError('access_denied', 'Same-origin review submission required', 403)
        token, cookie = data.get('proof', ''), request.cookies.get(self.cookie_name(pid), '')
        if not token or not cookie or len(token) > 6000 or not hmac.compare_digest(digest(token), digest(cookie)):
            raise OAuthError('access_denied', 'Review cookie/CSRF proof missing; reopen review page', 403)
        p = self.a.decode(token, 'review')
        if p.get('plan_id') != pid or p.get('epoch') != self.a.epoch or p.get('stage') != stage:
            raise OAuthError('access_denied', 'Review proof mismatch', 403)
        return p

    async def handle(self, request):
        pid = request.path_params['plan_id']
        if not self.m.store or not re.fullmatch('[A-Za-z0-9_-]{32}', pid):
            return Response(status_code=404)
        self.a.require_ready()
        self.a.throttle('change-review', 30, 900)
        path = '/review/' + pid
        if request.method == 'GET':
            # Do not confirm plan existence or disclose its preview to an anonymous GET.
            proof = self.proof(pid, 'login')
            page = self.render('<p>输入实例口令后才能查看修改内容。不会在此处索取 Zotero API Key。</p>'
                f'<form action="{path}" method="post"><input type="hidden" name="proof" value="{proof}">'
                '<label>Instance passphrase / MCP_LOGIN_PASSWORD <input type="password" name="password" '
                'required maxlength="512" autocomplete="current-password"></label>'
                '<button type="submit">查看计划 / View plan</button></form>')
            return self.attach(page, pid, proof)
        data = await form_data(request)
        if request.url.path.endswith('/approve'):
            p = self.check(request, pid, data, 'approve')
            try:
                row = self.m.store.get(pid, epoch=self.a.epoch)
                if p.get('digest') != row['digest']:
                    raise OAuthError('access_denied', 'Plan digest changed', 403)
                decision = data.get('decision')
                if decision not in {'approve', 'cancel'}:
                    raise OAuthError('invalid_request', 'An explicit decision is required')
                self.m.store.transition(pid, 'pending', 'approved' if decision == 'approve' else 'cancelled')
            except DataError:
                return self.render('<p>计划已过期、已提交或已撤销，请返回 ChatGPT 查看计划状态。</p>', 409)
            page = self.render('<p>已批准这份精确计划，尚未修改 Zotero。返回 ChatGPT 执行 apply_changes。</p>'
                               if decision == 'approve' else '<p>已撤销；没有修改 Zotero。</p>')
            page.delete_cookie(self.cookie_name(pid), path='/', secure=not self.a.s.local_dev,
                               httponly=True, samesite='strict')
            return page
        self.check(request, pid, data, 'login')
        password = data.get('password', '')
        if not 32 <= len(password) <= 512 or not hmac.compare_digest(digest(password), digest(self.a.s.login_password)):
            raise OAuthError('access_denied', 'Incorrect instance passphrase', 401)
        try:
            row = self.m.store.get(pid, epoch=self.a.epoch)
        except DataError:
            return self.render('<p>找不到可用计划，或服务凭证/范围已变更。</p>', 404)
        proof = self.proof(pid, 'approve', row['digest'])
        # Escape all untrusted titles, note HTML and field values. No scripts or links.
        preview = html.escape(json.dumps(row['plan']['preview'], ensure_ascii=False, indent=2))
        page = self.render('<p><strong>请逐项核对。批准可能允许修改、合并、移入回收站或永久删除；批量操作不是事务。</strong></p>'
            f'<p>Status: {html.escape(row["status"])}<br>Digest: <code>{row["digest"]}</code></p>'
            f'<pre style="white-space:pre-wrap;overflow-wrap:anywhere">{preview}</pre>'
            f'<form action="{path}/approve" method="post"><input type="hidden" name="proof" value="{proof}">'
            '<button name="decision" value="approve">批准这份计划 / Approve exact changes</button> '
            '<button name="decision" value="cancel">撤销 / Cancel</button></form>')
        return self.attach(page, pid, proof)
