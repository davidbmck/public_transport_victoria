"""Shared route alert polling with explicit entry ownership and subscriptions."""

import asyncio
import logging
from datetime import timedelta

from homeassistant.const import CONF_API_KEY, CONF_ID
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_ROUTE, DOMAIN
from .PublicTransportVictoria.public_transport_victoria import Connector
from .PublicTransportVictoria.route_disruptions import non_expired_disruptions

_LOGGER = logging.getLogger(__name__)
ALERT_UPDATE_INTERVAL = timedelta(minutes=10)
DATA_ALERT_MANAGER = f"{DOMAIN}_alert_manager"


class RouteAlertCoordinator(DataUpdateCoordinator):
    """Poll one route/credential scope, independent of any owning timetable."""

    def __init__(self, hass, route_id, developer_id, api_key):
        # A shared coordinator must not be shut down when its first entry unloads.
        # The manager owns shutdown; HA owns the HTTP session.
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name="PTV route alerts",
            update_interval=ALERT_UPDATE_INTERVAL,
        )
        self.route_id = route_id
        self._connector = Connector(hass, developer_id, api_key, route=route_id)
        self._owners = set()
        self._subscriptions = {}
        self._refresh_task = None
        self._update_task = None
        self._refresh_complete = None
        self._closed = False
        self.data = []
        self.last_update_success = False
        self.last_successful_update = None

    @callback
    def async_add_owner(self, entry_id):
        """Acquire ownership without subscribing or fetching."""
        self._owners.add(entry_id)

    @callback
    def async_release_owner(self, entry_id):
        """Drop this entry's subscriptions even if platform removal was partial."""
        for unsubscribe in tuple(self._subscriptions.get(entry_id, ())):
            unsubscribe()
        self._owners.discard(entry_id)

    @callback
    def async_add_listener(self, update_callback, context=None):
        """Subscribe an enabled entity using its owning entry ID as context."""
        if self._closed or context not in self._owners:
            raise ValueError("Route alert subscription requires a current entry owner")
        remove_listener = super().async_add_listener(update_callback, context)
        subscriptions = self._subscriptions.setdefault(context, set())

        @callback
        def unsubscribe():
            if unsubscribe not in subscriptions:
                return
            subscriptions.remove(unsubscribe)
            remove_listener()
            if not subscriptions:
                self._subscriptions.pop(context, None)

        subscriptions.add(unsubscribe)
        return unsubscribe

    async def async_ensure_fresh(self):
        """After subscribing, reuse healthy data younger than ten minutes."""
        if self._closed or not self._subscriptions:
            return
        if self._refresh_task is not None or self._update_task is not None:
            await self.async_refresh()
            return
        now = dt_util.utcnow()
        if (
            self.last_update_success
            and self.last_successful_update is not None
            and timedelta(0)
            <= now - self.last_successful_update
            < ALERT_UPDATE_INTERVAL
        ):
            notices = non_expired_disruptions(self.data, now=now)
            if notices != self.data:
                self.data = notices
                self.async_update_listeners()
            return
        await self.async_refresh()

    async def async_refresh(self):
        """Join concurrent initial/manual refreshes instead of queuing duplicates."""
        if self._closed or not self._subscriptions:
            return
        if self._refresh_task is None:
            self._refresh_task = self.hass.async_create_background_task(
                self._async_refresh_once(),
                "PTV route alerts refresh",
                eager_start=False,
            )
            self._refresh_task.add_done_callback(self._async_refresh_task_done)
        task = self._refresh_task
        try:
            # Cancelling one consumer must not cancel another consumer's request.
            await asyncio.shield(task)
        finally:
            if task.done() and self._refresh_task is task:
                self._refresh_task = None

    @callback
    def _async_refresh_task_done(self, task):
        """Release completed requests even when every waiting consumer cancelled."""
        if self._refresh_task is task:
            self._refresh_task = None

    async def _async_refresh_once(self):
        if self._closed or not self._subscriptions:
            return
        # Scheduled updates use HA's own refresh path. Join one already underway
        # before acquiring HA's refresh lock, rather than fetch again afterwards.
        if self._refresh_complete is not None and not self._refresh_complete.done():
            await asyncio.shield(self._refresh_complete)
        else:
            await super().async_refresh()

    async def async_request_refresh(self):
        """Manual entity updates use the same coalesced refresh as setup."""
        await self.async_refresh()

    async def _async_update_data(self):
        """Only a complete API success replaces data and advances its timestamp."""
        if self._closed or not self._subscriptions:
            raise UpdateFailed("PTV route alerts have no enabled subscribers")
        self._update_task = asyncio.current_task()
        self._refresh_complete = self.hass.loop.create_future()
        try:
            notices = await self._connector.async_route_disruptions()
        except asyncio.CancelledError:
            self._refresh_complete.set_result(None)
            raise
        except Exception as err:
            # HA also logs exception tracebacks at debug level. Suppress the raw
            # exception context so signed URLs and credentials cannot reach logs.
            raise UpdateFailed(
                f"PTV alert refresh failed ({type(err).__name__})"
            ) from None
        else:
            self.last_successful_update = dt_util.utcnow()
            return notices
        finally:
            self._update_task = None

    @callback
    def _async_refresh_finished(self):
        """Wake consumers after HA has committed success or failure availability."""
        if self._refresh_complete is not None and not self._refresh_complete.done():
            self._refresh_complete.set_result(None)

    async def async_shutdown(self):
        """Stop timers and await cancellation of any outstanding shared request."""
        self._closed = True
        for entry_id in tuple(self._subscriptions):
            self.async_release_owner(entry_id)
        await super().async_shutdown()
        tasks = {self._refresh_task, self._update_task} - {None, asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._refresh_task = None


class RouteAlertManager:
    """Keep credential scope private and release coordinators with their last owner."""

    def __init__(self, hass):
        self.hass = hass
        self._coordinators = {}
        self._entry_keys = {}

    async def async_acquire(self, entry):
        """Reuse by integer route and exact credential pair, without HTTP requests."""
        key = (
            int(entry.data[CONF_ROUTE]),
            entry.data[CONF_ID],
            entry.data[CONF_API_KEY],
        )
        coordinator = self._coordinators.get(key)
        if coordinator is None:
            coordinator = RouteAlertCoordinator(self.hass, *key)
            self._coordinators[key] = coordinator
            await coordinator.async_register_shutdown()
        coordinator.async_add_owner(entry.entry_id)
        self._entry_keys[entry.entry_id] = key
        return coordinator

    async def async_release(self, entry_id):
        """Remove only this entry's subscriptions; stop the last owner's coordinator."""
        key = self._entry_keys.pop(entry_id, None)
        if key is None:
            return
        coordinator = self._coordinators[key]
        coordinator.async_release_owner(entry_id)
        if key not in self._entry_keys.values():
            self._coordinators.pop(key)
            await coordinator.async_shutdown()


@callback
def async_get_alert_manager(hass):
    """Return the integration's shared, in-memory manager."""
    if DATA_ALERT_MANAGER not in hass.data:
        hass.data[DATA_ALERT_MANAGER] = RouteAlertManager(hass)
    return hass.data[DATA_ALERT_MANAGER]
