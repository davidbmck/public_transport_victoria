"""Selection rules and real HA timestamp, refresh and callback behaviour."""

from datetime import timedelta

import pytest
from aiohttp import ClientConnectionError
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.public_transport_victoria.const import DOMAIN
from custom_components.public_transport_victoria.departure_summary import (
    parse_departure_time,
    select_departures,
    service_identity,
)

from .test_alert_coordinator import ALERT_PATH, get_alert_sensor
from .test_alert_sensor import seed_departures
from .test_sensor import DEPARTURE_PATH, setup_entry

NOW = dt_util.parse_datetime("2026-10-13T00:00:00Z")


def departure(minute, reference="service-a", **changes):
    record = {
        "run_id": -1,
        "run_ref": reference,
        "scheduled_departure_utc": (NOW + timedelta(minutes=minute)).isoformat(),
        "estimated_departure_utc": None,
    }
    record.update(changes)
    return record


@pytest.mark.parametrize(
    "invalid", [None, "", "bad", "2026-10-13T00:01:00", 123, {}, True]
)
def test_invalid_estimate_falls_back_to_scheduled(invalid):
    selected, following = select_departures(
        [departure(1, estimated_departure_utc=invalid)], NOW
    )
    assert selected.timestamp == NOW + timedelta(minutes=1)
    assert selected.attributes["time_source"] == "scheduled"
    assert selected.attributes["estimated_departure_utc"] is None
    assert following is None


@pytest.mark.parametrize("value", [None, "-1", "0", "", " ", [], {}, True])
def test_unknown_references_do_not_establish_identity(value):
    assert service_identity({"run_ref": value, "run_id": -1}) is None


@pytest.mark.parametrize("value", [None, -1, 0, "-1", "0", "", "bad", [], True, 1.2])
def test_unknown_run_ids_do_not_establish_identity(value):
    assert service_identity({"run_id": value}) is None


def test_identity_preference_and_numeric_id_normalization():
    assert service_identity({"run_ref": " ref-a ", "run_id": 5}) == ("run_ref", "ref-a")
    assert service_identity({"run_id": "0005"}) == ("run_id", "5")
    assert service_identity({"run_ref": "-1", "run_id": 5}) == ("run_id", "5")


def test_estimates_reorder_delayed_services_and_keep_original_identifiers():
    records = [
        departure(1, "a", estimated_departure_utc="2026-10-13T00:08:00Z"),
        departure(4, "b"),
        departure(3, "c", estimated_departure_utc="2026-10-13T11:02:00+11:00"),
    ]
    first, second = select_departures(records, NOW)
    assert (first.run_ref, second.run_ref) == ("c", "b")
    assert first.timestamp == NOW + timedelta(minutes=2)
    assert first.attributes == {
        "service_reference": "c",
        "scheduled_departure_utc": "2026-10-13T00:03:00+00:00",
        "estimated_departure_utc": "2026-10-13T00:02:00+00:00",
        "time_source": "estimated",
        "run_ref": "c",
        "run_id": -1,
    }
    assert "departure" not in records[0]  # Selection does not mutate records.


def test_duplicates_and_simultaneous_services_have_deterministic_selection():
    records = [departure(1, "b"), departure(1, "a"), departure(1, "a")]
    for ordered in (records, list(reversed(records))):
        first, second = select_departures(ordered, NOW)
        assert (first.run_ref, second.run_ref) == ("a", "b")
        assert first.timestamp == second.timestamp
    first, second = select_departures(
        [departure(1, None, run_id=7), departure(2, None, run_id="007")], NOW
    )
    assert first.run_id == 7
    assert second is None


def test_more_than_five_records_and_unknown_identities_remain_candidates():
    records = [departure(20 + n, str(n)) for n in range(6)]
    records += [departure(1, None), departure(1, None)]
    first, second = select_departures(records, NOW)
    assert first.timestamp == second.timestamp == NOW + timedelta(minutes=1)
    assert first.identity is second.identity is None
    assert first.attributes["service_reference"] is None


