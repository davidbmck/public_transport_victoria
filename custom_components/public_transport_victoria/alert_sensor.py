"""Route-wide alert counts and structured notices for each owning entry."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_DIRECTION_NAME,
    CONF_ROUTE_NAME,
    CONF_ROUTE_TYPE,
    CONF_STOP_NAME,
    DOMAIN,
)


class PublicTransportVictoriaAlertSensor(CoordinatorEntity, SensorEntity):
    """Expose a shared route snapshot with a stable, entry-specific identity."""

    _attr_has_entity_name = True
    _attr_translation_key = "route_alerts"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_attribution = ATTRIBUTION
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, context=entry.entry_id)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_route_alerts"
        self._attr_translation_placeholders = {
            "route_name": entry.data[CONF_ROUTE_NAME],
            "direction_name": entry.data[CONF_DIRECTION_NAME],
            "stop_name": entry.data[CONF_STOP_NAME],
        }
        self._route_type = int(entry.data[CONF_ROUTE_TYPE])

    async def async_added_to_hass(self):
        """Only an enabled entity subscribes and requests its initial snapshot."""
        await super().async_added_to_hass()
        await self.coordinator.async_ensure_fresh()

    @property
    def native_value(self):
        """Return the number of normalized notices, including a successful zero."""
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        updated = self.coordinator.last_successful_update
        return {
            "alerts": self.coordinator.data,
            "route_id": self.coordinator.route_id,
            "route_type": self._route_type,
            "last_successful_update": updated.isoformat() if updated else None,
        }
