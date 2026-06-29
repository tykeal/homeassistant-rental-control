# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Generic Rental Control helper behavior."""

from __future__ import annotations

from collections.abc import Coroutine
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import tzinfo
import hashlib
import logging
from pathlib import Path
import re
from typing import Any
from typing import NamedTuple
from typing import cast
import uuid

from homeassistant.components.automation import DOMAIN as AUTO_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import SERVICE_RELOAD
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util import dt
from homeassistant.util import slugify

from .const import CONF_PATH
from .const import DEFAULT_PATH
from .const import DOMAIN
from .const import EARLY_CHECKOUT_GRACE_MINUTES
from .const import NAME

_LOGGER = logging.getLogger(__name__)
_CLEARED_KEYMASTER_TEXT_STATES = frozenset(("", str(STATE_UNKNOWN).casefold(), "none"))
_UNREADABLE_KEYMASTER_TEXT_STATE = str(STATE_UNAVAILABLE).casefold()


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of a physical Keymaster slot service operation."""

    kind: str
    slot: int
    confirmed: bool = False
    unconfirmed: bool = False
    failed: bool = False
    lingering_name: bool = False
    lingering_pin: bool = False
    error: str | None = None


class EventIdentity(NamedTuple):
    """Structured identity for a calendar event."""

    name: str
    start: datetime
    end: datetime
    uid: str | None


def _keymaster_text_state_token(value: Any) -> str | None:
    """Return a canonical comparison token for a Keymaster text state."""
    if value is None:
        return None
    return str(value).strip().casefold()


def is_cleared_keymaster_text_state(value: Any) -> bool:
    """Return whether a Keymaster text state is confirmed cleared."""
    token = _keymaster_text_state_token(value)
    return token is None or token in _CLEARED_KEYMASTER_TEXT_STATES


def is_unreadable_keymaster_text_state(value: Any) -> bool:
    """Return whether a Keymaster text state is unreadable."""
    return _keymaster_text_state_token(value) == _UNREADABLE_KEYMASTER_TEXT_STATE


def normalize_keymaster_text_state(value: Any) -> str | None:
    """Return normalized text state, or ``None`` when it is unreadable."""
    if is_unreadable_keymaster_text_state(value):
        return None
    if is_cleared_keymaster_text_state(value):
        return ""
    return str(value)


def get_entry_data(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    """Return Rental Control entry data when domain and entry data exist."""
    domain_data = cast("dict[str, dict[str, Any]] | None", hass.data.get(DOMAIN))
    if domain_data is None:
        return None
    entry_data = domain_data.get(entry_id)
    if entry_data is None:
        return None
    return entry_data


def normalize_uid(value: str | None) -> str | None:
    """Normalize a calendar UID for consistent comparison."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def check_gather_results(
    results: Sequence[object],
    context: str,
    logger: logging.Logger = _LOGGER,
) -> None:
    """Check asyncio.gather results for exceptions."""
    for result in results:
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            logger.error(
                "%s failed: %s",
                context,
                result,
                exc_info=(type(result), result, result.__traceback__),
            )


def _raise_first_gather_exception(results: Sequence[object]) -> None:
    """Re-raise the first Exception captured by gather."""
    for result in results:
        if isinstance(result, Exception):
            raise result


def add_call(
    hass: HomeAssistant,
    coro: list[Coroutine],
    domain: str,
    service: str,
    target: str,
    data: dict[str, Any],
) -> list[Coroutine]:
    """Append a new async_call to the coro list."""
    coro.append(
        hass.services.async_call(
            domain=domain,
            service=service,
            target={"entity_id": target},
            service_data=data,
            blocking=True,
        )
    )
    return coro


