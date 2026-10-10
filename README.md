# Public Transport Victoria

The `public transport victoria` sensor platform uses the [Public Transport Victoria (PTV)](https://www.ptv.vic.gov.au/) as a source for public transport departure times for Victoria, Australia.

## About this fork

This is a development fork of [bremor/public_transport_victoria](https://github.com/bremor/public_transport_victoria). The aim is to add general, structured route alerts and disruption notices while preserving existing configurations and departure entities, then contribute the feature back upstream.

The implementation includes current and planned notices across transport modes, alert polling independent of departures, and regression tests. **Version 0.8.0 is the first route-alert version**, based on upstream 0.7. See its [release notes, validation checklist and rollback instructions](docs/release-0.8.0.md), [published releases](https://github.com/davidbmck/public_transport_victoria/releases) and the [development backlog](https://github.com/davidbmck/public_transport_victoria/issues/1).

Each configuration entry now adds one alert sensor alongside its five existing departure sensors. It is enabled and visible by default, including for existing entries, unless Home Assistant is configured to disable newly added entities. Its state counts route-wide notices, and its `alerts` attribute contains their structured details. Alerts refresh every ten minutes even with no departures; matching routes and credentials share requests. Failed alert requests make the sensor unavailable until recovery. Disable the alert sensor in Home Assistant to opt out; disabling the last sensor sharing a feed stops alert polling.

The [route alert contract](docs/route-alert-contract.md) defines sensor identity, attributes, dates and polling behavior, with [synthetic response fixtures](tests/fixtures/route_alerts/README.md). All 180 regression tests pass using mocked APIs. The candidate also passed disposable installation and upgrade through HACS 2.0.5's backend on Home Assistant 2026.9.4, with real PTV requests across four transport modes, preserved departure identities, and alert failure/recovery checks. The [release checklist](docs/release-0.8.0.md) records the observed results and remaining publication gates; production installation and dashboard migration require a separate deployment task.

The [alert user guide](docs/alerts.md) includes state/attribute reference, a standard Markdown card, a feed-status automation, an optional scrolling display, consumer filtering and guidance for switching code or migrating a local `disruptions` patch. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and contribution checks.

For isolated regression tests, run `sh scripts/test-container.sh` from this checkout. The [development guide](docs/development.md) explains the pinned Home Assistant container, checks, cleanup, coverage and validation limits.

This fork is unlikely to become a long-term maintained alternative to the original integration. The intention is to use it for development and testing, then return to upstream if the changes are accepted. That may change, but there is no commitment to ongoing maintenance or regular releases. Credit for the original integration remains with its upstream authors and contributors.

## Installation (There are two methods, with HACS or manual)

[![hacs][hacsbadge]][hacs]

This fork is not in the HACS default store. To install it through HACS, add `https://github.com/davidbmck/public_transport_victoria` as a [custom repository](https://www.hacs.xyz/docs/faq/custom_repositories/) with type **Integration**. For manual installation, copy `custom_components/public_transport_victoria` into your Home Assistant configuration directory.

Once published, select release `0.8.0` in HACS rather than assuming an older tag contains alerts. The release archive contains the integration's Python source and metadata, with no JavaScript build. Keep a private backup and the current integration source for rollback. Preserve existing configuration entries and departure entity IDs; consumers of a local custom `disruptions` attribute need the [separate alert migration](docs/alerts.md#migrate-a-local-disruptions-patch).

This fork's original additions and changes are licensed under [MIT](LICENSE). The inherited upstream integration has **no declared software licence**; MIT does not relicense that code. See [licence scope and provenance](LICENSE_SCOPE.md). PTV's existing data attribution is separate and preserved.

## Prerequisites

### Developer ID and API Key
Please follow the instructions on http://ptv.vic.gov.au/ptv-timetable-api/ for obtaining a Developer ID and API Key.

## Configuration
After you have installed the custom component (see above):
1. Goto the `Configuration` -> `Integrations` page.  
2. On the bottom right of the page, click on the `+ Add Integration` sign to add an integration.
3. Search for `Public Transport Victoria`. (If you don't see it, try refreshing your browser page to reload the cache.)
4. Click `Submit` to add the integration.

## Notes
This integration will refresh data every 10 minutes. If you wish to update the departure information more frequently during interesting periods, you may use an automation like the one below. It will update the sensors every minute between 7:30AM-8:30AM and 4:45PM-5:45PM.
```yaml
automation:

  - alias: 'update_trains'
    initial_state: true
    trigger:
      trigger:
      - platform: time_pattern
        minutes: "/1"
    condition:
      condition: or
      conditions:
        - condition: time
          after: '07:30:00'
          before: '08:30:00'
        - condition: time
          after: '16:45:00'
          before: '17:45:00'
    action:
      - service: 'homeassistant.update_entity'
        data:
          entity_id:
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_0'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_1'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_2'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_3'
            - 'sensor.werribee_line_to_city_flinders_street_from_aircraft_station_4'
```

[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
