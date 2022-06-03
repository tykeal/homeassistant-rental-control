"""Services for RentalControl"""
from __future__ import annotations

import logging
from datetime import date
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def update_code_slot(
    hass: HomeAssistant,
    service: ServiceCall,
) -> None:
    """Update RentalControl with start and end times of a given slot."""
    _LOGGER.debug("In update_code_slot")
    _LOGGER.debug("Service: '%s'", service)

    if "lockname" in service.data:
        lockname = service.data["lockname"]
    else:
        lockname = None

    if "slot" in service.data:
        slot = service.data["slot"]
    else:
        slot = 0

    if "slot_code" in service.data and service.data["slot_code"]:
        slot_code = service.data["slot_code"]
    else:
        slot_code = None

    if "slot_name" in service.data and service.data["slot_name"]:
        slot_name = service.data["slot_name"]
    else:
        slot_name = None

    if "start_time" in service.data:
        start_time = service.data["start_time"]
    else:
        dt = date.today()
        start_time = datetime.combine(dt, datetime.min.time())

    if "end_time" in service.data:
        end_time = service.data["end_time"]
    else:
        dt = date.today()
        end_time = datetime.combine(dt, datetime.min.time())

    # Return on bad data or nothing to do
    if slot == 0 or not lockname:
        _LOGGER.debug("Nothing to do")
        return None

    # Search for which device
    for uid in hass.data[DOMAIN]:
        rc = hass.data[DOMAIN][uid]
        _LOGGER.debug(
            """rc.start_slot: '%s'
            rc.max_events: '%s'
            combined: '%s'
            name: '%s'
            rc.lockname: '%s'""",
            rc.start_slot,
            rc.max_events,
            rc.start_slot + rc.max_events,
            rc.name,
            rc.lockname,
        )
        if (
            slot >= rc.start_slot
            and slot < rc.start_slot + rc.max_events
            and lockname == rc.lockname
        ):
            _LOGGER.debug("rc for slot: '%s', calling update_event_overrides", rc.name)
            await rc.update_event_overrides(
                slot, slot_code, slot_name, start_time, end_time
            )
            break
