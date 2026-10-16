import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from huawei_bridge.__main__ import handler
from huawei_bridge.collector import mac, normalize, signal, virtual_mac
from huawei_bridge.control import Control
from huawei_bridge.protocol import BOOTSTRAP_KEY, ProtocolError, decode, encode
from huawei_bridge.unifi import UniFi, apply_response, payload, validate_wlan_map

NOW = 1792184400
STAMP = '2026-10-16T21:00:00Z'


def field(value):
    return {'_value': value, '_timestamp': STAMP}


def fixture():
    wlan = {'SSID': field('Test <SSID>'), 'Channel': field(36), 'Enable': field(True),
            'BSSID': field('00:11:22:33:44:50'), 'AssociatedDevice': {'1': {
                'AssociatedDeviceMACAddress': field('00:11:22:33:44:66'),
                'AssociatedDeviceIPAddress': field('192.0.2.10'),
                'AssociatedDeviceRssi': field(55)}}}
    return [{'_id': 'test-ap', '_deviceId': {'_Manufacturer': 'Huawei Device Co., Ltd.',
            '_ProductClass': 'TestAP'}, '_lastInform': STAMP, 'InternetGatewayDevice': {
            'ManagementServer': {'ConnectionRequestURL': field('https://192.0.2.2:7547/callback')},
            'DeviceInfo': {'SoftwareVersion': field('test')},
            'LANDevice': {'1': {'WLANConfiguration': {'2': wlan}, 'Hosts': {'Host': {'1': {
                'MACAddress': field('00:11:22:33:44:66'), 'HostName': field('Client'),
                'IPAddress': field('192.0.2.10')}}}}}}}]


class CollectorTests(unittest.TestCase):
    def test_real_fields_and_identity(self):
        result = normalize(fixture(), NOW)
        ap, client = result['aps'][0], result['clients'][0]
        self.assertTrue(ap['online'])
        self.assertEqual(ap['ip'], '192.0.2.2')
        self.assertEqual(ap['client_count'], 1)
        self.assertEqual(client['signal'], -55)
        self.assertEqual(client['hostname'], 'Client')
        self.assertEqual(client['radio_id'], ap['radios'][0]['id'])
        self.assertEqual(ap['virtual_mac'], virtual_mac('test-ap'))
        self.assertEqual(int(ap['virtual_mac'][:2], 16) & 3, 2)

    def test_old_values_are_not_online(self):
        result = normalize(fixture(), NOW + 181)
        self.assertFalse(result['aps'][0]['online'])
        self.assertTrue(result['clients'][0]['stale'])
        self.assertEqual(result['aps'][0]['client_count'], 0)

    def test_deduplicate_and_disabled_radio(self):
        devices = fixture()
        radios = devices[0]['InternetGatewayDevice']['LANDevice']['1']['WLANConfiguration']
        radios['3'] = copy.deepcopy(radios['2'])
        self.assertEqual(len(normalize(devices, NOW)['clients']), 1)
        radios['2']['Enable'] = field(False)
        radios['3']['Enable'] = field(False)
        self.assertEqual(normalize(devices, NOW)['clients'], [])

    def test_unknown_signal_and_other_manufacturer(self):
        self.assertIsNone(signal(0))
        self.assertIsNone(signal(True))
        self.assertIsNone(signal(float('nan')))
        self.assertIsNone(mac('ff:ff:ff:ff:ff:ff'))
        devices = fixture()
        devices[0]['_deviceId']['_Manufacturer'] = 'Other'
        self.assertEqual(normalize(devices, NOW)['aps'], [])


