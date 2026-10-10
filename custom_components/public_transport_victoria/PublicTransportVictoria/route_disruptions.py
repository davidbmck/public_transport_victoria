"""Validate and normalize route notices according to the route alert contract."""

import json
import re
from copy import deepcopy
from datetime import datetime, timezone

_SCALAR_FIELDS = (
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
)
_ABSOLUTE_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3])(?::?[0-5]\d)?)"
)
_UNKNOWN_TIME = datetime.min.replace(tzinfo=timezone.utc)


class RouteDisruptionsError(ValueError):
    """The API returned offline or malformed route disruption data."""


def _absolute_timestamp(value):
    """Return an aware UTC instant, leaving unknown values uninterpreted."""
    if not isinstance(value, str) or not _ABSOLUTE_TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _reference_instant(now):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Route disruption reference instant must be timezone-aware")
    return now.astimezone(timezone.utc)


def non_expired_disruptions(notices, *, now=None):
    """Re-evaluate a successful normalized snapshot without changing its records.

    This also supports expiry evaluation when a future coordinator reuses a
    successful snapshot. Never use it to present a failed refresh as healthy.
    """
    reference = _reference_instant(now)
    return [
        notice
        for notice in notices
        if (end := _absolute_timestamp(notice.get("to_date"))) is None
        or end >= reference
    ]


def parse_route_disruptions(payload, *, now=None):
    """Validate the whole response, select duplicates, normalize, then expire.

    Original objects are copied; unknown fields and malformed scalar values
    remain intact. Error messages contain neither response data nor credentials.
    """
    reference = _reference_instant(now)
    if not isinstance(payload, dict) or not isinstance(
        payload.get("disruptions"), dict
    ):
        raise RouteDisruptionsError("Route disruptions must be an object")
    if "status" in payload:
        status = payload["status"]
        if not isinstance(status, dict):
            raise RouteDisruptionsError("Route disruptions status must be an object")
        health = status.get("health")
        if health is not None:
            if type(health) is not int or health not in (0, 1):
                raise RouteDisruptionsError("Invalid route disruptions health")
            if health == 0:
                raise RouteDisruptionsError("Route disruptions API is offline")

    selected = {}
    for category, bucket in payload["disruptions"].items():
        if bucket is None:
            continue
        if not isinstance(bucket, list):
            raise RouteDisruptionsError("Route disruptions category must be an array")
        for source in bucket:
            if not isinstance(source, dict):
                raise RouteDisruptionsError("Route disruption notice must be an object")
            for field in ("routes", "stops"):
                coverage = source.get(field)
                if coverage is not None and (
                    not isinstance(coverage, list)
                    or any(not isinstance(item, dict) for item in coverage)
                ):
                    raise RouteDisruptionsError(
                        "Route disruption coverage must be object arrays"
                    )
            try:
                canonical = json.dumps(
                    source,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            except (TypeError, ValueError):
                raise RouteDisruptionsError(
                    "Route disruption is not valid JSON data"
                ) from None
            identifier = source.get("disruption_id")
            usable_id = type(identifier) is int and -(2**63) <= identifier < 2**63
            key = (0, identifier) if usable_id else (1, canonical)
            updated = _absolute_timestamp(source.get("last_updated"))
            published = _absolute_timestamp(source.get("published_on"))
            rank = (
                updated is not None,
                updated or _UNKNOWN_TIME,
                published is not None,
                published or _UNKNOWN_TIME,
                canonical,
            )
            if key not in selected:
                selected[key] = (rank, source, {category})
            else:
                old_rank, _, categories = selected[key]
                categories.add(category)
                if rank > old_rank:
                    selected[key] = (rank, source, categories)

    notices = []
    for key in sorted(selected):
        _, source, categories = selected[key]
        notice = deepcopy(source)
        for field in _SCALAR_FIELDS:
            notice.setdefault(field, None)
        for field in ("routes", "stops"):
            if notice.get(field) is None:
                notice[field] = []
        notice["categories"] = sorted(categories)
        notices.append(notice)
    return non_expired_disruptions(notices, now=reference)
