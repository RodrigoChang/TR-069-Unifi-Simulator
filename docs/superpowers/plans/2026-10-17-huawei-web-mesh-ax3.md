# Huawei Web and Mesh Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Owner approved implementation; do not create commits or deploy without authorization.

**Goal:** Add optional read-only web telemetry, mesh inventory and safe AX3 capability handling without changing the existing deployment.

**Architecture:** A separate optional polling thread collects Huawei web data. Pure topology parsers produce sourced inventory; conservative matching enriches existing snapshots without replacing GenieACS identities or enabling web control.

**Tech Stack:** Python 3.11+, standard-library HTTP/cookies/crypto primitives, existing `cryptography`, `unittest`, existing static dashboard and Compose.

**Spec:** [Huawei web/mesh/AX3 design](../specs/2026-10-17-huawei-web-mesh-ax3-design.md).

## Global Constraints

- Disabled by default; plain `make up` remains unchanged.
- No new dependencies or Home Assistant runtime.
- 30-second web polling, 5-second request timeouts, 1 MiB response limit, 180-second staleness.
- Password-file input; in-memory session only; verified TLS; no redirects or inherited proxies.
- POST allowlist contains login nonce/proof endpoints only; telemetry is GET-only.
- Preserve physical/virtual identities, provisioning safeguards, image digests and required Compose secrets/bindings.
- Unknown topology, PHY or radio data must remain unknown; incomplete nodes are inventory only.

## Review Focus

1. A topology advertises an attacker-controlled address: no automatic requests or credential forwarding (Task 1).
2. A physical node has different management/radio MACs: no guessed matching or duplicate virtual adoption (Task 3).
3. Empty/partial/conflicting topology: no default-primary ownership or fabricated client count (Task 2).
4. Failed or slow web polling: no refreshed timestamps, blocked inform worker or lost ACS telemetry (Task 3).
5. AX3 identity already adopted under a different profile: no silent model change or readoption (Task 4).

## Task 1: Authenticated, read-only web client

**Files:** Create `huawei_bridge/web.py`, `tests/test_web.py`.

**Interfaces:** `HuaweiWeb(url, username, password_file, ca_file=None)`;
`HuaweiWeb.collect() -> dict` with endpoint keys `deviceinfo`, `hosts`,
`hilink`, `easymesh`, plus `collected_at` and `warnings`.

- [x] Write `test_disabled_client_performs_no_io`, `test_login_proof_and_csrf`,
  `test_reauth_is_bounded`, `test_discovered_addresses_are_not_contacted`,
  `test_redirect_does_not_forward_credentials`, `test_response_limit`,
  `test_bad_challenge_is_rejected`, and `test_diagnostics_redact_secrets`.
  Use synthetic local HTTP peers; assert actual requests and exposed results.
- [x] Run `python3 -m unittest discover -s tests -p test_web.py -v`; observe the
  intended missing-feature failures before implementing the client.
- [x] Implement the above interface with explicit endpoint allowlists, secure
  nonce generation, bounded challenge parsing, cookie/CSRF handling and at most
  one reauthentication attempt per read. Enforce 1–1000000 PBKDF2 iterations,
  reject invalid nonce/salt encodings, and use generic public errors.
- [x] Ensure missing optional topology endpoints do not discard successful
  inventory reads. Never instantiate a secondary client from response data.
- [x] Rerun the targeted tests and `make test`; record exact failures or passes.

## Task 2: Pure HiLink/EasyMesh normalization

**Files:** Create `huawei_bridge/mesh.py`, `tests/test_mesh.py`.

**Interfaces:** `normalize_web(raw: dict, now=None) -> dict` returning `nodes`,
`clients`, `warnings`, `collected_at` as specified in the design.
Consumes Task 1 output and existing `collector.mac`, `ip`, `signal`, `text` helpers.

- [x] Write `test_hilink_nested_membership`, `test_easymesh_parent_and_roles`,
  `test_missing_parent_remains_unknown`, `test_conflicting_parent_is_unresolved`,
  `test_empty_children_do_not_prove_zero_clients`, `test_unknown_phy_stays_unknown`,
  `test_malformed_and_cyclic_topology_is_bounded`, and `test_failed_poll_keeps_old_age`.
  Use synthetic MACs/IPs; assert normalized observations, not parser internals.
- [x] Run `python3 -m unittest discover -s tests -p test_mesh.py -v` and confirm red.
- [x] Implement separate parsers for `ConnectedDevices` and `routers`/`devices`.
  Bound traversal to 16 levels and 4096 records; reject larger input rather than
  silently manufacturing complete topology. Keep a visited set for nodes.
- [x] Join `HostInfo` only by canonical MAC. Preserve null activity/parent values,
  source labels and timestamps; parse only explicit known PHY strings.
- [x] Rerun targeted tests and `make test`; verify no guessed parent or counter output.

## Task 3: Optional runtime, conservative merge and inventory UI

**Files:** Modify `huawei_bridge/__main__.py`, `huawei_bridge/static/app.js`,
`huawei_bridge/static/index.html`, `.env.example`, `README.md`;
create `compose.web.yml`, `tests/test_web_integration.py`.

**Interfaces:** `merge_web(snapshot: dict, web_snapshot: dict, devices: list) -> dict`
in `mesh.py`; `Application.collect_web()` publishes completed web snapshots;
`Application.snapshot()` adds `web_status` and `mesh` without replacing existing keys.

