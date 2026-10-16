import fcntl
import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

from .collector import normalize
from .control import Control
from .unifi import UniFi

LOG = logging.getLogger('huawei-bridge')
STATIC = Path(__file__).parent / 'static'
MAX_BODY = 16 * 1024 * 1024


class Application:
    def __init__(self):
        self.directory = Path(os.environ.get('STATE_DIR', '.state'))
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lockfile = (self.directory / '.lock').open('a')
        fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.nbi = os.environ.get('GENIEACS_URL', 'http://127.0.0.1:7557').rstrip('/')
        self.public_url = os.environ.get('PUBLIC_URL', 'http://127.0.0.1:8090').rstrip('/')
        self.allowed_hosts = {urlsplit(self.public_url).netloc, 'localhost:8090', '127.0.0.1:8090'}
        self.devices = []
        self.last_collection = None
        self.last_error = None
        self.last_refresh = 0
        self.mutex = threading.RLock()
        self.control = Control(self.directory / 'control', self.request,
                               os.environ.get('HUAWEI_CONTROL_IP', ''),
                               os.environ.get('HUAWEI_CONTROL_DEVICE_ID', ''))
        self.unifi = UniFi(self.directory / 'unifi', os.environ.get('UNIFI_INFORM_URL', ''),
                           json.loads(os.environ.get('UNIFI_WLAN_MAP') or '{}'),
                           self.control, self.device_lookup, self.mutex)
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def device_lookup(self, identity):
        return next((d for d in self.devices if d.get('_id') == identity), {})

    def request(self, path, data=None):
        req = urllib.request.Request(self.nbi + path, data=data,
                                     headers={'Content-Type': 'application/json'})
        with self.http.open(req, timeout=8) as response:
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError('ACS response too large')
        return json.loads(raw)

    def collect(self):
        devices = self.request('/devices')
        if not isinstance(devices, list):
            raise ValueError('Invalid ACS response')
        with self.mutex:
            self.devices = devices
            self.last_collection = time.time()
            self.last_error = None
        self.control.reconcile(devices)
        if time.time() - self.last_refresh >= 60:
            # Periodic telemetry reads are independent of explicit controller provisioning.
            tasks = self.request('/tasks')
            queued = {(task.get('device'), task.get('objectName')) for task in tasks}
            for device in normalize(devices)['aps']:
                identity = device['id']
                if (identity, 'InternetGatewayDevice.LANDevice') in queued:
                    continue
                self.request('/devices/' + quote(identity, safe='') + '/tasks',
                             json.dumps({'name': 'refreshObject', 'objectName': 'InternetGatewayDevice.LANDevice'}).encode())
            self.last_refresh = time.time()

    def snapshot(self):
        with self.mutex:
            result = normalize(self.devices)
            result.update(acs_last_collection=self.last_collection, acs_error=self.last_error,
                          unifi_enabled=bool(self.unifi.url), unifi=dict(self.unifi.status))
            if self.last_error:
                result['warnings'].append('No se pudo actualizar GenieACS: ' + self.last_error)
            unmapped = sorted({ssid for status in result['unifi'].values()
                               for ssid in status.get('unmapped_ssids', [])})
            if unmapped:
                result['warnings'].append('SSID sin vínculo WLAN en UniFi: ' + ', '.join(unmapped))
            return result

    def worker(self):
        next_collect = 0
        while True:
            if time.monotonic() >= next_collect:
                try:
                    self.collect()
                except Exception as exc:
                    with self.mutex:
                        self.last_error = type(exc).__name__
                    LOG.warning('Fallo al consultar ACS: %s', type(exc).__name__)
                next_collect = time.monotonic() + 10
            self.unifi.tick(self.snapshot())
            time.sleep(1)


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, fmt, *args):
            pass

        def allowed(self):
            origin = self.headers.get('Origin')
            return self.headers.get('Host') in app.allowed_hosts and (
                not origin or origin in {app.public_url, 'http://localhost:8090'})

        def send(self, status, data, content_type='application/json'):
            if not isinstance(data, bytes):
                data = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {'error': 'Origin or Host rejected'})
            path = urlsplit(self.path).path
            if path == '/api/snapshot':
                return self.send(200, app.snapshot())
            if path == '/health':
                return self.send(200 if app.last_collection and time.time() - app.last_collection < 60 and not app.last_error else 503,
                                 {'acs': bool(app.last_collection), 'error': app.last_error})
            files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                     '/style.css': ('style.css', 'text/css')}
            if path in files:
                filename, kind = files[path]
                return self.send(200, (STATIC / filename).read_bytes(), kind + '; charset=utf-8')
            return self.send(404, {'error': 'Not found'})
    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    app = Application()
    thread = threading.Thread(target=app.worker, daemon=True)
    thread.start()
    server = ThreadingHTTPServer((os.environ.get('LISTEN_HOST', '127.0.0.1'), int(os.environ.get('PORT', '8090'))), handler(app))
    server.daemon_threads = True
    LOG.info('Huawei Bridge disponible en %s', app.public_url)
    server.serve_forever()


if __name__ == '__main__':
    main()