def delete_rc_and_base_folder(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Delete packages folder for RC and base rental_control folder if empty."""
    base_path = Path(hass.config.path(), config_entry.data.get(CONF_PATH, DEFAULT_PATH))
    rc_name_slug = slugify(config_entry.data.get(CONF_NAME))
    delete_folder(base_path, rc_name_slug)
    if base_path.exists() and not any(base_path.iterdir()):
        base_path.rmdir()


def delete_folder(absolute_path: str | Path, *relative_paths: str) -> None:
    """Recursively delete folder and all children files and folders."""
    path = Path(absolute_path, *relative_paths)
    if not path.exists():
        return
    if path.is_file():
        path.unlink()
        return
    for child in path.iterdir():
        delete_folder(child)
    path.rmdir()


def trim_name(name: str, max_length: int) -> str:
    """Trim a slot name to fit within *max_length* on a word boundary."""
    name = " ".join(name.split())
    if len(name) <= max_length:
        return name
    words = name.split()
    if len(words[0]) > max_length:
        return words[0][:max_length]
    result: list[str] = [words[0]]
    current_length = len(words[0])
    for word in words[1:]:
        needed = current_length + 1 + len(word)
        if needed <= max_length:
            result.append(word)
            current_length = needed
        else:
            break
    return " ".join(result)


def apply_buffer(
    start: date | datetime,
    end: date | datetime,
    before_minutes: int,
    after_minutes: int,
    coordinator: object,
) -> tuple[date | datetime, date | datetime]:
    """Return buffered start/end times for Keymaster date ranges."""
    if not before_minutes and not after_minutes:
        return start, end
    dt_start = _ensure_datetime(start, coordinator)
    dt_end = _ensure_datetime(end, coordinator)
    if before_minutes:
        dt_start = dt_start - timedelta(minutes=before_minutes)
    if after_minutes:
        dt_end = dt_end + timedelta(minutes=after_minutes)
    return dt_start, dt_end


def _ensure_datetime(value: str | date | datetime, rc) -> datetime:
    """Coerce a bare ``date`` to a timezone-aware ``datetime``."""
    tz = getattr(rc, "timezone", None)
    if not isinstance(tz, tzinfo):
        tz = dt.UTC
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value
    if isinstance(value, str):
        parsed = dt.parse_datetime(value)
        if parsed is not None:
            parsed_dt = cast("datetime", parsed)
            if parsed_dt.tzinfo is None:
                return parsed_dt.replace(tzinfo=tz)
            return parsed_dt
        parsed_date = dt.parse_date(value)
        if parsed_date is not None:
            value = cast("date", parsed_date)
        else:
            msg = f"Cannot coerce {value!r} to datetime"
            raise ValueError(msg)
    if not isinstance(value, date):
        msg = f"Cannot coerce {value!r} to datetime"
        raise ValueError(msg)
    return datetime.combine(value, time.min, tz)


def get_event_identities(rc, calendar: list | None = None) -> list[EventIdentity]:
    """Get structured event identities for slot reconciliation."""
    events = calendar if calendar is not None else rc.data
    if not events:
        return []
    identities: list[EventIdentity] = []
    for event in events:
        name = get_slot_name(
            event.summary, event.description or "", rc.event_prefix or ""
        )
        if name:
            uid = normalize_uid(event.uid if hasattr(event, "uid") else None)
            identities.append(
                EventIdentity(
                    name,
                    _ensure_datetime(event.start, rc),
                    _ensure_datetime(event.end, rc),
                    uid,
                )
            )
    return identities


def get_event_names(rc, calendar: list | None = None) -> list[str]:
    """Get the current event names from coordinator data."""
    return [eid.name for eid in get_event_identities(rc, calendar=calendar)]


def gen_uuid(created: str) -> str:
    """Generation a UUID from the NAME and creation time."""
    m = hashlib.md5(f"{NAME} {created}".encode("utf-8"))
    return str(uuid.UUID(m.hexdigest()))


def compute_early_expiry_time(
    now: datetime,
    original_end: datetime,
    grace_minutes: int = EARLY_CHECKOUT_GRACE_MINUTES,
) -> datetime:
    """Compute the earliest safe lock-code expiry time after early checkout."""
    return min(now + timedelta(minutes=grace_minutes), original_end)


def get_slot_name(summary: str, description: str, prefix: str) -> str | None:
    """Determine the name for a given slot / event."""
    if prefix:
        name = re.compile(f"{prefix} (.*)").findall(summary)[0]
    else:
        name = summary
    if re.compile("Not available|Blocked").search(name):
        return None
    if "Reserved" in name:
        reserved_name = _slot_name_from_reserved(name, description)
        if name == "Reserved" or reserved_name is not None:
            return reserved_name
    for pattern in (
        r"Tripadvisor.*: (.*)",
        r"\s*CLOSED - (.*)",
        r"^Reservation (.*)",
        r"-(.*)-.*-",
    ):
        ret = re.compile(pattern).findall(name)
        if len(ret):
            return str(ret[0]).strip()
    return str(name).strip()


def _slot_name_from_reserved(name: str, description: str) -> str | None:
    """Return a slot name parsed from Reserved event text when applicable."""
    if name == "Reserved":
        if not description:
            return None
        match = re.compile(r"([A-Z][A-Z0-9]{9})").search(description)
        return str(match[0]).strip() if match is not None else None
    matches = re.compile(r" - (.*)$").findall(name)
    if len(matches):
        return str(matches[0]).strip()
    return None


async def async_reload_package_platforms(hass: HomeAssistant) -> bool:
    """Reload package platforms to pick up any changes to package files."""
    _LOGGER.debug("In async_reload_package_platforms")
    for domain in [AUTO_DOMAIN]:
        try:
            await hass.services.async_call(domain, SERVICE_RELOAD, blocking=True)
        except ServiceNotFound:
            return False
    return True
