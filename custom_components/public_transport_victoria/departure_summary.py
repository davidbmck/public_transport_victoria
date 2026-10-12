"""Select upcoming services from the fetched timetable, without additional I/O."""

from dataclasses import dataclass
from datetime import datetime

from homeassistant.util import dt as dt_util


def parse_departure_time(value):
    """Accept only valid timezone-aware timestamps and normalize to UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = dt_util.parse_datetime(value)
        if parsed is not None and parsed.utcoffset() is not None:
            return dt_util.as_utc(parsed)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def service_identity(record):
    """Prefer a meaningful run reference, then a positive numeric run ID.

    Missing/sentinel identifiers do not establish identity. In particular, an
    unknown record must not be deduplicated against another unknown record.
    """
    reference = record.get("run_ref")
    if isinstance(reference, str):
        reference = reference.strip()
        if reference and reference not in {"-1", "0"}:
            return ("run_ref", reference)
    run_id = record.get("run_id")
    if type(run_id) is int and run_id > 0:
        return ("run_id", str(run_id))
    if isinstance(run_id, str) and run_id.isascii() and run_id.isdecimal():
        # Strip leading zeroes without converting arbitrarily large numbers.
        normalized = run_id.lstrip("0")
        if normalized:
            return ("run_id", normalized)
    return None


@dataclass(frozen=True)
class DepartureSelection:
    """The effective timestamp and small public metadata for one record."""

    timestamp: datetime
    scheduled: datetime | None
    estimated: datetime | None
    identity: tuple[str, str] | None
    run_ref: str | int | None
    run_id: str | int | None

    @property
    def attributes(self):
        """Expose validated times and original scalar identifiers."""
        return {
            "service_reference": self.identity[1] if self.identity else None,
            "scheduled_departure_utc": self.scheduled.isoformat()
            if self.scheduled
            else None,
            "estimated_departure_utc": self.estimated.isoformat()
            if self.estimated
            else None,
            "time_source": "estimated" if self.estimated else "scheduled",
            "run_ref": self.run_ref,
            "run_id": self.run_id,
        }


def select_departures(records, now):
    """Return the next two distinct services with effective time strictly > now.

    Equal times sort by known identity (type/value), then API record position.
    Duplicate known identities use their earliest qualifying record. Unknown
    identities remain separate candidates; distinctness cannot be established.
    """
    candidates = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        scheduled = parse_departure_time(record.get("scheduled_departure_utc"))
        estimated = parse_departure_time(record.get("estimated_departure_utc"))
        effective = estimated or scheduled
        if effective is None or effective <= now:
            continue
        identity = service_identity(record)
        selection = DepartureSelection(
            effective,
            scheduled,
            estimated,
            identity,
            record.get("run_ref")
            if type(record.get("run_ref")) in (str, int)
            else None,
            record.get("run_id") if type(record.get("run_id")) in (str, int) else None,
        )
        candidates.append((effective, identity or ("unknown", ""), index, selection))
    candidates.sort(key=lambda candidate: candidate[:3])
    selected = []
    seen = set()
    for _, _, _, selection in candidates:
        if selection.identity is not None:
            if selection.identity in seen:
                continue
            seen.add(selection.identity)
        selected.append(selection)
        if len(selected) == 2:
            break
    return tuple(selected + [None] * (2 - len(selected)))
