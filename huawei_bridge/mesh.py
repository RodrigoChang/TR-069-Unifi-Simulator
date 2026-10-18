"""Bounded, observation-only Huawei inventory; never creates ACS/control targets."""
import copy
import time

from .collector import ip, mac, number, signal, text, val

PHY = {'802.11n': 4, '802.11ac': 5, '802.11ax': 6, '802.11be': 7}
BANDS = {'2.4GHz': '2.4g', '5GHz': '5g', '6GHz': '6g'}
# A small offline hint set, not a complete copy of the IEEE registry.
HUAWEI_OUIS = frozenset('''
    00:18:82 00:1E:10 00:25:68 00:25:9E 00:2E:C7 00:34:FE 00:46:4B
    00:4F:1A 00:5A:13 00:61:51 00:66:4B 00:6B:6F 00:99:1D 00:9A:CD
    00:A9:1D 00:BE:3B 00:CC:05 00:E0:FC 00:E1:2F 00:E4:06 00:F5:FD
    00:F7:AD 00:F8:1C 00:F9:52 04:02:1F 04:14:71 04:18:92 04:25:C5
    04:27:58 04:33:89 04:4A:6C 2C:AB:00 48:AD:08 54:2F:2B
    90:D7:33 C0:BF:C0
'''.lower().split())


def manufacturer_hint(address):
    """Return a conservative offline OUI hint for globally assigned unicast MACs."""
    if not address or int(address[:2], 16) & 3:
        return None
    return 'Huawei (probable)' if address[:8] in HUAWEI_OUIS else None


def normalize_web(raw, now=None):
    now = time.time() if now is None else now
    observed = number(raw.get('collected_at'))
    stale = observed is None or not 0 <= now - observed <= 180
    nodes, clients, parents, conflicts = {}, {}, {}, set()
    warnings = list(raw.get('warnings', []))

    def record(data, address, source, node=False, parent=None):
        if not address:
            return
        table = nodes if node else clients
        row = table.setdefault(address, {'mac': address, 'ip': None, 'hostname': None,
            'online': None, 'parent_mac': None, 'sources': [], 'updated_at': observed,
            'stale': stale, **({'role': None, 'model': None, 'firmware': None,
                              'client_count': None} if node else
                             {'band': None, 'signal': None, 'phy_mode': None, 'wifi_generation': None})})
        hint = manufacturer_hint(address)
        if hint:
            row['manufacturer'] = hint
        if source not in row['sources']:
            row['sources'].append(source)
        # Never copy arbitrary router response fields (keys/cookies may be present).
        fields = {'ip': ip(data.get('IPAddress')), 'hostname': text(data.get('ActualName') or data.get('HostName') or data.get('FriendlyName'), 63) or None}
        active = data.get('Active')
        if type(active) is bool:
            fields['online'] = active
        if node:
            custinfo = data.get('custinfo')
            model = data.get('ModelName') or (custinfo.get('CustDeviceName') if isinstance(custinfo, dict) else None)
            fields.update(model=text(model, 64) or None,
                          firmware=text(data.get('SoftwareVersion'), 64) or None)
            if data.get('apRole') in ('controller', 'agent'):
                fields['role'] = data['apRole']
        else:
            mode = data.get('phyMode')
            interface = data.get('InterfaceType')
            fields.update(band=BANDS.get(interface) if isinstance(interface, str) else None,
                          signal=signal(data.get('rssi')),
                          phy_mode=mode if isinstance(mode, str) and mode in PHY else None,
                          wifi_generation=PHY.get(mode) if isinstance(mode, str) else None)
        for key, value in fields.items():
            conflict = (node, address, key)
            if value is not None and conflict not in conflicts:
                if row.get(key) is not None and row[key] != value:
                    warnings.append('Conflicting web field: ' + key)
                    # A conflicting observation is not proof of a value.
                    row[key] = None
                    conflicts.add(conflict)
                else:
                    row[key] = value
        if parent:
            parents.setdefault((node, address), set()).add(parent)

    # Each optional topology is validated completely before publishing any of it.
    def hilink(data):
        pending = [(item, None, 1) for item in data] if isinstance(data, list) else None
        if pending is None:
            raise ValueError
        result, seen, count = [], set(), 0
        while pending:
            item, parent, depth = pending.pop(0)
            count += 1
            if not isinstance(item, dict) or depth > 16 or count > 4096 or id(item) in seen:
                raise ValueError
            seen.add(id(item))
            address = mac(item.get('MACAddress'))
            is_node = item.get('HiLinkType') == 'Device'
            result.append((item, address, is_node, parent))
            children = item.get('ConnectedDevices', [])
            if not isinstance(children, list) or len(children) + len(pending) + count > 4096:
                raise ValueError
            # An untyped intermediary cannot prove the owner of descendants.
            pending.extend((child, address if is_node else None, depth + 1) for child in children)
        return result

    def easymesh(data):
        if not isinstance(data, dict) or not isinstance(data.get('routers'), list):
            raise ValueError
        result = []
        for item in data['routers']:
            if not isinstance(item, dict):
                raise ValueError
            address = mac(item.get('devMac'))
            result.append((item, address, True, mac(item.get('uplinkAp'))))
            children = item.get('devices', [])
            if not isinstance(children, list) or len(children) + len(result) > 4096:
                raise ValueError
            for child in children:
                if not isinstance(child, dict):
                    raise ValueError
                result.append((child, mac(child.get('devMac')), False, address))
            if len(result) > 4096:
                raise ValueError
        return result

    for source, parser in (('hilink', hilink), ('easymesh', easymesh)):
        if source not in raw:
            continue
        try:
            entries = parser(raw[source])
        except (ValueError, TypeError):
            warnings.append(source + ': invalid or oversized topology')
            continue
        for data, address, node, parent in entries:
            record(data, address, source, node, parent)
    info = raw.get('deviceinfo')
    if isinstance(info, dict):
        record(info, mac(info.get('MACAddress')), 'deviceinfo', True)
    hosts = raw.get('hosts', [])
    slave_flags = {}
    if not isinstance(hosts, list) or len(hosts) > 4096 or any(not isinstance(h, dict) for h in hosts):
        warnings.append('hosts: invalid or oversized inventory')
    else:
        # Strict booleans only; HiLinkDevice=false also occurs on real AX3 nodes.
        for host in hosts:
            address = mac(host.get('MACAddress'))
            flag = host.get('IsSlave')
            if address and type(flag) is bool:
                slave_flags.setdefault(address, set()).add(flag)
                if flag:
                    record(host, address, 'hosts', True)
        for host in hosts:
            address = mac(host.get('MACAddress'))
            record(host, address, 'hosts', address in nodes)
    for address, row in nodes.items():
        flags = slave_flags.get(address, set())
        row['reported_role'] = row['role']
        contradictory = (len(flags) > 1 or (row['role'] == 'agent' and False in flags)
                         or (True, address, 'role') in conflicts)
        row['classification'] = 'conflict' if contradictory else (
            'candidate' if row['sources'] == ['hosts'] else 'reported')
        if row['classification'] in ('conflict', 'candidate'):
            row['role'] = None
        if contradictory:
            warnings.append('Conflicting web device role; ownership withheld')
            for host in hosts if isinstance(hosts, list) and len(hosts) <= 4096 else []:
                if isinstance(host, dict) and mac(host.get('MACAddress')) == address:
                    record(host, address, 'hosts')
    for (node, address), candidates in parents.items():
        row = (nodes if node else clients)[address]
        if len(candidates) == 1 and address not in candidates:
            row['parent_mac'] = next(iter(candidates))
        else:
            warnings.append('Conflicting web parent')
    # Detect semantic MAC cycles too, not only cyclic Python fixture objects.
    for address in nodes:
        seen, current = set(), address
        while current in nodes:
            if current in seen or len(seen) >= 16:
                warnings.append('Cyclic or oversized node ancestry; ownership withheld')
                for row in (*nodes.values(), *clients.values()):
                    row['parent_mac'] = None
                break
            seen.add(current)
            current = nodes[current]['parent_mac']
    for address in nodes:
        if nodes[address]['classification'] != 'conflict':
            clients.pop(address, None)
    for row in (*nodes.values(), *clients.values()):
        parent = nodes.get(row['parent_mac'])
        if row.get('classification') in ('conflict', 'candidate') or (
                parent and parent['classification'] in ('conflict', 'candidate')):
            row['parent_mac'] = None
    return {'nodes': list(nodes.values()), 'clients': list(clients.values()),
            'warnings': warnings, 'collected_at': observed}


