"""CSP/OAuth response-contract tests; these do not emulate browser enforcement."""
import base64
import hashlib
import re
import unittest
from urllib.parse import parse_qs, urlsplit
import test_service as fixtures


def form_sources(response):
    for directive in response.headers['content-security-policy'].split(';'):
        parts = directive.strip().split()
        if parts and parts[0] == 'form-action':
            return parts[1:]
    raise AssertionError('form-action must remain explicitly restricted')


class OAuthCspTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ServiceTests()
        self.fixture.setUp()
        self.c = self.fixture.c
        self.s = self.fixture.s

    def tearDown(self):
        self.fixture.tearDown()

    def begin(self, callback):
        r = self.c.post('/oauth/register', json={**self.fixture.meta, 'redirect_uris': [callback]})
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(fixtures.VERIFIER.encode()).digest()).rstrip(b'=').decode()
        p = {'client_id': d['client_id'], 'response_type': 'code', 'redirect_uri': callback,
             'scope': fixtures.SCOPE, 'state': 'synthetic-csp-state', 'resource': self.s.resource,
             'code_challenge_method': 'S256', 'code_challenge': challenge}
        page = self.c.get('/oauth/authorize', params=p)
        self.assertEqual(page.status_code, 200, page.text)
        rid = re.search(r'name="request_id" value="([^"]+)"', page.text)[1]
        return page, rid, d

    def test_consent_csp_allows_both_chatgpt_callbacks_without_wildcards(self):
        for callback in ['https://chatgpt.com/connector_platform_oauth_redirect', fixtures.CALLBACK]:
            page, _, _ = self.begin(callback)
            self.assertEqual(form_sources(page), ["'self'", 'https://chatgpt.com'])
            self.assertEqual(page.headers['referrer-policy'], 'same-origin')
            self.assertIn("frame-ancestors 'none'", page.headers['content-security-policy'])

    def test_other_pages_keep_self_only_form_action(self):
        for path in ['/', '/healthz', '/readyz', '/privacy', '/.well-known/oauth-authorization-server']:
            self.assertEqual(form_sources(self.c.get(path)), ["'self'"])

    def test_first_submit_303_duplicate_rejected_and_code_exchange_works(self):
        callback = 'https://chatgpt.com/connector_platform_oauth_redirect'
        page, rid, d = self.begin(callback)
        data = {'request_id': rid, 'decision': 'allow', 'password': fixtures.PASSWORD}
        r = self.c.post('/oauth/approve', data=data, headers={'Origin': self.s.base_url}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        target = urlsplit(r.headers['location'])
        q = parse_qs(target.query)
        self.assertEqual(target.scheme + '://' + target.netloc, 'https://chatgpt.com')
        self.assertIn(target.scheme + '://' + target.netloc, form_sources(page))
        self.assertEqual(q['state'], ['synthetic-csp-state'])
        self.assertNotIn(fixtures.PASSWORD, r.headers['location'])
        second = self.c.post('/oauth/approve', data=data, headers={'Origin': self.s.base_url}, follow_redirects=False)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(second.json()['error_description'], 'authorization_already_submitted')
        token = self.c.post('/oauth/token', data={
            'client_id': d['client_id'], 'client_secret': d['client_secret'],
            'grant_type': 'authorization_code', 'code': q['code'][0],
            'redirect_uri': callback, 'code_verifier': fixtures.VERIFIER,
            'resource': self.s.resource})
        self.assertEqual(token.status_code, 200, token.text)
        payload = self.fixture.rpc('zotero_status', token=token.json()['access_token']).json()['result']
        self.assertFalse(payload['isError'])

    def test_attacker_redirect_and_cross_site_approval_still_rejected(self):
        r = self.c.post('/oauth/register', json={**self.fixture.meta, 'redirect_uris': ['https://attacker.example/callback']})
        self.assertEqual(r.status_code, 400)
        _, rid, _ = self.begin(fixtures.CALLBACK)
        data = {'request_id': rid, 'decision': 'allow', 'password': fixtures.PASSWORD}
        for origin in ['https://attacker.example', 'null', 'https://chatgpt.com']:
            r = self.c.post('/oauth/approve', data=data, headers={'Origin': origin}, follow_redirects=False)
            self.assertEqual(r.status_code, 403)

    def test_denial_redirects_to_callback_without_issuing_code(self):
        page, rid, _ = self.begin(fixtures.CALLBACK)
        r = self.c.post('/oauth/approve', data={'request_id': rid, 'decision': 'deny'},
                        headers={'Origin': self.s.base_url}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn('https://chatgpt.com', form_sources(page))
        q = parse_qs(urlsplit(r.headers['location']).query)
        self.assertEqual(q['error'], ['access_denied'])
        self.assertNotIn('code', q)
