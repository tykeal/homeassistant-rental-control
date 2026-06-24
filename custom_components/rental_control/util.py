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
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.components.persistent_notification import async_dismiss as pn_dismiss
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.components.text import DOMAIN as TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import SERVICE_RELOAD
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt
from homeassistant.util import slugify

from .const import CONF_PATH
from .const import COORDINATOR
from .const import DEFAULT_PATH
from .const import DOMAIN
from .const import EARLY_CHECKOUT_GRACE_MINUTES
from .const import NAME

_LOGGER = logging.getLogger(__name__)
_CLEARED_KEYMASTER_TEXT_STATES = frozenset(("", str(STATE_UNKNOWN).casefold(), "none"))
_UNREADABLE_KEYMASTER_TEXT_STATE = str(STATE_UNAVAILABLE).casefold()
_SET_CODE_CONFIRMATION_TIMEOUT = 5.0


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


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of a physical Keymaster slot service operation.

    Returned by ``async_fire_clear_code()``, ``async_fire_set_code()``,
    and ``async_fire_update_times()``. Successful helper paths set one
    of ``confirmed``, ``unconfirmed``, or ``failed`` to ``True``.
    ``lingering_name`` and ``lingering_pin`` are supplementary flags for
    clear operations. Raw PIN values are never stored.
    """

    kind: str
    slot: int
    confirmed: bool = False
    unconfirmed: bool = False
    failed: bool = False
    lingering_name: bool = False
    lingering_pin: bool = False
    error: str | None = None


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


def _state_matches_expected_name(
    hass: HomeAssistant, entity_id: str, name: str
) -> bool:
    """Return whether the entity state currently matches the expected name."""
    state = hass.states.get(entity_id)
    return state is not None and isinstance(state.state, str) and state.state == name


def _state_has_non_string_value(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether the entity exists with a non-HA state value."""
    state = hass.states.get(entity_id)
    return state is not None and not isinstance(state.state, str)


async def _async_wait_for_expected_name(
    hass: HomeAssistant, entity_id: str, name: str, timeout: float
) -> bool:
    """Wait briefly for one name entity to match the expected slot name."""
    if _state_matches_expected_name(hass, entity_id, name):
        return True
    if _state_has_non_string_value(hass, entity_id):
        return False

    matched = asyncio.Event()

    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        """Set the wait flag when the target entity reaches the expected name."""
        new_state = event.data.get("new_state")
        if new_state is not None and new_state.state == name:
            matched.set()

    unsub = async_track_state_change_event(hass, [entity_id], _handle_state_change)
    try:
        if _state_matches_expected_name(hass, entity_id, name):
            return True
        if _state_has_non_string_value(hass, entity_id):
            return False
        try:
            async with asyncio.timeout(timeout):
                await matched.wait()
        except TimeoutError:
            return False
        return _state_matches_expected_name(hass, entity_id, name)
    finally:
        unsub()


def _state_matches_expected_datetime(
    hass: HomeAssistant, entity_id: str, expected: datetime
) -> bool:
    """Return whether the entity state matches the expected datetime."""
    state = hass.states.get(entity_id)
    if state is None or not isinstance(state.state, str):
        return False
    parsed = dt.parse_datetime(state.state)
    return parsed is not None and dt.as_utc(parsed) == dt.as_utc(expected)


