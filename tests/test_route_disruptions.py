"""Exercise the route disruption client with the agreed synthetic contract."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.public_transport_victoria.PublicTransportVictoria import (
    public_transport_victoria as ptv_api,
)
from custom_components.public_transport_victoria.PublicTransportVictoria import (
    route_disruptions as disruptions,
)

from .conftest import FIXTURE_ROOT, SYNTHETIC_API_KEY

CASES = json.loads((FIXTURE_ROOT / "route_alerts/cases.json").read_text())["cases"]
SCALAR_FIELDS = {
    "disruption_id",
    "title",
    "description",
    "url",
    "disruption_status",
    "disruption_type",
    "published_on",
    "last_updated",
    "from_date",
    "to_date",
}
PATH = "/v3/disruptions/route/9001"


@pytest.fixture
def connector(hass):
    """No departure initialization is needed for fetching route notices."""
    return ptv_api.Connector(hass, "12345", SYNTHETIC_API_KEY, route="9001")


def assert_source_preserved(notice, source):
    """Check supplied metadata and the contract's mandatory missing-field keys."""
    assert SCALAR_FIELDS | {"routes", "stops", "categories"} <= notice.keys()
    for field in SCALAR_FIELDS:
        assert notice[field] == source.get(field)
        assert type(notice[field]) is type(source.get(field))
    for field, value in source.items():
        if field == "categories":
            continue
        if field in ("routes", "stops") and value is None:
            assert notice[field] == []
        else:
            assert notice[field] == value
    for field in ("routes", "stops"):
        if field not in source:
            assert notice[field] == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
@pytest.mark.parametrize("time_zone", ["Australia/Melbourne", "UTC"])
async def test_contract_cases(
    hass,
    connector,
    ptv_responses,
    aioclient_mock,
    load_json_fixture,
    freezer,
    case,
    time_zone,
):
    """Consume every fixture expectation through the actual fetch method."""
    await hass.config.async_set_time_zone(time_zone)
    freezer.move_to(case["now"])
    payload = (
        load_json_fixture(f"route_alerts/{case['file']}")
        if "file" in case
        else deepcopy(case["payload"])
    )
    original = deepcopy(payload)
    ptv_responses(PATH, payload)
    if case["outcome"] == "failure":
        with pytest.raises(disruptions.RouteDisruptionsError):
            await connector.async_route_disruptions()
        return

    notices = await connector.async_route_disruptions()
    assert len(notices) == case["count"]
    assert [
        n["disruption_id"] if type(n["disruption_id"]) is int else None for n in notices
    ] == case["ids"]
    assert payload == original
    by_id = {n["disruption_id"]: n for n in notices if type(n["disruption_id"]) is int}
    assert not set(case.get("excluded_ids", [])) & by_id.keys()
    for identifier, title in case.get("selected_titles", {}).items():
        assert by_id[int(identifier)]["title"] == title
    for identifier, categories in case.get("categories", {}).items():
        assert by_id[int(identifier)]["categories"] == categories

    sources = [
        source
        for bucket in payload["disruptions"].values()
        if bucket
        for source in bucket
    ]
    for notice in notices:
        source = next(
            s
            for s in sources
            if (
                s.get("title") == notice["title"]
                and s.get("description") == notice["description"]
            )
        )
        assert_source_preserved(notice, source)

    if "anonymous_sources_in_order" in case:
        anonymous = [n for n in notices if type(n["disruption_id"]) is not int]
        for notice, source in zip(
            anonymous, case["anonymous_sources_in_order"], strict=True
        ):
            assert_source_preserved(notice, source)
        assert anonymous[1]["categories"] == ["general", "regional_train"]

    checks = case.get("checks", {})
    if "retained_categories" in checks:
        assert sorted({c for n in notices for c in n["categories"]}) == sorted(
            checks["retained_categories"]
        )
        for category, bucket in payload["disruptions"].items():
            assert by_id[bucket[0]["disruption_id"]]["categories"] == [category]
    if checks.get("preserve_null_direction"):
        assert notices[0]["routes"][0]["direction"] is None
    if checks.get("reverse_input_order_has_same_output"):
        reversed_payload = {
            "disruptions": {
                category: [
                    dict(reversed(list(source.items()))) for source in reversed(bucket)
                ]
                for category, bucket in reversed(list(payload["disruptions"].items()))
            }
        }
        aioclient_mock.clear_requests()
        ptv_responses(PATH, reversed_payload)
        assert await connector.async_route_disruptions() == notices


