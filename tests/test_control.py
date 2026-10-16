import copy
import tempfile
import unittest
from unittest.mock import patch

from huawei_bridge.control import Control, translate

PATH = 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.1'
CONFIG = '''radio.1.phyname=wifi0
radio.1.channel=6
radio.1.status=enabled
wireless.1.parent=wifi0
wireless.1.devname=ath0
wireless.1.ssid=Lab
wireless.1.status=enabled
wireless.1.wmm=enabled
wireless.1.security=wpapsk
aaa.1.wpa.psk=never-persist-this
users.1.password=never-persist-this
ebtables.1.cmd=never-execute-this
'''
BINDINGS = {'wifi0': {'path': PATH, 'wlan_id': '0123456789abcdef01234567'}}


def device(stamp='2026-10-16T21:00:00Z', channel=1):
    fields = {'SSID': 'Lab', 'Channel': channel, 'AutoChannelEnable': False,
              'Enable': True, 'WMMEnable': True, 'PossibleChannels': '1,6,11'}
    wlan = {k: {'_value': v, '_writable': k != 'PossibleChannels', '_timestamp': stamp}
            for k, v in fields.items()}
    return {'_id': 'cpe', 'InternetGatewayDevice': {'LANDevice': {'1': {'WLANConfiguration': {'1': wlan}}}}}


class TranslationTests(unittest.TestCase):
    def test_allowlist_and_explicit_radio_binding(self):
        desired = translate(CONFIG, BINDINGS, device())
        self.assertEqual(desired, {PATH + '.SSID': ['Lab', 'xsd:string'],
            PATH + '.Channel': [6, 'xsd:unsignedInt'], PATH + '.AutoChannelEnable': [False, 'xsd:boolean'],
            PATH + '.Enable': [True, 'xsd:boolean'], PATH + '.WMMEnable': [True, 'xsd:boolean']})
        self.assertNotIn('never-persist-this', str(desired))

    def test_auto_does_not_translate_channel_zero(self):
        result = translate(CONFIG.replace('channel=6', 'channel=auto'), BINDINGS, device())
        self.assertEqual(result[PATH + '.AutoChannelEnable'][0], True)
        self.assertNotIn(PATH + '.Channel', result)

    def test_rejects_invalid_nonwritable_and_ambiguous_values(self):
        for config in (CONFIG.replace('channel=6', 'channel=13'),
                       CONFIG.replace('status=enabled', 'status=bad'),
                       CONFIG + 'radio.1.channel=11\n',
                       CONFIG.replace('ssid=Lab', 'ssid=')):
            with self.assertRaises(ValueError):
                translate(config, BINDINGS, device())
        raw = device()
        raw['InternetGatewayDevice']['LANDevice']['1']['WLANConfiguration']['1']['Channel']['_writable'] = False
        with self.assertRaises(ValueError):
            translate(CONFIG, BINDINGS, raw)

    def test_missing_radio_does_not_disable_it(self):
        self.assertEqual(translate('', BINDINGS, device()), {})


