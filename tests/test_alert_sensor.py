"""Exercise public alert sensors and registry operations through Home Assistant."""

from datetime import timedelta

import pytest
from homeassistant.const import ATTR_ATTRIBUTION, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.public_transport_victoria.const import ATTRIBUTION, DOMAIN

from .test_alert_coordinator import ALERT_PATH, NOTICE, get_alert_sensor
from .test_sensor import DEPARTURE_PATH, ENTITY_PREFIX, setup_entry


def registered_alert(hass, entry):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{entry.entry_id}_route_alerts"
    )
    return registry.async_get(entity_id)


def seed_departures(hass, entry):
    """Represent existing version-1 registry records using supported HA APIs."""
    registry = er.async_get(hass)
    return [
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"Example Metro line to Example City from Example Station {index}",
            suggested_object_id=f"existing_departure_{index}",
            config_entry=entry,
        )
        for index in range(5)
    ]


@pytest.mark.parametrize("notices", [[], [NOTICE]])
async def test_upgrade_adds_enabled_alert_sensor_and_preserves_departure_registry(
    hass, config_entry_factory, ptv_responses, freezer, notices
):
    freezer.move_to("2026-10-10T00:00:00Z")
    entry = config_entry_factory()
    original_data = dict(entry.data)
    entry.add_to_hass(hass)
    departures = seed_departures(hass, entry)
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {"general": notices}})
    await setup_entry(hass, entry)
    registry = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 6
    for original in departures:
        current = registry.async_get(original.entity_id)
        assert (current.id, current.unique_id) == (original.id, original.unique_id)
        assert current.config_entry_id == entry.entry_id
        assert hass.states.get(current.entity_id).state == "No data"
    registered = registered_alert(hass, entry)
    assert registered.unique_id == f"{DOMAIN}_{entry.entry_id}_route_alerts"
    assert registered.config_entry_id == entry.entry_id
    assert registered.disabled_by is None
    assert registered.hidden_by is None
    assert registered.entity_category is None
    sensor = get_alert_sensor(hass, entry)
    assert type(sensor.native_value) is int
    assert sensor.native_value == len(notices)
    assert sensor.device_class is None
    assert sensor.state_class is None
    assert sensor.native_unit_of_measurement is None
    state = hass.states.get(registered.entity_id)
    assert state.state == str(len(notices))
    assert (
        state.name == "Example Metro line to Example City from Example Station alerts"
    )
    assert state.attributes["icon"] == "mdi:alert-circle-outline"
    assert state.attributes[ATTR_ATTRIBUTION] == ATTRIBUTION
    assert state.attributes["route_id"] == 9001
    assert state.attributes["route_type"] == 0
    assert type(state.attributes["route_id"]) is int
    assert type(state.attributes["route_type"]) is int
    assert state.attributes["last_successful_update"] == "2026-10-10T00:00:00+00:00"
    assert len(state.attributes["alerts"]) == len(notices)
    assert entry.version == 1
    assert dict(entry.data) == original_data


async def test_route_wide_structured_metadata_is_not_filtered_by_stop_or_direction(
    hass, config_entry_factory, ptv_responses, load_json_fixture, freezer
):
    freezer.move_to("2026-10-05T00:00:00Z")
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    payload = load_json_fixture("route_alerts/all_categories.json")
    # A long description remains in the notice rather than the sensor state.
    payload["disruptions"]["general"][0]["description"] = "Synthetic notice. " * 100
    ptv_responses(ALERT_PATH, payload)
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    state = hass.states.get(registered_alert(hass, entry).entity_id)
    assert state.state == "15"
    notices = state.attributes["alerts"]
    assert {n["categories"][0] for n in notices} == set(payload["disruptions"])
    for bucket in payload["disruptions"].values():
        for original in bucket:
            exposed = next(
                n for n in notices if n["disruption_id"] == original["disruption_id"]
            )
            for key, value in original.items():
                assert exposed[key] == value