def merge_web(snapshot, web_snapshot, devices):
    """Enrich eligible ACS stations only; topology is never an emitter input."""
    result = copy.deepcopy(snapshot)
    mesh = normalize_web(web_snapshot)
    result['mesh'] = mesh
    result['warnings'].extend(mesh['warnings'])
    physical = {}
    for device in devices:
        root = device.get('InternetGatewayDevice', {})
        if not isinstance(root, dict):
            continue
        address = mac(val(root.get('DeviceInfo', {}), 'MACAddress'))
        if address:
            physical.setdefault(address, set()).add(device.get('_id'))
    ap_matches = {next(iter(ids)): address for address, ids in physical.items() if len(ids) == 1}
    aps = {ap['id']: ap for ap in result['aps']}
    nodes = {node['mac'] for node in mesh['nodes']
             if not node['stale'] and node['classification'] == 'reported'}
    clients = {client['mac']: client for client in mesh['clients']}
    for client in result['clients']:
        ap = aps.get(client['ap_id'])
        web = clients.get(client['mac'])
        address = ap_matches.get(client['ap_id'])
        if (not ap or not ap.get('online') or client.get('stale') or not web
                or web['stale'] or web['online'] is False or address not in nodes):
            continue
        if web['parent_mac'] != address:
            if web['parent_mac'] is not None:
                result['warnings'].append('Web/ACS parent conflict; enrichment withheld')
            continue
        radio = next((r for r in ap['radios'] if r['id'] == client['radio_id']), None)
        if not radio or radio.get('stale') or radio.get('enabled') is False:
            continue
        if web['band'] is not None and web['band'] != client.get('band'):
            result['warnings'].append('Web/ACS band conflict; enrichment withheld')
            continue
        for key in ('ip', 'hostname', 'signal', 'phy_mode', 'wifi_generation'):
            if web.get(key) is None:
                continue
            if client.get(key) in (None, ''):
                client[key] = web[key]
                client.setdefault('web_fields', []).append(key)
                client['web_updated_at'] = web['updated_at']
            elif client[key] != web[key]:
                result['warnings'].append('Web/ACS field conflict: ' + key)
    return result
