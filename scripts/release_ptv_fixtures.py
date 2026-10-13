"""Synthetic PTV boundary for installation trials; GitHub/HACS stays real.

Every request to the PTV origin is intercepted before networking. These public
fixtures use no household data or live credentials and exist only in memory.
"""

import copy
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiohttp
from yarl import URL


class Response:
    """Minimal response interface used by the 0.8.0 and 0.9.0 connectors."""

    def __init__(self, payload, status):
        self.payload = payload
        self.status = status

    async def json(self):
        return copy.deepcopy(self.payload)

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url="https://timetableapi.ptv.vic.gov.au/mocked"),
                (),
                status=self.status,
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def install_mocks(credentials):
    """Patch only PTV requests, including synthetic authentication failures."""
    original = aiohttp.ClientSession._request
    now = datetime.now(timezone.utc).replace(microsecond=0)
    modes = {0: "Train", 1: "Tram", 2: "Bus", 3: "Regional", 4: "Night bus"}

    async def request(session, method, url, **kwargs):
        parsed = URL(url)
        if parsed.host != "timetableapi.ptv.vic.gov.au":
            return await original(session, method, url, **kwargs)
        raw = (
            str(parsed)
            .split("timetableapi.ptv.vic.gov.au", 1)[1]
            .split("&signature=", 1)[0]
        )
        expected = hmac.new(
            credentials["api_key"].encode(), raw.encode(), hashlib.sha1
        ).hexdigest()
        if parsed.query.get("signature") != expected:
            return Response({}, 401)
        path = parsed.path
        if path == "/v3/route_types":
            payload = {
                "route_types": [
                    {"route_type": mode, "route_type_name": name}
                    for mode, name in modes.items()
                ]
            }
        elif path == "/v3/routes":
            payload = {
                "routes": [
                    {
                        "route_id": 9001,
                        "route_name": "Example Metro",
                        "route_number": "1",
                    }
                ]
            }
        elif path.startswith("/v3/directions/"):
            payload = {
                "directions": [
                    {"direction_id": 1, "direction_name": "Example City"},
                    {"direction_id": 2, "direction_name": "Example Suburb"},
                ]
            }
        elif path.startswith("/v3/stops/"):
            payload = {"stops": [{"stop_id": 8001, "stop_name": "Example Station"}]}
        elif path.startswith("/v3/departures/"):
            payload = {
                "departures": [
                    {
                        "run_id": 7001 + index,
                        "run_ref": reference,
                        "route_id": 9001,
                        "stop_id": 8001,
                        "direction_id": int(parsed.query.get("direction_id", "1")),
                        "scheduled_departure_utc": (
                            now + timedelta(minutes=5 + 5 * index)
                        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "estimated_departure_utc": None,
                    }
                    for index, reference in enumerate(("synthetic-a", "synthetic-b"))
                ]
            }
        elif path.startswith("/v3/runs/"):
            payload = {"runs": [{"express_stop_count": 0}]}
        elif path.startswith("/v3/disruptions/"):
            payload = {"disruptions": {}}
        else:
            raise AssertionError("Unexpected synthetic PTV endpoint")
        return Response(payload, 200)

    aiohttp.ClientSession._request = request
