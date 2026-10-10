# Development and regression checks

This is the initial test harness for
[issue #6](https://github.com/davidbmck/public_transport_victoria/issues/6).
It protects the inherited departure integration before alert implementation in
#3–#5. Issue #6 stays open until the alert, shared-request, lifecycle and security
acceptance criteria are covered. The #3 API client tests exercise the actual
route disruption fetch method and parser. The #4 tests exercise the shared
coordinator and entry lifecycle. The #5 tests exercise production alert sensors,
upgrade identities, default enablement and supported entity-registry operations.

## Pinned environment

Tests run against **Home Assistant 2026.9.4**, a stable release, using
`pytest-homeassistant-custom-component==0.13.367`. The plugin pins HA and pytest;
the complete transitive dependency set is locked with hashes in
`requirements-test.txt`. Separate hashed build-tool requirements allow the few
source-only dependencies to build without fetching unpinned build requirements.
These are development dependencies only; the integration manifest is unchanged.

`Dockerfile.test` uses Python **3.14.7** on Debian Bookworm slim, pinned to an
immutable image digest. HA 2026.9.4 requires Python 3.14.2 or newer. Linux amd64
is the verified platform. Older HA versions and other architectures are not yet
part of the compatibility matrix.

Following the [Home Assistant testing guidance](https://developers.home-assistant.io/docs/development_testing/),
the harness uses real config-entry setup, sensor platform,
state machine, entity registry and service APIs. Only PTV HTTP responses are
mocked. All credentials, routes, stops and departures in the tests are synthetic.
The plugin and pytest configuration block external sockets; the container also
uses `--network none`, so tests cannot reach live APIs or a production HA service.

## Run and clean up

From the repository root, run:

```sh
sh scripts/test-container.sh
```

Cleanup error handling can be checked without Docker using
`python3 scripts/test-container-cleanup.py`; CI runs these checks before building.
They cover failed listings, remaining resources, confirmed absence and preserving
the original test failure status.

The script builds on the Docker engine selected by the current Docker context.
Confirm the selected context with `docker context show` before running; use an
engine suitable for disposable development tests and keep it separate from live
Home Assistant configuration.

The build copies only the public integration, test files and tool configuration
from this checkout into `/workspace` inside the image; `.dockerignore` is an
allowlist. Building requires internet access to retrieve the base image and
hashed development dependencies. Test runs use the copied files, no host mounts,
no published ports, no network, a read-only root filesystem and a temporary
512 MiB `/tmp` filesystem. HA's test configuration and registries exist only in
that temporary filesystem; there is no standalone HA daemon or persistent config.

The script creates a uniquely named builder, image and container using the Git
revision and process ID. It runs Ruff lint/format checks and pytest in foreground
containers with `--rm`, then an exit trap removes the task image and dedicated
builder/cache even when a check fails. It verifies that those named resources
are absent using successful resource listings. If Docker cannot list resources,
cleanup is reported as unverified, the script exits nonzero and prints exact
recovery commands. Base/tool images may remain in Docker's shared cache; do not prune
global images, builders or volumes to remove them.

For interrupted work, use the exact names printed by the build or recorded for
that run, rather than wildcards:

```sh
docker rm -f <task-container>
docker image rm <task-image>
docker buildx rm <task-builder>
docker container inspect <task-container>
docker image inspect <task-image>
docker buildx inspect <task-builder>
```

The final three inspections should report that each resource does not exist.
`AGENTS.md` contains the full lifecycle sequence and production boundaries.
Never restart HA, mount private configuration or operate devices to run tests.

## Current behavioural coverage

- Version-1 config-entry setup without new fields; unchanged departure names,
  entity IDs, unique IDs, all supplied attributes and owner associations.
- Two directions sharing a route, five entities with empty departures,
  estimated-versus-scheduled times and express-run metadata.
- Melbourne/UTC formatting and both Melbourne DST transitions.
- Two-minute manual-update throttling, ten-minute polling without per-sensor
  requests, network failure/unavailability/recovery, entry
  removal, and unload/reload with stable registry identities and no polling
  after unload.
- New-entry config flow and reuse of credentials by a second entry, plus route
  sorting and API network-exception propagation.
- Route disruption API responses across every contract fixture, in Melbourne
  and UTC: all categories, original metadata, duplicate selection, anonymous
  records, deterministic ordering, planned notices and expiry boundaries.
- Shared-session ownership, response-context consumption, bounded request
  timeouts, HTTP/authentication/network/JSON failures, cancellation and recovery.
  These API tests require no departure initialization.
- Shared route alert ownership and exact credential separation, successful empty
  cache reuse, ten-minute freshness, expiry on cache reuse without a new update
  timestamp, concurrent initial/manual/periodic request coalescing, cancellation,
  stopping the last subscriber, and restarting subscriptions.
- Real config-entry setup/unload/reload and production `CoordinatorEntity` sensors
  on the sensor platform: zero departures, independent initial and refresh
  failures, unavailable state with last-good coordinator data, retries/recovery
  and a registry-disabled sensor making no alert requests.
- Existing version-1 departure registry records preserved when adding the sixth
  entity; distinct entry-specific alert identities, translated names, integer
  counts, attribution, icons and complete route-wide metadata. Default enablement,
  the disable-new-entities preference, registry disable/enable with HA's automatic
  reload, shared polling, user name/entity-ID overrides, removal and manual updates.

Logging checks are mandatory across the behavioural suite, with debug capture
enabled and assertions that API keys and signed URL authentication parameters
are absent. Normal setup, new/reused credentials and failure/recovery paths are
covered. Configuration, initial setup and departure failures report exception types without
logging exception text, raw configuration or API payloads; request exceptions can
contain authentication data. Signing, flow error responses, setup failure states and entity availability
remain covered. No logging tests are marked as expected failures.

## Alert coverage to add alongside implementation

| Work item | Required additional behavioural checks |
| --- | --- |
| #3 API client | Covered by `tests/test_route_disruptions.py`, including every case in `tests/fixtures/route_alerts/cases.json`; extend these checks if the client contract changes. |
| #4 Alert coordinator | Covered by `tests/test_alert_coordinator.py`, using the actual API/coordinator, HA entry lifecycle and production entities. |
| #5 Alert entities | Covered by `tests/test_alert_sensor.py` and the production-entity lifecycle tests in `tests/test_alert_coordinator.py`, plus all existing departure regressions in `tests/test_sensor.py`. |
| #6 completion | Keep mandatory logging checks passing; complete the above tests before claiming route alerts are verified. |

Use `load_json_fixture("route_alerts/<file>.json")` for fresh fixture objects.
Do not duplicate the planned parser in tests or turn fixture counts into tests
of a reference implementation; assert outputs from the actual API/coordinator
and HA state machine once those components exist.

## CI and metadata validation

`Behavioural tests` runs the same container script on pushes, pull requests and
manual dispatches. It has read-only repository permissions and a 15-minute
timeout. Ruff checks apply to new tests only; inherited integration formatting
is outside this change. Tests are behavioural regression checks, not proof of
installation or release compatibility in a real HA deployment.

The inherited workflow is now accurately named `HACS validation`; it uses the
HACS action, not hassfest, and does not post PR comments. Its actual results must
be reported separately from the regression tests.

The first harness CI run on 5 October 2026 passed the behavioural workflow
(19 passed, two strict expected failures). HACS validation failed because the
fork has no recognised licence and no valid repository topics; its integration
manifest, HACS configuration and brands checks passed. Resolve the licence with
the upstream author rather than inventing a licence for inherited code, and
address repository topics separately before claiming HACS validation passes.

Official hassfest was run against this checkout on 5 October 2026 using
`ghcr.io/home-assistant/hassfest@sha256:39031fe75baf5566814a01f3c414764c029c54a0e1d742965d5b94a0d6120875`.
It fails on an inherited missing manifest `iot_class` and warns that
`async_setup` has no explicit configuration schema. Before making hassfest a
required passing gate, a focused metadata/setup follow-up must declare the
cloud-poll IoT class and the supported config-entry-only schema. No version bump
or release packaging change is needed for those fixes. They are not fixed or
hidden by this harness. A manual read-only check is:

```sh
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,mode=1777,size=512m \
  --mount type=bind,src="$(pwd)",dst=/github/workspace,readonly \
  ghcr.io/home-assistant/hassfest@sha256:39031fe75baf5566814a01f3c414764c029c54a0e1d742965d5b94a0d6120875
```

## Updating dependency locks

Change `requirements-test.in` or `requirements-build.in` deliberately, then
regenerate with `uv` in a disposable development environment (the pinned container
includes `uv==0.12.5`):

```sh
uv pip compile requirements-build.in --python-version 3.14.2 --universal \
  --generate-hashes --output-file requirements-build.txt --no-managed-python
uv pip compile requirements-test.in --python-version 3.14.2 --universal \
  --generate-hashes --output-file requirements-test.txt --no-managed-python
```

Do not install these requirements into the server's system Python or production
HA environment. Rebuild and run the container checks after a lock update; keep
the HA/plugin/Python baseline and any verified platform claims in this guide in
sync. Installation/upgrade trials and releases remain separate work in #8.
