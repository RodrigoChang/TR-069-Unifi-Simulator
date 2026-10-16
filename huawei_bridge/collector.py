"""Normalize Huawei TR-098 telemetry received by GenieACS."""

import hashlib
import ipaddress
import re
import time
from datetime import datetime
from urllib.parse import urlsplit


def mac(value):
    if not isinstance(value, str):
        return None
    value = value.lower().replace('-', ':')
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', value):
        return None
    raw = bytes.fromhex(value.replace(':', ''))
    return value if any(raw) and not raw[0] & 1 else None


def ip(value):
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except (ValueError, TypeError):
        return None


def val(obj, name, default=None):
    field = obj.get(name, {}) if isinstance(obj, dict) else {}
    return field.get('_value', default) if isinstance(field, dict) else default


def text(value, limit):
    # Allowlist printable ASCII only; reject non-ASCII digits/letters smuggled in CPE-reported strings.
    value = str(value) if value is not None else ''
    return ''.join(ch for ch in value if ch.isascii() and ch.isprintable())[:limit]


def rows(obj):
    if not isinstance(obj, dict):
        return []
    return sorted(((key, value) for key, value in obj.items()
                   if key.isascii() and key.isdigit() and isinstance(value, dict)),
                  key=lambda item: int(item[0]))


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if abs(result) < 1e16 else None
    except (TypeError, ValueError):
        return None


def signal(value):
    value = number(value)
    # Huawei reports either negative dBm or its positive magnitude; zero is unknown.
    return -abs(value) if value is not None and 1 <= abs(value) <= 127 else None


def virtual_mac(identity):
    data = bytearray(hashlib.sha256(('huawei-bridge:' + identity).encode()).digest()[:6])
    data[0] = (data[0] | 2) & 254
    return ':'.join(f'{part:02x}' for part in data)


def normalize(devices, now=None):
    now = now if now is not None else time.time()
    aps, clients = [], []
    for device in devices:
        root = device.get('InternetGatewayDevice', {})
        identity = device.get('_id')
        info = device.get('_deviceId', {})
        if not isinstance(root, dict) or not isinstance(identity, str):
            continue
        if 'huawei' not in str(info.get('_Manufacturer', '')).lower():
            continue
        last = timestamp(device.get('_lastInform'))
        hosts = {}
        for _, lan in rows(root.get('LANDevice')):
            for _, host in rows(lan.get('Hosts', {}).get('Host')):
                address = mac(val(host, 'MACAddress'))
                if address:
                    hosts[address] = {'hostname': text(val(host, 'HostName', ''), 63),
                                      'ip': ip(val(host, 'IPAddress'))}
        radios, stations = [], {}
        for lid, lan in rows(root.get('LANDevice')):
            for wid, wlan in rows(lan.get('WLANConfiguration')):
                channel = number(val(wlan, 'Channel'))
                if channel is None or channel < 1 or channel > 196:
                    continue
                band = '2.4g' if channel <= 14 else '5g'
                path = f'InternetGatewayDevice.LANDevice.{lid}.WLANConfiguration.{wid}'
                radio = {'id': path, 'ssid': text(val(wlan, 'SSID', ''), 32),
                         'channel': int(channel), 'band': band, 'bssid': mac(val(wlan, 'BSSID')),
                         'enabled': val(wlan, 'Enable'),
                         'standards': [s for s in str(val(wlan, 'Standard', '')).split('/')
                                       if s in ('a', 'b', 'g', 'n', 'ac', 'ax', 'be')],
                         'updated_at': timestamp(wlan.get('Channel', {}).get('_timestamp'))}
                radio['stale'] = radio['updated_at'] is None or now - radio['updated_at'] > 180
                radios.append(radio)
                if radio['enabled'] is False:
                    continue
                for _, station in rows(wlan.get('AssociatedDevice')):
                    address = mac(val(station, 'AssociatedDeviceMACAddress'))
                    if not address:
                        continue
                    observed = timestamp(station.get('AssociatedDeviceMACAddress', {}).get('_timestamp'))
                    candidate = {'mac': address, 'ip': ip(val(station, 'AssociatedDeviceIPAddress')),
                                 'signal': signal(val(station, 'AssociatedDeviceRssi')),
                                 'band': band, 'radio_id': path, 'ssid': radio['ssid'],
                                 'ap_id': identity, 'updated_at': observed,
                                 'stale': observed is None or now - observed > 180}
                    # Prefer the newest observation if stale rows remain after a roam.
                    previous = stations.get(address)
                    if previous is None or (observed or 0) > (previous['updated_at'] or 0):
                        stations[address] = candidate
        callback = val(root.get('ManagementServer'), 'ConnectionRequestURL', '')
        try:
            device_ip = ip(urlsplit(callback).hostname)
        except (TypeError, ValueError):
            device_ip = None
        online = bool(last and 0 <= now - last < 180)
        ap = {'id': identity, 'virtual_mac': virtual_mac(identity),
              'name': info.get('_ProductClass', 'Huawei AP'), 'ip': device_ip,
              'firmware': val(root.get('DeviceInfo'), 'SoftwareVersion'),
              'hardware': val(root.get('DeviceInfo'), 'HardwareVersion'),
              'last_inform': last, 'online': online, 'radios': radios,
              'client_count': sum(not c['stale'] and online for c in stations.values())}
        aps.append(ap)
        for address, client in stations.items():
            host = hosts.get(address, {})
            client['hostname'] = host.get('hostname', '')
            client['ip'] = client['ip'] or host.get('ip')
            client['stale'] = client['stale'] or not online
            clients.append(client)
    return {'updated_at': now, 'aps': aps, 'clients': clients, 'warnings': []}