@pytest.mark.parametrize("health", [None, 1])
async def test_unspecified_or_healthy_api(connector, ptv_responses, health):
    ptv_responses(PATH, {"disruptions": {}, "status": {"health": health}})
    assert await connector.async_route_disruptions() == []


@pytest.mark.parametrize("health", [False, 1.0, "1", -1, 2, [], {}])
async def test_invalid_health(connector, ptv_responses, health):
    ptv_responses(PATH, {"disruptions": {}, "status": {"health": health}})
    with pytest.raises(disruptions.RouteDisruptionsError):
        await connector.async_route_disruptions()


@pytest.mark.parametrize(
    "payload",
    [
        {"disruptions": [], "status": {}},
        {"disruptions": {"general": False}},
        {"disruptions": {"general": "bad"}},
        {"disruptions": {"general": [42]}},
        {"disruptions": {"general": [{"routes": [False]}]}},
        {"disruptions": {"general": [{"stops": {}}]}},
    ],
)
async def test_additional_malformed_payloads(connector, ptv_responses, payload):
    ptv_responses(PATH, payload)
    with pytest.raises(disruptions.RouteDisruptionsError):
        await connector.async_route_disruptions()


@pytest.mark.parametrize(
    "end",
    [
        "2026-02-30T00:00:00Z",
        "2026-01-01T00:00:00+00:60",
        "2026-01-01T00:00:00+24:00",
        "0001-01-01T00:00:00+01:00",
        "2026-01-01",
        "2026-01-01T00:00:00",
        "not a date",
        "",
        False,
        42,
    ],
)
async def test_unknown_ends_remain_original(connector, ptv_responses, freezer, end):
    freezer.move_to("2026-10-05T00:00:00Z")
    ptv_responses(PATH, {"disruptions": {"general": [{"to_date": end}]}})
    notices = await connector.async_route_disruptions()
    assert len(notices) == 1
    assert notices[0]["to_date"] == end
    assert type(notices[0]["to_date"]) is type(end)


async def test_unicode_canonical_tie_and_input_ownership(connector, ptv_responses):
    """Unescaped Unicode determines the tie; nested outputs do not alias inputs."""
    payload = {
        "disruptions": {
            "general": [
                {"disruption_id": 1, "title": "é", "routes": [{"extra": {"a": 1}}]},
                {"title": "Z", "routes": [{"extra": {"a": 1}}], "disruption_id": 1},
            ]
        },
        "status": {},
    }
    original = deepcopy(payload)
    ptv_responses(PATH, payload)
    notices = await connector.async_route_disruptions()
    assert len(notices) == 1
    assert notices[0]["title"] == "é"
    notices[0]["routes"][0]["extra"]["a"] = 2
    assert payload == original


async def test_id_limits_and_unknown_metadata(hass, connector):
    """Signed int64 endpoints are usable; floats and overflow IDs are anonymous."""
    sources = [
        {"disruption_id": value, "title": str(value)}
        for value in [2**63, -(2**63), 2**63 - 1, -(2**63) - 1, 3.0]
    ]
    sources[1].update(
        {
            "categories": ["untrusted category"],
            "unknown": {"nested": ["retained"]},
            "routes": [{"route_id": 42, "extra": {"nested": True}}],
        }
    )
    # HA's mock encoder rejects integers outside signed/unsigned int64. Decode
    # a synthetic JSON body using aiohttp's standard decoder for this case.
    payload = {"disruptions": {"new_mode": sources, "general": deepcopy(sources)}}
    response = Mock(
        status=200, json=AsyncMock(return_value=json.loads(json.dumps(payload)))
    )
    context = AsyncMock()
    context.__aenter__.return_value = response
    with patch.object(async_get_clientsession(hass), "get", return_value=context):
        notices = await connector.async_route_disruptions()
    assert [n["disruption_id"] for n in notices] == [
        -(2**63),
        2**63 - 1,
        -(2**63) - 1,
        3.0,
        2**63,
    ]
    assert len(notices) == 5
    for notice in notices:
        assert notice["categories"] == ["general", "new_mode"]
        assert_source_preserved(
            notice, next(s for s in sources if s["title"] == notice["title"])
        )


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
async def test_http_failures(connector, ptv_responses, status):
    ptv_responses(PATH, {"disruptions": {}}, status=status)
    with pytest.raises(aiohttp.ClientResponseError) as error:
        await connector.async_route_disruptions()
    assert error.value.status == status


