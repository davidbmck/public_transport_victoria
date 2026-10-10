"""Public Transport Victoria integration."""
import asyncio
import logging


from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .alert_coordinator import async_get_alert_manager

from .const import (
    CONF_DIRECTION, CONF_DIRECTION_NAME, CONF_ROUTE, CONF_ROUTE_NAME,
    CONF_ROUTE_TYPE, CONF_ROUTE_TYPE_NAME, CONF_STOP, CONF_STOP_NAME, DOMAIN
)
from .PublicTransportVictoria.public_transport_victoria import Connector


# Define the logger
_LOGGER = logging.getLogger(__name__)


PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Public Transport Victoria component."""
    hass.data.setdefault(DOMAIN, {})

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Public Transport Victoria from a config entry."""
    connector = Connector(
        hass,
        entry.data[CONF_ID],
        entry.data[CONF_API_KEY],
        entry.data[CONF_ROUTE_TYPE],
        entry.data[CONF_ROUTE],
        entry.data[CONF_DIRECTION],
        entry.data[CONF_STOP],
        entry.data[CONF_ROUTE_TYPE_NAME],
        entry.data[CONF_ROUTE_NAME],
        entry.data[CONF_DIRECTION_NAME],
        entry.data[CONF_STOP_NAME],
    )
    manager = async_get_alert_manager(hass)
    connector.alert_coordinator = await manager.async_acquire(entry)
    connector.departure_setup_error = None
    try:
        await connector._init()
        if not hasattr(connector, "departures"):
            raise UpdateFailed("PTV departure setup returned no successful data")
    except asyncio.CancelledError:
        await manager.async_release(entry.entry_id)
        raise
    except Exception as err:
        # A timetable outage must not prevent the independent alert platform.
        connector.departure_setup_error = UpdateFailed(
            f"PTV entry setup failed ({type(err).__name__})"
        )

    hass.data[DOMAIN][entry.entry_id] = connector

    # Use the new async_forward_entry_setups method

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await manager.async_release(entry.entry_id)
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        await async_get_alert_manager(hass).async_release(entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
