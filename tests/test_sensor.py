"""Protect existing departure behaviour through Home Assistant's public APIs."""

from datetime import timedelta

import pytest
from aiohttp import ClientConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ATTRIBUTION, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.public_transport_victoria.const import ATTRIBUTION, DOMAIN

from .conftest import SYNTHETIC_API_KEY

DEPARTURE_PATH = "/v3/departures/route_type/0/stop/8001/route/9001"
ENTITY_PREFIX = "sensor.example_metro_line_to_example_city_from_example_station"


@pytest.fixture
def departure_responses(ptv_responses, load_json_fixture):
    """Serve two departures and their run metadata without patching the connector."""

    def register():
        ptv_responses(DEPARTURE_PATH, load_json_fixture("departures.json"))
        ptv_responses("/v3/runs/7001", {"runs": [{"express_stop_count": 2}]})
        ptv_responses("/v3/runs/7002", {"runs": []})

    register()
    return register


async def setup_entry(hass, entry):
    """Exercise normal config-entry setup, including the actual sensor platform."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("time_zone", "estimated", "scheduled"),
    [("Australia/Melbourne", "11:05 AM", "11:10 AM"), ("UTC", "12:05 AM", "12:10 AM")],
)
async def test_existing_entry_departures(
    hass,
    config_entry_factory,
    departure_responses,
    load_json_fixture,
    time_zone,
    estimated,
    scheduled,
):
    """Keep version-1 entries, entity identity and departure attributes unchanged."""
    await hass.config.async_set_time_zone(time_zone)
    entry = config_entry_factory()
    original_data = dict(entry.data)
    await setup_entry(hass, entry)

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entities) == 5
    for index in range(5):
        entity_id = f"{ENTITY_PREFIX}_{index}"
        expected_name = (
            f"Example Metro line to Example City from Example Station {index}"
        )
        registered = registry.async_get(entity_id)
        assert registered is not None
        assert registered.unique_id == expected_name
        assert registered.config_entry_id == entry.entry_id
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.name == expected_name
        if index < 2:
            assert state.state == (estimated if index == 0 else scheduled)
            assert state.attributes["departure"] == state.state
            assert state.attributes["is_express"] is (index == 0)
            assert state.attributes[ATTR_ATTRIBUTION] == ATTRIBUTION
            assert state.attributes["run_id"] == 7001 + index
            assert state.attributes["platform_number"] == str(index + 1)
            original = load_json_fixture("departures.json")["departures"][index]
            for key, value in original.items():
                assert key in state.attributes
                assert state.attributes[key] == value
        else:
            assert state.state == "No data"
            assert ATTR_ATTRIBUTION not in state.attributes

    assert entry.version == 1
    assert dict(entry.data) == original_data


async def test_two_directions_on_one_route(
    hass, config_entry_factory, ptv_responses, load_json_fixture, aioclient_mock
):
    """Two existing entries keep separate departure identities and associations."""
    for direction in (1, 2):
        payload = load_json_fixture("departures.json")
        for departure in payload["departures"]:
            departure["direction_id"] = direction
        ptv_responses(DEPARTURE_PATH, payload, query={"direction_id": direction})
    ptv_responses("/v3/runs/7001", {"runs": [{"express_stop_count": 2}]})
    ptv_responses("/v3/runs/7002", {"runs": []})
    outbound = config_entry_factory()
    inbound = config_entry_factory(direction="2", direction_name="Example Suburb")
    await setup_entry(hass, outbound)
    await setup_entry(hass, inbound)

    registry = er.async_get(hass)
    first = er.async_entries_for_config_entry(registry, outbound.entry_id)
    second = er.async_entries_for_config_entry(registry, inbound.entry_id)
    assert len(first) == len(second) == 5
    assert {e.entity_id for e in first}.isdisjoint(e.entity_id for e in second)
    assert {e.unique_id for e in first}.isdisjoint(e.unique_id for e in second)
    assert all("Example City" in e.unique_id for e in first)
    assert all("Example Suburb" in e.unique_id for e in second)
    assert hass.states.get(f"{ENTITY_PREFIX}_0").attributes["direction_id"] == 1
    inbound_id = "sensor.example_metro_line_to_example_suburb_from_example_station_0"
    assert hass.states.get(inbound_id).attributes["direction_id"] == 2
    directions = {
        url.query["direction_id"]
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path == DEPARTURE_PATH
    }
    assert directions == {"1", "2"}


async def test_empty_departures(
    hass, config_entry_factory, ptv_responses, aioclient_mock
):
    """A successful empty timetable creates all five existing 'No data' entities."""
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    entry = config_entry_factory()
    await setup_entry(hass, entry)

    for index in range(5):
        assert hass.states.get(f"{ENTITY_PREFIX}_{index}").state == "No data"
    assert aioclient_mock.call_count == 1


async def test_unload_reload_preserves_registry(
    hass, config_entry_factory, departure_responses, aioclient_mock, freezer
):
    """Release an entry's data and recover its existing registry identities."""
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    registry = er.async_get(hass)
    identities = {
        e.entity_id: (e.id, e.unique_id)
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    initial_requests = aioclient_mock.call_count

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]
    # HA retains unavailable registry placeholders while an entry is unloaded.
    for entity_id in identities:
        state = hass.states.get(entity_id)
        assert state.state == STATE_UNAVAILABLE
        assert state.attributes["restored"] is True
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == initial_requests

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == initial_requests + 3
    assert identities == {
        e.entity_id: (e.id, e.unique_id)
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert hass.states.get(f"{ENTITY_PREFIX}_0").state != STATE_UNAVAILABLE


async def test_remove_entry(hass, config_entry_factory, departure_responses):
    """Entry removal releases integration data and all owning registry records."""
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert entry.entry_id not in hass.data[DOMAIN]
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_manual_refresh_respects_departure_throttle(
    hass, config_entry_factory, departure_responses, aioclient_mock, freezer
):
    """Manual refresh stays throttled for two minutes, then fetches real data."""
    await setup_entry(hass, config_entry_factory())
    assert await async_setup_component(hass, "homeassistant", {})
    initial_requests = aioclient_mock.call_count

    async def refresh():
        await hass.services.async_call(
            "homeassistant",
            "update_entity",
            {"entity_id": f"{ENTITY_PREFIX}_0"},
            blocking=True,
        )
        await hass.async_block_till_done()

    await refresh()
    assert aioclient_mock.call_count == initial_requests
    freezer.tick(timedelta(minutes=1, seconds=50))
    await refresh()
    assert aioclient_mock.call_count == initial_requests
    freezer.tick(timedelta(seconds=20))
    await refresh()
    assert aioclient_mock.call_count == initial_requests + 3


async def test_ten_minute_departure_polling(
    hass, config_entry_factory, departure_responses, aioclient_mock, freezer
):
    """Normal polling waits ten minutes rather than fetching once per sensor."""
    await setup_entry(hass, config_entry_factory())
    initial_requests = aioclient_mock.call_count
    freezer.tick(timedelta(minutes=9))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == initial_requests

    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == initial_requests + 3


@pytest.mark.parametrize("error_type", [ClientConnectionError, ValueError])
async def test_departure_failure_and_recovery(
    hass,
    config_entry_factory,
    departure_responses,
    aioclient_mock,
    ptv_responses,
    freezer,
    error_type,
    caplog,
):
    """Actual coordinator failure marks departures unavailable until recovery."""
    await setup_entry(hass, config_entry_factory())
    assert await async_setup_component(hass, "homeassistant", {})
    entity_id = f"{ENTITY_PREFIX}_0"
    previous_state = hass.states.get(entity_id).state

    async def refresh():
        freezer.tick(timedelta(minutes=3))
        await hass.services.async_call(
            "homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True
        )
        await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    sensitive = f"{SYNTHETIC_API_KEY} ?devid=12345&signature=synthetic-signature"
    ptv_responses(DEPARTURE_PATH, exc=error_type(sensitive))
    await refresh()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert f"PTV departure refresh failed ({error_type.__name__})" in caplog.text

    aioclient_mock.clear_requests()
    departure_responses()
    await refresh()
    assert hass.states.get(entity_id).state == previous_state


@pytest.mark.parametrize(
    ("error_type", "expected_state"),
    [
        (ClientConnectionError, ConfigEntryState.SETUP_ERROR),
        (ValueError, ConfigEntryState.SETUP_ERROR),
        (TimeoutError, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_initial_setup_failure_is_safe(
    hass, config_entry_factory, ptv_responses, error_type, expected_state, caplog
):
    """Initial requests fail before coordinator creation and must also be safe."""
    sensitive = f"{SYNTHETIC_API_KEY} ?devid=12345&signature=synthetic-signature"
    ptv_responses(DEPARTURE_PATH, exc=error_type(sensitive))
    entry = config_entry_factory()
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is expected_state
    assert entry.entry_id not in hass.data[DOMAIN]
    assert f"PTV entry setup failed ({error_type.__name__})" in caplog.text