class ProtocolTests(unittest.TestCase):
    def test_radio_capabilities_are_not_client_generations_or_measured_width(self):
        raw = fixture()
        wlan = raw[0]['InternetGatewayDevice']['LANDevice']['1']['WLANConfiguration']['2']
        wlan['Standard'] = field('a/n/ac/ax/be')
        raw[0]['_deviceId']['_ProductClass'] = 'WUKUN-BE32'
        snapshot = normalize(raw, NOW)
        body = payload(snapshot['aps'][0], snapshot, {}, 'http://192.0.2.1:8080/inform', 12,
                       {'Test <SSID>': '0123456789abcdef01234567'})
        radio = body['radio_table'][0]
        self.assertTrue(radio['has_ht160'])
        self.assertTrue(radio['is_11ac'] and radio['is_11ax'] and radio['is_11be'])
        self.assertNotIn('bw', body['radio_table_stats'][0])
        self.assertEqual(body['radio_table_stats'][0]['channel'], 36)
        self.assertNotIn('is_11be', body['vap_table'][0]['sta_table'][0])
        wlan['Standard'] = field('b/g/n/ax')
        wlan['Channel'] = field(1)
        snapshot = normalize(raw, NOW)
        radio = payload(snapshot['aps'][0], snapshot, {}, 'http://192.0.2.1:8080/inform', 12)['radio_table'][0]
        self.assertTrue(radio['is_11ax'])
        self.assertFalse(radio['is_11be'] or radio['is_11ac'] or radio['has_ht160'])

    def test_invalid_wlan_map_is_rejected(self):
        for invalid in ([], {'SSID': 'fake'}, {'': '0123456789abcdef01234567'},
                        {'SSID': None}, {'x' * 33: '0123456789abcdef01234567'}):
            with self.assertRaises(ValueError):
                validate_wlan_map(invalid)

    def test_wlan_id_uses_controller_mapping(self):
        snapshot = normalize(fixture(), NOW)
        wlan_id = '0123456789abcdef01234567'
        body = payload(snapshot['aps'][0], snapshot, {}, 'http://192.0.2.1:8080/inform', 12,
                       {'Test <SSID>': wlan_id})
        self.assertEqual(body['vap_table'][0]['id'], wlan_id)
        self.assertEqual(body['vap_table'][0]['essid'], 'Test <SSID>')
        self.assertEqual(body['vap_table'][0]['num_sta'], 1)
        self.assertEqual(body['model'], 'U7PRO')
        self.assertEqual(body['required_version'], '8.0.28')
        self.assertEqual({r['radio'] for r in body['radio_table']}, {'na'})
        self.assertNotIn('channel_width', body['radio_table'][0])

    def test_unmapped_wlan_is_not_exported(self):
        snapshot = normalize(fixture(), NOW)
        body = payload(snapshot['aps'][0], snapshot, {}, 'http://192.0.2.1:8080/inform', 12)
        self.assertEqual(body['vap_table'], [])
        self.assertEqual(len(body['radio_table']), 1)

    def test_crypto_and_tamper(self):
        for mode in ('cbc', 'gcm'):
            raw = encode({'_type': 'noop'}, '02:00:00:00:00:01', mode=mode)
            self.assertEqual(decode(raw).body, {'_type': 'noop'})
            with self.assertRaises(ProtocolError):
                decode(raw[:-1])

    def test_commands_do_not_apply(self):
        state = {'phase': 'factory'}
        for kind in ('reboot', 'upgrade', 'cmd'):
            apply_response(state, {'_type': kind, 'command': 'touch /tmp/never'})
        self.assertEqual(state, {'phase': 'factory'})
        with self.assertRaises(ProtocolError):
            apply_response(state, {'_type': 'setparam', 'mgmt_cfg': 'authkey=invalid'})

    def test_payload_observed_clients_only(self):
        snapshot = normalize(fixture(), NOW)
        ap = snapshot['aps'][0]
        body = payload(ap, snapshot, {}, 'http://192.0.2.1:8080/inform', 12,
                       {'Test <SSID>': '0123456789abcdef01234567'})
        self.assertEqual(body['vap_table'][0]['num_sta'], 1)
        self.assertEqual(body['vap_table'][0]['sta_table'][0]['signal'], -55)
        self.assertEqual(len(body['vap_table'][0]['id']), 24)
        self.assertEqual(body['vap_table'][0]['sta_table'][0]['essid'], 'Test <SSID>')
        self.assertNotIn('tx_bytes', body['vap_table'][0])
        self.assertNotEqual(body['mac'], ap['radios'][0]['bssid'])

    def test_live_peer_adoption_and_restart(self):
        private_key = bytes.fromhex('11223344556677889900aabbccddeeff')
        received = []

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                key = BOOTSTRAP_KEY if not received else private_key
                packet = decode(self.rfile.read(int(self.headers['Content-Length'])), key)
                received.append(packet)
                response = {'_type': 'setparam', 'mgmt_cfg': 'authkey=' + private_key.hex() + '\nuse_aes_gcm=true'} if len(received) == 1 else {'_type': 'noop'}
                raw = encode(response, packet.mac, key, mode=packet.mode)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

        peer = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                url = f'http://127.0.0.1:{peer.server_port}/inform'
                snapshot = normalize(fixture(), NOW)
                ap = snapshot['aps'][0]
                mapping = {'Test <SSID>': '0123456789abcdef01234567'}
                UniFi(tmp, url, mapping).exchange(ap, snapshot)
                restarted = UniFi(tmp, url, mapping)
                restarted.exchange(ap, snapshot)
                self.assertEqual(restarted.status[ap['virtual_mac']]['phase'], 'exchanging')
                self.assertEqual(received[1].mode, 'gcm')
                self.assertEqual(received[1].body['vap_table'][0]['id'], '0123456789abcdef01234567')
                self.assertEqual(restarted.status[ap['virtual_mac']]['stations_sent'], 1)
                statefile = next(Path(tmp).glob('*.json'))
                self.assertEqual(statefile.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(ValueError):
                    UniFi(tmp, 'http://127.0.0.2:8080/inform').exchange(ap, snapshot)
        finally:
            peer.shutdown()
            peer.server_close()

    def test_forgotten_ap_resets_local_and_control_state(self):
        """Simulates deleting/forgetting the virtual AP in the UniFi controller."""
        private_key = bytes.fromhex('11223344556677889900aabbccddeeff')
        # Request 0 adopts with the public bootstrap key; request 1 (still using the
        # adopted key) is the controller's forget/factory-reset signal; request 2 is
        # the device's next, unauthenticated report once it is unadopted again.
        keys = [BOOTSTRAP_KEY, private_key, BOOTSTRAP_KEY]
        responses = [{'_type': 'setparam', 'mgmt_cfg': 'authkey=' + private_key.hex()},
                     {'_type': 'setdefault'}, {'_type': 'noop'}]
        received = []

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                index = len(received)
                key = keys[index]
                packet = decode(self.rfile.read(int(self.headers['Content-Length'])), key)
                received.append(packet)
                raw = encode(responses[index], packet.mac, key, mode=packet.mode)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(raw)

        peer = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                url = f'http://127.0.0.1:{peer.server_port}/inform'
                snapshot = normalize(fixture(), NOW)
                ap = snapshot['aps'][0]
                mapping = {'Test <SSID>': '0123456789abcdef01234567'}
                calls = []
                control = Control(tmp + '/control', lambda *a: calls.append(a), ap['ip'])
                bridge = UniFi(tmp + '/unifi', url, mapping, control, lambda identity: {})

                bridge.exchange(ap, snapshot)  # Adoption.
                self.assertEqual(bridge.status[ap['virtual_mac']]['phase'], 'key-received')

                bridge.exchange(ap, snapshot)  # Controller forgets the AP.
                self.assertEqual(bridge.status[ap['virtual_mac']]['phase'], 'factory')
                statefile = Path(tmp + '/unifi') / (ap['virtual_mac'].replace(':', '') + '.json')
                self.assertFalse(json.loads(statefile.read_text()).get('authkey'))
                self.assertEqual(control.public(ap)['phase'], 'awaiting-baseline')
                self.assertEqual(calls, [])  # Forgetting never triggers an ACS write task.

                bridge.exchange(ap, snapshot)  # Device resumes unauthenticated discovery.
                self.assertEqual(received[2].mode, 'cbc')
                self.assertTrue(received[2].body['default'])
                self.assertEqual(received[2].body['state'], 0)
        finally:
            peer.shutdown()
            peer.server_close()


class WebTests(unittest.TestCase):
    def test_host_and_origin_boundary(self):
        app = SimpleNamespace(allowed_hosts={'127.0.0.1:8090'}, public_url='http://127.0.0.1:8090',
                              snapshot=lambda: {'aps': []})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/api/snapshot'
        try:
            req = urllib.request.Request(url, headers={'Host': '127.0.0.1:8090'})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(json.load(response), {'aps': []})
            req.add_header('Origin', 'https://attacker.example')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
