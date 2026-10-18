import hashlib
import hmac
import io
import json
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from huawei_bridge.web import HuaweiWeb, WebError, NoRedirect


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Peer:
    """HTTP transport double: exercises real request/authentication/parsing code."""
    def __init__(self):
        self.requests = []
        self.challenge = {'salt': 'aa' * 16, 'iterations': 2, 'servernonce': None,
                          'csrf_param': 'next', 'csrf_token': 'next-token'}
        self.hosts = [{'MACAddress': '02:00:00:00:00:22', 'IPAddress': '198.51.100.8'}]
        self.unauthorized = False

    def open(self, request, timeout):
        self.requests.append(request)
        path = request.selector
        if path == '/html/index.html':
            return Response(b'<meta name="csrf_param" content="param"><meta name="csrf_token" content="token">')
        if path.endswith('user_login_nonce'):
            value = dict(self.challenge)
            if value['servernonce'] is None:
                value['servernonce'] = json.loads(request.data)['data']['firstnonce'] + 'bb' * 32
        elif path.endswith('user_login_proof'):
            value = {'err': 0}
        elif self.unauthorized:
            value = {'errcode': -2}
        elif path.endswith('HostInfo'):
            value = self.hosts
        elif path.endswith('deviceinfo'):
            value = {'MACAddress': '02:00:00:00:00:02', 'FriendlyName': 'AX3'}
        else:
            raise urllib.error.HTTPError(request.full_url, 404, 'missing', {}, None)
        return Response(json.dumps(value).encode())


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.password = Path(self.tmp.name) / 'password'
        self.password.write_text('secret\n')
        self.peer = Peer()
        self.client = HuaweiWeb('https://192.0.2.1', 'admin', self.password)
        self.client.http = self.peer

    def test_disabled_client_performs_no_io(self):
        with patch('pathlib.Path.open', side_effect=AssertionError('secret opened')):
            self.assertIsNone(HuaweiWeb('', 'admin', '/missing').collect()['collected_at'])

    def test_login_proof_and_csrf(self):
        with patch('secrets.token_hex', return_value='cc' * 32):
            result = self.client.collect()
        request = self.peer.requests[2]
        data = json.loads(request.data)
        salted = hashlib.pbkdf2_hmac('sha256', b'secret', bytes.fromhex('aa' * 16), 2, 32)
        key = hmac.new(b'Client Key', salted, hashlib.sha256).digest()
        server = 'cc' * 32 + 'bb' * 32
        message = ','.join(['cc' * 32, server, server]).encode()
        signature = hmac.new(message, hashlib.sha256(key).digest(), hashlib.sha256).digest()
        self.assertEqual(data['data']['clientproof'], bytes(a ^ b for a, b in zip(key, signature)).hex())
        self.assertEqual(data['csrf']['csrf_param'], 'next')
        self.assertEqual(result['hosts'], self.peer.hosts)
        self.assertEqual(len(result['warnings']), 2)

    def test_opaque_server_nonce_is_preserved_in_proof(self):
        server = 'cc' * 32 + 'Z+/=' * 8
        self.peer.challenge['servernonce'] = server
        with patch('secrets.token_hex', return_value='cc' * 32):
            self.client.collect()
        data = json.loads(self.peer.requests[2].data)['data']
        self.assertEqual(data['finalnonce'], server)
        salted = hashlib.pbkdf2_hmac('sha256', b'secret', bytes.fromhex('aa' * 16), 2, 32)
        key = hmac.new(b'Client Key', salted, hashlib.sha256).digest()
        message = ','.join(['cc' * 32, server, server]).encode()
        signature = hmac.new(message, hashlib.sha256(key).digest(), hashlib.sha256).digest()
        self.assertEqual(data['clientproof'], bytes(a ^ b for a, b in zip(key, signature)).hex())

    def test_server_nonce_requires_bounded_printable_extension_of_client_nonce(self):
        for server in ('bb' * 64, 'cc' * 32, 'cc' * 32 + 'x' * 449,
                       *('cc' * 32 + suffix for suffix in ('\r', '\n', '\x00', ' ', ',', '\u00e9'))):
            with self.subTest(server_length=len(server)):
                self.peer.requests.clear()
                self.client.ready = False
                self.peer.challenge['servernonce'] = server
                with patch('secrets.token_hex', return_value='cc' * 32):
                    with self.assertRaises(WebError):
                        self.client.collect()
                self.assertFalse(any(r.selector.endswith('user_login_proof') for r in self.peer.requests))

    def test_host_override_keeps_url_and_tls_verification(self):
        client = HuaweiWeb('https://router.example.test', 'admin', self.password,
                           host_header='192.0.2.1')
        tls = next(h for h in client.http.handlers if isinstance(h, urllib.request.HTTPSHandler))
        self.assertTrue(tls._context.check_hostname)
        self.assertEqual(tls._context.verify_mode, ssl.CERT_REQUIRED)
        client.http = self.peer
        client.collect()
        self.assertEqual(len(self.peer.requests), 7)
        for request in self.peer.requests:
            self.assertEqual(request.get_header('Host'), '192.0.2.1')
            self.assertEqual(request.host, 'router.example.test')
            self.assertTrue(request.full_url.startswith('https://router.example.test/'))

    def test_host_override_rejects_non_ipv4_authorities_before_io(self):
        for value in ('192.0.2.1\r\nCookie: secret', '192.0.2.1\n', ' 192.0.2.1',
                      '192.0.2.1:443', 'https://192.0.2.1', 'user@192.0.2.1',
                      '192.0.2.1/path', 'router.example.test', '::1', '999.0.0.1', True):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                HuaweiWeb('https://router.example.test', 'admin', self.password,
                          host_header=value)

    def test_default_host_is_not_overridden(self):
        self.client.collect()
        self.assertTrue(all(r.get_header('Host') is None for r in self.peer.requests))

    def test_reauth_is_bounded(self):
        self.peer.unauthorized = True
        with self.assertRaises(WebError):
            self.client.collect()
        self.assertEqual(sum(r.selector.endswith('user_login_nonce') for r in self.peer.requests), 2)

    def test_discovered_addresses_are_not_contacted(self):
        self.client.collect()
        self.assertTrue(all(r.host == '192.0.2.1' for r in self.peer.requests))
        self.assertEqual({r.selector for r in self.peer.requests if r.get_method() == 'POST'},
                         {'/api/system/user_login_nonce', '/api/system/user_login_proof'})

    def test_redirect_does_not_forward_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'http://198.51.100.1'))
        for url in ('https://user:pw@192.0.2.1', 'https://192.0.2.1/?x=1', 'ftp://192.0.2.1', 'http://192.0.2.1/path'):
            with self.assertRaises(ValueError):
                HuaweiWeb(url, 'admin', self.password)

    def test_response_limit(self):
        self.peer.open = lambda *a, **k: Response(b'x' * (1024 * 1024 + 1))
        with self.assertRaises(WebError):
            self.client.collect()

    def test_bad_challenge_is_rejected(self):
        original = dict(self.peer.challenge)
        for value in (True, 0, 1000001, '2'):
            self.peer.challenge['iterations'] = value
            with self.assertRaises(WebError):
                self.client.collect()
        for field, value in (('salt', 'zz'), ('servernonce', 'not-hex'), ('salt', 'aa' * 257)):
            self.peer.challenge = dict(original, **{field: value})
            with self.assertRaises(WebError):
                self.client.collect()

    def test_diagnostics_redact_secrets(self):
        self.peer.open = lambda *a, **k: (_ for _ in ()).throw(ValueError('secret csrf cookie'))
        with self.assertRaises(WebError) as caught:
            self.client.collect()
        self.assertNotIn('secret', str(caught.exception))

    def test_real_opener_preserves_cookie_and_blocks_redirect(self):
        requests, peer = [], self.peer
        class Wire(urllib.request.HTTPHandler):
            def http_open(self, request):
                requests.append(request)
                headers = Message()
                code = 200
                if request.selector == '/html/index.html':
                    headers['Set-Cookie'] = 'SessionID_R3=synthetic; Path=/'
                if request.selector.endswith('deviceinfo'):
                    headers['Location'] = 'http://198.51.100.8/stolen'
                    raw, code = b'', 302
                else:
                    raw = peer.open(request, timeout=5).read()
                response = urllib.response.addinfourl(io.BytesIO(raw), headers, request.full_url, code)
                response.msg = 'synthetic'
                return response
        client = HuaweiWeb('http://router.example.test', 'admin', self.password,
                           host_header='192.0.2.1')
        client.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(client.cookies), Wire())
        with self.assertRaises(WebError):
            client.collect()
        self.assertEqual(len(requests), 4)
        self.assertEqual(requests[1].get_header('Cookie'), 'SessionID_R3=synthetic')
        self.assertTrue(all(r.host == 'router.example.test' for r in requests))
        self.assertTrue(all(r.get_header('Host') == '192.0.2.1' for r in requests))
