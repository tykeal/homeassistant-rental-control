# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Creating sensors for upcoming events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import ICON
from ..const import NAME
from ..reconciliation import make_reservation_fingerprint
from ..util import gen_uuid
from ..util import get_slot_name
from .calsensor_helpers import attributes
from .calsensor_helpers import codes
from .calsensor_helpers import descriptions
from .calsensor_helpers import slots
from .calsensor_helpers import state as render_state
from .calsensor_helpers.models import CalendarSensorRenderResult
from .calsensor_helpers.models import DoorCodeRequest
from .calsensor_helpers.models import SlotAssignmentContext
from .calsensor_helpers.models import SlotReadContext

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

    _KNOWN_FIELDS = descriptions.KNOWN_FIELDS

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: RentalControlCoordinator,
        sensor_name: str,
        event_number: int,
    ) -> None:
        """
        Initialize the sensor.

        sensor_name is accepted for backward compatibility but unused;
        the entity name is derived from NAME and event_number.
        event_number indicates which upcoming event this is; numbering is
        zero-based, so 0 refers to the first upcoming event.
        """
        super().__init__(coordinator)
        del sensor_name
        summary = attributes.build_no_reservation_summary(coordinator.event_prefix)
        self._attr_has_entity_name = True
        self._attr_name = f"{NAME} Event {event_number}"
        self._code_generator = coordinator.code_generator
        self._code_length = coordinator.code_length
        self._entity_category = EntityCategory.DIAGNOSTIC
        self._event_attributes: dict[str, Any] = (
            attributes.build_no_reservation_attributes(coordinator.event_prefix)
        )
        self._parsed_attributes: dict[str, str] = {}
        self._event_number = event_number
        self._hass = hass
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

    def _description(self) -> Any:
        """Return the current event description attribute."""
        description = self._event_attributes["description"]
        if description is None:
            return None
        return description

    def _extract_email(self) -> str | None:
        """Extract guest email from a description"""
        return descriptions.extract_email(self._description())

    def _extract_last_four(self) -> str | None:
        """Extract the last 4 digits from a description."""
        return descriptions.extract_last_four(
            self._description(),
            self._extract_phone_number,
        )

    def _extract_num_guests(self) -> str | None:
        """Extract the number of guests from a description."""
        return descriptions.extract_num_guests(self._description())

    def _extract_phone_number(self) -> str | None:
        """Extract guest phone number from a description"""
        return descriptions.extract_phone_number(self._description())

    def _extract_url(self) -> str | None:
        """Extract reservation URL."""
        return descriptions.extract_url(self._description())

    def _extract_booking_id(self) -> str | None:
        """Extract booking ID from a description."""
        return descriptions.extract_booking_id(self._description())

    def _extract_dynamic_attributes(self) -> dict[str, str]:
        """Extract unrecognised 'Field: Value' lines from description."""
        return descriptions.extract_dynamic_attributes(self._description())

    def _generate_door_code(self) -> str:
        """Generate a door code based upon the selected type."""
        last_four = None
        if (
            self._code_generator == "last_four"
            and self._code_length == 4
            and self._event_attributes["description"] is not None
        ):
            last_four = self._extract_last_four()
        request = DoorCodeRequest(
            generator=self._code_generator,
            code_length=self._code_length,
            start=self._event_attributes["start"],
            end=self._event_attributes["end"],
            uid=self._event_attributes.get("uid"),
            description=self._event_attributes["description"],
            last_four=last_four,
        )
        return codes.generate_door_code(request)

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
        if not self.coordinator.last_update_success:
            self.async_write_ha_state()
            return

        self._refresh_code_settings()
        event = render_state.select_event(self.coordinator.data, self._event_number)
        if event is None:
            self._handle_no_reservation_update()
        else:
            self._handle_event_update(event)
        self.async_write_ha_state()

    def _refresh_code_settings(self) -> None:
        """Refresh generated-code settings from the coordinator."""
        self._code_generator = self.coordinator.code_generator
        self._code_length = self.coordinator.code_length

    def _handle_no_reservation_update(self) -> None:
        """Apply the legacy no-reservation render result."""
        _LOGGER.debug(
            "No events available for sensor %s, removing from calendar %s",
            str(self._event_number),
            self.name,
        )
        self._apply_render_result(
            render_state.render_no_reservation(self.coordinator.event_prefix)
        )

    def _handle_event_update(self, event: Any) -> None:
        """Apply the render result for a selected calendar event."""
        _LOGGER.debug(
            "Adding event %s - Start %s - End %s - as event %s to calendar %s",
            event.summary,
            event.start,
            event.end,
            str(self._event_number),
            self.name,
        )
        event_attributes = self._build_event_attributes(event)
        self._event_attributes.update(event_attributes)
        if self._event_attributes["slot_code"] is None:
            self._event_attributes["slot_code"] = self._generate_door_code()
        result = render_state.render_event_result(
            event,
            self._event_attributes,
            self._build_parsed_attributes(),
        )
        self._apply_render_result(result)

    def _build_event_attributes(self, event: Any) -> dict[str, Any]:
        """Build event attributes for the selected calendar event."""
        eta = attributes.calculate_eta(event.start)
        slot = slots.read_slot(self._slot_read_context(event), self.coordinator)
        return attributes.build_event_attributes(event, eta, slot)

    def _slot_read_context(self, event: Any) -> SlotReadContext:
        """Build a read-only slot lookup context with patchable callables."""
        return SlotReadContext(
            entry_id=self.coordinator.entry_id,
            summary=event.summary,
            description=event.description,
            event_prefix=self.coordinator.event_prefix or "",
            start=event.start,
            end=event.end,
            event_overrides_present=self.coordinator.event_overrides is not None,
            get_slot_name=get_slot_name,
            make_reservation_fingerprint=make_reservation_fingerprint,
        )

    def _build_parsed_attributes(self) -> dict[str, str]:
        """Build parsed attributes using the retained private wrappers."""
        parsed_attributes: dict[str, str] = {}
        self._add_optional_attr(
            parsed_attributes, "last_four", self._extract_last_four()
        )
        self._add_optional_attr(
            parsed_attributes,
            "number_of_guests",
            self._extract_num_guests(),
        )
        self._add_optional_attr(parsed_attributes, "guest_email", self._extract_email())
        self._add_optional_attr(
            parsed_attributes,
            "phone_number",
            self._extract_phone_number(),
        )
        self._add_optional_attr(
            parsed_attributes, "reservation_url", self._extract_url()
        )
        self._add_optional_attr(
            parsed_attributes, "booking_id", self._extract_booking_id()
        )
        for key, value in self._extract_dynamic_attributes().items():
            if key not in parsed_attributes:
                parsed_attributes[key] = value
        return parsed_attributes

    @staticmethod
    def _add_optional_attr(
        parsed_attributes: dict[str, str],
        key: str,
        value: str | None,
    ) -> None:
        """Add a parsed attribute when its legacy extractor returned a value."""
        if value is not None:
            parsed_attributes[key] = value

    def _apply_render_result(self, result: CalendarSensorRenderResult) -> None:
        """Assign a render result to the entity's mutable state."""
        self._event_attributes = result.event_attributes
        self._parsed_attributes = result.parsed_attributes
        self._state = result.state

    async def _async_handle_slot_assignment(
        self,
        context: SlotAssignmentContext,
    ) -> None:
        """No-op backward-compatible shim; slot assignment is owned by the coordinator.

        The per-event slot-assignment scheduling path — where
        ``_handle_coordinator_update`` would call this method to invoke
        :meth:`~..event_overrides.EventOverrides.async_reserve_or_get_slot`
        or fire set-code / clear-code / update-times events — has been
        removed.  All slot mutation is now performed exclusively by
        :class:`~..coordinator.RentalControlCoordinator` through the
        reconciliation cycle (``compute_desired_plan`` / ``async_apply_plan``).

        This shim is retained so that any external call sites produce a
        harmless no-op rather than an ``AttributeError``.  It is verified
        by ``test_async_handle_slot_assignment_is_noop`` as a regression
        guard.  The method must never be called or scheduled from
        :meth:`_handle_coordinator_update`.

        .. deprecated::
            The per-event scheduling and mutation fallback path has been
            retired.  This shim will be removed in a future release once
            all external references are eliminated.
        """
        del context
