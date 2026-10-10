"""Validate payload failures through the API, coordinator and public HA sensor."""

from datetime import timedelta

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.setup import async_setup_component

from .test_alert_coordinator import ALERT_PATH, NOTICE, get_alert_sensor
from .test_sensor import DEPARTURE_PATH, ENTITY_PREFIX, setup_entry


@pytest.mark.parametrize(
    "fixture", ["malformed_bucket.json", "malformed_record.json", "offline.json"]
)
async def test_payload_failure_retains_snapshot_and_recovers_through_public_sensor(
    hass,
    config_entry_factory,
    ptv_responses,
    aioclient_mock,
    load_json_fixture,
    freezer,
    fixture,
    caplog,
):
    """A partial/offline response cannot publish zero or a fresh success timestamp."""
    ptv_responses(DEPARTURE_PATH, {"departures": []})
    ptv_responses(ALERT_PATH, {"disruptions": {"general": [NOTICE]}})
    entry = config_entry_factory()
    await setup_entry(hass, entry)
    sensor = get_alert_sensor(hass, entry)
    previous = sensor.coordinator.data
    updated = sensor.coordinator.last_successful_update
    assert hass.states.get(sensor.entity_id).state == "1"
    assert await async_setup_component(hass, "homeassistant", {})

    async def refresh(payload):
        aioclient_mock.clear_requests()
        ptv_responses(ALERT_PATH, payload)
        freezer.tick(timedelta(seconds=1))
        await hass.services.async_call(
            "homeassistant",
            "update_entity",
            {"entity_id": sensor.entity_id},
            blocking=True,
        )
        assert aioclient_mock.call_count == 1
        assert hass.states.get(f"{ENTITY_PREFIX}_0").state == "No data"

    await refresh(load_json_fixture(f"route_alerts/{fixture}"))
    assert hass.states.get(sensor.entity_id).state == STATE_UNAVAILABLE
    assert sensor.coordinator.data == previous
    assert sensor.coordinator.last_successful_update == updated
    assert "PTV alert refresh failed (RouteDisruptionsError)" in caplog.text

    await refresh({"disruptions": {}})
    state = hass.states.get(sensor.entity_id)
    assert state.state == "0"
    assert state.attributes["alerts"] == []
    assert sensor.coordinator.last_successful_update > updated

    await refresh({"disruptions": {"general": [NOTICE]}})
    state = hass.states.get(sensor.entity_id)
    assert state.state == "1"
    assert state.attributes["alerts"][0]["disruption_id"] == 1