@pytest.mark.parametrize(
    "records",
    [
        [],
        [departure(-1), departure(0)],
        [None, "bad", {}, {"estimated_departure_utc": "bad"}],
        [departure(1, estimated_departure_utc="2026-10-12T23:59:00Z")],
    ],
)
def test_no_qualifying_departures(records):
    assert select_departures(records, NOW) == (None, None)


def test_single_service_and_missing_scheduled_time():
    first, second = select_departures([departure(1), departure(2)], NOW)
    assert first.timestamp == NOW + timedelta(minutes=1)
    assert second is None
    record = {"estimated_departure_utc": "2026-10-13T00:01:00Z"}
    first, _ = select_departures([record], NOW)
    assert first.scheduled is None
    assert first.attributes["time_source"] == "estimated"


@pytest.mark.parametrize(
    "timestamp",
    ["2026-04-05T02:30:00+11:00", "2026-04-05T02:30:00+10:00"],
)
def test_timezone_aware_dst_timestamps(timestamp):
    parsed = parse_departure_time(timestamp)
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.isoformat() in (
        "2026-04-04T15:30:00+00:00",
        "2026-04-04T16:30:00+00:00",
    )


def summary_entity(hass, entry, key="next_departure"):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{entry.entry_id}_{key}"
    )
    return hass.data["sensor"].get_entity(entity_id)


def summary_state(hass, entry, key="next_departure"):
    return hass.states.get(summary_entity(hass, entry, key).entity_id)


@pytest.fixture
def summary_responses(ptv_responses, freezer):
    freezer.move_to(NOW)

    def register(records=None):
        if records is None:
            records = [departure(1, "a"), departure(4, "b"), departure(7, "c")]
        ptv_responses(DEPARTURE_PATH, {"departures": records})
        ptv_responses(ALERT_PATH, {"disruptions": {}})

    register()
    return register


@pytest.mark.parametrize("mode", ["0", "1", "2", "3", "4"])
async def test_modes_use_entry_timetable_without_extra_requests(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer, mode
):
    freezer.move_to(NOW)
    path = f"/v3/departures/route_type/{mode}/stop/8001/route/9001"
    ptv_responses(path, {"departures": [departure(1), departure(4, "b")]})
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    entry = config_entry_factory(route_type=mode)
    await setup_entry(hass, entry)
    assert summary_state(hass, entry).state == "2026-10-13T00:01:00+00:00"
    assert summary_state(hass, entry, "following_departure").state == (
        "2026-10-13T00:04:00+00:00"
    )
    assert summary_state(hass, entry).attributes["device_class"] == "timestamp"
    assert summary_entity(hass, entry).native_value.tzinfo is not None
    assert aioclient_mock.call_count == 2


async def advance(hass, freezer, minutes):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_exact_time_boundary_exhaustion_and_unload_reload(
    hass, config_entry_factory, summary_responses, aioclient_mock, freezer
):
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    coordinator = summary_entity(hass, entry).coordinator
    assert coordinator._unsubscribe_time is not None
    initial = aioclient_mock.call_count
    await advance(hass, freezer, 1)
    assert summary_state(hass, entry).attributes["service_reference"] == "b"
    assert (
        summary_state(hass, entry, "following_departure").attributes[
            "service_reference"
        ]
        == "c"
    )
    await advance(hass, freezer, 3)
    assert summary_state(hass, entry).attributes["service_reference"] == "c"
    assert summary_state(hass, entry, "following_departure").state == STATE_UNKNOWN
    await advance(hass, freezer, 3)
    assert summary_state(hass, entry).state == STATE_UNKNOWN
    assert coordinator._unsubscribe_time is None
    assert aioclient_mock.call_count == initial

    # Restore a future cached selection, then unload while its timer is pending.
    coordinator.timetable.async_set_updated_data([departure(9, "d")])
    assert coordinator._unsubscribe_time is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._unsubscribe_time is None
    assert coordinator._unsubscribe_timetable is None
    await advance(hass, freezer, 11)
    assert aioclient_mock.call_count == initial
    aioclient_mock.clear_requests()
    summary_responses([departure(20, "e"), departure(21, "f")])
    await setup_entry(hass, entry)
    replacement = summary_entity(hass, entry).coordinator
    assert replacement is not coordinator
    assert replacement._unsubscribe_time is not None
    initial = aioclient_mock.call_count
    await advance(hass, freezer, 2)
    assert summary_state(hass, entry).attributes["service_reference"] == "f"
    assert aioclient_mock.call_count == initial


