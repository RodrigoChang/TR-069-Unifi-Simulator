import copy
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from huawei_bridge.__main__ import Application
from huawei_bridge.mesh import merge_web

A = '02:00:00:00:00:02'
C = '02:00:00:00:00:06'


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'aps': [{'id': 'cpe', 'virtual_mac': '02:11:22:33:44:55', 'online': True,
            'radios': [{'id': 'radio', 'stale': False, 'enabled': True, 'band': '5g'}]}],
            'clients': [{'ap_id': 'cpe', 'radio_id': 'radio', 'mac': C, 'band': '5g',
                         'signal': None, 'stale': False, 'updated_at': 10}], 'warnings': []}
        self.devices = [{'_id': 'cpe', 'InternetGatewayDevice': {'DeviceInfo': {'MACAddress': {'_value': A}}}}]
        self.raw = {'collected_at': time.time(), 'deviceinfo': {'MACAddress': A},
                    'hosts': [{'MACAddress': C, 'phyMode': '802.11ax', 'rssi': -55, 'InterfaceType': '5GHz'}],
                    'easymesh': {'routers': [{'devMac': A, 'devices': [{'devMac': C}]}]}}

    def merge(self):
        return merge_web(self.snapshot, self.raw, self.devices)

    def test_disabled_source_preserves_existing_snapshot(self):
        result = merge_web(self.snapshot, {}, self.devices)
        self.assertEqual(result['aps'], self.snapshot['aps'])
        self.assertEqual(result['clients'], self.snapshot['clients'])

    def test_unique_physical_match_preserves_virtual_identity(self):
        result = self.merge()
        self.assertEqual(result['aps'], self.snapshot['aps'])
        self.assertEqual(result['clients'][0]['wifi_generation'], 6)
        self.assertEqual(result['clients'][0]['updated_at'], 10)
        self.assertNotIn('wifi_generation', self.snapshot['clients'][0])

    def test_ip_or_bssid_only_match_is_rejected(self):
        self.devices[0]['InternetGatewayDevice']['DeviceInfo'] = {}
        self.assertNotIn('wifi_generation', self.merge()['clients'][0])

    def test_unmatched_nodes_inventory_only(self):
        self.raw['easymesh']['routers'].append({'devMac': '02:00:00:00:00:08'})
        result = self.merge()
        self.assertEqual(len(result['aps']), 1)
        self.assertEqual(len(result['mesh']['nodes']), 2)

    def test_web_inventory_cannot_become_control_target(self):
        self.assertEqual(self.merge()['aps'], self.snapshot['aps'])

    def test_conflicting_node_cannot_enrich_acs_clients(self):
        self.raw['easymesh']['routers'][0]['apRole'] = 'agent'
        self.raw['hosts'].append({'MACAddress': A, 'IsSlave': False})
        result = self.merge()
        self.assertEqual(result['clients'], self.snapshot['clients'])
        self.assertEqual(result['aps'], self.snapshot['aps'])

    def test_conflicting_parent_does_not_move_station(self):
        self.raw['easymesh']['routers'].append({'devMac': '02:00:00:00:00:08', 'devices': [{'devMac': C}]})
        self.assertEqual(self.merge()['clients'], self.snapshot['clients'])

    def test_web_does_not_reactivate_stale_acs_station(self):
        self.snapshot['clients'][0]['stale'] = True
        self.assertEqual(self.merge()['clients'], self.snapshot['clients'])

    def test_fresh_acs_fields_win_over_conflicting_web_values(self):
        self.snapshot['clients'][0]['signal'] = -30
        result = self.merge()
        self.assertEqual(result['clients'][0]['signal'], -30)
        self.assertTrue(result['warnings'])

    def test_duplicate_physical_identity_is_not_enriched(self):
        self.devices.append(dict(self.devices[0], _id='other'))
        self.assertEqual(self.merge()['clients'], self.snapshot['clients'])

    def test_explicit_parent_disagreement_is_reported(self):
        self.raw['easymesh']['routers'] = [{'devMac': '02:00:00:00:00:08', 'devices': [{'devMac': C}]}]
        result = self.merge()
        self.assertEqual(result['clients'], self.snapshot['clients'])
        self.assertTrue(any('parent conflict' in warning for warning in result['warnings']))

    def application(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict('os.environ', {'STATE_DIR': tmp.name, 'HUAWEI_WEB_URL': '', 'UNIFI_INFORM_URL': ''}):
            app = Application()
        self.addCleanup(app.lockfile.close)
        return app

    def test_web_failure_preserves_acs_reporting(self):
        app = self.application()
        app.web_snapshot = copy.deepcopy(self.raw)
        app.web.collect = lambda: (_ for _ in ()).throw(ValueError('secret'))
        before = app.snapshot()['aps']
        app.collect_web()
        self.assertEqual(app.snapshot()['aps'], before)
        self.assertEqual(app.web_snapshot['collected_at'], self.raw['collected_at'])
        self.assertEqual(app.snapshot()['web_status']['error'], 'ValueError')
        self.assertIsNone(app.last_error)

    def test_application_uses_configured_host_header(self):
        from test_web import Peer
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict('os.environ', {
            'STATE_DIR': tmp.name, 'UNIFI_INFORM_URL': '',
            'HUAWEI_WEB_URL': 'https://router.example.test',
            'HUAWEI_WEB_HOST_HEADER': '192.0.2.1', 'HUAWEI_WEB_CA_FILE': '',
        }):
            app = Application()
        self.addCleanup(app.lockfile.close)
        peer = Peer()
        app.web.http = peer
        app.web._request('/html/index.html', html=True)
        self.assertEqual(peer.requests[0].get_header('Host'), '192.0.2.1')
        self.assertEqual(peer.requests[0].host, 'router.example.test')

    def test_web_poll_does_not_block_inform(self):
        app = self.application()
        started, release = threading.Event(), threading.Event()
        def slow():
            started.set()
            release.wait(2)
            return self.raw
        app.web.collect = slow
        thread = threading.Thread(target=app.collect_web)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            with app.mutex:
                app.snapshot()
        finally:
            release.set()
            thread.join(3)
