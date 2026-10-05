"""Exercise existing configuration flows without live credentials."""

import logging
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.public_transport_victoria.const import DOMAIN

from .conftest import SYNTHETIC_API_KEY


async def test_user_form(hass):
    """The first entry still asks only for the existing developer ID and API key."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert {str(field) for field in result["data_schema"].schema} == {"id", "api_key"}


async def test_complete_flow(hass, ptv_responses):
    """Create the same version-1 configuration without new mandatory fields."""
    ptv_responses(
        "/v3/route_types",
        {"route_types": [{"route_type": 0, "route_type_name": "Train"}]},
    )
    ptv_responses(
        "/v3/routes", {"routes": [{"route_id": 9001, "route_name": "Example Metro"}]}
    )
    ptv_responses(
        "/v3/directions/route/9001",
        {"directions": [{"direction_id": 1, "direction_name": "Example City"}]},
    )
    ptv_responses(
        "/v3/stops/route/9001/route_type/0",
        {"stops": [{"stop_id": 8001, "stop_name": "Example Station"}]},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    for data, step in [
        ({"id": "12345", "api_key": SYNTHETIC_API_KEY}, "route_types"),
        ({"route_type": "0"}, "routes"),
        ({"route": "9001"}, "directions"),
        ({"direction": "1"}, "stops"),
    ]:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == step

    # Setup triggered by entry creation is covered through real HA APIs in
    # test_sensor.py; isolate this test to the config flow and its API requests.
    with patch.object(hass.config_entries, "async_setup", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"stop": "8001"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Example Metro line to Example City from Example Station"
    assert result["data"] == {
        "id": "12345",
        "api_key": SYNTHETIC_API_KEY,
        "route_type": "0",
        "route_type_name": "Train",
        "route": "9001",
        "route_name": "Example Metro",
        "direction": "1",
        "direction_name": "Example City",
        "stop": "8001",
        "stop_name": "Example Station",
    }


async def test_existing_entry_reuses_credentials(
    hass, config_entry_factory, ptv_responses
):
    """A second entry skips the credential prompt and retains existing selection."""
    config_entry_factory().add_to_hass(hass)
    ptv_responses(
        "/v3/route_types",
        {"route_types": [{"route_type": 0, "route_type_name": "Train"}]},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "route_types"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Inherited config flow logs credential-bearing data; "
        "safe logging remains in #6."
    ),
)
async def test_existing_credentials_not_logged(
    hass, config_entry_factory, ptv_responses, caplog
):
    """Detect the actual pre-existing key leak, without logging real credentials."""
    config_entry_factory().add_to_hass(hass)
    ptv_responses("/v3/route_types", {"route_types": []})
    caplog.set_level(logging.DEBUG)
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert SYNTHETIC_API_KEY not in caplog.text