class ControlTests(unittest.TestCase):
    def test_baseline_then_task_readback_and_restart(self):
        calls = []
        def request(path, data=None):
            calls.append((path, data))
            if data:
                return {'_id': 'task1'}
            return []
        ap = {'id': 'cpe', 'ip': '192.0.2.2', 'virtual_mac': '02:00:00:00:00:01',
              'radios': [{'id': PATH, 'ssid': 'Lab', 'band': '2.4g'}]}
        mapping = {'Lab': '0123456789abcdef01234567'}
        with tempfile.TemporaryDirectory() as tmp:
            control = Control(tmp, request, '192.0.2.2')
            control.receive(ap, {'system_cfg': CONFIG.replace('channel=6', 'channel=1'), 'cfgversion': 'a'}, device(), mapping)
            self.assertEqual(calls, [])
            self.assertEqual(control.public(ap)['phase'], 'baseline')
            control.receive(ap, {'system_cfg': CONFIG, 'cfgversion': 'b'}, device(), mapping)
            self.assertEqual(control.public(ap)['phase'], 'queued')
            self.assertEqual(len(calls), 1)
            self.assertIn('setParameterValues', calls[0][1].decode())
            control.receive(ap, {'system_cfg': CONFIG, 'cfgversion': 'b'}, device(), mapping)
            self.assertEqual(len(calls), 1)
            restarted = Control(tmp, request, '192.0.2.2')
            restarted.record(ap)
            restarted.reconcile([device(channel=1)])
            self.assertEqual(restarted.public(ap)['phase'], 'reading')
            self.assertEqual(restarted.ack(ap), 'a')
            restarted.reconcile([device('2099-01-01T00:00:00Z', channel=6)])
            self.assertEqual(restarted.public(ap)['phase'], 'confirmed')
            self.assertEqual(restarted.ack(ap), 'b')
            restarted.reset(ap)
            self.assertIsNone(restarted.ack(ap))
            calls.clear()
            restarted.receive(ap, {'system_cfg': CONFIG.replace('channel=6', 'channel=11'), 'cfgversion': 'new-controller'}, device(), mapping)
            self.assertEqual(restarted.public(ap)['phase'], 'baseline')
            self.assertEqual(calls, [])

    def test_fault_timeout_and_unknown_submission_never_ack_or_retry(self):
        ap = {'id': 'cpe', 'ip': '192.0.2.2', 'virtual_mac': '02:00:00:00:00:01',
              'radios': [{'id': PATH, 'ssid': 'Lab', 'band': '2.4g'}]}
        for outcome in ('fault', 'timeout', 'unknown'):
            calls = []
            def request(path, data=None):
                calls.append((path, data))
                if data:
                    if outcome == 'unknown':
                        raise TimeoutError()
                    return {'_id': 'task1'}
                return [{'channel': 'task_task1'}] if path.startswith('/faults') and outcome == 'fault' else []
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                control = Control(tmp, request, '192.0.2.2')
                control.receive(ap, {'system_cfg': CONFIG, 'cfgversion': 'a'}, device(), {'Lab': BINDINGS['wifi0']['wlan_id']})
                control.receive(ap, {'system_cfg': CONFIG.replace('channel=6', 'channel=11'), 'cfgversion': 'b'}, device(), {})
                with patch('huawei_bridge.control.time.time', return_value=control.record(ap)['requested_at'] + 301):
                    control.reconcile([device()])
                self.assertEqual(control.public(ap)['phase'], 'uncertain' if outcome == 'unknown' else 'failed')
                self.assertEqual(control.ack(ap), 'a')
                control.reconcile([device()])
                self.assertEqual(sum(data is not None for _, data in calls), 1)

    def test_wrong_device_never_writes(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            control = Control(tmp, lambda *args: calls.append(args), '192.0.2.2')
            ap = {'id': 'other', 'ip': '192.0.2.99', 'virtual_mac': '02:00:00:00:00:02'}
            control.receive(ap, {'system_cfg': CONFIG, 'cfgversion': 'a'}, device(), {})
            self.assertEqual(calls, [])

    def test_ambiguous_ip_disables_writes_fail_safe(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            control = Control(tmp, lambda *args: calls.append(args), '192.0.2.2')
            ap = {'id': 'cpe', 'ip': '192.0.2.2', 'virtual_mac': '02:00:00:00:00:01'}
            other = {'id': 'other', 'ip': '192.0.2.2'}
            self.assertFalse(control.controlled(ap, [ap, other]))
            self.assertTrue(control.controlled(ap, [ap]))
            control.receive(ap, {'system_cfg': CONFIG, 'cfgversion': 'a'}, device(), {}, [ap, other])
            self.assertEqual(calls, [])

    def test_device_id_overrides_ip_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Control(tmp, lambda *args: None, '192.0.2.2', device_id='cpe')
            self.assertTrue(control.controlled({'id': 'cpe', 'ip': '203.0.113.5'}))
            self.assertFalse(control.controlled({'id': 'other', 'ip': '192.0.2.2'}))
