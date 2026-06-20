# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
##############################################################################
# COPYRIGHT 2022 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control utils."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from collections.abc import Sequence
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
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.components.persistent_notification import async_dismiss as pn_dismiss
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.util import dt
from homeassistant.util import slugify

from .const import CONF_PATH
from .const import COORDINATOR
from .const import DEFAULT_PATH
from .const import DOMAIN
from .const import EARLY_CHECKOUT_GRACE_MINUTES
from .const import NAME

_LOGGER = logging.getLogger(__name__)


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
    """Normalize a calendar UID for consistent comparison.

    Strips surrounding whitespace and converts empty strings
    to ``None`` so that all UID storage and comparison paths
    use an identical canonical form.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def check_gather_results(
    results: Sequence[object],
    context: str,
    logger: logging.Logger = _LOGGER,
) -> None:
    """Check asyncio.gather results for exceptions.

    Re-raises BaseException subclasses that should not be
    swallowed (CancelledError, SystemExit, KeyboardInterrupt)
    and logs ordinary Exception instances with traceback so
    failures are actionable from logs.
    """
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
    """Re-raise the first Exception captured by gather.

    Called after ``check_gather_results`` when the caller needs
    failures to propagate (e.g. for retry tracking).
    """
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
    # It is possible that the path may not exist because of RCs not
    # being connected to Keymaster configurations
    if base_path.exists():
        if not any(base_path.iterdir()):
            base_path.rmdir()


def delete_folder(absolute_path: str | Path, *relative_paths: str) -> None:
    """Recursively delete folder and all children files and folders (depth first)."""
    path = Path(absolute_path, *relative_paths)

    # RC that doesn't manage a lock has no files to purge
    if not path.exists():
        return

    if path.is_file():
        path.unlink()
    else:
        for child in path.iterdir():
            delete_folder(child)
        path.rmdir()


async def async_fire_clear_code(
    coordinator, slot: int, expected_name: str | None = None
) -> None:
    """Fire a clear_code signal."""
    _LOGGER.debug(
        "In async_fire_clear_code - slot: %s, name: %s", slot, coordinator.name
    )
    hass = coordinator.hass
    reset_entity = f"{BUTTON}.{coordinator.lockname}_code_slot_{slot}_reset"

    if not coordinator.lockname:
        return

    if (
        expected_name is not None
        and not coordinator.event_overrides.verify_slot_ownership(slot, expected_name)
    ):
        _LOGGER.warning(
            "Slot %d ownership verification failed for '%s'; aborting clear_code",
            slot,
            expected_name,
        )
        return

    try:
        # Reset the slot
        await hass.services.async_call(
            domain=BUTTON,
            service="press",
            target={"entity_id": reset_entity},
            blocking=True,
        )
    except Exception:
        escalated = coordinator.event_overrides.record_retry_failure(slot)
        if escalated:
            pn_create(
                hass,
                f"Slot {slot} clear command failed after repeated "
                f"retries. Manual intervention may be required.",
                title="Rental Control: Lock Command Failure",
                notification_id=f"rental_control_slot_{slot}_clear_failure",
            )
        raise

    # Give Keymaster time to propagate the state change
    await asyncio.sleep(0.5)

    # Verify the slot name was actually cleared
    name_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_name"
    name_state = hass.states.get(name_entity)
    if name_state is not None and name_state.state not in (
        "",
        "unknown",
        "unavailable",
    ):
        _LOGGER.warning(
            "Slot %d name '%s' persisted after reset; "
            "forcing name clear via text.set_value",
            slot,
            name_state.state,
        )
        try:
            await hass.services.async_call(
                domain=TEXT,
                service="set_value",
                target={"entity_id": name_entity},
                service_data={"value": ""},
                blocking=True,
            )
        except Exception:
            _LOGGER.exception(
                "Failed to force-clear name for slot %d",
                slot,
            )

    was_escalated = coordinator.event_overrides._escalated.get(slot, False)
    coordinator.event_overrides.record_retry_success(slot)
    if was_escalated:
        pn_dismiss(
            hass,
            notification_id=f"rental_control_slot_{slot}_clear_failure",
        )


def trim_name(name: str, max_length: int) -> str:
    """Trim a slot name to fit within *max_length* on a word boundary.

    Algorithm:
        1. Normalize whitespace (collapse runs, strip edges).
        2. If the result already fits, return it unchanged.
        3. Split on whitespace and accumulate words left-to-right,
           adding each word only if the running length plus a space
           separator plus the word length stays within *max_length*.
        4. If even the first word exceeds *max_length*, hard-truncate
           it to *max_length* characters.

    Args:
        name: The raw slot name string.
        max_length: Maximum allowed character length (inclusive).

    Returns:
        A trimmed string whose ``len()`` is ``<= max_length`` and
        that has no trailing whitespace.
    """
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
    """Return buffered start/end times for Keymaster date ranges.

    When a non-zero buffer is applied, bare ``date`` values are
    normalised via ``_ensure_datetime`` before arithmetic.  When
    both buffer values are zero the inputs are returned unchanged.
    """
    if not before_minutes and not after_minutes:
        return start, end
    dt_start = _ensure_datetime(start, coordinator)
    dt_end = _ensure_datetime(end, coordinator)
    if before_minutes:
        dt_start = dt_start - timedelta(minutes=before_minutes)
    if after_minutes:
        dt_end = dt_end + timedelta(minutes=after_minutes)
    return dt_start, dt_end


async def async_fire_set_code(coordinator, event, slot: int) -> None:
    """Set codes into a slot."""
    _LOGGER.debug("In async_fire_set_code - slot: %s", slot)
    _LOGGER.debug("Event: %s", event)
    _LOGGER.debug("Slot: %s", slot)

    lockname: str = coordinator.lockname
    coro: list[Coroutine] = []

    if not lockname:
        return

    if coordinator.event_prefix:
        prefix = f"{coordinator.event_prefix} "
    else:
        prefix = ""

    slot_name = f"{prefix}{event.extra_state_attributes['slot_name']}"

    if coordinator.trim_names:
        guest = event.extra_state_attributes["slot_name"]
        guest_max = coordinator.max_name_length - len(prefix)
        slot_name = f"{prefix}{trim_name(guest, guest_max)}"

    expected_name = event.extra_state_attributes["slot_name"]
    if not coordinator.event_overrides.verify_slot_ownership(slot, expected_name):
        _LOGGER.warning(
            "Slot %d ownership verification failed for '%s'; aborting set_code",
            slot,
            expected_name,
        )
        return

    try:
        # Disable the slot, this should help avoid notices from Keymaster
        # about pin changes
        coro = add_call(
            coordinator.hass,
            coro,
            SWITCH,
            "turn_off",
            f"{SWITCH}.{lockname}_code_slot_{slot}_enabled",
            {},
        )
        results = await asyncio.gather(*coro, return_exceptions=True)
        check_gather_results(results, "Lock slot operation")
        _raise_first_gather_exception(results)

        coro.clear()

        # Load the slot data
        # The new Keymaster requires that we enable date before we can set
        # anything
        await coordinator.hass.services.async_call(
            domain=SWITCH,
            service="turn_on",
            target={
                "entity_id": (
                    f"{SWITCH}.{lockname}_code_slot_{slot}_use_date_range_limits"
                )
            },
            blocking=True,
        )

        # Compute buffered validity window for Keymaster
        buffered_start, buffered_end = apply_buffer(
            event.extra_state_attributes["start"],
            event.extra_state_attributes["end"],
            coordinator.code_buffer_before,
            coordinator.code_buffer_after,
            coordinator,
        )

        coro = add_call(
            coordinator.hass,
            coro,
            DATETIME,
            "set_value",
            f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_end",
            {"datetime": buffered_end},
        )

        coro = add_call(
            coordinator.hass,
            coro,
            DATETIME,
            "set_value",
            f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_start",
            {"datetime": buffered_start},
        )

        coro = add_call(
            coordinator.hass,
            coro,
            TEXT,
            "set_value",
            f"{TEXT}.{lockname}_code_slot_{slot}_pin",
            {"value": event.extra_state_attributes["slot_code"]},
        )

        coro = add_call(
            coordinator.hass,
            coro,
            TEXT,
            "set_value",
            f"{TEXT}.{lockname}_code_slot_{slot}_name",
            {"value": slot_name},
        )
        results = await asyncio.gather(*coro, return_exceptions=True)
        check_gather_results(results, "Lock slot operation")
        _raise_first_gather_exception(results)

        # Turn on the slot
        coro.clear()
        coro = add_call(
            coordinator.hass,
            coro,
            SWITCH,
            "turn_on",
            f"{SWITCH}.{lockname}_code_slot_{slot}_enabled",
            {},
        )

        results = await asyncio.gather(*coro, return_exceptions=True)
        check_gather_results(results, "Lock slot operation")
        _raise_first_gather_exception(results)
    except Exception:
        escalated = coordinator.event_overrides.record_retry_failure(slot)
        if escalated:
            pn_create(
                coordinator.hass,
                f"Slot {slot} set command failed after repeated "
                f"retries. Manual intervention may be required.",
                title="Rental Control: Lock Command Failure",
                notification_id=f"rental_control_slot_{slot}_failure",
            )
        raise

    was_escalated = coordinator.event_overrides._escalated.get(slot, False)
    coordinator.event_overrides.record_retry_success(slot)
    if was_escalated:
        pn_dismiss(
            coordinator.hass,
            notification_id=f"rental_control_slot_{slot}_failure",
        )


async def async_fire_update_times(coordinator, event, slot: int) -> None:
    """Update times on slot."""

    lockname: str = coordinator.lockname
    coro: list[Coroutine] = []
    slot_name: str = event.extra_state_attributes["slot_name"]

    if not slot or not lockname:
        return

    if not coordinator.event_overrides.verify_slot_ownership(slot, slot_name):
        _LOGGER.warning(
            "Slot %d ownership verification failed for '%s'; aborting update_times",
            slot,
            slot_name,
        )
        return

    # Compute buffered validity window for Keymaster
    buffered_start, buffered_end = apply_buffer(
        event.extra_state_attributes["start"],
        event.extra_state_attributes["end"],
        coordinator.code_buffer_before,
        coordinator.code_buffer_after,
        coordinator,
    )

    coro = add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_end",
        {"datetime": buffered_end},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_start",
        {"datetime": buffered_start},
    )
    results = await asyncio.gather(*coro, return_exceptions=True)
    check_gather_results(results, "Lock slot operation")


def _ensure_datetime(value: date | datetime, rc) -> datetime:
    """Coerce a bare ``date`` to a timezone-aware ``datetime``.

    ``CalendarEvent`` may carry ``date`` values for all-day
    events.  Converting to midnight in the coordinator timezone
    prevents ``TypeError`` when comparing with ``datetime``
    override timestamps.  Falls back to UTC when no valid
    timezone is available.
    """
    if isinstance(value, datetime):
        return value
    tz = getattr(rc, "timezone", None)
    if not isinstance(tz, tzinfo):
        tz = dt.UTC
    return datetime.combine(value, time.min, tz)


class EventIdentity(NamedTuple):
    """Structured identity for a calendar event."""

    name: str
    start: datetime
    end: datetime
    uid: str | None


def get_event_identities(rc, calendar: list | None = None) -> list[EventIdentity]:
    """Get structured event identities for slot reconciliation.

    Returns name, time range, and UID for each event so that
    override cleanup can distinguish same-named events by their
    time windows and calendar UIDs.

    Bare ``date`` values are normalised to timezone-aware
    ``datetime`` at midnight so downstream overlap comparisons
    never mix types.
    """
    events = calendar if calendar is not None else rc.data
    if not events:
        return []
    identities: list[EventIdentity] = []
    for event in events:
        name = get_slot_name(
            event.summary,
            event.description or "",
            rc.event_prefix or "",
        )
        if name:
            uid = normalize_uid(event.uid if hasattr(event, "uid") else None)
            start = _ensure_datetime(event.start, rc)
            end = _ensure_datetime(event.end, rc)
            identities.append(EventIdentity(name, start, end, uid))
    return identities


def get_event_names(rc, calendar: list | None = None) -> list[str]:
    """Get the current event names from coordinator data.

    Delegates to ``get_event_identities`` so that filtering logic
    is maintained in a single place.
    """
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
    """Compute the earliest safe lock-code expiry time after early checkout.

    Returns ``now + grace_minutes`` when more than *grace_minutes*
    remain before *original_end*, otherwise returns *original_end*
    unchanged (the reservation is already about to expire naturally).

    Args:
        now: Current wall-clock time.
        original_end: The originally scheduled reservation end time.
        grace_minutes: Number of minutes to keep the code active after
            early checkout (default from ``EARLY_CHECKOUT_GRACE_MINUTES``).

    Returns:
        The computed expiry time, which is
        ``min(now + timedelta(minutes=grace_minutes), original_end)``.
    """
    return min(now + timedelta(minutes=grace_minutes), original_end)


def get_slot_name(summary: str, description: str, prefix: str) -> str | None:
    """Determine the name for a given slot / event."""

    # strip off any prefix if it's being used
    if prefix:
        p = re.compile(f"{prefix} (.*)")
        name = p.findall(summary)[0]
    else:
        name = summary

    # Blocked and Unavailable should not have anything
    p = re.compile("Not available|Blocked")
    if p.search(name):
        return None

    # Airbnb and VRBO
    if "Reserved" in name:
        # Airbnb
        if name == "Reserved":
            p = re.compile(r"([A-Z][A-Z0-9]{9})")
            if description:
                ret = p.search(description)  # type: Any
                if ret is not None:
                    return str(ret[0]).strip()
                else:
                    return None
            else:
                return None
        else:
            p = re.compile(r" - (.*)$")
            ret = p.findall(name)
            if len(ret):
                return str(ret[0]).strip()

    # Tripadvisor
    if "Tripadvisor" in name:
        p = re.compile(r"Tripadvisor.*: (.*)")
        ret = p.findall(name)
        if len(ret):
            return str(ret[0]).strip()

    # Booking.com
    if "CLOSED" in name:
        p = re.compile(r"\s*CLOSED - (.*)")
        ret = p.findall(name)
        if len(ret):
            return str(ret[0]).strip()

    # Guesty API
    p = re.compile(r"^Reservation (.*)")
    ret = p.findall(name)
    if len(ret):
        return str(ret[0]).strip()

    # Guesty
    p = re.compile(r"-(.*)-.*-")
    ret = p.findall(name)
    if len(ret):
        return str(ret[0]).strip()

    # Degenerative case, we can't figure it out at all, we'll just use the
    # name as is, this could cause duplicate slot names but this is likely
    # a custom calendar anyway
    return str(name).strip()


async def handle_state_change(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    event: Event[EventStateChangedData],
) -> None:
    """Listener to track state changes of Keymaster input entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname

    if not lockname or not coordinator.event_overrides:
        return

    # we can get state changed storms when a slot (or multiple slots) clear and
    # a new code is set, put in a small sleep to let things settle
    await asyncio.sleep(0.1)

    entity_id = event.data["entity_id"]

    _LOGGER.debug(
        "Handling state change for %s in %s with event: %s",
        entity_id,
        lockname,
        event,
    )

    slot_match = re.search(r"_code_slot_(\d+)_", entity_id)
    if slot_match is None:
        _LOGGER.warning("Could not extract slot number from entity_id: %s", entity_id)
        return
    slot_num = int(slot_match.group(1))

    if "_reset" in entity_id:
        _LOGGER.debug("Resetting overrides %s for %s.", slot_num, lockname)
        await coordinator.event_overrides.async_update(
            slot_num, "", "", dt.start_of_local_day(), dt.start_of_local_day()
        )
        return

    slot_state = hass.states.get(f"switch.{lockname}_code_slot_{slot_num}_enabled")
    _LOGGER.debug("Slot %s state: %s", slot_num, slot_state)
    if slot_state is None:
        return

    if slot_state.state != "on":
        _LOGGER.debug(
            "Slot %s is not enabled, skipping update for %s.",
            slot_num,
            lockname,
        )
        return

    slot_code = hass.states.get(f"text.{lockname}_code_slot_{slot_num}_pin")
    slot_name = hass.states.get(f"text.{lockname}_code_slot_{slot_num}_name")

    use_date_range = hass.states.get(
        f"switch.{lockname}_code_slot_{slot_num}_use_date_range_limits"
    )
    _LOGGER.debug("Use Date Range: %s", use_date_range)
    if use_date_range and use_date_range.state == "on":
        g_start_time = hass.states.get(
            f"datetime.{lockname}_code_slot_{slot_num}_date_range_start"
        )
        g_end_time = hass.states.get(
            f"datetime.{lockname}_code_slot_{slot_num}_date_range_end"
        )
    else:
        g_start_time = None
        g_end_time = None

    if slot_code is None:
        return
    slot_code_value = (
        "" if slot_code.state in ("unknown", "unavailable") else slot_code.state
    )
    if slot_name is None:
        return
    slot_name_value = (
        "" if slot_name.state in ("unknown", "unavailable") else slot_name.state
    )

    start_time = dt.start_of_local_day()
    end_time = dt.start_of_local_day()

    if g_start_time is not None:
        p_start_time = dt.parse_datetime(g_start_time.state)
        if p_start_time:
            start_time = p_start_time

    if g_end_time is not None:
        p_end_time = dt.parse_datetime(g_end_time.state)
        if p_end_time:
            end_time = p_end_time

    _LOGGER.debug(
        "updating overrides for %s slot %s. "
        "slot_name: '%s', slot_code: '%s', "
        "start_time: '%s', end_time: '%s'",
        lockname,
        slot_num,
        slot_name_value,
        slot_code_value,
        start_time,
        end_time,
    )
    # When trim_names is enabled the name in Keymaster is the shortened
    # display value.  Preserve the original untrimmed name already stored
    # in the override only when the Keymaster value matches the expected
    # trimmed form, so that manual/external name changes are honoured.
    if coordinator.trim_names and slot_name_value:
        existing = (
            coordinator.event_overrides.overrides.get(slot_num)
            if coordinator.event_overrides
            else None
        )
        if existing and existing["slot_name"]:
            prefix = f"{coordinator.event_prefix} " if coordinator.event_prefix else ""
            guest_max = coordinator.max_name_length - len(prefix)
            expected_trimmed = trim_name(existing["slot_name"], guest_max)
            # Strip prefix before comparing since overrides store
            # the guest-only portion.
            incoming_guest = (
                slot_name_value[len(prefix) :]
                if prefix and slot_name_value.startswith(prefix)
                else slot_name_value
            )
            if incoming_guest == expected_trimmed:
                # Prepend the prefix so that async_update's
                # _strip_prefix round-trip is idempotent.
                slot_name_value = prefix + existing["slot_name"]

    await coordinator.update_event_overrides(
        slot_num,
        slot_code_value,
        slot_name_value,
        start_time,
        end_time,
    )

    await coordinator.event_overrides.async_check_overrides(coordinator)


async def async_reload_package_platforms(hass: HomeAssistant) -> bool:
    """Reload package platforms to pick up any changes to package files."""
    _LOGGER.debug("In async_reload_package_platforms")
    for domain in [
        AUTO_DOMAIN,
    ]:
        try:
            await hass.services.async_call(domain, SERVICE_RELOAD, blocking=True)
        except ServiceNotFound:
            return False
    return True
