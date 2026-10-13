# Release 0.9.0: departure summaries

This release follows [PR #22](https://github.com/davidbmck/public_transport_victoria/pull/22)
and [issue #21](https://github.com/davidbmck/public_transport_victoria/issues/21).
It adds **Next departure** and **Following departure** timestamp sensors per
timetable entry. They select upcoming services from the fetched timetable and
advance as cached times pass, without additional API polling. See the
[summary guide](departure-summaries.md) for attributes, identity rules, exact
time boundaries, availability and a generic dashboard example.

Known services are deduplicated; simultaneous distinct services remain eligible.
Unknown identifiers do not become invented reliable service references. A
successful exhausted list gives `unknown`; timetable failures give `unavailable`
independently of route alerts. HTTP/payload failures and throttled retries no
longer present retained timetable data as a healthy refresh. Missing/malformed
timestamps are handled safely.

The integration domain, version-1 configuration entries, existing departure and
alert identities, valid departure states/attributes, timezone handling and
polling intervals are preserved. No entry recreation or credential prompt is
needed. New sensors are enabled unless Home Assistant's disable-new-entities
preference applies.

The upstream baseline remains
[`dc4bf2b4a50e7b335a9d736a00e42fa8b52c21bb`](https://github.com/bremor/public_transport_victoria/commit/dc4bf2b4a50e7b335a9d736a00e42fa8b52c21bb)
(upstream 0.7). MIT covers this fork's original extensions and modifications;
inherited source is not relicensed. Original attribution and
[licence scope](../LICENSE_SCOPE.md) remain intact.

## Release validation

The release candidate uses manifest/tag `0.9.0`. PR #22 passed the offline
231-test suite, official hassfest, and GitHub behavioural/HACS workflows before
merge. On 13 October 2026, the 0.9.0 candidate also passed:

- All **231 behavioural tests** and Ruff lint/format checks in the pinned Python
  3.14.7 / Home Assistant 2026.9.4 Linux amd64 test environment.
- All four cleanup error-handling checks.
- Official hassfest: one integration, zero invalid integrations.
- Real HACS 2.0.5 installation of published `0.8.0` and upgrade to candidate
  `a5135337d583829feed3274011d1a7f049ed0880` on official HA 2026.9.4, with
  mocked PTV responses. All **12 existing registry records**, including ten
  departure identities and the customised departure name/ID, were preserved.
  Four new timestamp summaries were available after restart and unload/reload;
  alert authentication failure/recovery and successful empty feeds passed.
- Installed integration source matched the candidate checkout byte-for-byte.
- Archive integrity, deterministic rebuild and rejection of a mismatched tag.
  The 16-file archive SHA-256 is
  `6819ba219ff30bee8cbf149d6eb82f11bb298bbc6fe1e4ade1e39db72f06c20b`.

Task test/installation containers, images and builders were removed and absence
was verified. GitHub workflow and final source commit results are included in
the published release notes; the archive is rebuilt from the merged release
commit before publication.

The disposable upgrade trial uses official Home Assistant 2026.9.4 and HACS
2.0.5. It installs this fork's published `0.8.0` baseline through HACS, creates
two version-1 entries with synthetic PTV data, customises one departure name/ID,
persists storage, installs the candidate through HACS and starts a separate
process with the upgraded source. The checker compares installed source bytes,
all existing registry records and the ten departure identities, then verifies
four timestamp summaries, independent alerts, simulated authentication failure
and recovery, and unload/reload.

Reproduce from the checkout root, replacing the candidate ref:

```sh
python3 scripts/test-container-cleanup.py
sh scripts/test-container.sh
printf '%s\n' '{"mock_ptv":true,"baseline":{"repository":"davidbmck/public_transport_victoria","ref":"0.8.0","version":"0.8.0"}}' \
  | sh scripts/release-container.sh --networked <candidate-ref>
```

Only public GitHub/HACS downloads use networking. All PTV requests in this mode
are intercepted in memory with synthetic data; no live PTV credentials or
household configuration are used. Release trial containers are foreground,
disposable, read-only, with temporary `/tmp` storage, no host mounts, ports,
devices or Docker socket. Offline behavioural containers remain network-disabled.
Cleanup removes and verifies only the task's container, image and temporary
builder. This trial does not drive HACS's browser/OAuth setup wizard.

Live PTV checks for the new summaries, production deployment and other HA
versions/platforms are not established by these checks. A fetched-list
exhaustion does not prove there are no later services outside that list.

## Install and roll back

Select `0.9.0` in this fork's HACS custom integration repository and follow HA's
restart instructions. Preserve a private backup and the previous integration
source before an actual installation. For manual installation, extract the
release archive and replace only `custom_components/public_transport_victoria`.
Use exactly one source provider for the integration domain.

After installation, verify existing departure/alert entities and both new
summaries. Use the actual registry IDs for dashboards; do not delete/recreate
entries or edit registry storage. To roll back, reinstall `0.8.0` through HACS
or restore its saved integration source, then restart through HA's supported
interface. Existing configuration requires no reverse migration; the added
summary sensors become unavailable and their consumers must handle that.
Production installation and restarts require a separate deployment task.

The release archive contains tracked integration source/metadata plus licence
and scope files. Its SHA-256 sidecar, archive integrity and exact manifest/tag
match are verified. It contains no private configuration, dependencies, tests,
bytecode, JavaScript build or generated cache files.
