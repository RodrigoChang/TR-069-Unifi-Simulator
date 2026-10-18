# Review handoff: optional Huawei web / mesh inventory

## Local MAC manufacturer hint follow-up

AI assistance: Yes — OpenAI Codex added an offline partial Huawei prefix lookup,
dashboard display, documentation and synthetic fixtures. The lookup rejects
locally administered addresses and never changes device classification. Automated
verification: 18 mesh tests and the standalone dashboard check passed. The
bundled prefix set is intentionally incomplete and does not identify device role.
No hardware/controller validation was run for this display change.

## Conservative classification follow-up

AI assistance: Yes — OpenAI Codex implemented classification, dashboard labels,
documentation and synthetic regressions. No deployment or router writes performed.
Topology claims contradicted by explicit slave flags remain conflicts; standalone
slave hosts are candidates. Both are excluded from ACS enrichment ownership.
Real-device observations informed the fixtures; the changed container itself has
not been rebuilt or hardware/controller validated. At the previous check, 71
Python tests ran and 68 passed; loopback binding was denied for
`test_forgotten_ap_resets_local_and_control_state`,
`test_live_peer_adoption_and_restart`, and `test_host_and_origin_boundary`.
The standalone dashboard regression is run with `node tests/test_dashboard.js`.

### Documentation refresh and branch publication

AI assistance: Yes — OpenAI Codex reviewed and updated English project and
contributor documentation, reconciled test/hardware-validation statements and
prepared this branch for owner-requested publication. Verification on this tree:
`make test` ran 73 tests; 70 passed and three loopback HTTP-server tests failed
because this environment denied socket binding. `node tests/test_dashboard.js`,
`node --check huawei_bridge/static/app.js`, and `git diff --check` passed. These
results do not claim full-suite or post-push hardware validation. The owner
explicitly authorized a commit and push to `feat/huawei-web-mesh-ax3`.

## Summary

Optional single-origin Huawei nonce/proof authentication, read-only telemetry,
bounded HiLink/EasyMesh inventory, explicit local client PHY and conservative ACS
enrichment. Separate poller and source status; no new dependencies. Opt-in Compose
override, local dashboard inventory and operation/security documentation added.
No web writes, automatic satellite logins or web-only UniFi emission.

## AI assistance disclosure (required)

- [x] Yes, AI was used in this contribution.
- [ ] No, AI was not used in this contribution.

Tools: OpenAI Codex, including one independent read-only reviewer agent.
Scope: design, implementation, synthetic tests, source/diff review, documentation
and this handoff text. This is an agent review, not a maintainer approval.
Contributor review: comparison against AGENTS.md and the approved design/plan;
identity, control allowlist, bootstrap guard, freshness and disabled defaults
reviewed. Reviewer's minor conflict-reporting and plan-status findings addressed.

## Verification

- `make test`: 66 tests attempted; 63 pass, 3 existing socket tests fail with
  sandbox `PermissionError` (not skipped):
  `test_forgotten_ap_resets_local_and_control_state`,
  `test_live_peer_adoption_and_restart`, `test_host_and_origin_boundary`.
- All 43 new feature tests pass with synthetic fixtures/transports, including
  real urllib cookie and redirect processing without a bound HTTP server.
- Compose base + override: `config --quiet` passes with synthetic environment
  values and `--env-file /dev/null`; no actual deployment configuration printed.
- JS syntax and synthetic DOM rendering/escaping pass; `make -n up stop` and
  `git diff --check` pass. No live browser visual QA performed.
- At the initial implementation review, physical router and controller validation
  had not been performed. Follow-up owner checks then confirmed BE3 HTTPS behavior,
  read-only login/collection and a mesh snapshot. This is limited to the tested
  device/firmware and does not establish complete mesh discovery or AX3 support.
  Exact upstream master revision could not be resolved; attribution records
  inspected file URLs and date instead of claiming an unverified pin.

### HTTP Host compatibility follow-up

Owner-run diagnostics on the BE3 confirmed that the certificate DNS name passes
TLS validation but receives HTTP 404, while an explicit management-IP `Host`
header returns HTTP 200 and CSRF fields. `HUAWEI_WEB_HOST_HEADER` now accepts an
explicit bare IPv4 address, empty by default. It applies to the login and read
allowlists without changing the URL, TLS settings or connection destination.
URL/port/hostname/whitespace/CRLF values are rejected. No deployment identifiers
are included in public examples, and no private configuration was changed.

AI assistance: Yes. OpenAI Codex generated this follow-up code, regression tests
and documentation, with an independent read-only agent review. Verification:
25 web/integration tests pass; full-suite outcome above; synthetic Compose
configuration checked. Automatic authentication with the new option still needs
owner-side deployment verification; no successful login is claimed for this change.

### Opaque nonce compatibility follow-up

Owner diagnostics reached the nonce endpoint with integer iterations and valid
hex salt, but the server nonce had 96 characters, a matching client-nonce prefix
and a non-hex suffix. The previous hex-only server-nonce check rejected it before
proof submission. Server nonce validation now requires 65–512 printable ASCII
characters, excludes comma/whitespace/controls, and requires the generated
64-character client-nonce prefix plus a nonempty extension. It is not hex-decoded
or otherwise transformed. Salt and PBKDF2 work limits are unchanged.

AI assistance: Yes, OpenAI Codex. Scope: minimal validation fix, synthetic fixtures,
independent proof/regression tests, documentation and read-only agent review.
Checks: 27 web/integration tests pass; full suite result above; diff whitespace
check passes. Independent review found no important issues. Authentication with
the actual firmware remains an owner-side verification gate, not a claimed pass.

## Impact and recovery

Disabled by default; base Compose unchanged. Existing BE3 profile and virtual
identity remain stable. Adopted profiles are pinned, with mismatch status rather
than automatic migration. New AX3/unknown models stay inventory-only until a
controller profile is verified. PHY stays local pending verified UniFi schema.
All allowlisted writes still use TR-069 and existing readback protection.

Opt in only after setting a private password file and authorized origin, as
documented in README. Disable by recreating the bridge from base Compose only.
Preserve named volumes, authkeys and adoption. The implementation did not change
`.env`, router settings, running services or volumes. Branch publication is a
separate owner-authorized Git operation.

## Checklist

- [x] CONTRIBUTING.md and PR template consulted.
- [x] AI assistance and actual review disclosed.
- [x] Relevant tests/docs updated; blocked/unperformed checks disclosed.
- [x] Generic examples only; attribution and existing safety controls preserved.
- [ ] Maintainer review and hardware/controller validation (owner gate).
