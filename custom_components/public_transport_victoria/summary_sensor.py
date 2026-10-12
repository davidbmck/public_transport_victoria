"""Entry-scoped timestamp summaries with one cancellable local time callback."""

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTRIBUTION,
    CONF_DIRECTION_NAME,
    CONF_ROUTE_NAME,
    CONF_STOP_NAME,
    DOMAIN,
)
from .departure_summary import select_departures

_LOGGER = logging.getLogger(__name__)


class DepartureSummaryCoordinator(DataUpdateCoordinator):
    """Observe the existing timetable and expire selections locally."""

    def __init__(self, hass, timetable, entry):
        super().__init__(
            hass, _LOGGER, name="PTV departure summaries", config_entry=entry
        )
        self.timetable = timetable
        self.data = (None, None)
        self.last_update_success = timetable.last_update_success
        self._unsubscribe_timetable = None
        self._unsubscribe_time = None

    @callback
    def async_add_listener(self, update_callback, context=None):
        """Subscribe only while at least one summary entity is enabled."""
        remove = super().async_add_listener(update_callback, context)
        if self._unsubscribe_timetable is None:
            self._unsubscribe_timetable = self.timetable.async_add_listener(
                self._recalculate
            )
            self._recalculate()

        @callback
        def unsubscribe():
            remove()
            if not list(self.async_contexts()):
                self._stop()

        return unsubscribe

    @callback
    def _cancel_time(self):
        if self._unsubscribe_time is not None:
            self._unsubscribe_time()
            self._unsubscribe_time = None

    @callback
    def _stop(self):
        self._cancel_time()
        if self._unsubscribe_timetable is not None:
            self._unsubscribe_timetable()
            self._unsubscribe_timetable = None

    async def async_shutdown(self):
        """Remove both callbacks even if an entry unload interrupts setup."""
        self._stop()
        await super().async_shutdown()

    async def async_request_refresh(self):
        """Manual updates use the original coordinator and its existing throttle."""
        await self.timetable.async_request_refresh()

    @callback
    def _recalculate(self):
        self._cancel_time()
        if not self.timetable.last_update_success:
            self.data = (None, None)
            self.async_set_update_error(UpdateFailed("PTV timetable unavailable"))
            return
        selected = select_departures(self.timetable.data or [], dt_util.utcnow())
        # Updating this non-polling coordinator cannot reset timetable polling.
        self.async_set_updated_data(selected)
        if selected[0] is not None:
            self._unsubscribe_time = async_track_point_in_utc_time(
                self.hass, self._time_passed, selected[0].timestamp
            )

    @callback
    def _time_passed(self, _now):
        self._unsubscribe_time = None
        self._recalculate()


class PublicTransportVictoriaSummarySensor(CoordinatorEntity, SensorEntity):
    """Expose a timestamp rather than reconstructing other entities' attributes."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator, entry, number):
        super().__init__(coordinator, context=number)
        self._number = number
        key = "next_departure" if number == 0 else "following_departure"
        self._attr_translation_key = key
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{key}"
        self._attr_translation_placeholders = {
            "route_name": entry.data[CONF_ROUTE_NAME],
            "direction_name": entry.data[CONF_DIRECTION_NAME],
            "stop_name": entry.data[CONF_STOP_NAME],
        }

    @property
    def native_value(self):
        selected = self.coordinator.data[self._number]
        return selected.timestamp if selected else None

    @property
    def extra_state_attributes(self):
        selected = self.coordinator.data[self._number]
        return selected.attributes if selected else {}
