# Route alerts: use, examples and migration

This guide describes the development implementation in this fork. Installation
and release validation remain [issue #8](https://github.com/davidbmck/public_transport_victoria/issues/8).
No production installation or particular live PTV response is implied by the
isolated tests. Original integration credit remains with
[bremor/public_transport_victoria](https://github.com/bremor/public_transport_victoria);
PTV data retains the integration's Creative Commons Attribution 4.0 attribution.

## Find the sensor

Each configuration entry has five existing departure sensors and one additional
alert sensor, named `<route_name> line to <direction_name> from <stop_name> alerts`
in English. Find it under the entry in **Settings → Devices & services**, then
copy its actual entity ID. The examples use `sensor.example_route_alerts` as a
placeholder: replace every occurrence, including each card's `entity_id` or
`entities` list. Home Assistant assigns entity IDs; no particular spelling is
guaranteed. User overrides are preserved on reload.

Alert unique IDs use `public_transport_victoria_<entry_id>_route_alerts`. Existing
entries require no recreation, new fields or credential prompts. Alerts are
enabled and visible by default, including on upgrade, unless HA's setting to
disable newly added entities applies. Disable the sensor through HA to opt out.

## Count, metadata and dates

| State | Meaning |
| --- | --- |
| `0` | The latest complete successful feed contains no retained notices. |
| Positive integer | Count of deduplicated notices, including current and planned notices. |
| `unavailable`, `unknown` or missing entity | No usable current feed; do not interpret this as zero. |

| Attribute | Meaning |
| --- | --- |
| `alerts` | Ordered list of structured notice records; `[]` after a successful empty response. |
| `route_id`, `route_type` | Configured integer route and mode identifiers. |
| `last_successful_update` | UTC ISO timestamp of the last complete success; not advanced by failures or cache reuse. |
| `attribution` | Licensed from Public Transport Victoria under a Creative Commons Attribution 4.0 International Licence. |

Every notice has the following keys. Supplied additional fields are retained.

| Record keys | Contents and missing values |
| --- | --- |
| `disruption_id` | Original ID or `null`; unusable IDs remain as supplied, without invented replacements. |
| `title`, `description`, `url` | Original text/URL; missing values are `null`. |
| `disruption_status`, `disruption_type` | Original status/type, including unfamiliar values; missing values are `null`. |
| `published_on`, `last_updated`, `from_date`, `to_date` | Original date values, including malformed values; missing values are `null`. |
| `routes`, `stops` | Original arrays and nested metadata; missing/null arrays become `[]`. Direction data can be nested under `direction`, including `direction_id`, `route_direction_id`, `direction_name` and `service_time`. |
| `categories` | Sorted source bucket names; includes general notices and future categories. |

No mode or English-description filter is applied. Notices for parking,
accessibility and general service information can be present alongside service
disruptions. Duplicate usable IDs count once and records have deterministic
ordering; see the [full contract](route-alert-contract.md) for duplicate rules.

Both current and planned notices are retained, even with a future start. A notice
expires only when its usable `to_date` is strictly before the evaluation time.
At exactly that end time it remains. A usable date is an ISO date/time with `Z`
or an explicit UTC offset; date-only, naive, missing or malformed ends stay in
the feed. No timezone or end date is guessed. Offsets are compared as UTC
instants, independent of Melbourne DST or the HA display timezone. Status is
preserved, not inferred from dates.

## Refresh and failures

Alerts poll every ten minutes, independently of the departure timetable and its
manual-update throttle. Entries with the same route and exact credentials share
one feed even for different stops/directions. Successful empty results are cached
too. Removing the last enabled alert subscriber stops periodic alert requests;
re-enabling resumes them. A fresh shared snapshot can be reused on setup without
another request; cached expiry is re-evaluated without advancing its timestamp.
Expiry between successful evaluations is not driven by a per-notice timer.

API, network, timeout and malformed/offline payload failures make the alert
sensor unavailable. Timetable and alert failures remain independent, including
when there are no departures. Last-good data and its timestamp may be retained
internally, but are not proof of a healthy feed. HA can omit custom attributes
while unavailable. Before any success there is no successful timestamp. Recovery
atomically replaces the snapshot, including with a successful `0` and `[]`.

## Standard Markdown card

Paste this into a standard dashboard Markdown card's YAML editor. It needs no
custom frontend component. The availability check comes before attribute access;
a positive count without details is reported explicitly. External text is
HTML-escaped and placed inside a block, with no external URL inserted as a link.
The display includes both current and planned notices.

```yaml
type: markdown
title: Route notices
entity_id: sensor.example_route_alerts
content: |
  {% set e = 'sensor.example_route_alerts' %}
  {% set count = states(e) | int(none) %}
  {% set notices = state_attr(e, 'alerts') %}
  {% if count is none or count < 0 %}
  <p>Route alert feed unavailable.</p>
  {% elif count == 0 %}
  <p>No route notices. Feed is available.</p>
  {% elif notices is not sequence or notices is string or notices is mapping or not notices %}
  <p>Notice details are not available.</p>
  {% else %}
  <p>{{ count }} route notices, including planned notices.</p>
  <div>
  {% for notice in notices if notice is mapping %}
  <p><strong>{{ (notice.get('title') or 'Untitled notice') | string | e }}</strong><br>
  {{ (notice.get('description') or 'No description supplied') | string | e }}<br>
  Status: {{ (notice.get('disruption_status') or 'Unknown') | string | e }}</p>
  {% endfor %}
  </div>
  {% endif %}
```

The standard card's template and explicit update-entity option follow
[HA's Markdown-card documentation](https://www.home-assistant.io/dashboards/markdown/).
Display fallbacks such as “Untitled notice” do not modify the underlying record.

## Feed-status automation

Paste this as one automation in HA's YAML automation editor, replacing the two
entity-ID occurrences. It creates or replaces a single persistent notification
inside HA for **notices**, **no notices**, or **unavailable feed**. It requires no
phone, email, external notification service or credentials. It runs at HA start
and on state changes, including failure/recovery; attribute-only description
updates with an unchanged count do not trigger it. Adapt notification behavior
to your preferences after checking the example.

```yaml
id: ptv_route_alert_feed_status_example
alias: PTV route alert feed status
mode: queued
max: 5
triggers:
  - trigger: state
    entity_id: sensor.example_route_alerts
    to: null
  - trigger: homeassistant
    event: start
actions:
  - variables:
      alert_entity: sensor.example_route_alerts
  - choose:
      - conditions: "{{ is_number(states(alert_entity)) and states(alert_entity) | int(-1) > 0 }}"
        sequence:
          - action: persistent_notification.create
            data:
              notification_id: ptv_route_alerts_example
              title: Route notices
              message: "{{ states(alert_entity) }} route notices, including planned notices. Check the alert sensor for details."
      - conditions: "{{ is_state(alert_entity, '0') }}"
        sequence:
          - action: persistent_notification.create
            data:
              notification_id: ptv_route_alerts_example
              title: No route notices
              message: No route notices. Feed is available.
    default:
      - action: persistent_notification.create
        data:
          notification_id: ptv_route_alerts_example
          title: Route alert feed unavailable
          message: The feed is unavailable. This does not mean there are no notices.
```

See HA's [state/start triggers](https://www.home-assistant.io/docs/automation/trigger/)
and [choose actions](https://www.home-assistant.io/docs/scripts/#choose-a-group-of-actions).
The notification reports count/feed status, not only disruptions affecting travel
right now. These examples do not send or test notifications on your installation.

## Optional scrolling display

Use this only if you already have the compatible
[HTML Jinja2 Template card](https://github.com/PiotrMachowski/Home-Assistant-Lovelace-HTML-Jinja2-Template-card).
It is optional; the integration and standard example require no custom card.
This uses that card's `content`, `entities` and `ignore_line_breaks` options with
HA's template engine. It escapes all external title/description/status text
before HTML insertion, inserts no external URLs, and guards missing entities and
attributes. Hover or focus pauses movement; reduced-motion preferences disable
the animation. Browser appearance and card-version compatibility require a
preview in your own dashboard; they are not established by backend tests.

```yaml
type: custom:html-template-card
title: Route notices
entities:
  - sensor.example_route_alerts
ignore_line_breaks: true
content: |
  {% set e = 'sensor.example_route_alerts' %}
  {% set count = states(e) | int(none) %}
  {% set notices = state_attr(e, 'alerts') %}
  {% if count is none or count < 0 %}
  <p>Route alert feed unavailable.</p>
  {% elif count == 0 %}
  <p>No route notices. Feed is available.</p>
  {% elif notices is not sequence or notices is string or notices is mapping or not notices %}
  <p>Notice details are not available.</p>
  {% else %}
  <style>
    .ptv-scroll { overflow: hidden; white-space: nowrap; }
    .ptv-track { display: inline-block; padding-left: 100%; animation: ptv-notices 40s linear infinite; }
    .ptv-scroll:hover .ptv-track, .ptv-scroll:focus .ptv-track { animation-play-state: paused; }
    @keyframes ptv-notices { from { transform: translateX(0); } to { transform: translateX(-100%); } }
    @media (prefers-reduced-motion: reduce) {
      .ptv-track { animation: none; padding-left: 0; white-space: normal; }
      .ptv-scroll { white-space: normal; }
    }
  </style>
  <div class="ptv-scroll" tabindex="0" aria-label="Route notices, including planned notices">
  <span class="ptv-track">
  {% for notice in notices if notice is mapping %}
  {{ (notice.get('title') or 'Untitled notice') | string | e }} —
  {{ (notice.get('description') or 'No description supplied') | string | e }}
  [{{ (notice.get('disruption_status') or 'Unknown') | string | e }}]
  {% if not loop.last %} • {% endif %}
  {% endfor %}
  </span></div>
  {% endif %}
```

## Optional consumer filtering

The entry's stop/direction identifies its owner and display name. The feed itself
is route-wide. The count always represents the full feed; a filtered display
must label its own count separately. Never modify the integration's shared data.

For a stop display, compare your selected stop ID with `stops[].stop_id`. For a
direction display, inspect the supplied `routes[].direction` and
`stops[].direction` objects; distinguish `direction_id` and `route_direction_id`
instead of assuming they are interchangeable. Keep notices with missing/unknown
coverage as general notices, or clearly label any stricter policy that hides
them. A supplied nonmatching scope may be excluded by a consumer. Metadata can
describe multiple routes/stops, and missing metadata does not prove irrelevance.

Status filtering is also optional. Compare exact supplied `disruption_status`
values; do not infer status from description text or start dates. For example,
this HA template selects explicitly `Current` and `Planned` records while also
retaining missing status. Unfamiliar supplied statuses are excluded by this
example's explicit policy. Leaving `allowed` empty keeps every status.

```jinja
{% set e = 'sensor.example_route_alerts' %}
{% set allowed = ['Current', 'Planned'] %}
{% set notices = state_attr(e, 'alerts') %}
{% set ns = namespace(selected=[]) %}
{% if is_number(states(e)) and notices is sequence and notices is not string and notices is not mapping %}
  {% for notice in notices if notice is mapping %}
    {% if not allowed or notice.get('disruption_status') is none or notice.get('disruption_status') in allowed %}
      {% set ns.selected = ns.selected + [notice] %}
    {% endif %}
  {% endfor %}
  {{ ns.selected }}
{% else %}
  Feed unavailable or details missing
{% endif %}
```

## Switch between this fork and upstream

Keep a rollback copy of your installed integration source and a normal private HA
backup before changing an installation. Keep private backups, `.storage`, tokens
and household configuration outside this public repository. Use exactly one
provider of `custom_components/public_transport_victoria` at a time; both forks
use the same domain and cannot operate side by side.

For HACS installations, select the intended repository/version through HACS and
verify which code it installs. The fork is a custom repository, not a default
store replacement, and an old release/tag may lack the development alerts. For
manual installation, replace only the integration source directory with the
chosen version, retaining the previous source for rollback. Follow the chosen
version's installation/restart instructions and verify the sensors afterward.
Do not delete or recreate HA configuration entries or edit registries to switch
code: that can change persisted entry IDs and the new alert identity.

This fork keeps the upstream 0.7 baseline's domain, version-1 configuration fields
and departure identities. The regression suite verifies those existing-entry
identities in a disposable HA environment; it does not replace an installation
trial. Returning to a version without this alert feature can leave the new alert
sensor unavailable; consumers must handle that. The five departure sensors should
retain their existing identities for that compatible baseline. Check newer
upstream versions for their own migration requirements rather than assuming
indefinite compatibility. Revert source to your saved version if verification
fails, using the retained configuration/backup as needed. Release/upgrade trials
remain #8 and an upstream contribution remains #9.

## Migrate a local `disruptions` patch

If your local patch exposes `disruptions` on a departure sensor, its consumers
need a separate migration. This implementation adds `alerts` on a dedicated
alert sensor; it does not emulate or migrate a private custom attribute. Preserve
your patched source and consumer configuration for rollback and inspect your
patch's actual format/identity changes before replacing it.

Update each dashboard/template/automation to use the new alert entity and
`state_attr(new_entity, 'alerts')`. Iterate structured records (`title`,
`description`, dates, status and coverage), rather than assuming a list of
description strings. Check availability before treating a missing list as empty.
The new feed contains all categories and planned/general notices, which can
change counts compared with a local departure-linked or V/Line-only patch.
Do not rename the alert sensor to an existing departure entity ID to conceal this
consumer migration. Unmodified upstream departure identities are preserved;
compatibility with arbitrary private-patch identities is not claimed.

## Develop and contribute

Run `sh scripts/test-container.sh` and `python3 scripts/test-container-cleanup.py`
from the checkout root. The examples in this document are read directly by
`tests/test_alert_examples.py`: HA template rendering and automation actions are
tested with synthetic states, including missing attributes and escaped markup.
Those tests do not install a frontend card or verify browser animation.

Read [CONTRIBUTING.md](../CONTRIBUTING.md), the
[development guide](development.md) and [maintainer instructions](../AGENTS.md).
Keep API attribution and original project acknowledgements. Use generic examples
and mocked APIs; no credentials, household dashboards, registries or production
HA deployment are required to contribute.
