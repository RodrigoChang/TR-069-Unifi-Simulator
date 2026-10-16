"""Experimental AP reporting and allowlisted optional provisioning."""

import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .protocol import BOOTSTRAP_KEY, MAX_PAYLOAD, ProtocolError, decode, encode

MODEL = 'U7PRO'
VERSION = '8.7.11.19419'


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
    finally:
        if os.path.exists(name):
            os.unlink(name)


def inform_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Invalid inform URL')
    if parsed.path != '/inform' or parsed.query or parsed.fragment:
        raise ValueError('Expected /inform URL without query or fragment')
    address = socket.gethostbyname(parsed.hostname)
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
    body = {'model': MODEL, 'model_display': 'Huawei BE3 bridge (virtual U7 Pro)',
            'version': VERSION, 'required_version': '8.0.28',
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
    def __init__(self, directory, url='', wlan_map=None, control=None, device_lookup=None):
        self.directory = Path(directory)
        self.url = url
        self.wlan_map = validate_wlan_map(wlan_map if wlan_map is not None else {})
        self.control, self.device_lookup = control, device_lookup
        self.status = {}
        self.started = time.monotonic()
        self.next_send = {}
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def exchange(self, ap, snapshot):
        identity = ap['virtual_mac']
        path = self.directory / (identity.replace(':', '') + '.json')
        state = json.loads(path.read_text()) if path.exists() else {'phase': 'factory'}
        if state.get('controller_url', self.url) != self.url:
            raise ValueError('State belongs to another controller')
        state['controller_url'] = self.url
        state['virtual_mac'] = identity
        mapping = self.wlan_map
        controlled = bool(self.control and self.control.allowed_ip == ap.get('ip'))
        if self.control:
            mapping = self.control.mapping(ap, mapping)
            ack = self.control.ack(ap)
            if controlled and ack is not None:
                state['cfgversion'] = ack
            elif controlled:
                state['cfgversion'] = ''  # Request a baseline without applying existing defaults.
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
        if controlled and decoded.body.get('_type') == 'setdefault':
            self.control.reset(ap)
        if controlled and decoded.body.get('_type') == 'setparam' and 'system_cfg' in decoded.body:
            try:
                self.control.receive(ap, decoded.body, self.device_lookup(ap['id']), mapping)
            except ValueError as exc:
                self.control.reject(ap, exc)
            state['cfgversion'] = self.control.ack(ap) or previous_cfg
        state['last_success'] = time.time()
        atomic_json(path, state)
        self.status[identity] = {'phase': state['phase'], 'last_success': state['last_success'],
                                  'model': MODEL, 'experimental': True,
                                  'stations_sent': sum(len(v['sta_table']) for v in data['vap_table']),
                                  'unmapped_ssids': sorted({r['ssid'] for r in ap['radios']
                                      if r.get('enabled') is not False and not r.get('stale')
                                      and r['ssid'] not in mapping})}
        if self.control:
            self.status[identity]['configuration'] = self.control.public(ap)
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
                self.status[ap['virtual_mac']] = {'phase': 'error', 'error': type(exc).__name__,
                    'http_status': getattr(exc, 'code', None), 'experimental': True}
                self.next_send[ap['virtual_mac']] = time.monotonic() + 30
