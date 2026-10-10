# Route-alert release candidate 0.8.0

Tracked in [issue #8](https://github.com/davidbmck/public_transport_victoria/issues/8).
This document is the candidate checklist and intended release notes, not a claim
that a release has already been published. The upstream base is
[`dc4bf2b4a50e7b335a9d736a00e42fa8b52c21bb`](https://github.com/bremor/public_transport_victoria/commit/dc4bf2b4a50e7b335a9d736a00e42fa8b52c21bb)
(upstream 0.7). Original author acknowledgements and PTV data attribution remain.

## Changes

One enabled alert sensor is added to each configuration entry alongside the five
departure sensors. Its integer state counts route-wide notices across all PTV
categories, including general and planned notices. The `alerts` attribute retains
IDs, dates, status, descriptions, URLs and nested route/stop/direction metadata.
See the [user guide](alerts.md) for the complete schema, examples and filtering.

Only a usable end time strictly before evaluation expires a notice. Unknown end
dates are retained. Ten-minute alert polling works independently of departures,
shares requests only within the same route and exact credentials, and marks
failed feeds unavailable. Successful empty feeds report `0`; retained old data
is never presented as fresh after failure.

The integration domain, version-1 entry configuration and five departure
identities remain unchanged. No entry recreation or credential prompt is needed
for compatible existing entries. The new alert entity can be disabled in HA.
This release also declares the cloud-poll IoT class and config-entry-only schema;
it does not add YAML integration configuration.

## Candidate checks

- [x] Contract, API/coordinator/entities, regression audit and consumer examples merged.
- [x] Manifest candidate version `0.8.0`; documentation/support point to this fork.
- [x] Upstream base commit recorded; existing author credits preserved.
- [x] HACS repository topics supplied.
- [x] All 180 offline regression tests and four cleanup harness tests pass.
- [x] Official hassfest passes for the candidate.
- [x] Actual HACS 2.0.5 backend installs upstream 0.7 and upgrades to the candidate.
- [x] A fresh disposable HA configuration persists two version-1 entries and
      their ten departure identities across the source upgrade and HA restart.
- [x] Live PTV route discovery/notices across train, tram, bus and regional modes,
      availability, real authentication failure/recovery and unload/reload checked.
- [x] Installed manifest/source verified byte-for-byte against the checkout;
      deterministic package, checksum and tag/version rejection checked.
- [ ] HACS metadata validation fully passes with MIT for the fork's extensions.
- [ ] Release PR reviewed and merged; tag and release artifact rebuilt from that
      reviewed commit, with tag exactly equal to manifest version.
- [ ] Separately authorised production trial (not part of candidate development).

The offline suite establishes repeatable empty/malformed/date boundary behavior
using mocked APIs. A live trial can establish only the responses observed on its
date; it cannot force PTV to publish an empty feed or every notice category.
Neither kind of check establishes a production deployment or browser appearance.

On 10 October 2026, live PTV route samples were train `1` (three notices,
`metro_train`), tram `721` (two, `metro_tram`), bus `786` (zero), and regional
`1512` (zero). These are observations, not guarantees of future feed contents.
Two configured directions shared one healthy coordinator. A request signed with
a synthetic invalid key made both alert sensors unavailable, retained their last
successful snapshot/timestamp, and recovered with the real key. Unloading both
entries stopped the old coordinator; reloading restored available alert sensors.
All ten departure IDs/unique IDs/entry associations survived the source upgrade,
including a customised entity ID and display name. The restarted candidate's
captured debug logs contained no API key or signed request URL. Upstream 0.7's
debug logs do contain signed URLs; the trial keeps their raw contents in memory
and reports only source locations.

HACS currently passes eight of nine metadata checks. The remaining licence check
reads GitHub's repository metadata, which still describes the default branch
without a licence. Rerun it after merging this PR; do not ignore the check or
claim that MIT grants rights in inherited upstream source.

## Reproduce validation

Run `python3 scripts/test-container-cleanup.py` and
`sh scripts/test-container.sh` for offline checks. Their runtime network remains
disabled. The explicitly networked trial is separate:

```sh
sh scripts/release-container.sh --networked <candidate-ref> < <private-credentials.json>
```

The private input has a `ptv` object containing string `id` and `api_key` fields.
Keep it outside the checkout; do not paste credentials into a command or commit
it. No GitHub token is required for public downloads, subject to GitHub's
unauthenticated rate limit. Supplying `install_only: true` instead of PTV
credentials runs only source installation and does not establish upgrade or live
API acceptance. Logs are captured in memory and checked without printing signed
URLs or credentials. Failures report exception type/source location only.

The trial uses `Dockerfile.release`, pinned to the official Home Assistant
2026.9.4 image (Python 3.14.6), and a named foreground disposable container,
Docker bridge networking, read-only root and a temporary `/tmp` filesystem. There
are no host mounts, published ports, Docker socket or device access. It downloads
digest-verified official HACS 2.0.5 and exercises its actual registration/download
backend in Home Assistant. It does not drive HACS's browser/OAuth setup wizard.
Only fresh trial config entries/registries are created. A separate process loads
each installed source version so Python's module cache cannot hide the upgrade.
Task container, image and builder are removed and absence verified, including on
failure. Listing failures are nonzero with exact recovery commands.

## Package and publish

After reviewing and merging the release PR, check out its reviewed commit and run:

```sh
python3 scripts/package-release.py --tag 0.8.0 \
  --output /tmp/public_transport_victoria-0.8.0.zip
git diff --check
```

The archive contains Git-tracked integration Python/metadata/assets under
`custom_components/public_transport_victoria/`, plus the MIT text and licence
scope statement. A SHA-256 sidecar is written and
the archive's layout/version/integrity checked. No tests, private configuration,
registries, credentials, bytecode, dependencies or JavaScript build are packaged.
HACS uses the integration source in the repository; a manual installer extracts
the archive into their HA configuration directory.

Publish tag `0.8.0` from the reviewed release commit and attach the archive and
checksum using these notes. Do not create a tag from a branch with a different
manifest version or describe a draft/candidate as a verified published release.
MIT covers this fork's original extensions and modifications only. The inherited
upstream source has no declared licence and is not relicensed by this release;
see [scope and provenance](../LICENSE_SCOPE.md). A passing HACS licence check does
not grant rights in that inherited code.

## Install, upgrade and roll back

Keep a private HA backup and a separate copy of the installed integration source,
including any local alert patch, before changing it. Keep these outside this
public repository. Production deployment/restarts require separate authorization.

Add this fork to HACS as an **Integration** custom repository and select the
published `0.8.0` release once available. Check its installed manifest/source,
follow HA/HACS restart instructions and verify both configured directions,
departure identities, alert availability and any scrolling display. For manual
installation, replace only `custom_components/public_transport_victoria` with the
archive's matching directory. Keep exactly one source provider for the domain.

Do not delete/recreate configuration entries or edit registries to upgrade. A
local departure-sensor `disruptions` attribute needs a separate consumer migration
to the dedicated alert sensor's `alerts` records, with availability guards; see
the [migration guide](alerts.md#migrate-a-local-disruptions-patch). Keep old source
and consumer configuration until the replacement is verified.

If checks fail, restore the saved integration source and restart the disposable
or explicitly authorised production HA instance through its supported interface.
Restore its private backup if required by the chosen version's migration rules.
Returning to upstream 0.7 removes the additional alert capability; consumers must
handle unavailable/missing alert entities. Do not retire the old patch or delete
its rollback copy merely because installation succeeds.
