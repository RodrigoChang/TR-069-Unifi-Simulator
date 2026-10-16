"""Allowlisted UniFi Wi-Fi provisioning, with ACS task and readback confirmation."""

import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from .collector import ip, timestamp, val


def field(device, path):
    result = device
    for key in path.split('.'):
        result = result.get(key, {}) if isinstance(result, dict) else {}
    return result if isinstance(result, dict) else {}


def enabled(value):
    if value not in ('enabled', 'disabled'):
        raise ValueError('Invalid enabled/disabled value')
    return value == 'enabled'


def translate(config, bindings, device):
    if not isinstance(config, str) or len(config) > 1024 * 1024:
        raise ValueError('Invalid provisioning text')
    values = {}
    for line in config.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        if not re.fullmatch(r'(radio|wireless)\.[0-9]+\.(phyname|parent|ssid|status|channel|wmm)', key):
            continue
        if key in values or '\x00' in value:
            raise ValueError('Duplicate or invalid provisioning field')
        values[key] = value
    desired = {}
    seen = set()
    for key, phy in values.items():
        if not key.endswith('.phyname') or not key.startswith('radio.') or phy not in bindings:
            continue
        if phy in seen:
            raise ValueError('Ambiguous radio binding')
        seen.add(phy)
        prefix = key.rsplit('.', 1)[0]
        path = bindings[phy]['path']
        if not re.fullmatch(r'InternetGatewayDevice\.LANDevice\.[0-9]+\.WLANConfiguration\.[0-9]+', path):
            raise ValueError('Invalid CPE radio binding')
        wlan = field(device, path)
        channel = values.get(prefix + '.channel')
        if channel == 'auto':
            desired[path + '.AutoChannelEnable'] = [True, 'xsd:boolean']
        elif channel is not None:
            allowed = str(val(wlan, 'PossibleChannels', '')).split(',')
            if not (channel.isascii() and channel.isdigit()) or channel not in allowed:
                raise ValueError('Channel not advertised by CPE')
            desired[path + '.AutoChannelEnable'] = [False, 'xsd:boolean']
            desired[path + '.Channel'] = [int(channel), 'xsd:unsignedInt']
        status = values.get(prefix + '.status')
        if status is not None:
            desired[path + '.Enable'] = [enabled(status), 'xsd:boolean']
        wireless = [k.rsplit('.', 1)[0] for k, v in values.items()
                    if k.startswith('wireless.') and k.endswith('.parent') and v == phy]
        if len(wireless) > 1:
            raise ValueError('Multiple VAPs on one Huawei radio are not supported')
        if wireless:
            wp = wireless[0]
            ssid = values.get(wp + '.ssid')
            if ssid is not None:
                if not ssid or len(ssid.encode('utf-8')) > 32:
                    raise ValueError('Invalid SSID')
                desired[path + '.SSID'] = [ssid, 'xsd:string']
            if wp + '.status' in values:
                desired[path + '.Enable'] = [enabled(values[wp + '.status']) and
                    (enabled(status) if status is not None else True), 'xsd:boolean']
            if wp + '.wmm' in values:
                desired[path + '.WMMEnable'] = [enabled(values[wp + '.wmm']), 'xsd:boolean']
    for path, pair in desired.items():
        current = field(device, path)
        if not current or (current.get('_value') != pair[0] and current.get('_writable') is not True):
            raise ValueError('CPE field is absent or not writable')
        if path.endswith('.Channel') and current.get('_type') in ('xsd:int', 'xsd:unsignedInt'):
            pair[1] = current['_type']
    return desired


