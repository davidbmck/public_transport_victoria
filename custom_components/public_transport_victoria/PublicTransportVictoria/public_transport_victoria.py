"""Public Transport Victoria API connector."""
import aiohttp
import asyncio
import datetime
import hmac
from hashlib import sha1

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import Throttle
from homeassistant.util.dt import get_time_zone

from .route_disruptions import RouteDisruptionsError, parse_route_disruptions
from ..departure_summary import parse_departure_time, service_identity


BASE_URL = "https://timetableapi.ptv.vic.gov.au"
DEPARTURES_PATH = "/v3/departures/route_type/{}/stop/{}/route/{}?direction_id={}&max_results={}"
DIRECTIONS_PATH = "/v3/directions/route/{}"
DISRUPTIONS_PATH = "/v3/disruptions/route/{}"
DISRUPTIONS_TIMEOUT = aiohttp.ClientTimeout(
    total=30, connect=10, sock_connect=10, sock_read=20
)
MIN_TIME_BETWEEN_UPDATES = datetime.timedelta(minutes=2)
MAX_RESULTS = 5
ROUTE_TYPES_PATH = "/v3/route_types"
ROUTES_PATH = "/v3/routes?route_types={}"
STOPS_PATH = "/v3/stops/route/{}/route_type/{}"

class Connector:
    """Public Transport Victoria connector."""

    manufacturer = "Demonstration Corp"

    def __init__(self, hass, id, api_key, route_type=None, route=None,
                 direction=None, stop=None, route_type_name=None,
                 route_name=None, direction_name=None, stop_name=None):
        """Init Public Transport Victoria connector."""
        self.hass = hass
        self.id = id
        self.api_key = api_key
        self.route_type = route_type
        self.route = route
        self.direction = direction
        self.stop = stop
        self.route_type_name = route_type_name
        self.route_name = route_name
        self.direction_name = direction_name
        self.stop_name = stop_name
        self.departure_fetch_failed = False

    async def _init(self):
        """Async Init Public Transport Victoria connector."""
        self.departures_path = DEPARTURES_PATH.format(
            self.route_type, self.stop, self.route, self.direction, MAX_RESULTS
        )
        await self.async_update()

    async def async_route_types(self):
        """Get route types from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, ROUTE_TYPES_PATH)

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            route_types = {}
            for r in response["route_types"]:
                route_types[str(r["route_type"])] = r["route_type_name"]

            return route_types

    async def async_route_disruptions(self):
        """Fetch structured route-wide notices without touching departures.

        Transport/HTTP/JSON and payload failures propagate to the caller. Only
        a complete successful response can return an empty list. Callers must
        not log raw request exceptions, which can contain signed URLs.
        """
        url = build_URL(self.id, self.api_key, DISRUPTIONS_PATH.format(int(self.route)))
        session = async_get_clientsession(self.hass)
        async with session.get(url, timeout=DISRUPTIONS_TIMEOUT) as response:
            response.raise_for_status()
            if not 200 <= response.status < 300:
                raise RouteDisruptionsError("Unexpected route disruptions HTTP status")
            payload = await response.json()
        return parse_route_disruptions(payload)

    async def async_routes(self, route_type):
        """Get routes from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, ROUTES_PATH.format(route_type))

        timeout = aiohttp.ClientTimeout(
            total=60,
            connect=30,
            sock_read=60,
            sock_connect=30
        )
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }
            async with session.get(url, headers=headers) as response:
                if response is not None and response.status == 200:
                    response = await response.json()
                    
                    route_list = []
                    for r in response["routes"]:
                        route_number = r.get("route_number", "")
                        try:
                            sort_key = int(route_number) if route_number else float('inf')
                        except ValueError:
                            sort_key = (1, route_number)
                            
                        route_list.append((
                            r["route_id"],
                            sort_key,
                            f"{route_number} - {r['route_name']}" if route_number else r["route_name"]
                        ))
                    
                    def sort_key(x):
                        sort_val = x[1]
                        if isinstance(sort_val, tuple):
                            return sort_val
                        return (0, sort_val)
                    
                    route_list.sort(key=sort_key)
                    
                    routes = {str(route_id): display_name for route_id, _, display_name in route_list}
                    
                    self.route_type = route_type
                    return routes
                else:
                    return {}

    async def async_directions(self, route):
        """Get directions from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, DIRECTIONS_PATH.format(route))

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            directions = {}
            for r in response["directions"]:
                directions[str(r["direction_id"])] = r["direction_name"]

            self.route = route

            return directions

    async def async_stops(self, route):
        """Get stops from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, STOPS_PATH.format(route, self.route_type))

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            stops = {}
            for r in response["stops"]:
                stops[str(r["stop_id"])] = r["stop_name"]

            self.route = route

            return stops

    async def async_update(self):
        """Update the departure information."""
        await self._async_update_departures()
        # A throttled retry after an unsuccessful fetch is still a failure.
        if self.departure_fetch_failed:
            raise ValueError("PTV timetable has no successful refresh")

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def _async_update_departures(self):
        """Publish a complete successful snapshot, retaining failures explicitly."""
        self.departure_fetch_failed = True
        url = build_URL(self.id, self.api_key, self.departures_path)

        session = async_get_clientsession(self.hass)
        async with session.get(url, timeout=DISRUPTIONS_TIMEOUT) as response:
            response.raise_for_status()
            if response.status != 200:
                raise ValueError("Unexpected timetable HTTP status")
            payload = await response.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("departures"), list)
        ):
            raise ValueError("Malformed timetable response")
        departures = [dict(r) for r in payload["departures"] if isinstance(r, dict)]

        async def run_info(record):
            run_id = record.get("run_id")
            if service_identity({"run_id": run_id}) is not None:
                return await self.async_run(run_id)
            return None

        run_infos = await asyncio.gather(*[run_info(r) for r in departures])
        for r, info in zip(departures, run_infos):
            timestamp = (
                parse_departure_time(r.get("estimated_departure_utc"))
                or parse_departure_time(r.get("scheduled_departure_utc"))
            )
            r["departure"] = (
                timestamp.astimezone(
                    get_time_zone(self.hass.config.time_zone)
                ).strftime("%I:%M %p")
                if timestamp else "No data"
            )
            r["is_express"] = info.get("express_stop_count", 0) > 0 if info else False
        self.departures = departures
        self.departure_fetch_failed = False


    async def async_run(self, run_id):
        """Get run information from Public Transport Victoria API."""
        url = build_URL(self.id, self.api_key, f"/v3/runs/{run_id}")

        async with aiohttp.ClientSession() as session:
            response = await session.get(url)

        if response is not None and response.status == 200:
            response = await response.json()
            if response.get("runs") and len(response["runs"]) > 0:
                return response["runs"][0]
        return None

def build_URL(id, api_key, request):
    request = request + ('&' if ('?' in request) else '?')
    raw = request + 'devid={}'.format(id)
    hashed = hmac.new(api_key.encode('utf-8'), raw.encode('utf-8'), sha1)
    signature = hashed.hexdigest()
    url = BASE_URL + raw + '&signature={}'.format(signature)
    return url

def convert_utc_to_local(utc, hass):
    """Convert UTC to Home Assistant local time."""
    d = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ")
    # Get the Home Assistant configured time zone
    local_tz = get_time_zone(hass.config.time_zone)
    # Convert the time to the Home Assistant time zone
    d = d.replace(tzinfo=datetime.timezone.utc).astimezone(local_tz)
    return d.strftime("%I:%M %p")