async def test_refresh_replaces_boundary_and_manual_update_is_throttled(
    hass, config_entry_factory, summary_responses, aioclient_mock, freezer
):
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    assert await async_setup_component(hass, "homeassistant", {})
    coordinator = summary_entity(hass, entry).coordinator
    initial = aioclient_mock.call_count
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": summary_entity(hass, entry).entity_id},
        blocking=True,
    )
    assert aioclient_mock.call_count == initial
    old_timer = coordinator._unsubscribe_time
    coordinator.timetable.async_set_updated_data([departure(4, "delayed")])
    assert coordinator._unsubscribe_time is not old_timer
    await advance(hass, freezer, 1)
    assert summary_state(hass, entry).attributes["service_reference"] == "delayed"
    await advance(hass, freezer, 3)
    assert summary_state(hass, entry).state == STATE_UNKNOWN
    assert aioclient_mock.call_count == initial


@pytest.mark.parametrize("failure", ["http", "network", "payload"])
async def test_failure_throttled_retry_empty_and_recovery_are_independent(
    hass,
    config_entry_factory,
    summary_responses,
    ptv_responses,
    aioclient_mock,
    freezer,
    failure,
):
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    coordinator = summary_entity(hass, entry).coordinator
    timetable = coordinator.timetable
    await advance(hass, freezer, 3)
    aioclient_mock.clear_requests()
    if failure == "http":
        ptv_responses(DEPARTURE_PATH, {}, status=503)
    elif failure == "network":
        ptv_responses(DEPARTURE_PATH, exc=ClientConnectionError("synthetic"))
    else:
        ptv_responses(DEPARTURE_PATH, {"departures": {}})
    await timetable.async_refresh()
    await hass.async_block_till_done()
    for key in ("next_departure", "following_departure"):
        assert summary_state(hass, entry, key).state == STATE_UNAVAILABLE
    assert coordinator._unsubscribe_time is None
    assert get_alert_sensor(hass, entry).available
    # No successful fetch: retry within the throttle must remain unavailable.
    await timetable.async_refresh()
    assert not timetable.last_update_success
    assert aioclient_mock.call_count == 1

    await advance(hass, freezer, 3)
    aioclient_mock.clear_requests()
    summary_responses([])
    await timetable.async_refresh()
    await hass.async_block_till_done()
    assert summary_state(hass, entry).state == STATE_UNKNOWN
    assert coordinator.last_update_success
    await advance(hass, freezer, 3)
    aioclient_mock.clear_requests()
    summary_responses([departure(12, "recovered")])
    await timetable.async_refresh()
    await hass.async_block_till_done()
    assert summary_state(hass, entry).attributes["service_reference"] == "recovered"
    assert coordinator._unsubscribe_time is not None
    # Alert failure does not affect the recovered timetable.
    aioclient_mock.clear_requests()
    ptv_responses(ALERT_PATH, {}, status=503)
    await get_alert_sensor(hass, entry).coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not get_alert_sensor(hass, entry).available
    assert summary_entity(hass, entry).available


