# Huawei web telemetry, mesh inventory and AX3 compatibility

Status: implemented experimentally. Owner validation confirmed read-only BE3 web
authentication and inventory collection. Comprehensive mesh and AX3 firmware
validation remain outstanding. The optional Compose override remains opt-in.
Branch: `feat/huawei-web-mesh-ax3`.

## Intent

Add an optional, read-only Huawei web telemetry source alongside GenieACS.
This addresses routers and mesh satellites whose firmware does not expose ACS
configuration, without requiring changes to their mesh mode or physical settings.
The existing TR-069 path, adoption identity and provisioning safeguards remain intact.

The owner requested a review branch after analysis of
[vmakeev/huawei_mesh_router](https://github.com/vmakeev/huawei_mesh_router).
That project declares AX3 WS7100/WS7200/WS7206 support; this is a useful reference,
not proof that this bridge supports every AX3 firmware or region.

## Reference findings

- `client/coreapi.py` implements nonce/proof login, cookie management and CSRF renewal.
- `client/huaweiapi.py` reads `HostInfo`, `deviceinfo` and recursive `topology`.
- `update_coordinator.py` joins host records with mesh node membership.
- That implementation defaults unmatched clients to the primary router. Do not
  copy that heuristic: partial topologies must produce unknown associations.
- Its HiLink topology is not a universal EasyMesh adapter. The BE3's observed
  `l2topo` format needs separate parsing and validation.
- Its client classes do not interpret `phyMode`. That field is an additional
  observed Huawei source to validate, not an upstream feature to claim as reused.

## Proposed deliverable

1. A Huawei web client using Python's standard library and existing dependencies.
   It authenticates itself, without borrowing browser cookies.
2. Separate parsers for HiLink `ConnectedDevices` and EasyMesh `routers`/`devices`.
3. An explicitly sourced mesh inventory in the dashboard/API, including partial
   nodes and unknown associations rather than fabricated radio measurements.
4. Safe enrichment of uniquely matched existing AP/client records. Preserve
   GenieACS IDs and virtual MACs; never merge on display names or IP alone.
5. AX3 radio capability/profile handling based on observed standards and known
   model identity, with the existing BE3 identity/profile unchanged.

This first deliverable does not promise a UniFi AP for every discovered satellite.
A mesh node without validated radio, SSID and association data remains inventory
only. Additional per-node emission is gated on real, sanitized radio fixtures.

## Configuration and authentication

Design choice: disabled by default, with explicit opt-in through a separate
`compose.web.yml` override. Plain `make up` remains unchanged.

- `HUAWEI_WEB_URL`: empty disables the source; otherwise a configured HTTP(S) origin.
- `HUAWEI_WEB_USERNAME`: local administrator username; never logged.
- `HUAWEI_WEB_PASSWORD_FILE`: host-side secret file for the optional Compose secret;
  the container reads `/run/secrets/huawei_web_password`. No password in public
  examples, command arguments or snapshot output.
- `HUAWEI_WEB_CA_FILE`: optional trusted CA file for HTTPS. Certificate verification
  stays enabled; do not add a blanket insecure-TLS default.

An explicit origin can be a LAN IP or a locally resolvable hostname. Reject URL
userinfo, query strings and fragments. Disable redirects and proxy inheritance.
Send credentials only to that configured origin. Do not automatically log into
addresses learned from topology; explicit secondary-target configuration is deferred.

Allow POST only to the two login endpoints. All telemetry requests are GETs:
`/api/system/deviceinfo`, `/api/system/HostInfo`, `/api/device/topology`,
`/api/device/l2topo`. Unsupported optional endpoints are marked unavailable.
Do not expose a general POST API or inherit upstream reboot/configuration methods.

Use a secure random nonce, bounded challenge parsing and bounded PBKDF2 iterations.
Allow at most one reauthentication attempt per failed read and back off to the
next polling cycle on failure. Never log passwords, proofs, cookies, CSRF values
or full response bodies. Retain session state in memory only.

## Data contracts

Web collection returns separate `nodes`, `clients`, `warnings` and `collected_at`
fields. Every observation carries its source and observation timestamp.

- Nodes: canonical physical MAC, reported IP/name/model/firmware if present,
  role, parent MAC and explicit online state when available. Missing values are null.
- Clients: canonical MAC, reported IP/name/band/RSSI/PHY and an optional parent
  node MAC backed by topology evidence. Conflicting parents remain unresolved.
- Topology membership does not prove current activity. Do not turn a missing
  activity field into true, or an empty child list into proof of zero clients.
- Retain web data age across failed polls. Values older than 180 seconds become
  stale; failed requests never refresh observation timestamps.
- Keep rates optional and clearly sourced. Do not convert `UpRate`/`DownRate`
  into cumulative byte counters or publish them until units are hardware-validated.
- Accept only explicit known PHY strings: `802.11n`, `802.11ac`, `802.11ax`,
  `802.11be`; empty/unknown strings remain unknown. PHY is not inferred from band.

Existing APs may be enriched only when the web router's physical MAC matches
validated CPE identity evidence, such as an exact `DeviceInfo.MACAddress` field.
Do not equate the management MAC with a radio BSSID or infer MAC offsets. If the
current CPE does not expose matching evidence, retain the web inventory separately.
Only uniquely matched, fresh client observations can enrich an existing client;
do not overwrite its AP association with an unresolved web parent.

Merge policy for this deliverable: ACS determines eligibility for the existing
UniFi emitter. Web data must not reactivate stale/offline ACS clients or radios,
or overwrite ACS observation timestamps. Preserve fresh, non-null ACS fields;
web values only fill missing fields and add explicitly sourced PHY metadata.
Enrich association-dependent client fields only when the web parent uniquely
matches that ACS AP's verified physical identity. On disagreement, retain the
ACS association and values, report a conflict, and keep web observations in the
separate inventory. Web-only emission is deferred until validated radio data exists.

## Runtime integration

Use an optional web polling thread at 30-second intervals, with 5-second request
timeouts and a 1 MiB response limit. It must not delay the current ACS/inform
worker. Publish completed snapshots under the existing application mutex.
Expose web-source status independently of ACS health. An unavailable web source
must leave existing TR-069 data and UniFi reporting operational.

Read-only web records must never become provisioning targets, even if their
addresses match `HUAWEI_CONTROL_IP`. Preserve `HUAWEI_CONTROL_DEVICE_ID`, ambiguous
IP fail-safe behavior and the private-adoption-key requirement for inbound control.

For known AX3 models, select a Wi-Fi 6-compatible virtual profile for newly
introduced identities only, after checking its code against the target controller.
Do not announce BE, 6 GHz or 160 MHz merely because a model belongs to the AX3 family.
An existing adopted identity's model must not silently change or trigger readoption;
report a profile mismatch and require an owner-approved migration instead.

## Non-goals

- No web-based router writes, mesh reconfiguration, firmware updates or reboots.
- No LAN scans, automatic logins to discovered nodes or browser-cookie extraction.
- No generic IEEE 1905/EasyMesh controller implementation.
- No invented AP radios, channel widths, BSSIDs, counter values or client ownership.
- No weakening of Compose hardening, image digest pins, required `LAN_IP` or
  required `GENIEACS_UI_JWT_SECRET`.
- No deployment, volume changes, commits, push or PR creation in this planning step.

## Acceptance and rollout

Synthetic fixtures must exercise AX3/HiLink and BE3/EasyMesh structures, missing
data, conflicting ownership, timeouts, bad authentication, redirects and stale
data. Existing provisioning/adoption tests must remain green.

Before claiming hardware support, validate read-only behavior on the actual
firmware, request authorization before storing any sanitized samples, and confirm
that the current virtual identity remains unchanged. Do not log into a router
merely because it was discovered in the mesh.

The owner approved implementation. See the companion plan for verification,
deviations and remaining gates. Keep the optional override disabled until the
owner separately opts into a trial.

## Attribution and AI disclosure

If upstream code is adapted, record its exact revision and preserve Vladimir
Makeev's MIT notice in `THIRD_PARTY_LICENSES.md`. Keep the existing
`unifi-simulator` attribution unchanged. Do not import Home Assistant as a dependency.

AI assistance: Yes. Tool: OpenAI Codex. Scope: repository comparison, design,
implementation and tests. Verification and remaining gaps are recorded in the
companion plan; no router authentication or hardware tests were performed.