@pytest.mark.parametrize("error_type", [aiohttp.ClientConnectionError, TimeoutError])
async def test_transport_failure_and_recovery(
    connector, ptv_responses, aioclient_mock, error_type
):
    """Raw exceptions reach callers without being logged; the next fetch can recover."""
    ptv_responses(
        PATH, exc=error_type(f"{SYNTHETIC_API_KEY} devid=12345 signature=synthetic")
    )
    with pytest.raises(error_type):
        await connector.async_route_disruptions()
    aioclient_mock.clear_requests()
    ptv_responses(PATH, {"disruptions": {}})
    assert await connector.async_route_disruptions() == []
    aioclient_mock.clear_requests()
    ptv_responses(PATH, {"disruptions": {"general": [{"disruption_id": 1}]}})
    assert [n["disruption_id"] for n in await connector.async_route_disruptions()] == [
        1
    ]


async def test_shared_session_and_response_context(hass, connector):
    """Consume JSON in context with bounded waits; leave HA's session open."""
    session = async_get_clientsession(hass)
    response = Mock(status=200)
    inside = False

    async def enter():
        nonlocal inside
        inside = True
        return response

    async def leave(*args):
        nonlocal inside
        inside = False

    async def json_response():
        assert inside
        return {"disruptions": {}}

    response.json = AsyncMock(side_effect=json_response)
    context = AsyncMock()
    context.__aenter__.side_effect = enter
    context.__aexit__.side_effect = leave
    with patch.object(session, "get", return_value=context) as get:
        assert await connector.async_route_disruptions() == []
    assert not session.closed
    assert not inside
    response.raise_for_status.assert_called_once()
    context.__aexit__.assert_awaited_once()
    url = urlsplit(get.call_args.args[0])
    assert url.path == PATH
    assert set(parse_qs(url.query)) == {"devid", "signature"}
    timeout = get.call_args.kwargs["timeout"]
    assert timeout.total == 30
    assert 0 < timeout.connect <= timeout.total
    assert 0 < timeout.sock_connect <= timeout.total
    assert 0 < timeout.sock_read <= timeout.total
    assert not hasattr(connector, "departures")


@pytest.mark.parametrize(
    "failure", ["invalid_json", "content_type", "timeout", "cancelled"]
)
async def test_body_failure_releases_response(hass, connector, failure):
    """Body errors and cancellation release the response, keeping HA's session."""
    session = async_get_clientsession(hass)
    errors = {
        "invalid_json": json.JSONDecodeError("invalid JSON", "<html>", 0),
        "content_type": aiohttp.ContentTypeError(Mock(), (), message="wrong type"),
        "timeout": TimeoutError("synthetic body timeout"),
        "cancelled": asyncio.CancelledError(),
    }
    error = errors[failure]
    response = Mock(status=200, json=AsyncMock(side_effect=error))
    context = AsyncMock()
    context.__aenter__.return_value = response
    with patch.object(session, "get", return_value=context):
        with pytest.raises(type(error)):
            await connector.async_route_disruptions()
    context.__aexit__.assert_awaited_once()
    assert context.__aexit__.call_args.args[0] is type(error)
    assert not session.closed


async def test_successful_snapshot_expiry(connector, ptv_responses, freezer):
    """A later consumer can re-evaluate an already successful normalized result."""
    now = datetime.fromisoformat("2026-10-05T00:00:00Z")
    freezer.move_to(now)
    ptv_responses(
        PATH,
        {
            "disruptions": {
                "general": [
                    {"disruption_id": 1, "to_date": "2026-10-05T00:00:00Z"},
                    {"disruption_id": 2, "to_date": None},
                ]
            }
        },
    )
    notices = await connector.async_route_disruptions()
    original = deepcopy(notices)
    assert [n["disruption_id"] for n in notices] == [1, 2]
    assert [
        n["disruption_id"]
        for n in disruptions.non_expired_disruptions(
            notices, now=now + timedelta(microseconds=1)
        )
    ] == [2]
    assert notices == original
    with pytest.raises(ValueError, match="timezone-aware"):
        disruptions.non_expired_disruptions(notices, now=now.replace(tzinfo=None))
