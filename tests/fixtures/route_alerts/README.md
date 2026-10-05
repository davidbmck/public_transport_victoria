# Synthetic route alert fixtures

These examples support the [route alert contract](../../../docs/route-alert-contract.md)
and [issue #2](https://github.com/davidbmck/public_transport_victoria/issues/2).
They are fixture inputs and expectations, not an implemented alert test suite.
Use the [issue #6 harness](../../../docs/development.md) to add behavioural
coverage alongside issues #3–#5.

## Provenance

All notice text, IDs, routes, stops and URLs were authored for this repository.
No live PTV requests, local Home Assistant files or other-fork patches were used.
The structure follows the public
[PTV v3 Swagger schema](https://timetableapi.ptv.vic.gov.au/swagger/docs/v3),
checked on 5 October 2026. `example.invalid` URLs are deliberately non-resolving
placeholders, not PTV article URLs. No fixture contains authentication material
or household configuration.

`all_categories.json` is a composite parser example: it exercises every currently
documented category plus an invented future category in one payload. Its routes
do not represent the response for a real single route. The fixtures test how the
integration handles the endpoint's supplied notices, not whether PTV will return
a particular category for a live route. Malformed values and omissions are
intentional robustness cases, not claims about observed API responses.

PTV explicitly documents null `to_date` and `service_time` values despite their
Swagger string types. The representative category fixture keeps those documented
exceptions and omits an optional direction object when none is supplied. The
inline `defensive_null_direction` case separately exercises an undocumented null
direction value that the integration chooses to preserve.

## Catalogue

| File | Coverage and expected behaviour |
| --- | --- |
| `all_categories.json` | General, metro train/tram/bus, V/Line train/coach, regional bus, school bus, telebus, night bus, ferry, interstate train, SkyBus, taxi and future mode: 15 notices. Includes planned notices, unknown ends, multiple routes/stops, nested directions and `service_time`, GTFS IDs and display metadata. Retain the V/Line parking/lift notice even with false display flags. |
| `dates.json` | Current/planned/unknown status, future starts, old publication, expired ends, exact boundaries, offsets, fractions, null/missing/empty/malformed/naive/date-only/non-string dates and inconsistent start/end pairs. 15 notices at the fixed clock; 11 two microseconds later. |
| `duplicates_and_missing.json` | Cross-category IDs (including a signed negative ID), newer-update selection, publication ranking, canonical tie-break, newer expired update, ID `0`, missing/null/string/boolean IDs, empty notice object, exact anonymous copies and distinct notices sharing a title. 12 notices; input-order reversal must not change output. |
| `dst_boundaries.json` | Explicit-offset instants on Melbourne's 2026 spring jump and autumn repeated hour. Spring case retains IDs 402 and 403; autumn case retains 401, 402, 403 and 405. |
| `empty.json` | All documented categories present as empty arrays: available with state `0` and `alerts: []`. |
| `empty_optional.json` | Missing buckets, a null bucket and no root status: successful empty response. |
| `empty_object.json` | Empty disruptions object and no status: successful empty response. |
| `malformed_bucket.json` | An object-valued category: failure, never an empty result. |
| `malformed_record.json` | A good notice followed by a null notice: failure, never partial success. |
| `missing_disruptions.json` | Missing required data container: failure. |
| `offline.json` | Explicit offline API health with empty data: failure. |
| `cases.json` | Fixed clocks and machine-readable expected results for the above files plus an inline defensive null-direction case and malformed payloads. |

## Consuming the expectations

Each case supplies `file` (relative to this directory) or an inline `payload`,
an aware UTC `now`, and `outcome`. Success cases specify `count` and ordered
`ids`; failure cases specify a reason and intentionally have no successful count.
Anonymous slots in `ids` use `null` to mean **no usable integer ID**, rather than
the literal normalized value. `anonymous_sources_in_order` gives the exact raw
anonymous objects and their expected order. Preserve each malformed supplied ID
in the exposed record; do not turn the expectation's placeholder into an ID.

`selected_titles`, `categories`, `excluded_ids` and `checks` describe additional
assertions for the behavioural harness. Check structured field preservation,
including routes, stops, directions, original date strings and optional fields;
checking counts alone is insufficient. ID `0` in the duplicate fixture has null
coverage arrays and omitted text/date fields: normalize its arrays to `[]` and
absent standard scalar keys to `null`. Notice ID 301's selected record must keep
the updated description and its stop/direction metadata.
ID `-1` is a valid signed `int64` identifier under the API schema: its newer
cross-category record wins, it counts once, and it sorts before ID `0`.

Run fixed-clock cases independently of the execution host's timezone. The
ordinary reference instant `2026-10-05T00:00:00Z` is 11:00 AEDT in Melbourne.
Repeat expiry checks with Home Assistant configured in both Melbourne and UTC;
the results must match. The spring/autumn fixtures compare supplied offsets,
not ambiguous local clock text.

Further request mocks belong in #6: HTTP authentication/server errors, timeouts,
network failures, invalid JSON, and good → failure → empty → non-empty recovery.
Lifecycle mocks must cover zero departures, independent timetable/alert
failures, shared requests with two entries, credential separation, disabling,
unload/reload, and safe logging. These JSON examples alone cannot verify them.
