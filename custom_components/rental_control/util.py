# SPDX-License-Identifier: Apache-2.0
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
import hashlib
import logging
import os
import re
import uuid
from typing import Any  # noqa: F401
from typing import Coroutine
from typing import Dict
from typing import List

from homeassistant.components.automation import DOMAIN as AUTO_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN
from homeassistant.components.input_datetime import DOMAIN as INPUT_DATETIME
from homeassistant.components.input_text import DOMAIN as INPUT_TEXT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.event import EventStateChangedData
from homeassistant.util import dt
from homeassistant.util import slugify

from .const import CONF_PATH
from .const import COORDINATOR
from .const import DEFAULT_PATH
from .const import DOMAIN
from .const import NAME

_LOGGER = logging.getLogger(__name__)


def add_call(
    hass: HomeAssistant,
    coro: List[Coroutine],
    domain: str,
    service: str,
    target: str,
    data: Dict[str, Any],
) -> List[Coroutine]:
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
    base_path = os.path.join(
        hass.config.path(), config_entry.data.get(CONF_PATH, DEFAULT_PATH)
    )
    rc_name_slug = slugify(config_entry.data.get(CONF_NAME))

    delete_folder(base_path, rc_name_slug)
    # It is possible that the path may not exist because of RCs not
    # being connected to Keymaster configurations
    if os.path.exists(base_path):
        if not os.listdir(base_path):
            os.rmdir(base_path)


def delete_folder(absolute_path: str, *relative_paths: str) -> None:
    """Recursively delete folder and all children files and folders (depth first)."""
    path = os.path.join(absolute_path, *relative_paths)

    # RC that doesn't manage a lock has no files to purge
    if not os.path.exists(path):
        return

    if os.path.isfile(path):
        os.remove(path)
    else:
        for file_or_dir in os.listdir(path):
            delete_folder(path, file_or_dir)
        os.rmdir(path)


async def async_fire_clear_code(coordinator, slot: int) -> None:
    """Fire a clear_code signal."""
    _LOGGER.debug(f"In async_fire_clear_code - slot: {slot}, name: {coordinator.name}")
    hass = coordinator.hass
    reset_entity = f"{INPUT_BOOLEAN}.reset_codeslot_{coordinator.lockname}_{slot}"

    if not coordinator.lockname:
        return

    # Make sure that the reset is already off before sending a turn on event
    await hass.services.async_call(
        domain=INPUT_BOOLEAN,
        service="turn_off",
        target={"entity_id": reset_entity},
        blocking=True,
    )
    await hass.services.async_call(
        domain=INPUT_BOOLEAN,
        service="turn_on",
        target={"entity_id": reset_entity},
        blocking=True,
    )


async def async_fire_set_code(coordinator, event, slot: int) -> None:
    """Set codes into a slot."""
    _LOGGER.debug(f"In async_fire_set_code - slot: {slot}")

    lockname: str = coordinator.lockname
    coro: List[Coroutine] = []

    if not lockname:
        return

    # Disable the slot, this should help avoid notices from Keymaster about
    # pin changes
    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_BOOLEAN,
        "turn_off",
        f"input_boolean.enabled_{lockname}_{slot}",
        {},
    )
    await asyncio.gather(*coro)

    coro.clear()

    # Load the slot data
    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_DATETIME,
        "set_datetime",
        f"input_datetime.end_date_{lockname}_{slot}",
        {"datetime": event.extra_state_attributes["end"]},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_DATETIME,
        "set_datetime",
        f"input_datetime.start_date_{lockname}_{slot}",
        {"datetime": event.extra_state_attributes["start"]},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_TEXT,
        "set_value",
        f"input_text.{lockname}_pin_{slot}",
        {"value": event.extra_state_attributes["slot_code"]},
    )

    if coordinator.event_prefix:
        prefix = f"{coordinator.event_prefix} "
    else:
        prefix = ""

    slot_name = f"{prefix}{event.extra_state_attributes['slot_name']}"

    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_TEXT,
        "set_value",
        f"input_text.{lockname}_name_{slot}",
        {"value": slot_name},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_BOOLEAN,
        "turn_on",
        f"input_boolean.daterange_{lockname}_{slot}",
        {},
    )

    # Make sure the reset bool is turned off
    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_BOOLEAN,
        "turn_off",
        f"input_boolean.reset_codeslot_{lockname}_{slot}",
        {},
    )

    # Update the slot details
    await asyncio.gather(*coro)

    # Turn on the slot
    coro.clear()
    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_BOOLEAN,
        "turn_on",
        f"input_boolean.enabled_{lockname}_{slot}",
        {},
    )

    await asyncio.gather(*coro)


async def async_fire_update_times(coordinator, event) -> None:
    """Update times on slot."""

    lockname: str = coordinator.lockname
    coro: List[Coroutine] = []
    slot_name: str = event.extra_state_attributes["slot_name"]
    slot = coordinator.event_overrides.get_slot_key_by_name(slot_name)

    if not slot or not lockname:
        return

    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_DATETIME,
        "set_datetime",
        f"input_datetime.end_date_{lockname}_{slot}",
        {"datetime": event.extra_state_attributes["end"]},
    )

    coro = add_call(
        coordinator.hass,
        coro,
        INPUT_DATETIME,
        "set_datetime",
        f"input_datetime.start_date_{lockname}_{slot}",
        {"datetime": event.extra_state_attributes["start"]},
    )

    # Update the slot details
    await asyncio.gather(*coro)


def get_event_names(rc) -> List[str]:
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
    event: EventStateChangedData,
) -> None:
    """Listener to track state changes of Keymaster input entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    lockname = coordinator.lockname

    if event.event_type != EVENT_STATE_CHANGED:
        return

    # we can get state changed storms when a slot (or multiple slots) clear and
    # a new code is set, put in a small sleep to let things settle
    await asyncio.sleep(0.1)

    entity_id = event.data["entity_id"]

    slot_num = int(entity_id.split("_")[-1])

    slot_code = hass.states.get(f"input_text.{lockname}_pin_{slot_num}")
    slot_name = hass.states.get(f"input_text.{lockname}_name_{slot_num}")
    start_time = hass.states.get(f"input_datetime.start_date_{lockname}_{slot_num}")
    end_time = hass.states.get(f"input_datetime.end_date_{lockname}_{slot_num}")

    _LOGGER.debug(f"updating overrides for {lockname} slot {slot_num}")
    await coordinator.update_event_overrides(
        slot_num,
        slot_code.as_dict()["state"],
        slot_name.as_dict()["state"],
        dt.parse_datetime(start_time.as_dict()["state"]),
        dt.parse_datetime(end_time.as_dict()["state"]),
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
