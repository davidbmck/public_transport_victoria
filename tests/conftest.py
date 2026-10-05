"""Shared fixtures for real Home Assistant tests with synthetic PTV responses."""

import json
import logging
import re
from pathlib import Path

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.public_transport_victoria.const import DOMAIN

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
PTV_ORIGIN = "https://timetableapi.ptv.vic.gov.au"
SYNTHETIC_API_KEY = "synthetic-test-key-not-a-credential"


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Load this checkout through Home Assistant's custom integration loader."""


@pytest.fixture
def load_json_fixture():
    """Load a fresh fixture object for each mocked response."""

    def load(name):
        return json.loads((FIXTURE_ROOT / name).read_text())

    return load


@pytest.fixture
def config_entry_factory():
    """Make existing version-1 entries without any alert configuration fields."""

    def create(*, direction="1", direction_name="Example City", **changes):
        data = {
            "id": "12345",
            "api_key": SYNTHETIC_API_KEY,
            "route_type": "0",
            "route": "9001",
            "direction": direction,
            "stop": "8001",
            "route_type_name": "Train",
            "route_name": "Example Metro",
            "direction_name": direction_name,
            "stop_name": "Example Station",
        }
        data.update(changes)
        return MockConfigEntry(
            domain=DOMAIN,
            version=1,
            data=data,
            title=f"Example Metro line to {direction_name} from Example Station",
        )

    return create


@pytest.fixture
def ptv_responses(aioclient_mock, monkeypatch):
    """Mock only the public API boundary, independent of URL signatures."""

    async def request(session, method, url, **kwargs):
        return await aioclient_mock.match_request(method, url, **kwargs)

    # The inherited connector creates its own sessions rather than using HA's
    # shared session. Mock the HTTP boundary for both session sources.
    monkeypatch.setattr(aiohttp.ClientSession, "_request", request)

    def register(path, payload=None, *, query=None, status=200, exc=None):
        pattern = rf"^{re.escape(PTV_ORIGIN + path)}"
        for key, value in (query or {}).items():
            pattern += rf"(?=.*[?&]{re.escape(key)}={re.escape(str(value))}(?:&|$))"
        aioclient_mock.get(
            re.compile(pattern + r"(?:\?|$)"),
            json=payload,
            status=status,
            exc=exc,
        )

    return register


@pytest.fixture(autouse=True)
def safe_logs(caplog):
    """Keep authentication data out of captured logs across the suite."""
    caplog.set_level(logging.DEBUG)
    yield
    formatter = logging.Formatter()
    captured = "\n".join(
        formatter.format(record)
        for phase in ("setup", "call", "teardown")
        for record in caplog.get_records(phase)
    )
    for sensitive in (SYNTHETIC_API_KEY, "devid=", "signature="):
        assert sensitive not in captured
