import unittest

from huawei_bridge.unifi import profile_for, selected_profile, payload


class ProfileTests(unittest.TestCase):
    def test_existing_be3_profile_and_identity_unchanged(self):
        state = {'authkey': 'ab' * 16, 'virtual_mac': '02:00:00:00:00:02'}
        before = dict(state)
        self.assertEqual(selected_profile({'name': 'WUKUN-BE32'}, state)['model'], 'U7PRO')
        self.assertEqual(state['authkey'], before['authkey'])
        self.assertEqual(state['virtual_mac'], before['virtual_mac'])

    def test_ax3_has_no_inferred_wifi7_or_160mhz(self):
        self.assertIsNone(profile_for({'name': 'WS7200'}))
        ap = {'name': 'WS7200', 'virtual_mac': '02:00:00:00:00:02', 'ip': None,
              'radios': [{'id': 'radio', 'band': '5g', 'channel': 36, 'ssid': 'Example', 'standards': ['ax']}]}
        state = {'authkey': 'ab' * 16}
        selected_profile(ap, state)
        radio = payload(ap, {'clients': []}, state, 'http://192.0.2.1/inform', 1)['radio_table'][0]
        self.assertFalse(radio['is_11be'])
        self.assertFalse(radio['has_ht160'])

    def test_unknown_model_is_inventory_only(self):
        self.assertIsNone(selected_profile({'name': 'Unknown'}, {}))

    def test_adopted_profile_mismatch_requires_explicit_migration(self):
        state = {'authkey': 'ab' * 16, 'profile': {'model': 'EXISTING', 'model_display': 'Existing',
                 'version': '1', 'required_version': '1'}}
        self.assertEqual(selected_profile({'name': 'WUKUN-BE32'}, state)['model'], 'EXISTING')
        self.assertTrue(state['profile_migration_required'])
