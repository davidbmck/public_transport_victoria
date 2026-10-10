"""Verify shared alert polling through real HA coordinators and entry lifecycles."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import Mock, patch

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.public_transport_victoria import async_setup_entry
from custom_components.public_transport_victoria.alert_coordinator import (
    ALERT_UPDATE_INTERVAL,
    async_get_alert_manager,
)
from custom_components.public_transport_victoria.const import DOMAIN

from .conftest import SYNTHETIC_API_KEY
from .test_sensor import DEPARTURE_PATH, ENTITY_PREFIX, setup_entry

ALERT_PATH = "/v3/disruptions/route/9001"
NOTICE = {"disruption_id": 1, "title": "Synthetic route notice", "to_date": None}


def get_alert_sensor(hass, entry):
    """Find the real alert entity on its owning HA sensor platform."""
    return next(
        entity
        for platform in async_get_platforms(hass, DOMAIN)
        for entity in platform.entities.values()
        if entity.unique_id == f"{DOMAIN}_{entry.entry_id}_route_alerts"
    )


@pytest.fixture
async def acquire(hass, config_entry_factory):
    """Acquire synthetic owners without starting the unrelated timetable platform."""
    manager = async_get_alert_manager(hass)
    entries = []

    async def create(**changes):
        entry = config_entry_factory(**changes)
        entry.add_to_hass(hass)
        entries.append(entry)
        return entry, await manager.async_acquire(entry)

    yield create
    for entry in entries:
        await manager.async_release(entry.entry_id)


@pytest.fixture
def delayed_alerts(hass, monkeypatch, ptv_responses):
    """Pause only the HTTP boundary to exercise genuinely overlapping requests."""
    started, release = asyncio.Event(), asyncio.Event()
    session = async_get_clientsession(hass)
    original = session.get

    @asynccontextmanager
    async def request(url, **kwargs):
        if "/v3/disruptions/" in str(url):
            started.set()
            await release.wait()
        async with original(url, **kwargs) as response:
            yield response

    monkeypatch.setattr(session, "get", request)
    return started, release


async def test_ownership_is_lazy_and_scope_is_exact(acquire, aioclient_mock):
    first_entry, first = await acquire()
    second_entry, second = await acquire(
        route="09001", direction="2", direction_name="Other", stop="8002"
    )
    assert first is second
    assert first.route_id == 9001
    assert first.update_interval == ALERT_UPDATE_INTERVAL
    assert not first.last_update_success
    assert first.data == []
    assert first.last_successful_update is None
    await first.async_ensure_fresh()
    await first.async_request_refresh()
    assert aioclient_mock.call_count == 0
    assert list(first.async_contexts()) == []
    assert first_entry.entry_id != second_entry.entry_id


@pytest.mark.parametrize(
    "changes",
    [
        {"route": "9002"},
        {"id": "54321"},
        {"api_key": "another-synthetic-key"},
        {"id": 12345},
    ],
)
async def test_different_route_or_credentials_do_not_share(
    acquire, changes, ptv_responses, aioclient_mock
):
    first_entry, first = await acquire()
    second_entry, second = await acquire(**changes)
    assert first is not second
    first.async_add_listener(Mock(), first_entry.entry_id)
    second.async_add_listener(Mock(), second_entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    await first.async_ensure_fresh()
    aioclient_mock.clear_requests()
    ptv_responses(
        f"/v3/disruptions/route/{second.route_id}",
        {"disruptions": {"general": [NOTICE]}},
    )
    await second.async_ensure_fresh()
    await first.async_ensure_fresh()
    assert first.data == []
    assert second.data[0]["disruption_id"] == 1
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize("notices", [[], [NOTICE]])
async def test_empty_and_nonempty_success_are_cached(
    acquire, ptv_responses, aioclient_mock, freezer, notices
):
    freezer.move_to("2026-10-10T00:00:00Z")
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {"general": notices}})
    await coordinator.async_ensure_fresh()
    updated = coordinator.last_successful_update
    assert coordinator.last_update_success
    assert len(coordinator.data) == len(notices)
    second_entry, second = await acquire(direction="2")
    second.async_add_listener(Mock(), second_entry.entry_id)
    freezer.tick(timedelta(minutes=9, seconds=59))
    await second.async_ensure_fresh()
    assert aioclient_mock.call_count == 1
    assert second.last_successful_update == updated
    freezer.tick(timedelta(seconds=1))
    await second.async_ensure_fresh()
    assert aioclient_mock.call_count == 2
    assert second.last_successful_update > updated


async def test_cache_reuse_expires_without_fabricating_a_refresh(
    acquire, ptv_responses, aioclient_mock, freezer
):
    freezer.move_to("2026-10-10T00:00:00Z")
    entry, coordinator = await acquire()
    changed = Mock()
    coordinator.async_add_listener(changed, entry.entry_id)
    ptv_responses(
        ALERT_PATH,
        {
            "disruptions": {
                "general": [
                    {"disruption_id": 1, "to_date": "2026-10-10T00:01:00Z"},
                    {"disruption_id": 2, "to_date": None},
                ]
            }
        },
    )
    await coordinator.async_ensure_fresh()
    updated = coordinator.last_successful_update
    freezer.tick(timedelta(minutes=1, microseconds=1))
    second_entry, shared = await acquire(direction="2")
    shared.async_add_listener(Mock(), second_entry.entry_id)
    await shared.async_ensure_fresh()
    assert [n["disruption_id"] for n in shared.data] == [2]
    assert shared.last_successful_update == updated
    assert aioclient_mock.call_count == 1
    assert changed.call_count == 2


@pytest.mark.parametrize("failed", [False, True])
async def test_concurrent_initial_and_manual_requests_coalesce(
    acquire, ptv_responses, delayed_alerts, aioclient_mock, failed
):
    first_entry, coordinator = await acquire()
    second_entry, shared = await acquire(direction="2")
    coordinator.async_add_listener(Mock(), first_entry.entry_id)
    shared.async_add_listener(Mock(), second_entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}}, status=500 if failed else 200)
    started, release = delayed_alerts
    initial = asyncio.create_task(coordinator.async_ensure_fresh())
    await started.wait()
    other = asyncio.create_task(shared.async_ensure_fresh())
    manual = asyncio.create_task(shared.async_request_refresh())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(initial, other, manual)
    assert aioclient_mock.call_count == 1
    assert coordinator.last_update_success is not failed


async def test_simultaneous_fast_manual_requests_coalesce(
    acquire, ptv_responses, aioclient_mock
):
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    await asyncio.gather(*(coordinator.async_request_refresh() for _ in range(5)))
    assert aioclient_mock.call_count == 1


async def test_manual_joins_scheduled_refresh(
    hass, acquire, ptv_responses, delayed_alerts, aioclient_mock, freezer
):
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    started, release = delayed_alerts
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await started.wait()
    manual = asyncio.create_task(coordinator.async_request_refresh())
    await asyncio.sleep(0)
    release.set()
    await manual
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 1


async def test_cancelling_one_consumer_preserves_the_shared_request(
    acquire, ptv_responses, delayed_alerts, aioclient_mock
):
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    started, release = delayed_alerts
    first = asyncio.create_task(coordinator.async_request_refresh())
    await started.wait()
    other = asyncio.create_task(coordinator.async_request_refresh())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    await other
    assert aioclient_mock.call_count == 1
    assert coordinator.last_update_success


async def test_cancelled_only_consumer_does_not_leave_a_completed_request_cached(
    hass, acquire, ptv_responses, delayed_alerts, aioclient_mock
):
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    started, release = delayed_alerts
    consumer = asyncio.create_task(coordinator.async_request_refresh())
    await started.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.last_update_success
    await coordinator.async_request_refresh()
    assert aioclient_mock.call_count == 2


@pytest.mark.parametrize(
    "error_type", [aiohttp.ClientConnectionError, TimeoutError, ValueError]
)
async def test_failure_retains_last_good_data_and_recovers(
    acquire, ptv_responses, aioclient_mock, freezer, error_type, caplog
):
    freezer.move_to("2026-10-10T00:00:00Z")
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    await coordinator.async_ensure_fresh()
    previous_data = coordinator.data
    updated = coordinator.last_successful_update
    aioclient_mock.clear_requests()
    sensitive = f"{SYNTHETIC_API_KEY} devid=12345 signature=synthetic"
    ptv_responses(ALERT_PATH, exc=error_type(sensitive))
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_request_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data == previous_data
    assert coordinator.last_successful_update == updated
    assert f"PTV alert refresh failed ({error_type.__name__})" in caplog.text
    # A recent success followed by failure is never eligible as a fresh cache.
    aioclient_mock.clear_requests()
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    await coordinator.async_ensure_fresh()
    assert aioclient_mock.call_count == 1
    assert coordinator.last_update_success
    assert coordinator.data == []
    assert coordinator.last_successful_update > updated


async def test_last_subscription_stops_polling_and_reenable_resumes(
    hass, acquire, ptv_responses, aioclient_mock, freezer
):
    first_entry, coordinator = await acquire()
    second_entry, shared = await acquire(direction="2")
    remove_first = coordinator.async_add_listener(Mock(), first_entry.entry_id)
    remove_second = shared.async_add_listener(Mock(), second_entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    await coordinator.async_ensure_fresh()
    remove_first()
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2
    remove_second()
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    await coordinator.async_request_refresh()
    assert aioclient_mock.call_count == 2
    coordinator.async_add_listener(Mock(), first_entry.entry_id)
    await coordinator.async_ensure_fresh()
    assert aioclient_mock.call_count == 3


async def test_owner_release_removes_only_its_subscriptions(
    hass, acquire, ptv_responses, aioclient_mock, freezer
):
    first_entry, coordinator = await acquire()
    second_entry, shared = await acquire(direction="2")
    manager = async_get_alert_manager(hass)
    removed = coordinator.async_add_listener(Mock(), first_entry.entry_id)
    shared.async_add_listener(Mock(), second_entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    await coordinator.async_ensure_fresh()
    await manager.async_release(first_entry.entry_id)
    removed()  # Late entity removal is harmless after explicit ownership cleanup.
    assert list(coordinator.async_contexts()) == [second_entry.entry_id]
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2
    await manager.async_release(second_entry.entry_id)
    assert list(coordinator.async_contexts()) == []
    with pytest.raises(ValueError, match="current entry owner"):
        coordinator.async_add_listener(Mock(), second_entry.entry_id)
    replacement = await manager.async_acquire(second_entry)
    assert replacement is not coordinator
    replacement.async_add_listener(Mock(), second_entry.entry_id)
    await replacement.async_ensure_fresh()
    assert aioclient_mock.call_count == 3


async def test_last_owner_unload_cancels_outstanding_http_request(
    hass, acquire, ptv_responses, delayed_alerts, aioclient_mock
):
    entry, coordinator = await acquire()
    coordinator.async_add_listener(Mock(), entry.entry_id)
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    started, _ = delayed_alerts
    refresh = asyncio.create_task(coordinator.async_request_refresh())
    await started.wait()
    await async_get_alert_manager(hass).async_release(entry.entry_id)
    with pytest.raises(asyncio.CancelledError):
        await refresh
    assert not coordinator.last_update_success
    assert coordinator.last_successful_update is None
    assert aioclient_mock.call_count == 0


async def test_no_departures_and_initial_alert_failure_retry(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {}}, status=503)
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    assert aioclient_mock.call_count == 2  # Timetable and initial alert request.
    probe = get_alert_sensor(hass, entry)
    state = hass.states.get(probe.entity_id)
    assert state.state == STATE_UNAVAILABLE
    assert probe.coordinator.data == []
    assert probe.coordinator.last_successful_update is None
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(f"{ENTITY_PREFIX}_0").state == "No data"
    aioclient_mock.clear_requests()
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(probe.entity_id).state == "1"
    assert hass.states.get(probe.entity_id).attributes["last_successful_update"]
    assert aioclient_mock.call_count == 2  # One timetable and one shared alert poll.


@pytest.mark.parametrize("failure", ["network", "http"])
async def test_initial_timetable_failure_does_not_block_alerts(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer, failure
):
    if failure == "network":
        ptv_responses(
            DEPARTURE_PATH, exc=aiohttp.ClientConnectionError(SYNTHETIC_API_KEY)
        )
    else:
        ptv_responses(DEPARTURE_PATH, {}, status=503)
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    probe = get_alert_sensor(hass, entry)
    assert hass.states.get(probe.entity_id).state == "1"
    assert hass.states.get(f"{ENTITY_PREFIX}_0").state == STATE_UNAVAILABLE
    aioclient_mock.clear_requests()
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(f"{ENTITY_PREFIX}_0").state == "No data"
    assert hass.states.get(probe.entity_id).state == "0"


async def test_alert_failure_does_not_interrupt_timetable_or_last_good_data(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    probe = get_alert_sensor(hass, entry)
    state = hass.states.get(probe.entity_id)
    previous = state.attributes["alerts"]
    updated = probe.coordinator.last_successful_update
    aioclient_mock.clear_requests()
    ptv_responses(ALERT_PATH, {"disruptions": {}}, status=401)
    assert await async_setup_component(hass, "homeassistant", {})
    freezer.tick(timedelta(seconds=1))
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": probe.entity_id}, blocking=True
    )
    failed = hass.states.get(probe.entity_id)
    assert failed.state == STATE_UNAVAILABLE
    assert probe.coordinator.data == previous
    assert probe.coordinator.last_successful_update == updated
    assert hass.states.get(f"{ENTITY_PREFIX}_0").state == "No data"
    assert aioclient_mock.call_count == 1


async def test_real_entry_unload_reload_preserves_shared_owner(
    hass, config_entry_factory, ptv_responses, aioclient_mock, freezer
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {}})
    first_entry = config_entry_factory()
    second_entry = config_entry_factory(direction="2", direction_name="Other")
    await setup_entry(hass, first_entry)
    await setup_entry(hass, second_entry)
    first_probe = get_alert_sensor(hass, first_entry)
    second_probe = get_alert_sensor(hass, second_entry)
    shared = first_probe.coordinator
    assert shared is second_probe.coordinator
    assert aioclient_mock.call_count == 3
    assert await hass.config_entries.async_unload(first_entry.entry_id)
    assert list(shared.async_contexts()) == [second_entry.entry_id]
    assert await hass.config_entries.async_setup(first_entry.entry_id)
    reloaded = get_alert_sensor(hass, first_entry)
    assert reloaded.coordinator is shared
    assert aioclient_mock.call_count == 4  # Reloaded timetable; alerts reuse [] cache.
    assert await hass.config_entries.async_unload(first_entry.entry_id)
    assert await hass.config_entries.async_unload(second_entry.entry_id)
    requests = aioclient_mock.call_count
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == requests
    assert await hass.config_entries.async_setup(first_entry.entry_id)
    new_probe = get_alert_sensor(hass, first_entry)
    assert new_probe.coordinator is not shared
    assert aioclient_mock.call_count == requests + 2


async def test_registry_disabled_alert_sensor_never_subscribes(
    hass, config_entry_factory, ptv_responses, aioclient_mock
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    entry = config_entry_factory()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{entry.entry_id}_route_alerts",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await setup_entry(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id].alert_coordinator
    assert list(coordinator.async_contexts()) == []
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_failed_platform_setup_releases_alert_owner(
    hass, config_entry_factory, ptv_responses, error_type
):
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    entry = config_entry_factory()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    manager = async_get_alert_manager(hass)
    previous = await manager.async_acquire(entry)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=error_type("synthetic platform error"),
    ):
        with pytest.raises(error_type, match="synthetic platform error"):
            await async_setup_entry(hass, entry)
    assert entry.entry_id not in hass.data[DOMAIN]
    replacement = await manager.async_acquire(entry)
    assert previous is not replacement
    await manager.async_release(entry.entry_id)


async def test_cancelled_initial_timetable_setup_releases_alert_owner(
    hass, config_entry_factory
):
    entry = config_entry_factory()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    manager = async_get_alert_manager(hass)
    previous = await manager.async_acquire(entry)
    with patch(
        "custom_components.public_transport_victoria.Connector._init",
        side_effect=asyncio.CancelledError,
    ):
        with pytest.raises(asyncio.CancelledError):
            await async_setup_entry(hass, entry)
    assert entry.entry_id not in hass.data[DOMAIN]
    replacement = await manager.async_acquire(entry)
    assert previous is not replacement
    await manager.async_release(entry.entry_id)
