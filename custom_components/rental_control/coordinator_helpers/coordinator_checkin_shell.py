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
        if sensor_state != CHECKIN_STATE_CHECKED_IN:
            self._slot_mappings.pop("expired_checkin", None)
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
        checked_out = snapshot.state == CHECKIN_STATE_CHECKED_OUT
        matched = checkin_protection.select_checkin_match(
            reservations,
            snapshot.guest_name,
            snapshot.start,
            snapshot.end,
            started_by=dt.now() if checked_out else None,
        )
        if matched is not None:
            if snapshot.state == CHECKIN_STATE_CHECKED_IN and matched.missing_count < 3:
                matched.protected_active = True
            elif snapshot.state == CHECKIN_STATE_CHECKED_IN:
                self._remember_expired_checkin(snapshot)
            elif checked_out:
                matched.checked_out = True
                _LOGGER.debug("Check-in sensor reports a reservation checked out")
            return

        if (
            snapshot.state == CHECKIN_STATE_CHECKED_IN
            and snapshot.start is not None
            and snapshot.end is not None
            and managed_slots
        ):
            protected = self._synthesize_checkin_reservation(snapshot, managed_slots)
            if protected is not None:
                for index, existing in enumerate(reservations):
                    if (
                        existing.identity_key == protected.identity_key
                        and existing.missing_count > 0
                    ):
                        protected.uid_aliases.update(existing.uid_aliases)
                        protected.booking_aliases.update(existing.booking_aliases)
                        protected.fingerprint_history.update(
                            existing.fingerprint_history
                        )
                        protected.published_once = existing.published_once
                        protected.issued_origin = existing.issued_origin
                        reservations[index] = protected
                        return
                reservations.append(protected)
            elif self._is_missing_checkin_stay(
                snapshot.guest_name, snapshot.start, snapshot.end
            ):
                self._remember_expired_checkin(snapshot)

    def _remember_expired_checkin(self, snapshot: CheckinProtectionSnapshot) -> None:
        """Retain the expired sensor identity until its state has reset."""
        if snapshot.start is not None and snapshot.end is not None:
            self._slot_mappings["expired_checkin"] = {
                "name": snapshot.guest_name,
                "start": snapshot.start.isoformat(),
                "end": snapshot.end.isoformat(),
            }

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
        identity_key = make_reservation_fingerprint(
            self._entry_id, snapshot.guest_name, start, end
        )
        matched_physical = self._find_observed_slot_by_name(
            managed_slots,
            snapshot.guest_name,
            display_slot_name,
            desired_start=buffered_start,
            desired_end=buffered_end,
        )
        matching_mappings = [
            (key, mapping)
            for key, mapping in self._slot_mappings.get("mappings", {}).items()
            if matched_physical is not None
            and isinstance(mapping, dict)
            and mapping.get("slot") == matched_physical.slot
            and isinstance(mapping.get("missing_count"), int)
            and self._mapping_matches_checkin(
                key, mapping, snapshot, identity_key, buffered_start, buffered_end
            )
        ]
        missing_count = max(
            (mapping["missing_count"] for _, mapping in matching_mappings), default=0
        )
        if missing_count >= 3 or any(
            mapping.get("missing_stay_pending_clear") is True
            for _, mapping in matching_mappings
        ):
            return None
        same_name_slots = [
            slot
            for slot in managed_slots
            if slot.managed
            and slot.status is _SlotStatus.OCCUPIED
            and self._physical_slot_name_matches_name(
                slot.actual_name, snapshot.guest_name, display_slot_name
            )
        ]
        if missing_count and len(matching_mappings) == 1:
            identity_key = matching_mappings[0][0]
        generated_code = self._generate_slot_code(start, end, None, None)
        suppression = self._reservation_build_context().reissue_suppression
        suppress_observed = identity_key in suppression.identity_keys or (
            matched_physical is not None and matched_physical.slot in suppression.slots
        )
        slot_code = (
            matched_physical.actual_code
            if matched_physical is not None
            and matched_physical.actual_code
            and not suppress_observed
            else generated_code
        )
        code_provenance = (
            "generated" if slot_code == generated_code else "manual_observed"
        )
        protected = checkin_protection.build_protected_reservation(
            snapshot,
            matched_physical,
            len(same_name_slots),
            (buffered_start, buffered_end),
            (identity_key, slot_code, suppress_observed),
            display_slot_name,
            code_provenance,
        )
        if protected is not None:
            protected.missing_count = missing_count
        return protected

    def _mapping_matches_checkin(
        self,
        key: str,
        mapping: dict[str, Any],
        snapshot: CheckinProtectionSnapshot,
        fingerprint: str,
        buffered_start: datetime,
        buffered_end: datetime,
    ) -> bool:
        """Match a tracked stay without conflating same-name occupants."""
        identity = mapping.get("identity")
        if not isinstance(identity, dict):
            return False
        name = identity.get("slot_name")
        if name != snapshot.guest_name:
            return False
        history = mapping.get("fingerprint_history", [])
        if key == fingerprint or (
            isinstance(history, (list, tuple, set)) and fingerprint in history
        ):
            return True
        identity_start = self._coerce_checkin_datetime(identity.get("start"))
        identity_end = self._coerce_checkin_datetime(identity.get("end"))
        if identity_start is not None and identity_end is not None:
            return (identity_start, identity_end) in {
                (snapshot.start, snapshot.end),
                (buffered_start, buffered_end),
            }
        actual = mapping.get("last_observed_actual")
        if not isinstance(actual, dict):
            return True
        actual_window = (
            self._coerce_checkin_datetime(actual.get("start_state")),
            self._coerce_checkin_datetime(actual.get("end_state")),
        )
        return None in actual_window or actual_window in {
            (snapshot.start, snapshot.end),
            (buffered_start, buffered_end),
        }

    @staticmethod
    def _coerce_checkin_datetime(value: Any) -> datetime | None:
        """Return a datetime from check-in sensor attributes."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = dt.parse_datetime(value)
            return parsed if isinstance(parsed, datetime) else None
        return None

    def _is_missing_checkin_stay(
        self, name: str | None, start: datetime | None, end: datetime | None
    ) -> bool:
        """Return whether this exact tracked stay exceeded feed-miss tolerance."""
        if not name or start is None or end is None:
            return False
        expired = self._slot_mappings.get("expired_checkin")
        if (
            isinstance(expired, dict)
            and expired.get("name") == name
            and self._coerce_checkin_datetime(expired.get("start")) == start
            and self._coerce_checkin_datetime(expired.get("end")) == end
        ):
            return True
        if any(
            res.slot_name == name
            and res.start == start
            and res.end == end
            and res.missing_count == 0
            for res in self._latest_res_by_key.values()
        ):
            return False
        buffered_start_raw, buffered_end_raw = apply_buffer(
            start, end, self.code_buffer_before, self.code_buffer_after, self
        )
        buffered_start = (
            buffered_start_raw if isinstance(buffered_start_raw, datetime) else start
        )
        buffered_end = (
            buffered_end_raw if isinstance(buffered_end_raw, datetime) else end
        )
        windows = {(start, end), (buffered_start, buffered_end)}
        fingerprint = make_reservation_fingerprint(self._entry_id, name, start, end)
        snapshot = CheckinProtectionSnapshot(
            state=CHECKIN_STATE_CHECKED_IN,
            guest_name=name,
            start=start,
            end=end,
            summary=name,
            attributes={},
        )
        counts = []
        for key, mapping in self._slot_mappings.get("mappings", {}).items():
            if isinstance(mapping, dict) and self._mapping_matches_checkin(
                key, mapping, snapshot, fingerprint, buffered_start, buffered_end
            ):
                count = mapping.get("missing_count", 0)
                counts.append(count if isinstance(count, int) else 0)
        if counts:
            return all(count >= 3 for count in counts)
        return any(
            res.slot_name == name
            and (
                (res.start, res.end) in windows
                or res.identity_key == fingerprint
                or fingerprint in res.fingerprint_history
            )
            and res.missing_count >= 3
            for res in self._latest_res_by_key.values()
        )

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
