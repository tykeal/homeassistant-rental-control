# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Coordinator shell mixin for reservation allocation behavior."""

# mypy: disable-error-code="attr-defined, has-type, var-annotated, misc, no-redef"

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any
import uuid

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt

from ..reconciliation import Reservation as _Reservation
from ..reconciliation.desired import select_eligible_reservations
from ..util import OperationResult
from . import allocation_failure
from . import code_allocation
from . import lockless_store
from . import reissue

_LOGGER = logging.getLogger(__name__)


def _coordinator_module() -> Any:
    """Return the public coordinator module for patched compatibility."""
    return importlib.import_module("custom_components.rental_control.coordinator")


class CoordinatorAllocationMixin:
    """Provide extracted coordinator allocation behavior."""

    async def _run_reconciliation(self, new_calendar: list[CalendarEvent]) -> None:
        """Build a desired plan from the calendar and apply it to the lock.

        An entry whose options gain a lock keeps running in place, so a
        failure report left by its earlier lockless cycles is cleared
        here; nothing else would ever clear it. Failures of this cycle
        are surfaced through ``allocation_failure`` as their own kind.
        """
        allocation_failure.forget(
            self.hass, self._entry_id, allocation_failure.LOCKLESS
        )
        event_overrides = self.event_overrides
        if event_overrides is None:
            return
        try:
            observed_slots = self._observe_managed_slots()
            reservations = self._prepare_reservations_for_adoption(
                new_calendar, observed_slots
            )
            self._apply_checkin_protection(reservations, observed_slots)
            result = await code_allocation.async_resolve_codes(
                reissue.CodeResolutionRequest(
                    hass=self.hass,
                    entry_id=self._entry_id,
                    lockname=self.lockname,
                    code_length=self.code_length,
                    managed_slots=observed_slots,
                    reservations=reservations,
                    suppression=reissue.current_suppression(self),
                    forced_reissues=reissue.forced_reissue_directives(
                        self, reservations
                    ),
                )
            )
            reissue.consume_cycle_result(self, result)

            plan_id = str(uuid.uuid4())
            plan = _coordinator_module().compute_desired_plan(
                reservations=reservations,
                managed_slots=observed_slots,
                max_events=self.max_events,
                plan_id=plan_id,
                generated_at=dt.now(),
                entry_id=self._entry_id,
                lockname=self.lockname,
                start_slot=self.start_slot,
            )

            res_by_key: dict[str, _Reservation] = {
                reservation.identity_key: reservation for reservation in reservations
            }
            for _violation in plan.validate(res_by_key):
                _LOGGER.warning("Reconciliation plan invariant violation")

            self._record_observed_slot_codes(plan, res_by_key, observed_slots)
            if self._must_defer_for_checkin_restore(reservations, observed_slots):
                _LOGGER.info(
                    "Deferring reconciliation until check-in state is "
                    "available; same-name physical slot has missing date state",
                )
                operation_results: list[OperationResult] = []
            else:
                operation_results = await event_overrides.async_apply_plan(
                    self, plan, res_by_key
                )
            self._apply_observed_code_results(plan, res_by_key, operation_results)
            self._sync_slot_store_from_plan(plan, res_by_key, operation_results)

            self._latest_plan = plan
            self._latest_res_by_key = res_by_key
            _LOGGER.debug(
                "Reconciliation completed: selected=%d overflow=%d actions=%d",
                len(plan.selected),
                len(plan.overflow),
                len(plan.actions),
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Reconciliation failed; skipping cycle (failure category %s)",
                type(err).__name__,
            )
            allocation_failure.report(
                self.hass,
                self._entry_id,
                self._name,
                err,
                allocation_failure.RECONCILIATION,
            )
        else:
            allocation_failure.resolve(
                self.hass, self._entry_id, self._name, allocation_failure.RECONCILIATION
            )

    async def _run_lockless_allocation(self, new_calendar: list[CalendarEvent]) -> None:
        """Allocate codes for entries that do not manage Keymaster slots.

        A failed cycle deliberately leaves ``_latest_res_by_key`` alone,
        so the entry keeps publishing the last codes it successfully
        issued rather than blanking every guest's code over a failure
        that may be transient. The failure is made visible through
        ``allocation_failure`` instead of through missing codes. A
        reconciliation report left from when the entry had a lock is
        cleared for the same reason the reverse case is.
        """
        allocation_failure.forget(
            self.hass, self._entry_id, allocation_failure.RECONCILIATION
        )
        try:
            reservations = self._prepare_reservations_for_adoption(new_calendar, [])
            self._apply_checkin_protection(reservations)
            result = await code_allocation.async_resolve_codes(
                reissue.CodeResolutionRequest(
                    hass=self.hass,
                    entry_id=self._entry_id,
                    lockname=None,
                    code_length=self.code_length,
                    managed_slots=[],
                    reservations=reservations,
                    suppression=reissue.current_suppression(self),
                    forced_reissues=reissue.forced_reissue_directives(
                        self, reservations
                    ),
                )
            )
            reissue.consume_cycle_result(self, result)
            kept = select_eligible_reservations(reservations, include_checked_out=True)
            res_by_key: dict[str, _Reservation] = {
                reservation.identity_key: reservation for reservation in kept
            }
            self._sync_lockless_slot_store(res_by_key)
            self._latest_res_by_key = res_by_key
            self._latest_plan = None
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Lockless allocation failed; skipping cycle (failure category %s)",
                type(err).__name__,
            )
            allocation_failure.report(self.hass, self._entry_id, self._name, err)
        else:
            allocation_failure.resolve(self.hass, self._entry_id, self._name)

    def _sync_lockless_slot_store(self, res_by_key: dict[str, _Reservation]) -> None:
        """Persist lockless reservation metadata and published-code state."""
        lockless_store.sync_lockless_slot_store(
            self._slot_mappings,
            res_by_key,
            entry_id=self._entry_id,
            start_slot=self.start_slot,
            max_events=self.max_events,
        )
