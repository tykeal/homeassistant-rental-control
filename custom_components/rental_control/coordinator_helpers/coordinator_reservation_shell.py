# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Coordinator shell mixins for behavior-preserving delegation."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

from datetime import datetime
import importlib
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEvent

from ..reconciliation import ManagedSlot as _ManagedSlot
from ..reconciliation import Reservation as _Reservation
from . import codegen
from . import keymaster_observation
from . import reservations as reservations_helper
from . import slot_matching

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorReservationMixin:
    """Provide extracted coordinator shell behavior."""

    def _generate_slot_code(
        self,
        start: datetime,
        end: datetime,
        description: str | None,
        uid: str | None,
    ) -> str:
        """Generate a slot code using the configured legacy generator."""
        return codegen.generate_slot_code(
            self.code_generator, self.code_length, start, end, description, uid
        )

    @staticmethod
    def _observed_value_as_datetime(value: Any) -> datetime | None:
        """Return a datetime for an observed Store value, if parseable."""
        return slot_matching.observed_value_as_datetime(value)

    @staticmethod
    def _physical_mapping_matches_reservation(
        mapping: dict[str, Any],
        reservation: _Reservation,
        actual_slot_names: dict[int, str],
    ) -> bool:
        """Return whether fresh physical state identifies a reservation."""
        return slot_matching.physical_mapping_matches_reservation(
            mapping, reservation, actual_slot_names
        )

    @staticmethod
    def _physical_mapping_name_matches_reservation(
        mapping: dict[str, Any],
        reservation: _Reservation,
        actual_slot_names: dict[int, str],
    ) -> bool:
        """Return whether a fresh physical slot name matches a reservation."""
        return slot_matching.physical_mapping_name_matches_reservation(
            mapping, reservation, actual_slot_names
        )

    def _build_reservations(
        self,
        calendar: list[CalendarEvent],
        managed_slots: list[_ManagedSlot] | None = None,
    ) -> list[_Reservation]:
        """Convert parsed CalendarEvent objects to Reservation objects.

        Produces one :class:`~.reconciliation.Reservation` per calendar
        event that has a usable slot name.  Persisted Store mappings are
        deliberately not consulted as authority; reservation identity and
        desired windows come from the current calendar cycle.

        Physical Keymaster observations are used only as current-cycle facts
        for stable-name matching and manual PIN preservation; missing feed
        entries are handled by the stateless planner from physical state.

        Args:
            calendar: Parsed and sorted calendar events from the current
                refresh cycle.
            managed_slots: Optional current physical slot observations.
                When provided, observed names are treated as fresh
                physical facts for rematching stale persisted mappings.

        Returns:
            List of :class:`~.reconciliation.Reservation` objects ready
            for the planner.
        """
        ctx = self._reservation_build_context()
        return reservations_helper.build_reservations(calendar, managed_slots, ctx)

    def _build_ghost_reservations(
        self,
        current_keys: set[str],
        persisted: dict[str, Any],
        prefix: str,
        observed_mapping_keys: set[str] | None = None,
    ) -> list[_Reservation]:
        """Build synthetic Reservations for assigned slots absent from the feed.

        When a previously-assigned reservation disappears from the calendar
        feed, this method reconstructs a ghost :class:`~.reconciliation.Reservation`
        with an incremented ``missing_count``.  The planner includes it so
        that the slot is retained for up to two consecutive misses (T089);
        on the third miss the ghost is filtered out by
        :func:`~.reconciliation._filter_eligible` and the slot becomes
        clearable.

        Raw PIN values are never stored in the persisted mapping, so the
        ghost ``slot_code`` is always an empty string.

        Args:
            current_keys: Identity keys already built from the current
                calendar feed.  Absent keys are those in *persisted* but
                not in this set.
            persisted: Snapshot of ``_slot_mappings["mappings"]`` passed
                by the caller so that updates made to ``missing_count``
                here are reflected directly in the coordinator's live
                store dict.
            prefix: Computed event-prefix string (e.g. ``"RC "``).
            observed_mapping_keys: Mapping keys whose observed actual
                state came from the current physical Keymaster read.

        Returns:
            List of ghost :class:`~.reconciliation.Reservation` objects
            for assigned slots missing from the current feed.
        """
        ctx = self._reservation_build_context()
        result = reservations_helper.build_ghost_reservations(
            current_keys, persisted, prefix, observed_mapping_keys, ctx
        )
        return result.reservations

    def _observe_managed_slots(self) -> list[_ManagedSlot]:
        """Read Keymaster entity states and build ManagedSlot observations.

        Reads Keymaster text, switch, and datetime entities for every slot
        in the managed range to determine the current physical state. Store
        cache contents are deliberately ignored for classification. The
        observed state is also written back to the
        :meth:`~.event_overrides.EventOverrides.update_actual_state`
        cache for diagnostics.

        Returns:
            List of :class:`~.reconciliation.ManagedSlot` instances, one
            per slot in ``start_slot .. start_slot + max_events - 1``.
        """
        if not self.lockname or not self.event_overrides:
            return []

        slots: list[_ManagedSlot] = []

        for i in range(self.start_slot, self.start_slot + self.max_events):
            snapshot = self._read_slot_snapshot(i)
            ms, actual_state = keymaster_observation.classify_slot(
                snapshot, self.event_overrides.get_last_slot_error(i)
            )
            slots.append(ms)
            self.event_overrides.update_actual_state(i, actual_state)

        return slots
