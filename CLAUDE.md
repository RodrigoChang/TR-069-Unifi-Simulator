# Claude Code project instructions

## Read first

Before making changes, read and follow:

- [AGENTS.md](AGENTS.md): project map, implementation contracts, operational
  safeguards and verification requirements. Keep shared agent rules there rather
  than duplicating them in this file.
- [CONTRIBUTING.md](CONTRIBUTING.md): contribution, review and AI disclosure policy.
- [README.md](README.md): setup, compatibility, supported controls and limitations.
- [PR template](.github/pull_request_template.md): required when preparing a PR.

This independent Huawei TR-069/GenieACS bridge reports telemetry to UniFi through
a virtual AP. It is based on the AP simulation work of `unifi-simulator`;
preserve upstream acknowledgements and [MIT attribution](THIRD_PARTY_LICENSES.md).
It does not provide native UniFi management of Huawei hardware.

## Working conventions

Respond to the owner in Spanish unless asked otherwise. Keep public documentation
and contribution/PR descriptions in English. Make the smallest complete change
within the request; do not add dependencies or unrelated features by default.

Never print, overwrite or commit local `.env`, private keys, cookies, adoption
state or deployment-specific identifiers. Do not reset/readopt devices, delete
volumes or modify physical router settings without explicit owner authorization.
Do not broaden the provisioning allowlist or fabricate unavailable telemetry.

## Commands

From the project root:

```sh
make -n up       # Preview startup without running it.
make up          # Build/start Docker services when the task requires it.
make logs        # Follow bridge logs; review output for sensitive data.
make test        # Run unittest with Python 3.11+ and requirements.txt installed.
```

Prefer an activated `.venv` for local tests. `.env` is read by Docker Compose,
not automatically by direct Python execution. Do not run a second emitter against
an active virtual AP or start/stop the stack just to validate documentation.

Report commands actually run, results and skipped checks. Separate automated
tests from real hardware/controller validation. HTTP tests require permission to
bind loopback sockets; a sandbox restriction is not a passing test result.

## AI transparency

Work generated or assisted by Claude must declare **AI assistance: Yes** in the
contribution or PR. Name Claude/Claude Code and any other tools actually used;
include the model only if known. Describe affected files/activities, review,
verification and remaining gaps. Update the disclosure during revisions and
never claim that agent-generated work was produced without AI.

Do not publish private prompts or session exports as evidence. Preserve the
project's AI development disclosure. Do not create commits, PRs or publish the
repository unless requested; generating these instructions is not authorization
to do so.
