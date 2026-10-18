"""Optional, single-origin Huawei telemetry. No router configuration operations.

Login proof adapted from vmakeev/huawei_mesh_router (MIT); see THIRD_PARTY_LICENSES.
"""
import hashlib
import hmac
import http.cookiejar
import ipaddress
import json
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

MAX_RESPONSE = 1024 * 1024
READS = {'deviceinfo': '/api/system/deviceinfo', 'hosts': '/api/system/HostInfo',
         'hilink': '/api/device/topology', 'easymesh': '/api/device/l2topo'}
LOGIN = {'/api/system/user_login_nonce', '/api/system/user_login_proof'}


class WebError(Exception):
    """Messages must never contain upstream bodies or credentials."""


class Unauthorized(WebError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') in ('csrf_param', 'csrf_token'):
            self.csrf[attrs['name']] = attrs.get('content')


class HuaweiWeb:
    def __init__(self, url, username, password_file, ca_file=None, host_header=''):
        self.url = url.rstrip('/')
        self.username, self.password_file = username, password_file
        self.host_header = host_header
        self.ready = False
        self.csrf = {}
        if not self.url:
            return
        parsed = urlsplit(self.url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Expected a Huawei HTTP(S) origin without credentials or path')
        # Validate the port before any credential reads or network operations.
        _ = parsed.port
        if not isinstance(host_header, str):
            raise ValueError('Huawei Host override must be a bare IPv4 address')
        if host_header:
            try:
                ipaddress.IPv4Address(host_header)
            except ValueError:
                raise ValueError('Huawei Host override must be a bare IPv4 address') from None
        self.cookies = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file or None)))

    def _request(self, path, data=None, html=False):
        if (data is not None and path not in LOGIN) or (
                data is None and path not in {*READS.values(), '/html/index.html'}):
            raise WebError('Endpoint rejected')
        headers = {'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
        if self.host_header:
            # HTTP routing only: URL, TCP destination and TLS identity stay unchanged.
            headers['Host'] = self.host_header
        request = urllib.request.Request(self.url + path,
            data=None if data is None else json.dumps({'csrf': self.csrf, 'data': data}).encode(),
            headers=headers)
        try:
            with self.http.open(request, timeout=5) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise Unauthorized('Session rejected') from None
            raise WebError('HTTP ' + str(code)) from None
        if len(raw) > MAX_RESPONSE:
            raise WebError('Response too large')
        if html:
            return raw.decode('utf-8')
        result = json.loads(raw)
        if isinstance(result, dict):
            self._csrf(result)
            code = result.get('err', result.get('errcode', 0))
            category = result.get('errorCategory')
            if code == -2 or category in ('csrf_error', 'unauthorized') or result.get('csrf') == 'Menu.csrf_err':
                raise Unauthorized('Session rejected')
            if code != 0:
                raise WebError('Router rejected request')
        if not isinstance(result, (dict, list)):
            raise WebError('Invalid response')
        return result

    def _csrf(self, data):
        if all(isinstance(data.get(k), str) and 0 < len(data[k]) <= 1024
               for k in ('csrf_param', 'csrf_token')):
            self.csrf = {k: data[k] for k in ('csrf_param', 'csrf_token')}

    def _login(self):
        self.ready = False
        self.cookies.clear()
        self.csrf = {}
        meta = Meta()
        meta.feed(self._request('/html/index.html', html=True))
        self._csrf(meta.csrf)
        if not self.csrf:
            raise WebError('Missing session parameters')
        with Path(self.password_file).open('rb') as handle:
            raw = handle.read(4097)
        if not raw or len(raw) > 4096:
            raise WebError('Invalid password file')
        password = raw.removesuffix(b'\n').removesuffix(b'\r')
        nonce = secrets.token_hex(32)
        challenge = self._request('/api/system/user_login_nonce',
                                  {'username': self.username, 'firstnonce': nonce})
        if not isinstance(challenge, dict):
            raise WebError('Invalid challenge')
        iterations, salt, server = (challenge.get(k) for k in ('iterations', 'salt', 'servernonce'))
        if (type(iterations) is not int or not 1 <= iterations <= 1000000
                or not isinstance(salt, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,256}', salt)
                # Server nonce is opaque, unlike the hex-encoded salt; exclude delimiters/controls.
                or not isinstance(server, str)
                or not re.fullmatch(r'[\x21-\x2b\x2d-\x7e]{65,512}', server)
                or not server.startswith(nonce)):
            raise WebError('Invalid challenge')
        salted = hashlib.pbkdf2_hmac('sha256', password, bytes.fromhex(salt), iterations, 32)
        message = ','.join([nonce, server, server]).encode()
        key = hmac.new(b'Client Key', salted, hashlib.sha256).digest()
        signature = hmac.new(message, hashlib.sha256(key).digest(), hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(key, signature)).hex()
        self._request('/api/system/user_login_proof', {'clientproof': proof, 'finalnonce': server})
        self.ready = True

    def _read(self, path):
        if not self.ready:
            self._login()
        try:
            return self._request(path)
        except Unauthorized:
            self._login()
            try:
                return self._request(path)
            except Unauthorized:
                self.ready = False
                raise

    def collect(self):
        result = {'collected_at': None, 'warnings': []}
        if not self.url:
            return result
        started = time.time()
        try:
            for name, path in READS.items():
                try:
                    result[name] = self._read(path)
                except WebError as exc:
                    if name in ('deviceinfo', 'hosts') or isinstance(exc, Unauthorized):
                        raise
                    result['warnings'].append(name + ': unavailable')
            result['collected_at'] = started
            return result
        except Exception:
            self.ready = False
            raise WebError('Huawei web collection failed') from None
