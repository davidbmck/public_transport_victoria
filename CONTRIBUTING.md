# Contributing to this development fork

This fork's purpose is to develop structured route alerts for a focused upstream
contribution, with no commitment to a continuing replacement or release schedule.
Original integration credit remains with
[bremor/public_transport_victoria](https://github.com/bremor/public_transport_victoria).
Preserve attribution and any existing licence information; do not invent a licence
for inherited code.

Read the relevant [backlog issue](https://github.com/davidbmck/public_transport_victoria/issues/1)
and [AGENTS.md](AGENTS.md) before editing. Keep each PR focused, use a branch based
on this fork's `master`, and preserve existing configuration entries, departure
identities and behavior. Document public changes and test their failure/recovery
paths. Release/version changes belong to #8; upstream contributions belong to #9.

From the checkout root, run:

```sh
python3 scripts/test-container-cleanup.py
sh scripts/test-container.sh
git diff --check
```

The [development guide](docs/development.md) explains the digest/hash-pinned HA
environment, shared local/CI checks, task-owned cleanup, test coverage and separate
HACS/hassfest gaps. Use mocked APIs and synthetic fixtures. Never mount a live HA
directory, Docker socket or devices in the test container, publish ports, deploy
to production or commit credentials/private configuration as part of development.

PRs target this fork's `master`, link their issue, describe compatibility, list
actual validation and its limits, and remain unmerged until separately authorized.
Prefer another issue for unrelated bugs or cleanup. The [alert guide](docs/alerts.md)
contains generic dashboard/automation examples and migration guidance.