async def test_initial_failure_recovers_without_duplicate_callbacks(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    freezer.move_to(NOW)
    ptv_responses(DEPARTURE_PATH, {}, status=503)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    coordinator = summary_entity(hass, entry).coordinator
    assert summary_state(hass, entry).state == STATE_UNAVAILABLE
    assert coordinator._unsubscribe_time is None
    await advance(hass, freezer, 3)
    aioclient_mock.clear_requests()
    ptv_responses(DEPARTURE_PATH, {"departures": [departure(4), departure(5, "b")]})
    await coordinator.timetable.async_refresh()
    await hass.async_block_till_done()
    assert summary_entity(hass, entry).available
    initial = aioclient_mock.call_count
    await advance(hass, freezer, 1)
    assert summary_state(hass, entry).attributes["service_reference"] == "b"
    assert aioclient_mock.call_count == initial


async def test_malformed_records_reach_summary_without_crashing(
    hass, config_entry_factory, summary_responses, aioclient_mock
):
    aioclient_mock.clear_requests()
    summary_responses(
        [
            None,
            {},
            {"run_id": -1, "estimated_departure_utc": "bad"},
            {"run_id": "malformed", "scheduled_departure_utc": "bad"},
            departure(1, estimated_departure_utc="bad"),
            {"estimated_departure_utc": "2026-10-13T00:02:00Z"},
        ]
    )
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    assert summary_state(hass, entry).state == "2026-10-13T00:01:00+00:00"
    assert summary_state(hass, entry).attributes["time_source"] == "scheduled"
    assert summary_state(hass, entry, "following_departure").state == (
        "2026-10-13T00:02:00+00:00"
    )


async def test_existing_entry_upgrade_customizations_and_two_entry_identities(
    hass, config_entry_factory, summary_responses
):
    registry = er.async_get(hass)
    first = config_entry_factory()
    first.add_to_hass(hass)
    originals = seed_departures(hass, first)
    overridden = registry.async_update_entity(
        originals[0].entity_id, name="My departure", new_entity_id="sensor.my_departure"
    )
    before = dict(first.data)
    await setup_entry(hass, first)
    second = config_entry_factory(direction="2", direction_name="Other")
    await setup_entry(hass, second)
    for key in ("next_departure", "following_departure"):
        a, b = summary_entity(hass, first, key), summary_entity(hass, second, key)
        assert a.unique_id != b.unique_id
        assert a.entity_id != b.entity_id
        assert registry.async_get(a.entity_id).config_entry_id == first.entry_id
        assert registry.async_get(b.entity_id).config_entry_id == second.entry_id
        assert a.coordinator is not b.coordinator
    assert summary_state(hass, first).name == (
        "Example Metro line to Example City from Example Station next departure"
    )
    for original in originals[1:]:
        current = registry.async_get(original.entity_id)
        assert (current.id, current.unique_id) == (original.id, original.unique_id)
    current = registry.async_get(overridden.entity_id)
    assert current.id == originals[0].id
    assert hass.states.get(overridden.entity_id).name == "My departure"
    assert dict(first.data) == before
    assert first.version == 1
    summary_original = registry.async_get(summary_entity(hass, first).entity_id)
    registry.async_update_entity(
        summary_original.entity_id, name="My next", new_entity_id="sensor.my_next"
    )
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(first.entry_id)
    await hass.async_block_till_done()
    assert summary_state(hass, first).name == "My next"
    assert summary_entity(hass, first).entity_id == "sensor.my_next"
    assert registry.async_get("sensor.my_next").id == summary_original.id


async def test_disabling_summaries_removes_timer_and_enable_restores_one(
    hass, config_entry_factory, summary_responses, freezer
):
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    coordinator = summary_entity(hass, entry).coordinator
    registry = er.async_get(hass)
    ids = [
        summary_entity(hass, entry, key).entity_id
        for key in ("next_departure", "following_departure")
    ]
    for entity_id in ids:
        registry.async_update_entity(
            entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
    await hass.async_block_till_done()
    assert coordinator._unsubscribe_time is None
    assert coordinator._unsubscribe_timetable is None
    registry.async_update_entity(ids[0], disabled_by=None)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    replacement = summary_entity(hass, entry).coordinator
    assert replacement._unsubscribe_time is not None
    assert list(replacement.async_contexts()) == [0]


async def test_disable_new_entities_does_not_create_summary_callbacks(
    hass, config_entry_factory, summary_responses
):
    entry = config_entry_factory()
    entry.add_to_hass(hass)
    seed_departures(hass, entry)
    hass.config_entries.async_update_entry(entry, pref_disable_new_entities=True)
    await setup_entry(hass, entry)
    for key in ("next_departure", "following_departure"):
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_{entry.entry_id}_{key}"
        )
        assert er.async_get(hass).async_get(entity_id).disabled_by is (
            er.RegistryEntryDisabler.INTEGRATION
        )
        assert hass.states.get(entity_id) is None
