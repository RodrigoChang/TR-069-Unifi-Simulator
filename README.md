# Huawei TR-069 to UniFi Bridge

An experimental, self-hosted bridge that collects Huawei router telemetry through
GenieACS, displays it in a local dashboard, and reports observed radios and Wi-Fi
clients to UniFi Network as a virtual access point.

This independent project is based on the virtual AP simulation approach of
[MartinDejmal/unifi-simulator](https://github.com/MartinDejmal/unifi-simulator).
It does not install or depend on that repository. The adapted TNBU codec and its
MIT attribution are documented in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

**This does not turn a Huawei router into a native UniFi device.** The virtual AP
provides visibility and limited configuration translation, not full UniFi
management. Missing measurements are not fabricated.

## Acknowledgements and attribution

Special thanks to **MartinDejmal and the unifi-simulator contributors** for
[unifi-simulator](https://github.com/MartinDejmal/unifi-simulator), the original
project that provided the foundation for this bridge's UniFi AP simulation work.
Their work on reporting non-UniFi hardware to UniFi made this Huawei adaptation
possible.

The TNBU codec in `huawei_bridge/protocol.py` is adapted from upstream revision
[`adef72ae011837b07f7406a2932bc153febf84d9`](https://github.com/MartinDejmal/unifi-simulator/tree/adef72ae011837b07f7406a2932bc153febf84d9).
The upstream MIT copyright notice and license are preserved in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

The Huawei TR-069 collection, local dashboard and allowlisted provisioning are
implemented in this separate project. This attribution does not imply endorsement
by the upstream authors, Huawei or Ubiquiti.

## AI development disclosure

**This project was developed with substantial generative AI assistance**, including
code implementation, test development, documentation and troubleshooting.
AI-assisted work can contain errors; this disclosure is not proof of correctness,
security, hardware compatibility or production readiness. Contributors and
maintainers remain responsible for reviewing changes and validating their claims.

AI-assisted and non-AI contributions are both welcome. **Every contribution and
pull request must explicitly declare whether AI was used (Yes or No)** and
describe its scope, tools and verification as required by
[CONTRIBUTING.md](CONTRIBUTING.md). Use the
[PR template](.github/pull_request_template.md) when submitting changes.

This statement describes work on this project; it does not make claims about
AI use by the upstream unifi-simulator authors. It is not an endorsement or
certification by an AI provider, Huawei, Ubiquiti or the upstream maintainers.

## Features and compatibility

- A local dashboard and JSON snapshot of directly registered Huawei CPEs.
- Observed SSIDs, channels, radio capabilities, client MAC/IP addresses and RSSI.
- Stable virtual identities, encrypted inform exchange and persisted adoption.
- Explicit mapping from Huawei SSIDs to existing UniFi WLAN IDs.
- Optional SSID, channel, enable/disable and WMM changes through GenieACS.

Reference hardware: Huawei BE3 (`WUKUN-BE32`), firmware
`4.2.0.53(SP1C1555)`, tested with UniFi Network `10.6.106`. Other hardware and
versions require their own validation. The dashboard currently uses Spanish labels.

```text
Huawei CPE -- TR-069/CWMP --> GenieACS -- NBI --> Bridge -- inform --> UniFi
                                                 |
                                           Local dashboard
```

The default mode only reads telemetry. Optional provisioning follows the reverse
path: UniFi configuration → validated translation → ACS task → Huawei → fresh
readback confirmation.

## Requirements

- Docker Engine/Desktop running, with Docker Compose v2.
- `make` for convenience commands, or use Docker Compose directly.
- A Huawei router with configurable TR-069 ACS settings and TR-098 WLAN data.
- Connectivity from the router to the Docker host's CWMP listener.
- For UniFi reporting: a reachable Network inform endpoint and an existing WLAN.
- Internet access for the initial image and dependency downloads.

Replace all `<PLACEHOLDERS>` with your own values. Example SSIDs and IDs are
synthetic. No deployment-specific LAN addresses are required in public files;
`127.0.0.1` below refers to the local Docker host.

## Quick start

From the project directory:

```sh
make up
```

Without `make`:

```sh
docker compose up -d --build
```

No `.env` is required for local monitoring. Without it, UniFi reporting and
Huawei writes are disabled. Startup never replaces an existing `.env` or deletes
data volumes.

| Service | Local address / binding |
| --- | --- |
| Dashboard | <http://127.0.0.1:8090> |
| Normalized snapshot | <http://127.0.0.1:8090/api/snapshot> |
| Collector health | <http://127.0.0.1:8090/health> |
| GenieACS UI | <http://127.0.0.1:3000> |
| GenieACS NBI | `127.0.0.1:7557`, loopback only; the bridge reaches it internally as `nbi:7557` |
| CWMP listener | TCP `7547`, published only on `LAN_IP` from `.env` (default `127.0.0.1`) |

The dashboard and `/api/snapshot` have no authentication of their own. They rely
entirely on being published on loopback and on the Host/Origin allowlist in
`huawei_bridge/__main__.py`; do not reverse-proxy or publish them more broadly
without adding your own authentication.

Allow a few seconds for startup, then check:

```sh
docker compose ps
curl --fail http://127.0.0.1:8090/health
```

Health confirms recent ACS collection, not AP adoption or client visibility.
Check those separately in the dashboard and UniFi.

### Configure the Huawei ACS connection

In the router's TR-069 settings:

| Setting | Value |
| --- | --- |
| ACS URL | `http://<DOCKER_HOST_LAN_IP>:7547/` |
| Periodic Inform | Enabled |
| Inform interval | `60` seconds |
| ACS username/password | Local test values if required by the router |

Use the Docker host's LAN address, **not** loopback, a container address or the
UniFi gateway address unless that gateway actually hosts this stack. Reserve the
host address in DHCP or keep it static. Set `LAN_IP` in `.env` to that same host
LAN address so Compose publishes the CWMP listener there instead of `0.0.0.0`.
Wait for an Inform and confirm the CPE appears in GenieACS and the dashboard.

**Lab security:** the supplied GenieACS configuration does not enforce the ACS
credentials sent by the CPE. Entering them in the router does not enable
server-side authentication. CWMP uses HTTP. `GENIEACS_UI_JWT_SECRET` has no
insecure default: set a real random value in `.env` (`.env.example` shows how to
generate one), or Compose refuses to start. `LAN_IP` restricts TCP `7547` to the
trusted LAN interface; never set it to `0.0.0.0` or expose this stack directly to
the Internet. GenieACS itself does not support authenticating incoming CWMP
connections in this stack; network-level restriction via `LAN_IP` is the control
that matters here.

## Enable UniFi reporting

Copy the template without overwriting an existing local file:

```sh
cp -n .env.example .env
```

Edit `.env` using your own inform address and a real WLAN ID:

```ini
UNIFI_INFORM_URL=http://<UNIFI_HOST>:8080/inform
UNIFI_WLAN_MAP={"ExampleSSID":"0123456789abcdef01234567"}
HUAWEI_CONTROL_IP=
LAN_IP=<DOCKER_HOST_LAN_IP>
GENIEACS_UI_JWT_SECRET=<RANDOM_SECRET>
```

The example ID is not usable for your controller. The hostname must resolve from
the bridge container, and TCP `8080` must be reachable; see Ubiquiti's
[Layer 3 adoption guidance](https://help.ui.com/hc/en-us/articles/204909754-Remote-Adoption-Layer-3).
Leave `HUAWEI_CONTROL_IP` empty to keep writes disabled. Apply `.env` edits by
recreating the bridge:

```sh
docker compose up -d --build bridge
make logs
```

### How to obtain the WLAN ID

The bridge needs the existing Wi-Fi network's **internal `_id`**, normally a
24-character lowercase hexadecimal string. It is not the SSID, BSSID, AP MAC,
VLAN ID, wired network ID or site ID. SSIDs must match exactly, including case.
Create the Wi-Fi network in the intended UniFi site first; this bridge does not
create WLANs.

This lookup uses UniFi Network's internal web API, verified on `10.6.106`, not
the [official Integration API](https://help.ui.com/hc/en-us/articles/30076656117655-Getting-Started-with-the-Official-UniFi-API).
Internal paths may change between versions. Do not assume IDs from other API
resources are interchangeable with the WLAN `_id` expected here.

#### Option A: read-only browser query

1. Sign in to your controller's **local UniFi Network web interface** and select
   the site containing the Wi-Fi network. Do not run this from the bridge
   dashboard or the cloud Site Manager page.
2. Open Developer Tools → **Console**.
3. Set `site` to the internal site key. `default` is common for single-site
   installations, but is not guaranteed. Use the key in the local Network URL
   or its network requests, not the site's display name.
4. Use `/proxy/network` for UniFi OS consoles. For standalone Network
   applications serving `/api/s/...` directly, set `prefix` to an empty string.
5. Review and run this GET-only query. It uses your signed-in session and prints
   only Wi-Fi names and IDs, without exporting cookies or requiring an API key:

```javascript
(async () => {
  const site = 'default';
  const prefix = '/proxy/network'; // Use '' for a standalone Network application.
  const response = await fetch(
    `${prefix}/api/s/${encodeURIComponent(site)}/rest/wlanconf`,
    { method: 'GET', credentials: 'same-origin', cache: 'no-store' }
  );
  if (!response.ok) throw new Error(`WLAN lookup failed: HTTP ${response.status}`);
  const result = await response.json();
  if (!Array.isArray(result.data)) throw new Error('Unexpected WLAN response');
  console.table(result.data.map(({ name, _id }) => ({ ssid: name, wlan_id: _id })));
})().catch(error => console.error(error.message));
```

Locate your SSID in the table and copy its `wlan_id` into `UNIFI_WLAN_MAP`. If
the browser blocks pasted console code, use the Network inspector below instead
of disabling browser safeguards.

#### Option B: Network inspector

1. While signed in to local UniFi Network, open Developer Tools → **Network**
   and filter requests by `wlanconf`.
2. Open or reload the Wi-Fi settings page. If your version uses this endpoint,
   select its successful GET request and inspect the JSON response.
3. In `data`, find the object whose `name` matches your SSID and copy only its
   `_id`. The request URL also identifies the API prefix and site key.

**Never share the full WLAN response or publish a HAR export.** It may contain
Wi-Fi passwords and other secrets, even though Option A only prints names and
IDs. If the request is absent, use Option A with the appropriate path or inspect
your version's request format; do not invent an ID.

Example mapping for two Wi-Fi networks, using synthetic IDs:

```ini
UNIFI_WLAN_MAP={"ExampleSSID":"0123456789abcdef01234567","GuestSSID":"89abcdef0123456789abcdef"}
```

Replace each value with an ID from the site where the virtual AP is adopted.
Without a valid mapping, the bridge still reports identity and radios, but omits
the corresponding virtual WLAN interfaces and clients. The dashboard warns about
unmapped SSIDs. If you delete and recreate a WLAN, obtain its new ID and update
the mapping.

### Adopt the virtual AP

In UniFi Devices, adopt the pending AP whose **virtual MAC** matches the bridge
dashboard. Do not use the physical Huawei MAC or run UniFi firmware/SSH commands
on the Huawei router.

The compatibility profile is `U7PRO` (U7 Pro), with emulated firmware version
`8.7.11.19419`. The dashboard retains the actual Huawei model and firmware.
The [U7 Pro profile supports Wi-Fi 7 and 160 MHz](https://techspecs.ui.com/unifi/wifi/u7-pro),
but only radios actually observed on the Huawei are published:

- The tested BE3 reports 2.4/5 GHz, not 6 GHz.
- Radio AC/AX/BE capability flags come from TR-098 `Standard`.
- `has_ht160` is limited to the 5 GHz radio of the BE3 `WUKUN-BE32`.
- Supporting 160 MHz does not mean the active channel width is 160 MHz.
- Selecting a width in UniFi does not currently apply it to the Huawei.

`key-received` means an adoption key was received; `exchanging` means encrypted
exchange succeeded. Neither alone proves the AP is Online or its clients are
visible. Verify those results in UniFi. SSH adoption and UDP discovery are not
implemented.

**Perform initial adoption on a trusted network.** The first exchange, before a
private key is adopted, is decrypted with a public bootstrap key (the published
UniFi discovery constant, not a secret), so anyone who can inject traffic on
that path could attempt to intercept or spoof it. The bridge never applies
controller-requested configuration from that pre-adoption exchange: writes to
the Huawei (see below) are only accepted once a per-device private key has been
adopted in a prior exchange. Still, perform adoption on a network you trust, and
re-adopt (not simply ignore a warning) if you suspect it was interfered with.

## Optional configuration from UniFi

To enable writes for one directly registered CPE, set its management/callback IP
in `.env` and recreate the bridge. This is disabled by default.

```ini
HUAWEI_CONTROL_IP=<HUAWEI_MANAGEMENT_IP>
```

If more than one registered CPE currently reports that same management IP, the
bridge refuses to enable writes for any of them (fail-safe) rather than guessing.
To target one CPE unambiguously regardless of IP, set its exact GenieACS device
`_id` instead (or in addition):

```ini
HUAWEI_CONTROL_DEVICE_ID=<GENIEACS_DEVICE_ID>
```

`HUAWEI_CONTROL_DEVICE_ID` takes precedence over `HUAWEI_CONTROL_IP` when both
are set. Find the device `_id` in the GenieACS UI's device list or via the NBI
`/devices` endpoint.

| UniFi field | Huawei TR-098 parameter |
| --- | --- |
| `wireless.N.ssid` | Bound WLAN's `SSID` |
| `radio.N.channel` | `Channel` + `AutoChannelEnable`; `auto` only enables automatic selection |
| `radio.N.status` / `wireless.N.status` | Bound WLAN's `Enable` |
| `wireless.N.wmm` | Bound WLAN's `WMMEnable` |

Radios bind through `phyname`/`parent` and observed TR-098 paths, not row position.
Only one virtual WLAN per radio is supported for provisioning; ambiguous mappings
are rejected. Channels must be advertised in `PossibleChannels`, and changed
parameters must exist and be writable.

The first configuration establishes a baseline **without writing controller
defaults to the router**. Later supported changes generate `setParameterValues`
tasks. A task ID is not success: matching values with fresh readback timestamps
are required before acknowledging the revision. Allow for the router's Inform
interval before expecting results.

The dashboard distinguishes baseline, queued, reading, confirmed, rejected,
failed and uncertain states. Faults or 300 seconds without confirmation fail the
change. Writes are not automatically resent. For an unknown submission outcome,
inspect GenieACS tasks, faults and current values before intervening; do not
delete state or blindly repeat a write. After resolving a failed revision,
generate a new revision in UniFi to retry. State and WLAN bindings persist across
restarts and SSID changes.

A controller-requested virtual reset requires a new baseline. If an ACS task
was already pending, further writes remain blocked for manual inspection.

**Not translated:** passwords, WPA security, VLANs, channel widths, transmit
power, firmware, reboots, SSH and shell commands. An acknowledgement covers only
allowlisted fields, not the entire UniFi configuration. Supported Wi-Fi changes
can still temporarily disconnect clients. GenieACS presets are independent and
may provision a router outside this bridge's allowlist.

## Telemetry and limitations

The collector reads `InternetGatewayDevice.LANDevice.*.WLANConfiguration.*`,
refreshes LAN data every 60 seconds without duplicating pending refreshes, and
marks observations older than 180 seconds as stale. Stale clients are excluded
from UniFi reporting. Duplicate associations are reduced to the newest record.
Client names come from the Hosts table when available. Zero RSSI is unknown;
Huawei's positive RSSI magnitude is converted to negative dBm.

No traffic counters, client speeds, noise, transmit power or connection duration
are fabricated. Band mapping targets the tested 2.4/5 GHz device; 6 GHz hardware
requires additional handling. Controller defaults are not Huawei measurements.

### Wi-Fi generation per client

The tested BE3 exposes MAC, IP and RSSI through TR-069 `AssociatedDevice`, but
not each client's Wi-Fi generation. `Standard` describes the radio's capabilities,
not a client's negotiated mode. **Per-client Wi-Fi generation is not currently
reported reliably to UniFi.**

Huawei's authenticated `GET /api/system/HostInfo` was observed to return
per-client `phyMode` values such as `802.11n`, `802.11ac`, `802.11ax` and
`802.11be`, with some values empty. Integrating it requires an authenticated web
collector and session-expiry handling. The current project does not export
browser cookies or store web administrator credentials.

### EasyMesh satellites

EasyMesh collection is not implemented. Each CPE currently needs to register
directly with GenieACS. The primary Huawei's authenticated
`GET /api/device/l2topo` was observed to expose controller/agent relationships,
but agent client lists can be empty. This is a possible future source, not proof
of complete per-node telemetry or an existing project feature.

## Operation and persistence

```sh
make logs                              # Follow bridge logs; Ctrl+C stops following.
make stop                              # Stop services without deleting data.
docker compose up -d --build bridge     # Rebuild only the bridge.
docker compose stop bridge             # Stop the bridge; leave GenieACS running.
```

`mongo-data` holds GenieACS data. `bridge-data` holds private adoption keys,
stable identities, WLAN bindings and provisioning state. **Do not delete these
volumes or run `docker compose down -v` as an update step.** Backups contain
sensitive data and must be stored securely.

Forgetting a virtual AP can discard controller history/settings. Readoption may
also need an explicit reset of local adoption credentials; an HTTP error alone
does not prove or authorize that action. Coordinate both ends and preserve the
virtual identity. Never factory-reset the physical Huawei to recover a virtual AP.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| No Huawei appears | Docker host LAN address in ACS URL, reachable TCP 7547, enabled Inform and CPE present in GenieACS. |
| Clients appear locally but not in UniFi | Exact SSID, real 24-character WLAN `_id`, correct site and recreated bridge after `.env` changes. |
| Pending adoption / offline virtual AP | Inform URL, container-to-controller TCP 8080 connectivity, controller version and persisted adoption state. |
| WLAN lookup returns HTTP 401/403 | Sign in to local Network with appropriate read access; check site and API prefix. Do not export cookies. |
| WLAN lookup returns HTTP 404 | Check UniFi OS versus standalone prefix, site key and Network version. |
| Dashboard returns HTTP 403 | Use the documented loopback URL; Host and Origin headers are validated. |
| Provisioning stays queued/reading | Wait for the next Inform; inspect its task and faults in GenieACS. |
| Wrong client generation or channel width | These may be controller defaults; see telemetry limitations. |

## Development and validation

For local execution and tests, use Python 3.11+:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
make test
python -m huawei_bridge
```

Local execution defaults to NBI at `http://127.0.0.1:7557` and `.state/` for
persistence. Export environment variables when running Python directly: `.env`
is consumed by Compose, not automatically loaded by the application. Do not run
a second emitter against the same virtual AP while the Docker bridge is active.
Tests use synthetic fixtures/local HTTP peers, not the real router; HTTP tests
require permission to bind loopback sockets.

The reference implementation has 22 automated tests covering normalization,
encryption/adoption, WLAN mapping, HTTP boundaries, translation, persistence,
readback, faults and uncertain outcomes. Hardware validation included adoption,
restart persistence, client visibility, 160 MHz capability display and an
idempotent WMM write followed by fresh ACS readback. Disruptive SSID/channel
changes have not been validated against the physical router.

| Variable | Purpose / default |
| --- | --- |
| `UNIFI_INFORM_URL` | Empty disables the emitter. |
| `UNIFI_WLAN_MAP` | JSON mapping of exact SSIDs to real WLAN IDs; empty means no mapped WLANs. |
| `HUAWEI_CONTROL_IP` | Empty disables writes; otherwise restricts provisioning to one management IP (writes stay disabled if more than one CPE shares it). |
| `HUAWEI_CONTROL_DEVICE_ID` | Optional; restricts provisioning to one CPE by its exact GenieACS `_id`, taking precedence over `HUAWEI_CONTROL_IP`. |
| `GENIEACS_URL` | NBI URL; local default `http://127.0.0.1:7557`. |
| `STATE_DIR` | Local default `.state/`; Docker uses `/data`. |
| `PUBLIC_URL` | Default `http://127.0.0.1:8090`; participates in request validation. |
| `LISTEN_HOST` / `PORT` | Local defaults `127.0.0.1` / `8090`; Docker listens internally on `0.0.0.0`. |
| `LAN_IP` | Docker-only; interface the CWMP listener (`7547`) is published on. Default `127.0.0.1`; set to the Docker host's trusted LAN address for real CPEs. |
| `GENIEACS_UI_JWT_SECRET` | Docker-only; required, no insecure default. GenieACS UI/NBI JWT signing secret. |

See [AGENTS.md](AGENTS.md) for contributor/agent constraints and
[compose.yml](compose.yml) for Docker settings.

## Before publishing to Git

- Keep deployment addresses, actual SSIDs, WLAN IDs and credentials in ignored
  `.env`, not public examples. The template leaves integrations disabled.
- Do not commit state, volume exports, keys, HAR/packet captures, raw ACS/WLAN
  responses or screenshots containing client information.
- `.gitignore` does not untrack files or scrub history. If a secret was committed,
  remove it from publication and rotate it; ignoring it afterward is insufficient.
- Review the exact files and diff being published. Preserve third-party
  attribution. `THIRD_PARTY_LICENSES.md` covers the adapted codec; do not assume
  that it grants a license for every other project file.

The dashboard, NBI and GenieACS UI bind to loopback in the supplied stack, and
CWMP binds only to `LAN_IP` (default loopback). Do not publish their APIs
without authentication and transport/access controls, and do not set `LAN_IP`
to `0.0.0.0` or a public interface. This is an experimental LAN integration,
not an officially supported Huawei/Ubiquiti
management solution.

All containers share a dedicated Compose network (`lab`) with `enable_ipv6: false`;
every published port is also bound to an explicit IPv4 address (`LAN_IP` or
`127.0.0.1`). The stack does not accept IPv6 traffic, matching the IPv4-only
address handling in `inform_url()` and the dashboard's `Host`/`Origin` allowlist.