- [x] Write `test_disabled_source_preserves_existing_snapshot`,
  `test_unique_physical_match_preserves_virtual_identity`,
  `test_ip_or_bssid_only_match_is_rejected`, `test_unmatched_nodes_inventory_only`,
  `test_web_failure_preserves_acs_reporting`, `test_web_poll_does_not_block_inform`,
  `test_conflicting_parent_does_not_move_station`, and
  `test_web_inventory_cannot_become_control_target`. Also add
  `test_web_does_not_reactivate_stale_acs_station` and
  `test_fresh_acs_fields_win_over_conflicting_web_values`.
- [x] Run `python3 -m unittest discover -s tests -p test_web_integration.py -v`
  and confirm the intended missing-feature failures.
- [x] Add the optional thread only when configured; publish under the existing
  mutex without holding it during HTTP I/O. Use `threading.Event.wait(30)` for
  polling, retain old timestamps on failure and expose separate source errors.
- [x] Implement exact physical-MAC matching using CPE `DeviceInfo.MACAddress`;
  absent evidence means separate inventory. Match clients only under a uniquely
  identified AP, never by management IP alone; keep existing IDs/virtual MACs.
- [x] Preserve ACS eligibility and timestamps; fill only missing fields from
  fresh, unambiguously associated web observations. Conflicts remain separate
  inventory with warnings, never a silent reassignment or freshness upgrade.
- [x] Show mesh source, role, parent, freshness and unknown fields in the panel;
  do not feed incomplete inventory into `UniFi.tick` or `Control`.
- [x] Add opt-in `compose.web.yml`: URL/username variables and a read-only password
  secret file, optional CA mount, no changes to existing ports/digests/hardening.
  Document explicit opt-in and rollback without deleting volumes.
- [x] Validate Compose using synthetic environment values without printing a real
  expanded configuration; run targeted tests and `make test`. Review rendered
  inventory with synthetic data if browser automation is available.

## Task 4: AX3 profiles, provenance and review handoff

**Files:** Modify `huawei_bridge/unifi.py`, `huawei_bridge/mesh.py`,
`tests/test_bridge.py`, `tests/test_web_integration.py`, `README.md`, `AGENTS.md`,
`THIRD_PARTY_LICENSES.md` when code is adapted.

**Interfaces:** `profile_for(ap: dict) -> dict | None` in `unifi.py`, providing
model/display/version/required-version for known hardware. Persist the selected
profile in virtual state; `None` means unsupported for new web-only emission.
No speculative profile codes: verify the AX3 Wi-Fi 6 profile against the target
controller before adding it; an unverified model remains inventory-only.

- [x] Write `test_existing_be3_profile_and_identity_unchanged`,
  `test_ax3_has_no_inferred_wifi7_or_160mhz`,
  `test_unknown_model_is_inventory_only`, and
  `test_adopted_profile_mismatch_requires_explicit_migration` before changing code.
- [x] Run the new targeted tests and observe the intended red state.
- [x] Implement profile selection without rewriting existing adopted identity or
  authkeys. Keep standards-derived capabilities separate from per-client PHY.
  Retain PHY in the local snapshot; export UniFi client flags only after their
  schema is separately verified. Do not invent missing radio/SSID data.
- [x] Record any adapted upstream revision and MIT copyright notice. Update docs
  to distinguish implemented inventory from unvalidated per-node UniFi emission,
  and retain all existing bootstrap/control and ambiguous-IP protection tests.
- [x] Run `make test` and `git diff --check`. Record checks blocked by the sandbox
  as blocked, not passed. Do not start Docker or contact hardware automatically.
- [x] Prepare a review summary using `.github/pull_request_template.md`, including
  `AI assistance: Yes`, actual tools, scope, checks and remaining hardware gates.
  Do not commit, push or create a PR without a separate request.

## Handoff status

Implemented in the working tree of `feat/huawei-web-mesh-ax3`; not committed,
deployed or enabled. Configuration, routers and persistent lab state were not touched.

Verification: 60 tests attempted, 57 pass; three existing loopback tests are blocked
by sandbox PermissionError: test_forgotten_ap_resets_local_and_control_state,
test_live_peer_adoption_and_restart, test_host_and_origin_boundary.
New feature tests use synthetic transport peers (including real urllib cookie and
redirect handlers), not bound sockets. Checkboxes indicate implementation/checks
performed, not a claim that the full suite passed. All 37 new tests pass.
Compose override validates with synthetic environment values; JS syntax, synthetic
rendering, make dry-run and diff whitespace checks pass.

Deviations and gates:
- No new worktree: owner selected the existing branch; Git metadata is read-only.
- Progress ledger is in the already ignored .private directory.
- Profile tests live in tests/test_profiles.py to isolate this feature.
- AX3/unknown new emission remains inventory-only pending real profile verification.
- Upstream attribution identifies the inspected master files and access date;
  an exact master SHA could not be resolved (network/cache limitation).
- Browser visual QA, physical firmware login, actual mesh completeness, TLS peer
  behavior and controller integration were unvalidated at the original plan
  handoff. Subsequent owner checks confirmed BE3 read-only web collection; those
  checks do not establish complete mesh discovery or AX3 firmware support.
- Final independent read-only review found no critical/important issues; its
  missing conflict warning and stale plan status were addressed.

AI assistance: Yes. Tool: OpenAI Codex. Scope: design, implementation, tests,
documentation and independent agent review. Review: source/diff review, synthetic
automated checks and safety-contract comparison. Owner/hardware review is still
required. At the original plan handoff no commit, push or PR existed. The owner
later explicitly authorized publication of this feature branch.
