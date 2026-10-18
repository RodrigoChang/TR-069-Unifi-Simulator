# Agent instructions

## Purpose and scope

An independent Huawei TR-069 → GenieACS → local dashboard / virtual UniFi AP
project, based on the AP simulation approach of
[MartinDejmal/unifi-simulator](https://github.com/MartinDejmal/unifi-simulator).
Do not modify or integrate this code into the upstream repository. Preserve the
acknowledgements and the adapted TNBU codec's MIT attribution in
`THIRD_PARTY_LICENSES.md`.

Respond to the owner in Spanish unless requested otherwise. Keep public project
documentation in English. Distinguish verified results from limitations and
hypotheses. Prefer small changes without new dependencies when the standard
library or existing components are sufficient.

## Project map

- `huawei_bridge/collector.py`: normalizes TR-098 radios, clients and data age.
- `huawei_bridge/web.py`: optional, authenticated, read-only Huawei web telemetry.
- `huawei_bridge/mesh.py`: bounded topology normalization and conservative enrichment.
- `huawei_bridge/protocol.py`: TNBU codec, CBC/GCM and packet validation.
- `huawei_bridge/unifi.py`: virtual profile, inform, adoption and WLAN mapping.
- `huawei_bridge/control.py`: allowlisted translation and ACS task confirmation.
- `huawei_bridge/__main__.py`: ACS collection, worker, HTTP API and dashboard.
- `huawei_bridge/static/`: dashboard without a framework or additional build step.
- `tests/`: `unittest` tests with synthetic fixtures and local HTTP peers.
- `compose.yml`, `Dockerfile*`: GenieACS/MongoDB/bridge lab stack.

## Startup and tests

From the project root, with Docker Engine/Desktop running and Compose v2:

```sh
make up
make logs
make stop
```

`make up` rebuilds and starts services without replacing `.env` or deleting
volumes. `make stop` stops services without deleting data. The dashboard is at
`http://127.0.0.1:8090`, GenieACS at `http://127.0.0.1:3000`, and collector health
at `/health`. The first build requires image and package downloads.

Run tests with Python 3.11+ and `requirements.txt` installed, preferably in an
activated `.venv`:

```sh
make test
```

HTTP tests need permission to bind loopback sockets; a sandbox denying that
operation does not mean the application is broken. Do not skip those tests and
claim the full suite passed. Verify Makefile changes using `make -n`; do not
execute `stop` merely to test it against the active lab.

## Configuration and persistent data

- Consult `.env.example` and README for variables. Do not print or overwrite an
  existing `.env`. Without `.env`, Compose disables the emitter and write control.
- `UNIFI_WLAN_MAP` must use real IDs of existing WLANs. Never fabricate IDs.
- An empty `HUAWEI_CONTROL_IP` means observation only. Enabling it technically
  allows the bridge to write to one CPE; it does not authorize the agent to
  expand the scope of changes.
- `HUAWEI_CONTROL_DEVICE_ID` pins control to one GenieACS device id; prefer it
  over/with `HUAWEI_CONTROL_IP`. If the IP matches more than one device, writes
  are disabled (fail-safe) — do not "fix" this by broadening the match.
- CWMP port 7547 must remain bound to one IPv4 host interface. Compose defaults
  to loopback; set `LAN_IP` to the trusted Docker-host LAN address for real CPEs.
  Never bind it to `0.0.0.0` or a public interface. `GENIEACS_UI_JWT_SECRET` has
  no insecure default and Compose fails fast if it is unset. Do not weaken either
  restriction or introduce an insecure default.
- Preserve `mongo-data` and `bridge-data`: they hold inventory, keys, adopted
  identity, WLAN bindings and provisioning state.
- Do not run `docker compose down -v`, remove volumes, forget APs, reset keys or
  readopt without explicit owner authorization. Readoption must affect only the
  virtual AP, never factory-reset the physical Huawei.

## Contracts to preserve

- The current profile is a virtual U7 Pro (`U7PRO`), not the Huawei's actual
  model or firmware. Keep the virtual MAC stable and the identity persistent.
- Publish only observed radios. The verified BE3 uses 2.4/5 GHz; do not add
  6 GHz merely because the profile is a U7 Pro.
- `Standard` describes radio capabilities, not each client's mode. Radio
  AC/AX/BE flags come from that field; `has_ht160` is limited to the 5 GHz radio
  of the BE3 `WUKUN-BE32`. A 160 MHz capability is not the active channel width.
- Do not infer missing generation, speeds, traffic, RSSI or topology. Exclude
  stale observations from reporting according to the collector's rules.
- Optional `web.py` collects authenticated, read-only Huawei telemetry. Never
  copy browser cookies or forward credentials to discovered nodes. Only login
  nonce/proof endpoints may use POST. TLS verification must remain enabled.
- `mesh.py` normalizes HiLink/EasyMesh inventory locally. Missing or conflicting
  ownership remains unknown; empty child lists do not prove zero clients.
  Exact, unique DeviceInfo MAC matching is required to enrich ACS clients.
  Web observations never refresh ACS timestamps or create control targets.
  Topology roles are reported claims, not verified router identities. Explicit
  contradictory slave/role observations remain conflicts; standalone boolean
  `IsSlave=true` hosts are candidates without inferred parents. Neither may
  supply ownership for ACS enrichment. HiLinkDevice=false alone is not exclusion.
  The local Huawei MAC prefix set is partial and yields only a manufacturer hint;
  missing a prefix does not rule out Huawei and a match does not verify router role.
- Explicit `phyMode` is local metadata, not a verified UniFi client PHY schema.
  AX3 and unknown new models remain inventory-only pending profile validation;
  adopted profiles stay pinned until an explicitly authorized migration.

## Writes and security

- Translate only SSID, fixed/automatic channel, enable/disable and WMM. Preserve
  the allowlist, unambiguous radio binding, channel validation and writable-field
  checks.
- The first configuration establishes a baseline without writes. Never impose
  UniFi defaults on the Huawei during startup or readoption.
- An ACS task ID does not confirm a write. Require matching readback values
  timestamped after the request. Preserve fault handling, timeouts, persistence
  and uncertain outcomes without automatic write retries.
- Do not translate passwords, WPA, VLANs, firmware, reboots, SSH, commands,
  transmit power or channel width without additional design and authorization.
- Never save full `system_cfg`, Wi-Fi keys, cookies or authkeys in logs, fixtures,
  public captures or documentation. Project only necessary fields when inspecting
  ACS/UniFi responses; they may contain secrets.
- Do not expose the dashboard, NBI or UI beyond loopback. Lab CWMP uses HTTP on
  port 7547 and must remain restricted to the LAN. GenieACS test authentication
  settings and the UI JWT secret are not a production configuration.
- `unifi.py` only processes inbound control messages (`control.receive`/`reset`)
  once a real `authkey` was adopted in a prior exchange — never while only the
  public bootstrap key is in use. Preserve this ordering; it blocks a
  pre-adoption MITM from pushing writes.
- Container images (bridge, GenieACS, Mongo) are pinned by digest and run with
  `read_only`/`cap_drop`/`no-new-privileges` where the service allows it. Do not
  swap a pinned digest for a bare tag or drop this hardening to "simplify" compose.
- Keep deployment-specific addresses and identifiers out of public files;
  preserve generic placeholders and disabled defaults in `.env.example`.

## Before handing off changes

Follow `CONTRIBUTING.md` and `.github/pull_request_template.md`. Every contribution
or PR must explicitly disclose AI assistance as Yes or No, including AI used for
code, tests, documentation, translations, incorporated suggestions and PR text.
For AI-assisted work, name the tools and describe their scope and actual review
and verification. Never claim No for agent-generated work. Keep disclosures
accurate during revisions without exposing private prompts, secrets or deployment
data. Preserve the project's AI development disclosure and upstream attribution.

Update tests and README when contracts, operation or limitations change. Run
relevant checks and report actual results, distinguishing automated tests from
hardware/controller validation. For new operational commands, verify that `.env`,
volumes and adoption state are preserved.

This directory may not be initialized in Git. Do not run `git init`, create
commits or publish the project unless the owner requests it.
