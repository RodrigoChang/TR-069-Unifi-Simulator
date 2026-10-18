import unittest

from huawei_bridge.mesh import normalize_web

A = '02:00:00:00:00:02'
B = '02:00:00:00:00:04'
C = '02:00:00:00:00:06'


class MeshTests(unittest.TestCase):
    def normalize(self, **values):
        return normalize_web(dict(collected_at=100, **values), now=101)

    def test_hilink_nested_membership(self):
        result = self.normalize(hilink=[{'MACAddress': A, 'HiLinkType': 'Device',
            'ConnectedDevices': [{'MACAddress': C, 'HiLinkType': 'None'}]}])
        self.assertEqual(result['clients'][0]['parent_mac'], A)
        self.assertIsNone(result['nodes'][0]['online'])

    def test_easymesh_parent_and_roles(self):
        result = self.normalize(easymesh={'routers': [
            {'devMac': A, 'apRole': 'controller'},
            {'devMac': B, 'apRole': 'agent', 'uplinkAp': A, 'devices': [{'devMac': C}]}]})
        self.assertEqual(result['nodes'][1]['parent_mac'], A)
        self.assertEqual(result['clients'][0]['parent_mac'], B)

    def test_missing_parent_remains_unknown(self):
        result = self.normalize(hosts=[{'MACAddress': C, 'Active': True}])
        self.assertIsNone(result['clients'][0]['parent_mac'])

    def test_conflicting_parent_is_unresolved(self):
        result = self.normalize(easymesh={'routers': [
            {'devMac': A, 'devices': [{'devMac': C}]},
            {'devMac': B, 'devices': [{'devMac': C}]}]})
        self.assertIsNone(result['clients'][0]['parent_mac'])
        self.assertTrue(result['warnings'])

    def test_empty_children_do_not_prove_zero_clients(self):
        result = self.normalize(easymesh={'routers': [{'devMac': A, 'devices': []}]})
        self.assertIsNone(result['nodes'][0]['client_count'])

    def test_unknown_phy_stays_unknown(self):
        result = self.normalize(hosts=[{'MACAddress': C, 'phyMode': 'future'},
                                      {'MACAddress': B, 'phyMode': '802.11ax'}])
        self.assertIsNone(result['clients'][0]['wifi_generation'])
        self.assertEqual(result['clients'][1]['wifi_generation'], 6)

    def test_malformed_and_cyclic_topology_is_bounded(self):
        node = {'MACAddress': A, 'HiLinkType': 'Device'}
        node['ConnectedDevices'] = [node]
        for topology in ([node], 'invalid', [{}] * 4097):
            result = self.normalize(hilink=topology)
            self.assertEqual(result['nodes'], [])
            self.assertTrue(result['warnings'])

    def test_failed_poll_keeps_old_age(self):
        result = normalize_web({'collected_at': 100, 'hosts': [{'MACAddress': C}]}, now=281)
        self.assertEqual(result['clients'][0]['updated_at'], 100)
        self.assertTrue(result['clients'][0]['stale'])

    def test_conflicts_cannot_be_erased_by_a_third_observation(self):
        result = self.normalize(hosts=[{'MACAddress': C, 'phyMode': mode}
                                       for mode in ('802.11ax', '802.11ac', '802.11ax')])
        self.assertIsNone(result['clients'][0]['wifi_generation'])

    def test_invalid_optional_fields_do_not_break_snapshot(self):
        result = self.normalize(hosts=[{'MACAddress': C, 'InterfaceType': [], 'phyMode': {}}])
        self.assertIsNone(result['clients'][0]['band'])

    def test_semantic_cycle_does_not_prove_ownership(self):
        result = self.normalize(easymesh={'routers': [
            {'devMac': A, 'uplinkAp': B}, {'devMac': B, 'uplinkAp': A}]})
        self.assertTrue(all(n['parent_mac'] is None for n in result['nodes']))
        self.assertTrue(result['warnings'])

    def test_router_model_from_explicit_custinfo(self):
        result = self.normalize(deviceinfo={'MACAddress': A, 'custinfo': {'CustDeviceName': 'WS7200'}})
        self.assertEqual(result['nodes'][0]['model'], 'WS7200')

    def test_agent_contradicted_by_host_is_not_a_router(self):
        result = self.normalize(easymesh={'routers': [{'devMac': A, 'apRole': 'agent',
            'devices': [{'devMac': C}]}]}, hosts=[{'MACAddress': A,
            'IsSlave': False, 'HiLinkDevice': False, 'Active': True}])
        node = result['nodes'][0]
        self.assertEqual(node.get('classification'), 'conflict')
        self.assertIsNone(node['role'])
        self.assertEqual(node['reported_role'], 'agent')
        self.assertIsNone(next(c for c in result['clients'] if c['mac'] == C)['parent_mac'])
        self.assertIn(A, [c['mac'] for c in result['clients']])
        self.assertTrue(result['warnings'])

    def test_slave_hosts_are_candidates_even_without_hilink_flag(self):
        for hilink in (False, True):
            result = self.normalize(hosts=[{'MACAddress': A, 'IsSlave': True,
                                           'HiLinkDevice': hilink, 'Active': False}])
            self.assertEqual(result['nodes'][0]['classification'], 'candidate')
            self.assertIsNone(result['nodes'][0]['role'])
            self.assertIsNone(result['nodes'][0]['parent_mac'])

    def test_hilink_flag_or_string_slave_does_not_promote_host(self):
        result = self.normalize(hosts=[{'MACAddress': A, 'HiLinkDevice': True,
                                       'IsSlave': 'true'}])
        self.assertEqual(result['nodes'], [])

    def test_conflicting_slave_flags_are_order_independent(self):
        for flags in ((True, False, True), (False, True, False)):
            result = self.normalize(hosts=[{'MACAddress': A, 'IsSlave': flag}
                                           for flag in flags])
            self.assertEqual(result['nodes'][0]['classification'], 'conflict')

    def test_known_huawei_oui_is_only_manufacturer_hint(self):
        result = self.normalize(easymesh={'routers': [{'devMac': '00:E0:FC:00:00:01',
                                                        'apRole': 'agent'}]})
        node = result['nodes'][0]
        self.assertEqual(node['manufacturer'], 'Huawei (probable)')
        self.assertEqual(node['classification'], 'reported')
        self.assertEqual(node['role'], 'agent')

    def test_unknown_or_locally_administered_mac_has_no_vendor_hint(self):
        result = self.normalize(hosts=[{'MACAddress': '02:E0:FC:00:00:01'},
                                       {'MACAddress': '00:00:00:00:00:01'}])
        self.assertTrue(all('manufacturer' not in row for row in result['clients']))
