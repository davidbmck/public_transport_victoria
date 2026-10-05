"""Exercise the inherited API client against mocked responses."""

import logging

import pytest
from aiohttp import ClientConnectionError

from custom_components.public_transport_victoria.PublicTransportVictoria import (
    public_transport_victoria as ptv_api,
)


@pytest.fixture
def connector(hass):
    """Use synthetic credentials and route identifiers only."""
    return ptv_api.Connector(hass, "12345", "synthetic-test-key-not-a-credential")


async def test_route_types(connector, ptv_responses):
    """Keep the string IDs used by existing config entries and selectors."""
    ptv_responses(
        "/v3/route_types",
        {"route_types": [{"route_type": 0, "route_type_name": "Train"}]},
    )
    assert await connector.async_route_types() == {"0": "Train"}


async def test_route_sorting(connector, ptv_responses):
    """Preserve numeric, text and missing route-number ordering."""
    ptv_responses(
        "/v3/routes",
        {
            "routes": [
                {"route_id": 4, "route_name": "Letter", "route_number": "A"},
                {"route_id": 3, "route_name": "Unnamed number"},
                {"route_id": 2, "route_name": "Ten", "route_number": "10"},
                {"route_id": 1, "route_name": "Two", "route_number": "2"},
            ]
        },
    )
    routes = await connector.async_routes("2")
    assert list(routes.items()) == [
        ("1", "2 - Two"),
        ("2", "10 - Ten"),
        ("3", "Unnamed number"),
        ("4", "A - Letter"),
    ]


async def test_route_network_failure(connector, ptv_responses):
    """An actual network exception reaches the caller rather than an empty result."""
    ptv_responses(
        "/v3/routes", exc=ClientConnectionError("synthetic connection failure")
    )
    with pytest.raises(ClientConnectionError):
        await connector.async_routes("2")


@pytest.mark.parametrize(
    ("utc", "local"),
    [
        ("2026-10-03T15:59:00Z", "01:59 AM"),
        ("2026-10-03T16:00:00Z", "03:00 AM"),
        ("2026-04-04T15:30:00Z", "02:30 AM"),
        ("2026-04-04T16:30:00Z", "02:30 AM"),
    ],
)
async def test_departure_dst_conversion(hass, utc, local):
    """Preserve the Melbourne spring jump and repeated autumn hour."""
    await hass.config.async_set_time_zone("Australia/Melbourne")
    assert ptv_api.convert_utc_to_local(utc, hass) == local


def test_signed_url_not_logged(caplog):
    """Signing still works without exposing authentication data in logs."""
    caplog.set_level(logging.DEBUG)
    url = ptv_api.build_URL(
        "12345", "synthetic-test-key-not-a-credential", "/v3/route_types"
    )
    assert "devid=12345" in url
    assert "signature=" in url
    assert "signature=" not in caplog.text
    assert "devid=" not in caplog.text
