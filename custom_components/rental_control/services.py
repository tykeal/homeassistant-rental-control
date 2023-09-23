"""Services for RentalControl"""
from __future__ import annotations

import logging
import os
from pprint import pformat

from homeassistant.components.persistent_notification import create
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall
from homeassistant.util import dt
from homeassistant.util import slugify

from .const import COORDINATOR
from .const import DOMAIN
from .const import NAME
from .util import async_reload_package_platforms
from .util import write_template_config

# from .util import reload_package_platforms

_LOGGER = logging.getLogger(__name__)


async def generate_package_files(hass: HomeAssistant, rc_name: str) -> None:
    """Generate the package files."""
    _LOGGER.debug("In generate_package_files: '%s'", rc_name)

    coordinator = None
    for entry_id in hass.data[DOMAIN]:
        if hass.data[DOMAIN][entry_id][COORDINATOR].name == rc_name:
            coordinator = hass.data[DOMAIN][entry_id][COORDINATOR]
            break

    _LOGGER.debug("config_entry is '%s'", coordinator)
    _LOGGER.debug(pformat(coordinator.__dict__))
    if not coordinator:
        raise ValueError(f"Couldn't find existing Rental Control entry for {rc_name}")

    rc_name_slug = slugify(rc_name)

    _LOGGER.debug("Starting file generation...")

    create(
        hass,
        (
            f"Package file genreation for `{rc_name}` has started. Once complete, "
            "we will attempt to automatically update Home Assistant to avoid "
            "requiring a full restart."
        ),
        title=f"{NAME} {rc_name} - Starting file generation",
    )

    output_path = os.path.join(hass.config.path(), coordinator.path, rc_name_slug)

    # If packages folder exists, delete it so we can recreate it
    if os.path.isdir(output_path):
        _LOGGER.debug("Directory %s already exists, cleaning it up", output_path)
        for file in os.listdir(output_path):
            os.remove(os.path.join(output_path, file))
    else:
        _LOGGER.debug("Creating pacakges directory %s", output_path)
        try:
            os.makedirs(output_path)
        except Exception as err:
            _LOGGER.critical("Error creating directory: %s", str(err))

    _LOGGER.debug("Packages directory is ready for file generation")

    templates = ["set_code"]

    for t in templates:
        write_template_config(output_path, t, NAME, rc_name, coordinator)

    platform_reloaded = await async_reload_package_platforms(hass)

    if platform_reloaded:
        # if reload_package_platforms(hass):
        create(
            hass,
            (
                f"Package generation for `{rc_name}` complete!\n\n"
                "All changes have beena automatically applied, so no restat is needed."
            ),
            title=f"{NAME} {rc_name} - Package file generation complete!",
        )
        _LOGGER.debug(
            "Package generation complete and all changes have been hot reloaded"
        )
    else:
        create(
            hass,
            (
                f"Package generation for `{rc_name}` complete!\n\n"
                "Changes couldn't be automatically applied, so a Home Assistant "
                "restart is needed to fully apply the changes."
            ),
            title=f"{NAME} {rc_name} - Package file generation complete!",
        )
        _LOGGER.debug("Package generation complete, Home Assistant restart needed")


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
        start_time = dt.parse_datetime(service.data["start_time"])
    else:
        start_time = dt.start_of_local_day()

    if "end_time" in service.data:
        end_time = dt.parse_datetime(service.data["end_time"])
    else:
        end_time = dt.start_of_local_day()

    # Return on bad data or nothing to do
    if slot == 0 or not lockname:
        _LOGGER.debug("Nothing to do")
        return None

    # Search for which device
    for uid in hass.data[DOMAIN]:
        rc = hass.data[DOMAIN][uid][COORDINATOR]
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