class Control:
    def __init__(self, directory, request, allowed_ip='', device_id=''):
        self.directory = Path(directory)
        self.request = request
        self.allowed_ip = ip(allowed_ip)
        if allowed_ip and not self.allowed_ip:
            raise ValueError('Invalid HUAWEI_CONTROL_IP')
        self.device_id = device_id.strip() if isinstance(device_id, str) and device_id.strip() else None
        self.records = {}

    def controlled(self, ap, aps=None):
        """Decide whether writes are permitted for this device (fail-safe by default).

        ``aps`` is the full normalized device list for the current collection, when
        available. If a configured HUAWEI_CONTROL_IP matches more than one known
        device, writes are rejected rather than guessing which one is intended.
        """
        if self.device_id is not None:
            return ap.get('id') == self.device_id
        if not self.allowed_ip or ap.get('ip') != self.allowed_ip:
            return False
        if aps is not None and sum(1 for a in aps if a.get('ip') == self.allowed_ip) > 1:
            return False
        return True

    def record(self, ap):
        identity = ap['virtual_mac']
        if identity not in self.records:
            path = self.directory / (identity.replace(':', '') + '.json')
            self.records[identity] = json.loads(path.read_text()) if path.exists() else {}
        record = self.records[identity]
        if record.get('device', ap['id']) != ap['id']:
            raise ValueError('Provisioning identity binding mismatch')
        return record

    def save(self, ap):
        from .unifi import atomic_json
        atomic_json(self.directory / (ap['virtual_mac'].replace(':', '') + '.json'), self.record(ap))

    def public(self, ap, aps=None):
        record = self.record(ap)
        return {key: record[key] for key in ('phase', 'error', 'requested_at', 'confirmed_at', 'task_id') if key in record} | {
            'enabled': self.controlled(ap, aps),
            'parameters': sorted(record.get('changes', {}))}

    def mapping(self, ap, original):
        result = dict(original)
        for binding in self.record(ap).get('bindings', {}).values():
            for radio in ap.get('radios', []):
                if radio['id'] == binding['path']:
                    result[radio['ssid']] = binding['wlan_id']
        return result

    def ack(self, ap):
        return self.record(ap).get('ack_version')

    def reset(self, ap):
        record = self.record(ap)
        if record.get('phase') in ('queued', 'reading', 'submitting', 'uncertain'):
            record.update(phase='uncertain', error='Controller reset while ACS task pending; inspect before retry')
        else:
            for key in ('desired', 'revision', 'ack_version', 'changes', 'error'):
                record.pop(key, None)
            record['phase'] = 'awaiting-baseline'
        self.save(ap)

    def reject(self, ap, error):
        record = self.record(ap)
        record.update(phase='rejected', error=str(error))
        self.save(ap)

    def receive(self, ap, response, device, mapping, aps=None):
        if not self.controlled(ap, aps) or response.get('system_cfg') is None:
            return
        record = self.record(ap)
        revision = response.get('cfgversion')
        if not isinstance(revision, str) or not revision or len(revision) > 256:
            raise ValueError('Invalid provisioning revision')
        if revision == record.get('revision'):
            return
        if record.get('phase') in ('queued', 'reading', 'submitting', 'uncertain'):
            return  # Finish the in-flight revision before consuming the next one.
        bindings = record.get('bindings', {})
        if not bindings:
            for phy, band in (('wifi0', '2.4g'), ('wifi1', '5g')):
                radios = [r for r in ap.get('radios', []) if r['band'] == band and r['ssid'] in mapping]
                if len(radios) > 1:
                    raise ValueError('Ambiguous Huawei radio')
                if radios:
                    r = radios[0]
                    bindings[phy] = {'path': r['id'], 'wlan_id': mapping[r['ssid']]}
        desired = translate(response['system_cfg'], bindings, device)
        if not desired:
            raise ValueError('No supported fields matched the bound radios')
        if 'desired' not in record:
            record.update(device=ap['id'], ip=ap['ip'], bindings=bindings, desired=desired,
                          revision=revision, ack_version=revision, phase='baseline', error=None)
            self.save(ap)
            return  # Existing controller defaults are not silently imposed on the router.
        changes = {p: v for p, v in desired.items() if (record.get('desired', {}).get(p) != v or record.get('phase') == 'failed')
                   and field(device, p).get('_value') != v[0]}
        record.update(revision=revision, desired=desired, changes=changes, error=None)
        if not changes:
            record.update(phase='confirmed', ack_version=revision, confirmed_at=time.time())
            self.save(ap)
            return
        record.update(phase='submitting', requested_at=time.time(), task_id=None, refresh_id=None)
        self.save(ap)
        try:
            task = self.request('/devices/' + quote(ap['id'], safe='') + '/tasks', json.dumps({
                'name': 'setParameterValues', 'parameterValues': [[p, *v] for p, v in changes.items()]}).encode())
            if not isinstance(task.get('_id'), str):
                raise ValueError('ACS omitted task ID')
            record.update(phase='queued', task_id=task['_id'])
        except Exception as exc:
            record.update(phase='uncertain', error=type(exc).__name__)
        self.save(ap)

    def reconcile(self, devices):
        for device in devices:
            ap = next(({'id': device['_id'], 'virtual_mac': k} for k, r in self.records.items()
                       if r.get('device') == device.get('_id')), None)
            if not ap:
                continue
            record = self.record(ap)
            if record.get('phase') not in ('queued', 'reading', 'submitting', 'uncertain'):
                continue
            if record['phase'] in ('submitting', 'uncertain'):
                record.update(phase='uncertain', error='Submission outcome unknown; no automatic retry')
                self.save(ap)
                continue
            changes = record['changes']
            if all(field(device, p).get('_value') == v[0] and
                   (timestamp(field(device, p).get('_timestamp')) or 0) >= record['requested_at']
                   for p, v in changes.items()):
                record.update(phase='confirmed', ack_version=record['revision'], confirmed_at=time.time(), error=None)
                self.save(ap)
                continue
            query = urlencode({'query': json.dumps({'device': ap['id']})})
            faults = self.request('/faults?' + query)
            ids = {record.get('task_id'), record.get('refresh_id')} - {None}
            if any(any(str(f.get('channel', '')).endswith(task) for task in ids) for f in faults):
                record.update(phase='failed', error='ACS task fault')
            elif time.time() - record['requested_at'] > 300:
                record.update(phase='failed', error='Readback timeout')
            else:
                tasks = self.request('/tasks?' + query)
                if not any(t.get('_id') in ids for t in tasks) and not record.get('refresh_id'):
                    result = self.request('/devices/' + quote(ap['id'], safe='') + '/tasks', json.dumps({
                        'name': 'getParameterValues', 'parameterNames': sorted(changes)}).encode())
                    record.update(phase='reading', refresh_id=result.get('_id'))
            self.save(ap)
