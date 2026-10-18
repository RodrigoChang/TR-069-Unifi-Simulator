"""Experimental AP reporting and allowlisted optional provisioning."""

import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .protocol import BOOTSTRAP_KEY, MAX_PAYLOAD, ProtocolError, decode, encode

MODEL = 'U7PRO'
VERSION = '8.7.11.19419'


def profile_for(ap):
    # AX3/unknown models remain inventory-only until a controller profile is verified.
    if ap.get('name') == 'WUKUN-BE32':
        return {'model': MODEL, 'model_display': 'Huawei BE3 bridge (virtual U7 Pro)',
                'version': VERSION, 'required_version': '8.0.28'}
    return None


def selected_profile(ap, state):
    desired = profile_for(ap)
    existing = state.get('profile')
    if not existing and state.get('authkey'):
        # State written before profile persistence always used this exact profile.
        existing = profile_for({'name': 'WUKUN-BE32'})
    if existing:
        state['profile'] = dict(existing)
        state['profile_migration_required'] = desired != existing
        return state['profile']
    if desired:
        state['profile'] = dict(desired)
        state['profile_migration_required'] = False
    return desired


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def inform_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Invalid inform URL')
    if parsed.path != '/inform' or parsed.query or parsed.fragment:
        raise ValueError('Expected /inform URL without query or fragment')
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(5)
    try:
        address = socket.gethostbyname(parsed.hostname)
    finally:
        socket.setdefaulttimeout(previous_timeout)
    ipaddress.IPv4Address(address)
    return urlunsplit((parsed.scheme, address + (f':{parsed.port}' if parsed.port else ''), '/inform', '', ''))


def validate_wlan_map(mapping):
    if not isinstance(mapping, dict) or any(
        not isinstance(ssid, str) or not ssid or len(ssid.encode('utf-8')) > 32
        or not isinstance(wlan_id, str) or not re.fullmatch(r'[0-9a-f]{24}', wlan_id)
        for ssid, wlan_id in mapping.items()
    ):
        raise ValueError('UNIFI_WLAN_MAP must map SSIDs to 24-character lowercase WLAN IDs')
    return dict(mapping)


def payload(ap, snapshot, state, url, uptime, wlan_map=None):
    vm = ap['virtual_mac']
    profile = selected_profile(ap, state)
    if profile is None:
        raise ValueError('No verified virtual profile for this hardware')
    body = {**profile,
            'mac': vm, 'serial': vm.replace(':', '').upper(),
            'hostname': 'Huawei-' + vm.replace(':', '')[-6:],
            'ip': ap['ip'] or '0.0.0.0', 'inform_url': url,
            'default': not state.get('authkey'), 'state': 2 if state.get('authkey') else 0,
            'cfgversion': state.get('cfgversion', ''), 'uptime': int(uptime), 'time': int(time.time()),
            'discovery_response': False, 'isolated': False, 'locating': False,
            'radio_table': [], 'radio_table_stats': [], 'vap_table': []}
    bands = {}
    for index, radio in enumerate(ap['radios']):
        if radio.get('enabled') is False or radio.get('stale'):
            continue
        name = 'wifi0' if radio['band'] == '2.4g' else 'wifi1'
        kind = 'ng' if radio['band'] == '2.4g' else 'na'
        standards = radio.get('standards', [])
        bands[name] = {'name': name, 'radio': kind, 'channel': radio['channel'],
                       'is_11ac': 'ac' in standards, 'is_11ax': 'ax' in standards,
                       'is_11be': 'be' in standards,
                       # BE3 capability, not its current channel width.
                       'has_ht160': ap['name'] == 'WUKUN-BE32' and radio['band'] == '5g'}
        wlan_id = (wlan_map or {}).get(radio['ssid'])
        if not wlan_id:
            continue
        stations = []
        for client in snapshot['clients']:
            if client['ap_id'] != ap['id'] or client['radio_id'] != radio['id'] or client['stale']:
                continue
            station = {'mac': client['mac'], 'radio': kind, 'essid': radio['ssid'],
                       'bssid': virtual_bssid(vm, index), 'channel': radio['channel']}
            for src, dst in [('ip', 'ip'), ('hostname', 'hostname'), ('signal', 'signal')]:
                if client.get(src) is not None:
                    station[dst] = client[src]
            stations.append(station)
        body['vap_table'].append({'id': wlan_id,
                                  'name': f'ath{index}', 'radio': kind,
                                  'bssid': virtual_bssid(vm, index), 'essid': radio['ssid'],
                                  'channel': radio['channel'], 'up': True, 'state': 'RUN',
                                  'usage': 'user', 'num_sta': len(stations), 'sta_table': stations})
    body['radio_table'] = list(bands.values())
    body['radio_table_stats'] = [{'name': r['name'], 'radio': r['radio'],
                                  'channel': r['channel'], 'state': 'RUN'} for r in bands.values()]
    return body


def virtual_bssid(address, index):
    raw = bytearray.fromhex(address.replace(':', ''))
    raw[-1] = (raw[-1] + index + 1) % 256
    return ':'.join(f'{part:02x}' for part in raw)


