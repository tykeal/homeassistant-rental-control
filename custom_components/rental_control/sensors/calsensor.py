# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Creating sensors for upcoming events."""

from __future__ import annotations

from datetime import datetime
import logging
import random
import re
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import ICON
from ..const import SECONDS_PER_HOUR
from ..const import SECONDS_PER_MINUTE
from ..util import async_fire_clear_code
from ..util import async_fire_set_code
from ..util import async_fire_update_times
from ..util import gen_uuid
from ..util import get_slot_name

if TYPE_CHECKING:
    from ..coordinator import RentalControlCoordinator

_LOGGER = logging.getLogger(__name__)


class RentalControlCalSensor(CoordinatorEntity["RentalControlCoordinator"]):
    """
    Implementation of a iCal sensor.

    Represents the Nth upcoming event.
    May have a name like 'sensor.mycalander_event_0' for the first
    upcoming event.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: RentalControlCoordinator,
        sensor_name: str,
        event_number: int,
    ) -> None:
        """
        Initialize the sensor.

        sensor_name is typically the name of the calendar.
        eventnumber indicates which upcoming event this is, starting at zero
        """
        super().__init__(coordinator)
        if coordinator.event_prefix:
            summary = f"{coordinator.event_prefix} No reservation"
        else:
            summary = "No reservation"
        self._code_generator = coordinator.code_generator
        self._code_length = coordinator.code_length
        self._entity_category = EntityCategory.DIAGNOSTIC
        self._event_attributes: dict[str, Any] = {
            "summary": summary,
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "eta_days": None,
            "eta_hours": None,
            "eta_minutes": None,
            "slot_name": None,
            "slot_code": None,
        }
        self._parsed_attributes: dict[str, str] = {}
        self._event_number = event_number
        self._hass = hass
        self._name = f"{sensor_name} Event {self._event_number}"
        self._state = summary
        self._unique_id = gen_uuid(
            f"{self.coordinator.unique_id} sensor {self._event_number}"
        )

    async def async_added_to_hass(self) -> None:
        """Register listener and process existing coordinator data."""
        await super().async_added_to_hass()
        # The first coordinator refresh completes before sensors are
        # created, so the listener registered above will not fire until
        # the next scheduled refresh.  Process the data that is already
        # available so the sensor shows current events immediately.
        if self.coordinator.last_update_success and self.coordinator.data is not None:
            self._handle_coordinator_update()

    def _extract_email(self) -> str | None:
        """Extract guest email from a description"""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Email:\s+(\S+@\S+)""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        else:
            return None

    def _extract_last_four(self) -> str | None:
        """Extract the last 4 digits from a description."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""\(?Last 4 Digits\)?:\s+(\d{4})""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        elif "Phone" in self._event_attributes["description"]:
            phone = self._extract_phone_number()
            if phone:
                phone = phone.replace(" ", "")
                if len(phone) >= 4:
                    return str(phone)[-4:]

        return None

    def _extract_num_guests(self) -> str | None:
        """Extract the number of guests from a description."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Guests:\s+(\d+)$""", re.M)
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        elif "Adults" in self._event_attributes["description"]:
            guests = 0
            p = re.compile(r"""Adults:\s+(\d+)$""", re.M)
            ret = p.findall(self._event_attributes["description"])
            if ret:
                guests = int(ret[0])

            p = re.compile(r"""Children:\s+(\d+)$""", re.M)
            ret = p.findall(self._event_attributes["description"])
            if ret:
                guests += int(ret[0])

            if guests > 0:
                return str(guests)

        return None

    def _extract_phone_number(self) -> str | None:
        """Extract guest phone number from a description"""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})""")
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0]).strip()
        else:
            return None

    def _extract_url(self) -> str | None:
        """Extract reservation URL."""
        if self._event_attributes["description"] is None:
            return None
        p = re.compile(r"""(https?://.*$)""", re.M)
        ret = p.findall(self._event_attributes["description"])
        if ret:
            return str(ret[0])
        else:
            return None

    def _generate_door_code(self) -> str:
        """Generate a door code based upon the selected type."""

        generator = self._code_generator
        code_length = self._code_length

        # If there is no event description force date_based generation
        # This is because VRBO does not appear to provide any descriptions in
        # their calendar entries!
        # This also gets around Unavailable and Blocked entries that do not
        # have a description either
        if self._event_attributes["description"] is None:
            generator = "date_based"

        # AirBnB provides the last 4 digits of the guest's registered phone
        #
        # VRBO does not appear to provide any phone numbers
        #
        # Guesty provides last 4 + either a full number or all but last digit
        # for VRBO listings and doesn't appear to provide anything for AirBnB
        # listings, or if it does provide them, my example Guesty calendar doesn't
        # have any new enough to have the data
        #
        # TripAdvisor does not appear to provide any phone number data

        ret = None

        # Last 4 is only valid for code lengths of 4
        if generator == "last_four" and code_length == 4:
            ret = self._extract_last_four()
        elif generator == "static_random":
            # If the description changes this will most likely change the code
            random.seed(self._event_attributes["description"])
            max_range = int("9999".rjust(code_length, "9"))
            ret = str(random.randrange(1, max_range, code_length)).zfill(code_length)

        if ret is None:
            # Generate code based on checkin/out days
            #
            # This generator will have a side effect of changing the code
            # if the start or end dates shift!
            #
            # This is the default and fall back generator if no other
            # generator produced a code
            start_day = self._event_attributes["start"].strftime("%d")
            start_month = self._event_attributes["start"].strftime("%m")
            start_year = self._event_attributes["start"].strftime("%Y")
            end_day = self._event_attributes["end"].strftime("%d")
            end_month = self._event_attributes["end"].strftime("%m")
            end_year = self._event_attributes["end"].strftime("%Y")
            # This should be longer than anybody ever needs
            code = f"{start_day}{end_day}{start_month}{end_month}{start_year}{end_year}"
            # use a zfill in case the code really wasn't long enough for some
            # weird reason
            ret = (
                code[:code_length]
                if len(code) > code_length
                else code.zfill(code_length)
            )

        return ret

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info block."""
        return self.coordinator.device_info

    @property
    def entity_category(self) -> EntityCategory:
        """Return the entity category."""
        return self._entity_category

    @property
    def extra_state_attributes(self) -> dict:
        """Return the attributes of the event."""
        attrib = {**self._event_attributes, **self._parsed_attributes}
        return attrib

    @property
    def icon(self) -> str:
        """Return the icon for the frontend."""
        return ICON

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self) -> str:
        """Return the date of the next event."""
        return self._state

    @property
    def unique_id(self) -> str:
        """Return the unique_id."""
        return self._unique_id

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug(
            "Running RentalControlCalSensor coordinator update for %s",
            self.name,
        )

        # Not yet successful, skip processing
        if not self.coordinator.last_update_success:
            self.async_write_ha_state()
            return

        self._code_generator = self.coordinator.code_generator
        self._code_length = self.coordinator.code_length
        event_list = self.coordinator.data
        if event_list and (self._event_number < len(event_list)):
            event = event_list[self._event_number]
            name = event.summary
            start = event.start

            _LOGGER.debug(
                "Adding event %s - Start %s - End %s - as event %s to calendar %s",
                event.summary,
                event.start,
                event.end,
                str(self._event_number),
                self.name,
            )

            self._event_attributes["summary"] = event.summary
            self._event_attributes["start"] = event.start
            self._event_attributes["end"] = event.end
            self._event_attributes["location"] = event.location
            self._event_attributes["description"] = event.description
            # get timedelta for eta
            td = start - datetime.now(start.tzinfo)
            eta_days = None
            eta_hours = None
            eta_minutes = None
            if td.total_seconds() >= 0:
                eta_days = td.days
                eta_hours = round(td.total_seconds() // SECONDS_PER_HOUR)
                eta_minutes = round(td.total_seconds() // SECONDS_PER_MINUTE)

            self._event_attributes["eta_days"] = eta_days
            self._event_attributes["eta_hours"] = eta_hours
            self._event_attributes["eta_minutes"] = eta_minutes
            self._state = f"{name} - {start.day} {start.strftime('%B %Y')}"
            self._state += f" {start.strftime('%H:%M')}"
            slot_name = get_slot_name(
                self._event_attributes["summary"],
                self._event_attributes["description"],
                self.coordinator.event_prefix or "",
            )
            self._event_attributes["slot_name"] = slot_name

            slot_code = self._generate_door_code()

            # Read-only lookup for display: show existing override code
            # immediately rather than the generated fallback.  This is
            # safe because it is not used for slot-assignment decisions.
            overrides = self.coordinator.event_overrides
            if overrides and slot_name is not None:
                existing = overrides.get_slot_with_name(slot_name)
                if existing and existing["slot_code"]:
                    slot_code = str(existing["slot_code"])
            self._event_attributes["slot_code"] = slot_code

            # attributes parsed from description
            parsed_attributes = {}

            last_four = self._extract_last_four()
            if last_four is not None:
                parsed_attributes["last_four"] = last_four

            num_guests = self._extract_num_guests()
            if num_guests is not None:
                parsed_attributes["number_of_guests"] = num_guests

            guest_email = self._extract_email()
            if guest_email is not None:
                parsed_attributes["guest_email"] = guest_email

            phone_number = self._extract_phone_number()
            if phone_number is not None:
                parsed_attributes["phone_number"] = phone_number

            reservation_url = self._extract_url()
            if reservation_url is not None:
                parsed_attributes["reservation_url"] = reservation_url

            self._parsed_attributes = parsed_attributes

            # Schedule atomic slot assignment with a snapshot of
            # event data so the async task is immune to subsequent
            # coordinator updates mutating self._event_attributes.
            # Gate on overrides.ready to avoid spurious overflow
            # warnings during bootstrap when _next_slot is still None.
            if overrides and overrides.ready and slot_name is not None:
                uid: str | None = event.uid if hasattr(event, "uid") else None
                self.hass.async_create_task(
                    self._async_handle_slot_assignment(
                        slot_name=slot_name,
                        slot_code=slot_code,
                        start_time=event.start,
                        end_time=event.end,
                        uid=uid,
                        prefix=self.coordinator.event_prefix or "",
                        eta_days=eta_days,
                    )
                )

        else:
            # No reservations
            _LOGGER.debug(
                "No events available for sensor %s, removing from calendar %s",
                str(self._event_number),
                self.name,
            )
            if self.coordinator.event_prefix:
                summary = f"{self.coordinator.event_prefix} No reservation"
            else:
                summary = "No reservation"
            self._event_attributes = {
                "summary": summary,
                "description": None,
                "location": None,
                "start": None,
                "end": None,
                "eta_days": None,
                "eta_hours": None,
                "eta_minutes": None,
                "slot_name": None,
                "slot_code": None,
            }
            self._parsed_attributes = {}
            self._state = summary

        self.async_write_ha_state()

    async def _async_handle_slot_assignment(
        self,
        *,
        slot_name: str,
        slot_code: str,
        start_time: datetime,
        end_time: datetime,
        uid: str | None,
        prefix: str,
        eta_days: int | None,
    ) -> None:
        """Atomically reserve or locate an existing slot for the current event.

        All event data is captured at call-time so the coroutine is
        immune to subsequent coordinator updates that could mutate
        ``self._event_attributes`` before execution.

        Uses ``async_reserve_or_get_slot`` to eliminate the
        check-then-act race that previously existed between
        ``get_slot_with_name`` and ``next_slot`` reads.
        """
        overrides = self.coordinator.event_overrides
        if overrides is None:
            return

        result = await overrides.async_reserve_or_get_slot(
            slot_name=slot_name,
            slot_code=slot_code,
            start_time=start_time,
            end_time=end_time,
            uid=uid,
            prefix=prefix,
        )

        if result.slot is None:
            return

        # Staleness guard: a subsequent coordinator update may have
        # changed this sensor to a different event.  The reservation
        # itself is still valid, but Keymaster side effects must not
        # fire for a stale event.
        current_attrs = self._event_attributes
        if (
            current_attrs.get("slot_name") != slot_name
            or current_attrs.get("start") != start_time
            or current_attrs.get("end") != end_time
        ):
            _LOGGER.debug(
                "Slot assignment for '%s' is stale, skipping "
                "Keymaster side effects for sensor %s",
                slot_name,
                self.name,
            )
            return

        if result.is_new:
            await async_fire_set_code(self.coordinator, self, result.slot)
            return

        # Existing slot — update displayed code from the override
        override = overrides.overrides.get(result.slot)
        if override and override["slot_code"]:
            self._event_attributes["slot_code"] = str(override["slot_code"])
            self.async_write_ha_state()

        if result.times_updated:
            if (
                self.coordinator.code_generator == "date_based"
                and self.coordinator.should_update_code
                and eta_days
                and eta_days > 0
            ):
                _LOGGER.debug(
                    "Clearing slot %s for sensor %s due to date shift",
                    result.slot,
                    self.name,
                )
                await async_fire_clear_code(self.coordinator, result.slot)
            else:
                await async_fire_update_times(self.coordinator, self)
