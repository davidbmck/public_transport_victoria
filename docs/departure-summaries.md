# Next and following departures

Each timetable entry adds two enabled timestamp sensors: **Next departure** and
**Following departure**. Their names include the configured route, direction and
stop. Select their actual entity IDs in Home Assistant; registry customisations
and conflicting names can change the generated IDs.

The summaries read the same fetched records as the existing five departure
sensors. They do not look up other entities or assume five source sensors. They
work across the configured transport modes and add no API requests. The existing
ten-minute polling interval and two-minute manual-update throttle still apply.

## States and availability

A selected service has a timezone-aware timestamp state (stored as UTC and
displayed in the frontend's local time). A successful timetable response with no
qualifying next/following service gives `unknown`, with no timestamp or service
attributes. A timetable fetch failure gives `unavailable` until a successful
refresh; a throttled retry cannot turn failed retained data into a healthy feed.
Route alerts keep their independent availability.

Only the fetched list is searched. Exhausting it means no upcoming service is
known **within that list**, not that no further services exist. The integration
keeps its existing request limit; fetching a larger horizon is separate work.

## Selection rules

1. Parse the estimated and scheduled timestamps independently. Accept valid ISO
   timestamps with a timezone; missing, malformed and timezone-naive values are
   ignored. Prefer a valid estimated time, otherwise a valid scheduled time.
2. Keep only records whose effective timestamp is **strictly later than now**.
   A service exactly at the current time is no longer upcoming. A valid estimate
   in the past is not replaced by a future scheduled time.
3. Sort by effective time, then service identity type/value in lexical order,
   then original API position. Equal timestamps can represent distinct services.
4. Prefer a nonempty string `run_ref` (trimmed for identity, excluding `-1` and
   `0`). Otherwise use a positive integer `run_id`, accepting decimal strings
   and normalising leading zeroes. Booleans, zero, negative IDs and malformed
   identifiers do not establish identity. References and numeric IDs are
   separate identity types: records with different references, or a reference
   versus only an ID, are not assumed to describe the same service.
5. Deduplicate known identities, retaining the earliest qualifying record.
   Select the first two remaining candidates. Records without a meaningful
   identity remain separate candidates, using API position to break equal-time
   ties; their distinctness cannot be established, and no reliable service
   reference is invented for them.

One shared local callback advances both summaries when the next selected time
is reached, without fetching data or resetting polling. Refreshes replace that
callback; failures, disabling both summaries and entry unload cancel it. Reload
creates a new subscription and timer. Existing departure sensors retain their
API ordering and local clock-string states; the summary rules apply to the new
sensors only. Invalid source timestamps now safely yield `No data` in a legacy
sensor instead of crashing timetable processing.

## Attributes

For a selected record, both sensors expose:

| Attribute | Meaning |
| --- | --- |
| `service_reference` | Normalised preferred `run_ref` or fallback `run_id`, as a string; `null` when identity is unknown. |
| `scheduled_departure_utc` | Valid scheduled time as an ISO UTC timestamp, otherwise `null`. |
| `estimated_departure_utc` | Valid estimated time as an ISO UTC timestamp, otherwise `null`. |
| `time_source` | `estimated` or `scheduled`, describing the state timestamp. |
| `run_ref`, `run_id` | Original scalar identifiers, including sentinel values; `null` for missing/nonscalar values. |

The normal integration attribution is also present. Dashboard wording, colours
and travel preferences remain dashboard choices.

## Generic dashboard example

Use direct timestamp sensor references in place of templates that collect and
sort individual departure entities. Replace these example IDs with those shown
in your entity registry:

```yaml
type: entities
title: Upcoming departures
entities:
  - entity: sensor.example_metro_line_to_example_city_from_example_station_next_departure
    name: Next departure
  - entity: sensor.example_metro_line_to_example_city_from_example_station_following_departure
    name: Following departure
```

The new unique IDs are entry-scoped:
`public_transport_victoria_<entry_id>_next_departure` and
`public_transport_victoria_<entry_id>_following_departure`. Two entries sharing a
route have separate summaries. Existing version-1 entries, the five departure
unique IDs, user names and entity IDs need no migration. Home Assistant's
disable-new-entities preference applies to the additions.