async def _async_wait_for_expected_datetime(
    hass: HomeAssistant, entity_id: str, expected: datetime, timeout: float
) -> bool:
    """Wait briefly for one datetime entity to match the expected value."""
    if _state_matches_expected_datetime(hass, entity_id, expected):
        return True

    matched = asyncio.Event()

    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        """Set the wait flag when the target entity reaches the expected time."""
        new_state = event.data.get("new_state")
        if new_state is None or not isinstance(new_state.state, str):
            return
        parsed = dt.parse_datetime(new_state.state)
        if parsed is not None and dt.as_utc(parsed) == dt.as_utc(expected):
            matched.set()

    unsub = async_track_state_change_event(hass, [entity_id], _handle_state_change)
    try:
        if _state_matches_expected_datetime(hass, entity_id, expected):
            return True
        try:
            async with asyncio.timeout(timeout):
                await matched.wait()
        except TimeoutError:
            return False
        return _state_matches_expected_datetime(hass, entity_id, expected)
    finally:
        unsub()


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
) -> OperationResult:
    """Fire a clear_code signal."""
    _LOGGER.debug(
        "In async_fire_clear_code - slot: %s, name: %s", slot, coordinator.name
    )
    hass = coordinator.hass
    reset_entity = f"{BUTTON}.{coordinator.lockname}_code_slot_{slot}_reset"

    if not coordinator.lockname:
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)

    if (
        expected_name is not None
        and not coordinator.event_overrides.verify_slot_ownership(slot, expected_name)
    ):
        _LOGGER.warning(
            "Slot %d ownership verification failed for '%s'; aborting clear_code",
            slot,
            expected_name,
        )
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)

    try:
        # Reset the slot
        await hass.services.async_call(
            domain=BUTTON,
            service="press",
            target={"entity_id": reset_entity},
            blocking=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        escalated = coordinator.event_overrides.record_retry_failure(slot)
        if escalated:
            pn_create(
                hass,
                f"Slot {slot} clear command failed after repeated "
                f"retries. Manual intervention may be required.",
                title="Rental Control: Lock Command Failure",
                notification_id=f"rental_control_slot_{slot}_clear_failure",
            )
        return OperationResult(
            kind="clear",
            slot=slot,
            failed=True,
            error=str(exc),
        )

    # Give Keymaster time to propagate the state change
    await asyncio.sleep(0.5)

    unconfirmed = False
    lingering_name = False
    lingering_pin = False
    name_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_name"
    pin_entity = f"{TEXT}.{coordinator.lockname}_code_slot_{slot}_pin"

    name_state = hass.states.get(name_entity)
    if name_state is None:
        unconfirmed = True
    elif is_unreadable_keymaster_text_state(name_state.state):
        unconfirmed = True
    elif not is_cleared_keymaster_text_state(name_state.state):
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
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Failed to force-clear name for slot %d",
                slot,
            )
        name_state = hass.states.get(name_entity)
        if name_state is None:
            unconfirmed = True
        elif is_unreadable_keymaster_text_state(name_state.state):
            unconfirmed = True
        elif not is_cleared_keymaster_text_state(name_state.state):
            lingering_name = True

    pin_state = hass.states.get(pin_entity)
    if pin_state is None:
        unconfirmed = True
    elif is_unreadable_keymaster_text_state(pin_state.state):
        unconfirmed = True
    elif not is_cleared_keymaster_text_state(pin_state.state):
        lingering_pin = True

    if lingering_name or lingering_pin:
        return OperationResult(
            kind="clear",
            slot=slot,
            unconfirmed=True,
            lingering_name=lingering_name,
            lingering_pin=lingering_pin,
        )
    if unconfirmed:
        return OperationResult(kind="clear", slot=slot, unconfirmed=True)

    was_escalated = coordinator.event_overrides._escalated.get(slot, False)
    coordinator.event_overrides.record_retry_success(slot)
    if was_escalated:
        pn_dismiss(
            hass,
            notification_id=f"rental_control_slot_{slot}_clear_failure",
        )
    return OperationResult(kind="clear", slot=slot, confirmed=True)


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


async def async_fire_set_code(coordinator, event, slot: int) -> OperationResult:
    """Set codes into a slot."""
    _LOGGER.debug("In async_fire_set_code - slot: %s", slot)
    _LOGGER.debug("Event: %s", event)
    _LOGGER.debug("Slot: %s", slot)

    lockname: str = coordinator.lockname
    coro: list[Coroutine] = []

    if not lockname:
        return OperationResult(kind="set", slot=slot, unconfirmed=True)

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
        return OperationResult(kind="set", slot=slot, unconfirmed=True)

    try:
        # Compute buffered validity window for Keymaster before mutating state.
        before = getattr(coordinator, "code_buffer_before", 0)
        after = getattr(coordinator, "code_buffer_after", 0)
        buffered_start, buffered_end = apply_buffer(
            event.extra_state_attributes["start"],
            event.extra_state_attributes["end"],
            before if isinstance(before, int) else 0,
            after if isinstance(after, int) else 0,
            coordinator,
        )
        buffered_start = _ensure_datetime(buffered_start, coordinator)
        buffered_end = _ensure_datetime(buffered_end, coordinator)
    except (TypeError, ValueError) as exc:
        return OperationResult(
            kind="set",
            slot=slot,
            failed=True,
            error=str(exc),
        )

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
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        escalated = coordinator.event_overrides.record_retry_failure(slot)
        if escalated:
            pn_create(
                coordinator.hass,
                f"Slot {slot} set command failed after repeated "
                f"retries. Manual intervention may be required.",
                title="Rental Control: Lock Command Failure",
                notification_id=f"rental_control_slot_{slot}_failure",
            )
        return OperationResult(
            kind="set",
            slot=slot,
            failed=True,
            error=str(exc),
        )

    name_entity = f"{TEXT}.{lockname}_code_slot_{slot}_name"
    if not await _async_wait_for_expected_name(
        coordinator.hass,
        name_entity,
        slot_name,
        _SET_CODE_CONFIRMATION_TIMEOUT,
    ):
        return OperationResult(kind="set", slot=slot, unconfirmed=True)

    was_escalated = coordinator.event_overrides._escalated.get(slot, False)
    coordinator.event_overrides.record_retry_success(slot)
    if was_escalated:
        pn_dismiss(
            coordinator.hass,
            notification_id=f"rental_control_slot_{slot}_failure",
        )
    return OperationResult(kind="set", slot=slot, confirmed=True)


