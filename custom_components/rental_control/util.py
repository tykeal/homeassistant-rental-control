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
import hashlib
import logging
from pathlib import Path
import re
from typing import Any
import uuid

from homeassistant.components.automation import DOMAIN as AUTO_DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON
from homeassistant.components.datetime import DOMAIN as DATETIME
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
from .const import NAME

_LOGGER = logging.getLogger(__name__)


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


async def async_fire_clear_code(coordinator, slot: int) -> None:
    """Fire a clear_code signal."""
    _LOGGER.debug(
        "In async_fire_clear_code - slot: %s, name: %s", slot, coordinator.name
    )
    hass = coordinator.hass
    reset_entity = f"{BUTTON}.{coordinator.lockname}_code_slot_{slot}_reset"

    if not coordinator.lockname:
        return

    # Reset the slot
    await hass.services.async_call(
        domain=BUTTON,
        service="press",
        target={"entity_id": reset_entity},
        blocking=True,
    )


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

    # Disable the slot, this should help avoid notices from Keymaster about
    # pin changes
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

    coro.clear()

    # Load the slot data
    # The new Keymaster requires that we enable date before we can set
    # anything
    await coordinator.hass.services.async_call(
        domain=SWITCH,
        service="turn_on",
        target={
            "entity_id": f"{SWITCH}.{lockname}_code_slot_{slot}_use_date_range_limits"
        },
        blocking=True,
    )

    coro = add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_end",
        {"datetime": event.extra_state_attributes["end"]},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"{DATETIME}.{lockname}_code_slot_{slot}_date_range_start",
        {"datetime": event.extra_state_attributes["start"]},
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
    # Update the slot details
    results = await asyncio.gather(*coro, return_exceptions=True)
    check_gather_results(results, "Lock slot operation")

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


async def async_fire_update_times(coordinator, event) -> None:
    """Update times on slot."""

    lockname: str = coordinator.lockname
    coro: list[Coroutine] = []
    slot_name: str = event.extra_state_attributes["slot_name"]
    slot = coordinator.event_overrides.get_slot_key_by_name(slot_name)

    if not slot or not lockname:
        return

    coro = add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_end",
        {"datetime": event.extra_state_attributes["end"]},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        DATETIME,
        "set_value",
        f"datetime.{lockname}_code_slot_{slot}_date_range_start",
        {"datetime": event.extra_state_attributes["start"]},
    )
    # Update the slot details
    results = await asyncio.gather(*coro, return_exceptions=True)
    check_gather_results(results, "Lock slot operation")


def get_event_names(rc) -> list[str]:
    """Get the current event names."""
    event_names = [
        e.extra_state_attributes["slot_name"]
        for e in rc.event_sensors
        if e.extra_state_attributes["slot_name"]
    ]
    return event_names


def gen_uuid(created: str) -> str:
    """Generation a UUID from the NAME and creation time."""
    m = hashlib.md5(f"{NAME} {created}".encode("utf-8"))
    return str(uuid.UUID(m.hexdigest()))


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
        coordinator.event_overrides.update(
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
    await coordinator.update_event_overrides(
        slot_num,
        slot_code_value,
        slot_name_value,
        start_time,
        end_time,
    )

    # validate overrides
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