async def test_two_entries_have_distinct_alert_identities_and_share_requests(
    hass, config_entry_factory, ptv_responses, aioclient_mock
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    first = config_entry_factory()
    second = config_entry_factory(direction="2", direction_name="Other")
    await setup_entry(hass, first)
    await setup_entry(hass, second)
    first_alert, second_alert = (
        registered_alert(hass, first),
        registered_alert(hass, second),
    )
    assert first_alert.unique_id != second_alert.unique_id
    assert first_alert.entity_id != second_alert.entity_id
    assert first_alert.config_entry_id == first.entry_id
    assert second_alert.config_entry_id == second.entry_id
    assert (
        get_alert_sensor(hass, first).coordinator
        is get_alert_sensor(hass, second).coordinator
    )
    assert aioclient_mock.call_count == 3  # Two timetables, one successful empty feed.


async def test_disable_new_entities_keeps_departures_and_skips_alert_requests(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    entry = config_entry_factory()
    entry.add_to_hass(hass)
    seed_departures(hass, entry)
    hass.config_entries.async_update_entry(entry, pref_disable_new_entities=True)
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    alert = registered_alert(hass, entry)
    assert alert.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(alert.entity_id) is None
    assert (
        list(hass.data[DOMAIN][entry.entry_id].alert_coordinator.async_contexts()) == []
    )
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2  # Only timetable requests.


async def test_registry_disable_and_enable_control_shared_polling(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    first = config_entry_factory()
    second = config_entry_factory(direction="2", direction_name="Other")
    await setup_entry(hass, first)
    await setup_entry(hass, second)
    first_alert, second_alert = (
        registered_alert(hass, first),
        registered_alert(hass, second),
    )
    shared = get_alert_sensor(hass, first).coordinator
    registry = er.async_get(hass)

    def alert_requests():
        return sum(url.path == ALERT_PATH for _, url, _, _ in aioclient_mock.mock_calls)

    registry.async_update_entity(
        first_alert.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert list(shared.async_contexts()) == [second.entry_id]
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert alert_requests() == 2
    registry.async_update_entity(
        second_alert.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert list(shared.async_contexts()) == []
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert alert_requests() == 2

    registry.async_update_entity(first_alert.entity_id, disabled_by=None)
    await hass.async_block_till_done()
    # Exercise HA's automatic reload after the normal registry-enable operation.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert get_alert_sensor(hass, first).coordinator is shared
    assert hass.states.get(first_alert.entity_id).state == "1"
    assert list(shared.async_contexts()) == [first.entry_id]
    assert alert_requests() == 3
    assert registered_alert(hass, first).id == first_alert.id


async def test_alert_registry_overrides_survive_reload_and_removal_stops_requests(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    original = registered_alert(hass, entry)
    registry = er.async_get(hass)
    changed = registry.async_update_entity(
        original.entity_id,
        new_entity_id="sensor.my_route_notices",
        name="My route notices",
    )
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, title="Renamed entry")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    current = registered_alert(hass, entry)
    assert (current.id, current.unique_id, current.entity_id) == (
        original.id,
        original.unique_id,
        changed.entity_id,
    )
    assert hass.states.get(current.entity_id).name == "My route notices"
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(current.entity_id) is None
    assert registry.async_get(current.entity_id) is None
    assert not er.async_entries_for_config_entry(registry, entry.entry_id)
    requests = aioclient_mock.call_count
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == requests


async def test_manual_alert_refresh_does_not_fetch_departures(
    hass, config_entry_factory, ptv_responses, aioclient_mock
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    assert await async_setup_component(hass, "homeassistant", {})
    aioclient_mock.clear_requests()
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": registered_alert(hass, entry).entity_id},
        blocking=True,
    )
    assert hass.states.get(registered_alert(hass, entry).entity_id).state == "1"
    assert hass.states.get(f"{ENTITY_PREFIX}_0").state != STATE_UNAVAILABLE
    assert aioclient_mock.call_count == 1
