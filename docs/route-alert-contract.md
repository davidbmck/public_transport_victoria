# Route alert contract

This specification resolves [issue #2](https://github.com/davidbmck/public_transport_victoria/issues/2)
under the [route alerts backlog](https://github.com/davidbmck/public_transport_victoria/issues/1).
It defines the behaviour to implement in issues #3–#5 and test in #6. The API
client implements the fetch and parsing contract below; the integration does
not yet provide alert polling or sensors.

`Connector.async_route_disruptions()` fetches the configured route independently
of departure initialization and returns normalized, ordered, non-expired notice
objects. It uses Home Assistant's shared HTTP session with a 30-second total
timeout (10-second connection limits and a 20-second socket-read limit), without
closing that session. HTTP, network, timeout and JSON failures propagate;
offline or malformed payloads raise `RouteDisruptionsError` with a safe message.
Callers must avoid logging raw transport exceptions, which may contain signed
URLs. Successful empty responses return `[]`.

Parsing is separate from HTTP fetching. `non_expired_disruptions()` can
re-evaluate a successful normalized snapshot against one aware reference instant
when the later coordinator reuses it; this does not make a failed refresh healthy.

## Entity and compatibility

Create one additional `sensor` entity for each existing or new configuration
entry. Its unique ID is exactly
`public_transport_victoria_<config_entry.entry_id>_route_alerts`. The entry ID is
already persisted by Home Assistant: it survives reloads, display-name changes
and upgrades, distinguishes two entries with the same route, and exposes no
credentials. Deleting and recreating an entry gives it a new identity.

The default English display name is
`<route_name> line to <direction_name> from <stop_name> alerts`, using the existing
entry's names and a translated name template. This identifies the owning entry;
the notices themselves cover the whole route. Home Assistant assigns the entity
ID and preserves user overrides through its registry. Do not promise a fixed
`sensor.*` entity ID or change an existing registry entry to obtain one.

Use `SensorEntity` with `CoordinatorEntity`, integer `native_value`, an alert icon
(`mdi:alert-circle-outline`), no device class, no state class, no unit, and no
entity category. Keep the existing PTV attribution from `const.ATTRIBUTION`.
Associate the entity with its owning configuration entry through supported
platform setup. No new device or configuration fields are required.

Leave all five departure sensors, their names, entity IDs, unique IDs, attributes,
selection, timezone conversion and polling behaviour unchanged. Existing entries
need no recreation, credentials prompt or config-entry migration.

## State and attributes

While available, the state is the integer number of retained, deduplicated,
non-expired notices. `0` is a successful result, not an error. Full descriptions
belong in attributes, never in the state.

| Attribute | Meaning |
| --- | --- |
| `alerts` | Ordered list of structured notices as defined below; `[]` after a successful empty response. |
| `route_id` | Configured route ID as an integer. |
| `route_type` | Configured route type as an integer. |
| `last_successful_update` | UTC ISO 8601 timestamp of the most recent complete successful alert response, or `null` before any success. |
| `attribution` | The integration's existing PTV attribution. |

Do not publish a changing timestamp merely for an unsuccessful attempt. On a
failed refresh, Home Assistant's state is `unavailable`. Last-good `alerts` and
`last_successful_update` may remain visible, but the state must stay unavailable
until a complete successful refresh. Before the first success, `alerts` is `[]`
and `last_successful_update` is `null`, with state unavailable, never `0`.
Consumers must check availability before interpreting the count or attributes.
On recovery, atomically replace the old snapshot, including with `[]` and `0`.

Each record keeps the PTV field names. Always expose these keys:

| Key | Missing-data rule |
| --- | --- |
| `disruption_id` | Original identifier; `null` if absent. No invented public ID. |
| `title`, `description`, `url` | Original values; `null` if absent. Do not substitute one for another. |
| `disruption_status`, `disruption_type` | Original values and case; `null` if absent. No local classification. |
| `published_on`, `last_updated`, `from_date`, `to_date` | Original timestamp values, including malformed values; `null` if absent. |
| `routes`, `stops` | Original arrays, including their nested metadata; `[]` if absent or `null`. |
| `categories` | Sorted, unique names of response buckets containing this notice. |

Retain other supplied notice fields too, such as `colour`, `display_on_board`
and `display_status`. The derived `categories` field takes precedence over a
same-named input field. Preserve complete route and stop objects, including
`route_type`, `route_id`, `route_name`, `route_number`, `route_gtfs_id`,
`stop_id`, `stop_name`, and nested `direction` fields (`route_direction_id`,
`direction_id`, `direction_name`, `service_time`) where supplied. Preserve unknown
nested fields. Do not flatten direction metadata into a guessed top-level value.

PTV defines route `direction` as an optional object. An absent direction remains
absent; it does not mean the configured direction. As a defensive policy,
preserve a supplied `null` direction, but do not treat it as a documented API
value. `service_time` is the API's local AEDT/AEST clock text, not an absolute
timestamp; retain it without converting it or inventing a date. PTV explicitly
documents `null` for `service_time` and for an unknown `to_date`, despite declaring
both fields as strings in the Swagger schema.
Missing text or metadata does not discard an otherwise usable notice. For
non-array, non-null `routes` or `stops`, or non-object elements in those arrays,
fail the response rather than silently lose coverage.

## Coverage and response validity

Fetch `/v3/disruptions/route/{route_id}` independently of departures and runs.
Omit the optional status filter to request current and planned notices. Do not
restrict the request to a stop, direction, disruption mode or route type.

Flatten every category list inside `disruptions`, including `general`,
`metro_train`, `metro_tram`, `metro_bus`, `regional_train`, `regional_coach` and
`regional_bus`. The public schema also currently lists `school_bus`, `telebus`,
`night_bus`, `ferry`, `interstate_train`, `skybus` and `taxi`. Process future list
buckets by the same rule without maintaining a category allowlist. A bucket
label is source metadata, not a reason to exclude a notice.

The route endpoint determines scope. Retain all notices it returns, even those
with no route metadata, multiple routes, a different stop or direction, a future
start, an unfamiliar status, or descriptions mentioning parking or accessibility.
Do not filter by English text, status, display flags or the entry's transport
mode. Stop/direction filtering is a consumer choice for the first release.

A successful response requires HTTP success, valid JSON with an object root,
and an object-valued `disruptions`. An empty object, missing category keys, empty
arrays and `null` category values are valid empty data. Each non-null bucket must
be an array of notice objects; an empty notice object is allowed. Missing or
null `disruptions`, a scalar bucket, or a non-object notice makes the entire
refresh fail. Do not publish a partially parsed response as successful.

`status` at the response root is API metadata, distinct from notice status. It
may be absent. If supplied, it must be an object; an explicit `health: 0`
(offline) fails the refresh. Missing or `null` health is unspecified, not proof
of failure. A supplied health value other than integer `0` or `1` is malformed
(booleans are not integers here). HTTP/authentication errors, timeouts, network
errors, invalid JSON and malformed payloads propagate as failures, never `[]`.

## Dates, status and expiry

Use one timezone-aware UTC reference instant per response. A usable absolute
timestamp is an ISO 8601 date and time with `Z` or an explicit numeric UTC
offset; fractional seconds are allowed. Interpret offsets as instants and
compare in UTC. Date-only strings, times without an offset, invalid calendar
dates, empty strings, `null`, numbers and booleans are unknown. Do not assume
Melbourne, Home Assistant's timezone or midnight for an unknown value.

After deduplication, exclude a notice only when its usable `to_date` is strictly
earlier than the reference instant. At exactly `to_date`, retain it; on the next
evaluation after that instant it expires. Missing, null, malformed or naive end
times retain the notice. There is no grace period or inferred end time.

Retain future `from_date` values and `Planned` notices. Preserve unknown status
values; do not recalculate status from dates. Publication/update times rank
duplicate records, not validity. A malformed start does not invalidate a valid
end. If a valid end precedes a valid start, the same end-only expiry rule applies;
do not repair the dates. DST and the Home Assistant display timezone cannot
change which instants are expired. All exposed date values remain as supplied.

Expiry is evaluated on each successful refresh, including reuse of a successful
shared snapshot at setup. No per-notice timer is promised; between refreshes a
notice may remain visible past its end, normally for up to the ten-minute poll
interval. A failed fetch marks the feed unavailable instead of locally ageing
cached data and reporting a healthy count.

## Duplicate IDs and ordering

A usable ID is a JSON integer in PTV's signed `int64` range
(`-9223372036854775808` through `9223372036854775807`), excluding booleans.
Negative IDs and `0` are usable IDs; the API specifies no non-negative minimum.
Group records with the same usable `disruption_id` across all categories. Choose
one complete source record by these priorities, highest first:

1. Latest usable `last_updated`; a usable value outranks an unknown value.
2. Latest usable `published_on`, with the same unknown-value rule.
3. Lexicographically greatest canonical source JSON as a deterministic tie-break.

Canonical JSON means recursive sorted object keys, compact separators, UTF-8
characters unescaped, and array order preserved (`sort_keys=True`,
`separators=(",", ":")`, `ensure_ascii=False` in Python). Compare the original
notice object, without derived `categories` or missing-field defaults. Input
object key order and category/list traversal order cannot affect selection.
Do not merge conflicting descriptions or dates. Union and sort all the source
category names for the selected notice. Select the duplicate winner before
checking expiry, so an older unexpired variant cannot revive an expired update.

Without a usable ID, preserve the supplied ID value and treat the record as
anonymous. Collapse only exact canonical-source-JSON duplicates, unioning their
categories. Distinct anonymous records, even with the same title, remain
distinct. No fabricated ID, title matching or unstable list-index identity.

Sort retained records with usable IDs first, by numeric ID ascending; follow
with anonymous records by canonical source JSON ascending. Do not reorder the
feed according to human-readable titles or the clock. State equals this final
list's length. Count a notice once even if it affects several routes or stops.

## Default enablement and polling lifecycle

The new alert entity is enabled and visible by default, including on upgrade.
Alerts are a primary feature, so users receive them without a second setup step.
This adds alert requests and recorded attributes to existing installations;
users can disable the sensor in Home Assistant to opt out. Respect Home
Assistant's integration-level setting to disable newly added entities.

Use a dedicated alert coordinator with a ten-minute refresh interval. It works
with zero departures and does not inherit the departure connector's two-minute
throttle. A timetable failure must not prevent alert setup or refresh; an alert
failure must not prevent timetable setup or refresh. On an initial alert
failure, expose an unavailable alert entity and retry; do not fail the entire
configuration entry solely because alerts failed.

Share a coordinator and completed responses for the same integer route ID and
exact developer-ID/API-key credential pair. Different stops/directions share;
different credentials do not. Keep the credential scope private in memory, never
in entity IDs, attributes or logs. Coalesce concurrent initial/manual refreshes;
a successful empty result is cached exactly like a non-empty result. A newly
enabled entity can reuse a still-fresh successful snapshot, re-evaluating expiry
against the current instant. A snapshot at least ten minutes old requires a
refresh. Failed refreshes cannot qualify as fresh successful snapshots.

Only enabled alert entities subscribe and drive requests. With no enabled
subscribers, do not make an initial alert request or keep a periodic alert timer.
Disabling one of several shared entities leaves polling active for the others;
disabling the last stops periodic requests. Re-enabling resumes refreshes.
Unloading an entry releases its ownership and subscription; unloading the last
owner releases shared resources. Reloading must not leak timers or duplicate
requests. Never close Home Assistant's shared HTTP session.

Use bounded timeouts and consume responses inside their HTTP context. Log only
safe error information: no API keys, signed request URLs, authentication headers
or complete configuration entries. Manual refresh uses the same coordinator and
does not trigger an alert request for each departure sensor. Leave departure
polling and its existing manual-update behaviour unchanged.

## Fixtures and implementation checks

The [fixture catalogue](../tests/fixtures/route_alerts/README.md) and
[case expectations](../tests/fixtures/route_alerts/cases.json) provide synthetic
payloads, fixed clocks, expected counts/order, duplicate choices and failure
cases. They contain no live responses, credentials, signatures or household
data. Extend the [#6 harness](development.md) alongside #3–#5 to test the actual
alert implementation; the fixture catalogue alone is not behavioural coverage.

Beyond fixture parsing, #6 must exercise existing-entry upgrades and departure
identity preservation; two entries on one route; zero departures; independent
failures and recovery; coalescing and credential separation; disabled entities;
unload/reload; timeout/context handling; and captured logs without secrets.
No runtime, Home Assistant installation or production verification is implied
by this specification or by syntactically valid JSON fixtures.

## Sources and provenance

The API structure was checked against the public
[PTV v3 Swagger schema](https://timetableapi.ptv.vic.gov.au/swagger/docs/v3)
on 5 October 2026, specifically the route disruption endpoint and the
`V3.DisruptionsResponse`, `V3.Disruptions`, `V3.Disruption`, route, direction,
stop and status models. Fixtures are authored examples of that structure;
optional-field omissions, malformed values and the invented `future_mode`
bucket deliberately test robustness. They do not assert live API behaviour.

Entity lifecycle decisions use Home Assistant's supported
[entity properties](https://developers.home-assistant.io/docs/core/entity/),
[registry enablement](https://developers.home-assistant.io/docs/entity_registry_disabled_by/)
and [data coordinators](https://developers.home-assistant.io/docs/integration_fetching_data/).
No code or private local patch was copied from other forks.