def apply_response(state, response):
    kind = response.get('_type')
    if kind == 'setparam':
        mgmt = response.get('mgmt_cfg', '')
        if not isinstance(mgmt, str):
            raise ProtocolError('Invalid management metadata')
        values = dict(line.split('=', 1) for line in mgmt.splitlines() if '=' in line)
        key = values.get('authkey')
        if key is not None:
            try:
                if len(key) != 32 or len(bytes.fromhex(key)) != 16 or bytes.fromhex(key) == BOOTSTRAP_KEY:
                    raise ValueError
            except ValueError:
                raise ProtocolError('Invalid private key') from None
        mode = values.get('use_aes_gcm')
        if mode not in (None, 'true', 'false', '1', '0'):
            raise ProtocolError('Invalid encryption mode')
        cfg = response.get('cfgversion', values.get('cfgversion', state.get('cfgversion', '')))
        if not isinstance(cfg, str) or len(cfg) > 256:
            raise ProtocolError('Invalid configuration version')
        if key:
            state['authkey'] = key
            state['phase'] = 'key-received'
        state['cfgversion'] = cfg
        if mode is not None:
            state['encryption'] = 'gcm' if mode in ('true', '1') else 'cbc'
    elif kind == 'noop' and state.get('authkey'):
        state['phase'] = 'exchanging'
    elif kind == 'setdefault' and state.get('authkey'):
        state.update(authkey=None, phase='factory', cfgversion='', encryption='cbc')
    # Raw system_cfg is handled separately by the allowlisted translator; commands are ignored.
    delay = response.get('interval', 10)
    return min(60, max(1, delay)) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else 10


class UniFi:
    def __init__(self, directory, url='', wlan_map=None, control=None, device_lookup=None, lock=None):
        self.directory = Path(directory)
        self.url = url
        self.wlan_map = validate_wlan_map(wlan_map if wlan_map is not None else {})
        self.control, self.device_lookup = control, device_lookup
        # Shared with the HTTP reader (Application.snapshot) so status reads never
        # observe a partially updated dict while the worker thread writes it.
        self.lock = lock if lock is not None else threading.Lock()
        self.status = {}
        self.started = time.monotonic()
        self.next_send = {}
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def exchange(self, ap, snapshot):
        identity = ap['virtual_mac']
        path = self.directory / (identity.replace(':', '') + '.json')
        state = json.loads(path.read_text()) if path.exists() else {'phase': 'factory'}
        if selected_profile(ap, state) is None:
            with self.lock:
                self.status[identity] = {'phase': 'inventory_only', 'experimental': True,
                                        'reason': 'No verified virtual profile'}
            self.next_send[identity] = time.monotonic() + 30
            return
        if state.get('controller_url', self.url) != self.url:
            raise ValueError('State belongs to another controller')
        state['controller_url'] = self.url
        state['virtual_mac'] = identity
        mapping = self.wlan_map
        controlled = bool(self.control and self.control.controlled(ap, snapshot['aps']))
        if self.control:
            mapping = self.control.mapping(ap, mapping)
            ack = self.control.ack(ap)
            if controlled and ack is not None:
                state['cfgversion'] = ack
            elif controlled:
                state['cfgversion'] = ''  # Request a baseline without applying existing defaults.
        # Captured before apply_response() may populate authkey below: control.receive()
        # must only trust packets decrypted with the adopted private key, never the
        # public bootstrap key used during the unauthenticated first exchange.
        had_authkey = bool(state.get('authkey'))
        key = bytes.fromhex(state['authkey']) if state.get('authkey') else BOOTSTRAP_KEY
        mode = state.get('encryption', 'cbc')
        data = payload(ap, snapshot, state, inform_url(self.url), time.monotonic() - self.started,
                       mapping)
        packet = encode(data, identity, key, mode=mode)
        request = urllib.request.Request(self.url, packet, headers={'Content-Type': 'application/x-binary',
                                          'User-Agent': 'AirControl Agent v1.0', 'Accept-Encoding': 'identity'})
        with self.http.open(request, timeout=8) as response:
            raw = response.read(MAX_PAYLOAD + 41)
        decoded = decode(raw, key)
        if decoded.mac != identity or (mode == 'gcm' and decoded.mode != 'gcm'):
            raise ProtocolError('Response identity or encryption mismatch')
        previous_cfg = state.get('cfgversion', '')
        delay = apply_response(state, decoded.body)
        if controlled and had_authkey and decoded.body.get('_type') == 'setdefault':
            self.control.reset(ap)
        if controlled and had_authkey and decoded.body.get('_type') == 'setparam' and 'system_cfg' in decoded.body:
            try:
                self.control.receive(ap, decoded.body, self.device_lookup(ap['id']), mapping, snapshot['aps'])
            except ValueError as exc:
                self.control.reject(ap, exc)
            state['cfgversion'] = self.control.ack(ap) or previous_cfg
        state['last_success'] = time.time()
        atomic_json(path, state)
        status = {'phase': state['phase'], 'last_success': state['last_success'],
                  'model': state['profile']['model'], 'experimental': True,
                  'profile_migration_required': state.get('profile_migration_required', False),
                  'stations_sent': sum(len(v['sta_table']) for v in data['vap_table']),
                  'unmapped_ssids': sorted({r['ssid'] for r in ap['radios']
                      if r.get('enabled') is not False and not r.get('stale')
                      and r['ssid'] not in mapping})}
        if self.control:
            status['configuration'] = self.control.public(ap, snapshot['aps'])
        with self.lock:
            self.status[identity] = status
        self.next_send[identity] = time.monotonic() + delay

    def tick(self, snapshot):
        if not self.url:
            return
        for ap in snapshot['aps']:
            if not ap['online'] or time.monotonic() < self.next_send.get(ap['virtual_mac'], 0):
                continue
            try:
                self.exchange(ap, snapshot)
            except Exception as exc:
                # HTTP/library messages may contain credentials or response bodies.
                with self.lock:
                    self.status[ap['virtual_mac']] = {'phase': 'error', 'error': type(exc).__name__,
                        'http_status': getattr(exc, 'code', None), 'experimental': True}
                self.next_send[ap['virtual_mac']] = time.monotonic() + 30