async def async_fire_update_times(coordinator, event, slot: int) -> OperationResult:
    """Update times on slot."""

    lockname: str = coordinator.lockname
    coro: list[Coroutine] = []
    slot_name: str = event.extra_state_attributes["slot_name"]

    if not slot or not lockname:
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)

    if not coordinator.event_overrides.verify_slot_ownership(slot, slot_name):
        _LOGGER.warning(
            "Slot %d ownership verification failed for '%s'; aborting update_times",
            slot,
            slot_name,
        )
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)

    try:
        # Compute buffered validity window for Keymaster
        buffered_start, buffered_end = apply_buffer(
            event.extra_state_attributes["start"],
            event.extra_state_attributes["end"],
            coordinator.code_buffer_before,
            coordinator.code_buffer_after,
            coordinator,
        )
        buffered_start = _ensure_datetime(buffered_start, coordinator)
        buffered_end = _ensure_datetime(buffered_end, coordinator)
    except (TypeError, ValueError) as exc:
        return OperationResult(
            kind="update_times",
            slot=slot,
            failed=True,
            error=str(exc),
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
    try:
        _raise_first_gather_exception(results)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return OperationResult(
            kind="update_times",
            slot=slot,
            failed=True,
            error=str(exc),
        )
    start_entity_id = f"datetime.{lockname}_code_slot_{slot}_date_range_start"
    end_entity_id = f"datetime.{lockname}_code_slot_{slot}_date_range_end"
    start_confirmed, end_confirmed = await asyncio.gather(
        _async_wait_for_expected_datetime(
            coordinator.hass,
            start_entity_id,
            buffered_start,
            _SET_CODE_CONFIRMATION_TIMEOUT,
        ),
        _async_wait_for_expected_datetime(
            coordinator.hass,
            end_entity_id,
            buffered_end,
            _SET_CODE_CONFIRMATION_TIMEOUT,
        ),
    )
    if not start_confirmed or not end_confirmed:
        return OperationResult(kind="update_times", slot=slot, unconfirmed=True)
    return OperationResult(kind="update_times", slot=slot, confirmed=True)


def _ensure_datetime(value: str | date | datetime, rc) -> datetime:
    """Coerce a bare ``date`` to a timezone-aware ``datetime``.

    ``CalendarEvent`` may carry ``date`` values for all-day
    events.  Converting to midnight in the coordinator timezone
    prevents ``TypeError`` when comparing with ``datetime``
    override timestamps.  Falls back to UTC when no valid
    timezone is available.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = dt.parse_datetime(value)
        if parsed is not None:
            return cast("datetime", parsed)
        parsed_date = dt.parse_date(value)
        if parsed_date is not None:
            value = cast("date", parsed_date)
        else:
            msg = f"Cannot coerce {value!r} to datetime"
            raise ValueError(msg)
    if not isinstance(value, date):
        msg = f"Cannot coerce {value!r} to datetime"
        raise ValueError(msg)
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
    event_new_state = event.data.get("new_state")
    event_new_value = (
        getattr(event_new_state, "state", None) if event_new_state is not None else None
    )
    event_has_new_value = event_new_value is not None

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
    existing_override: dict[str, Any] | None = None
    if event_has_new_value and coordinator.event_overrides:
        existing_override = coordinator.event_overrides.overrides.get(slot_num)

    if "_reset" in entity_id:
        _LOGGER.debug("Resetting overrides %s for %s.", slot_num, lockname)
        await coordinator.event_overrides.async_update(
            slot_num, "", "", dt.start_of_local_day(), dt.start_of_local_day()
        )
        return

    if event_has_new_value and coordinator.event_overrides.should_suppress_state_change(
        slot_num, entity_id, event_new_value
    ):
        _LOGGER.debug(
            "Ignoring coordinator feedback for %s slot %s.",
            lockname,
            slot_num,
        )
        return

    slot_state = hass.states.get(f"switch.{lockname}_code_slot_{slot_num}_enabled")
    _LOGGER.debug("Slot %s state: %s", slot_num, slot_state)
    if slot_state is None:
        return

    slot_enabled_entity_id = f"switch.{lockname}_code_slot_{slot_num}_enabled"
    slot_enabled_state = (
        event_new_value
        if event_has_new_value and entity_id == slot_enabled_entity_id
        else slot_state.state
    )

    if slot_enabled_state != "on":
        _LOGGER.debug(
            "Slot %s is not enabled, skipping update for %s.",
            slot_num,
            lockname,
        )
        return

    slot_code_entity_id = f"text.{lockname}_code_slot_{slot_num}_pin"
    slot_name_entity_id = f"text.{lockname}_code_slot_{slot_num}_name"
    slot_code = hass.states.get(slot_code_entity_id)
    slot_name = hass.states.get(slot_name_entity_id)

    use_date_range_entity_id = (
        f"switch.{lockname}_code_slot_{slot_num}_use_date_range_limits"
    )
    use_date_range = hass.states.get(use_date_range_entity_id)
    _LOGGER.debug("Use Date Range: %s", use_date_range)
    use_date_range_state = (
        event_new_value
        if event_has_new_value and entity_id == use_date_range_entity_id
        else use_date_range.state
        if use_date_range
        else None
    )
    if use_date_range_state == "on":
        start_time_entity_id = (
            f"datetime.{lockname}_code_slot_{slot_num}_date_range_start"
        )
        end_time_entity_id = f"datetime.{lockname}_code_slot_{slot_num}_date_range_end"
        g_start_time = hass.states.get(start_time_entity_id)
        g_end_time = hass.states.get(end_time_entity_id)
    else:
        start_time_entity_id = ""
        end_time_entity_id = ""
        g_start_time = None
        g_end_time = None

    if slot_code is None:
        return
    if event_has_new_value and entity_id == slot_code_entity_id:
        slot_code_value = normalize_keymaster_text_state(event_new_value)
    elif event_has_new_value and existing_override is not None:
        slot_code_value = str(existing_override["slot_code"])
    else:
        slot_code_value = normalize_keymaster_text_state(slot_code.state)
    if slot_code_value is None:
        return
    if slot_name is None:
        return
    if event_has_new_value and entity_id == slot_name_entity_id:
        slot_name_value = normalize_keymaster_text_state(event_new_value)
    elif event_has_new_value and existing_override is not None:
        slot_name_value = str(existing_override["slot_name"])
    else:
        slot_name_value = normalize_keymaster_text_state(slot_name.state)
    if slot_name_value is None:
        return
    if slot_code_value and not slot_name_value:
        _LOGGER.warning(
            "Ignoring Keymaster slot %s state change with a code but no "
            "readable name; keeping the slot out of the free pool.",
            slot_num,
        )
        return

    start_time = dt.start_of_local_day()
    end_time = dt.start_of_local_day()
    if event_has_new_value and existing_override is not None:
        start_time = existing_override["start_time"]
        end_time = existing_override["end_time"]

    if g_start_time is not None:
        if event_has_new_value and entity_id == start_time_entity_id:
            p_start_time = dt.parse_datetime(event_new_value)
            if p_start_time:
                start_time = p_start_time
        elif not (event_has_new_value and existing_override is not None):
            p_start_time = dt.parse_datetime(g_start_time.state)
            if p_start_time:
                start_time = p_start_time

    if g_end_time is not None:
        if event_has_new_value and entity_id == end_time_entity_id:
            p_end_time = dt.parse_datetime(event_new_value)
            if p_end_time:
                end_time = p_end_time
        elif not (event_has_new_value and existing_override is not None):
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
    # Reconciliation runs exclusively from coordinator apply_plan (R-007);
    # callbacks must not launch reconciliation.


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
