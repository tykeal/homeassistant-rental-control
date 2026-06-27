# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

from datetime import datetime
import importlib
import logging
from typing import Any

from homeassistant.util import dt

from ..const import CHECKIN_SENSOR
from ..const import CHECKIN_STATE_CHECKED_IN
from ..const import CHECKIN_STATE_CHECKED_OUT
from ..const import DOMAIN
from ..reconciliation import ManagedSlot as _ManagedSlot
from ..reconciliation import Reservation as _Reservation
from ..reconciliation import SlotStatus as _SlotStatus
from ..reconciliation import make_reservation_fingerprint
from ..util import apply_buffer
from . import calendar_parsing
from . import checkin_protection
from . import slot_matching
from .models import CheckinProtectionSnapshot
from .models import _format_display_slot_name

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorCheckinMixin:
    """Provide extracted coordinator shell behavior."""

    def _apply_checkin_protection(
        self,
        reservations: list[_Reservation],
        managed_slots: list[_ManagedSlot] | None = None,
    ) -> None:
        """Mark active checked-in reservation as protected, if present.

        Reads :class:`~.sensors.checkinsensor.CheckinTrackingSensor` state
        from ``hass.data`` and sets :attr:`~.reconciliation.Reservation.protected_active`
        on the matching reservation so that reconciliation never evicts
        the active guest mid-stay (T043).

        When the sensor state is ``checked_out``, the matching reservation
        is flagged with :attr:`~.reconciliation.Reservation.checked_out`
        so the planner can handle graceful post-checkout slot release.

        Args:
            reservations: Mutable list of reservations for the current
                refresh cycle.  Modified in-place.
            managed_slots: Optional current physical slot observations.
        """
        domain_data: dict[str, Any] | None = self.hass.data.get(DOMAIN)
        entry_data: dict[str, Any] = (
            domain_data.get(self._entry_id, {}) if domain_data is not None else {}
        )
        checkin_sensor = entry_data.get(CHECKIN_SENSOR)
        if checkin_sensor is None:
            return
        sensor_state: str = checkin_sensor.state
        if sensor_state not in (CHECKIN_STATE_CHECKED_IN, CHECKIN_STATE_CHECKED_OUT):
            return
        attrs: dict[str, Any] = checkin_sensor.extra_state_attributes
        guest_name: str | None = attrs.get("guest_name")
        if not guest_name:
            return
        snapshot = CheckinProtectionSnapshot(
            state=sensor_state,
            guest_name=guest_name,
            start=CoordinatorCheckinMixin._coerce_checkin_datetime(attrs.get("start")),
            end=CoordinatorCheckinMixin._coerce_checkin_datetime(attrs.get("end")),
            summary=str(attrs.get("summary") or guest_name),
            attributes=attrs,
        )
        matched = checkin_protection.select_checkin_match(
            reservations, snapshot.guest_name, snapshot.start, snapshot.end
        )
        if matched is not None:
            if snapshot.state == CHECKIN_STATE_CHECKED_IN:
                matched.protected_active = True
            elif snapshot.state == CHECKIN_STATE_CHECKED_OUT:
                matched.checked_out = True
            return

        if (
            snapshot.state == CHECKIN_STATE_CHECKED_IN
            and snapshot.start is not None
            and snapshot.end is not None
            and managed_slots
        ):
            protected = self._synthesize_checkin_reservation(snapshot, managed_slots)
            if protected is not None:
                reservations.append(protected)

    def _synthesize_checkin_reservation(
        self,
        snapshot: CheckinProtectionSnapshot,
        managed_slots: list[_ManagedSlot],
    ) -> _Reservation | None:
        """Synthesize a protected reservation for an active stay, if safe."""
        if snapshot.start is None or snapshot.end is None:
            return None
        start = snapshot.start
        end = snapshot.end
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        display_slot_name = _format_display_slot_name(
            snapshot.guest_name, prefix, self.trim_names, self.max_name_length
        )
        buffered_start_raw, buffered_end_raw = apply_buffer(
            start,
            end,
            self.code_buffer_before,
            self.code_buffer_after,
            self,
        )
        buffered_start = (
            buffered_start_raw if isinstance(buffered_start_raw, datetime) else start
        )
        buffered_end = (
            buffered_end_raw if isinstance(buffered_end_raw, datetime) else end
        )
        matched_physical = self._find_observed_slot_by_name(
            managed_slots,
            snapshot.guest_name,
            display_slot_name,
            desired_start=buffered_start,
            desired_end=buffered_end,
        )
        same_name_slots = [
            slot
            for slot in managed_slots
            if slot.managed
            and slot.status is _SlotStatus.OCCUPIED
            and self._physical_slot_name_matches_name(
                slot.actual_name, snapshot.guest_name, display_slot_name
            )
        ]
        identity_key = make_reservation_fingerprint(
            self._entry_id, snapshot.guest_name, start, end
        )
        slot_code = (
            matched_physical.actual_code
            if matched_physical is not None and matched_physical.actual_code
            else self._generate_slot_code(start, end, None, None)
        )
        return checkin_protection.build_protected_reservation(
            snapshot,
            matched_physical,
            len(same_name_slots),
            (buffered_start, buffered_end),
            (identity_key, slot_code),
            display_slot_name,
        )

    def _coerce_checkin_datetime(value: Any) -> datetime | None:
        """Return a datetime from check-in sensor attributes."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = dt.parse_datetime(value)
            return parsed if isinstance(parsed, datetime) else None
        return None

    def _datetimes_match(self, left: datetime, right: datetime) -> bool:
        """Return whether two datetimes represent the same instant."""
        return calendar_parsing.datetimes_match(left, right, self.timezone)

    def _must_defer_for_checkin_restore(
        self, reservations: list[_Reservation], managed_slots: list[_ManagedSlot]
    ) -> bool:
        """Return whether apply should wait for check-in sensor restore."""
        domain_data: dict[str, Any] | None = self.hass.data.get(DOMAIN)
        if domain_data is None or self._entry_id not in domain_data:
            return False
        if not self._checkin_restore_pending:
            return False
        entry_data: dict[str, Any] = domain_data.get(self._entry_id, {})
        if entry_data.get(CHECKIN_SENSOR) is not None:
            return False
        return checkin_protection.should_defer_restore(
            reservations,
            managed_slots,
            self._physical_slot_name_matches_reservation,
        )

    def _physical_slot_name_matches_name(
        self, actual_name: str | None, slot_name: str, display_slot_name: str
    ) -> bool:
        """Return whether a physical display name matches a logical name."""
        prefix = f"{self.event_prefix} " if self.event_prefix else ""
        return slot_matching.physical_slot_name_matches_name(
            actual_name, slot_name, display_slot_name, prefix
        )

    def _physical_slot_name_matches_reservation(
        self, actual_name: str | None, reservation: _Reservation
    ) -> bool:
        """Return whether a physical display name matches a reservation name."""
        return self._physical_slot_name_matches_name(
            actual_name, reservation.slot_name, reservation.display_slot_name
        )

    def _active_checkin_windows_for_name(
        self, slot_name: str
    ) -> set[tuple[datetime, datetime]]:
        """Return active check-in windows that physical slots must reserve."""
        domain_data: dict[str, Any] | None = self.hass.data.get(DOMAIN)
        entry_data: dict[str, Any] = (
            domain_data.get(self._entry_id, {}) if domain_data is not None else {}
        )
        checkin_sensor = entry_data.get(CHECKIN_SENSOR)
        if checkin_sensor is None or checkin_sensor.state != CHECKIN_STATE_CHECKED_IN:
            return set()
        attrs: dict[str, Any] = checkin_sensor.extra_state_attributes
        if attrs.get("guest_name") != slot_name:
            return set()
        tracked_start = CoordinatorCheckinMixin._coerce_checkin_datetime(
            attrs.get("start")
        )
        tracked_end = CoordinatorCheckinMixin._coerce_checkin_datetime(attrs.get("end"))
        if tracked_start is None or tracked_end is None:
            return set()
        buffered_start_raw, buffered_end_raw = apply_buffer(
            tracked_start,
            tracked_end,
            self.code_buffer_before,
            self.code_buffer_after,
            self,
        )
        buffered_start = (
            buffered_start_raw
            if isinstance(buffered_start_raw, datetime)
            else tracked_start
        )
        buffered_end = (
            buffered_end_raw if isinstance(buffered_end_raw, datetime) else tracked_end
        )
        return checkin_protection.checkin_windows(
            tracked_start, tracked_end, buffered_start, buffered_end
        )
