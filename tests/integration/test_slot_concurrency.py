# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for concurrent slot reservation scenarios.

These end-to-end tests validate the slot concurrency guarantees
introduced by spec 005-fix-duplicate-slot:

- SC-001: Concurrent reservations get unique slots
- SC-002: Idempotent re-delivery updates times
- SC-003: Identical re-delivery is a no-op
- SC-004: Overflow handled gracefully
- SC-005: Cleanup during assignment has no cross-contamination
- SC-006: Dedup rejection redirects to existing slot
- SC-007: Single-reservation lifecycle regression
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.event_overrides import EventOverrides
from custom_components.rental_control.event_overrides import ReserveResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dt(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> datetime:
    """Build a timezone-aware datetime in UTC."""
    return datetime(year, month, day, hour, minute, tzinfo=dt_util.UTC)


def _populated_eo(start_slot: int, max_slots: int) -> EventOverrides:
    """Return an EventOverrides pre-populated with empty slots (ready)."""
    eo = EventOverrides(start_slot=start_slot, max_slots=max_slots)
    now = dt_util.now()
    for slot in range(start_slot, start_slot + max_slots):
        eo.update(slot, "", "", now, now)
    assert eo.ready is True
    return eo


# ---------------------------------------------------------------------------
# SC-001 / SC-004 — Concurrent reservations (T027)
# ---------------------------------------------------------------------------


class TestConcurrentReservation:
    """Simulate 10 sensors scheduling reservations with only 5 slots."""

    @pytest.mark.asyncio
    async def test_ten_sensors_five_slots_no_duplicates(self) -> None:
        """First 5 guests get unique slots; remaining 5 overflow.

        Validates SC-001 (unique slots) and SC-004 (graceful overflow).
        """
        eo = _populated_eo(start_slot=10, max_slots=5)

        async def reserve(i: int) -> ReserveResult:
            """Reserve a slot for guest *i*."""
            return await eo.async_reserve_or_get_slot(
                slot_name=f"Guest {i}",
                slot_code=f"{1000 + i}",
                start_time=_make_dt(2025, 8, 1),
                end_time=_make_dt(2025, 8, 5),
                uid=f"uid-{i}",
            )

        results = await asyncio.gather(*[reserve(i) for i in range(10)])

        assigned = [r for r in results if r.slot is not None]
        overflows = [r for r in results if r.slot is None]

        # Exactly 5 slots should be filled
        assert len(assigned) == 5
        assert len(overflows) == 5

        # Each assigned slot is unique
        assigned_slots = [r.slot for r in assigned]
        assert len(set(assigned_slots)) == 5

        # All are new reservations
        assert all(r.is_new for r in assigned)

        # Overflows have correct flags
        for r in overflows:
            assert r.is_new is False
            assert r.times_updated is False

    @pytest.mark.asyncio
    async def test_concurrent_no_slot_overwrite(self) -> None:
        """Concurrent reservations must never overwrite each other.

        Validates SC-001: after 5 concurrent reservations on 5 slots,
        each slot's stored data must match exactly the guest that got it.
        """
        eo = _populated_eo(start_slot=1, max_slots=5)

        async def reserve(i: int) -> tuple[int, ReserveResult]:
            """Reserve a slot for guest *i* and return (i, result)."""
            result = await eo.async_reserve_or_get_slot(
                slot_name=f"Guest {i}",
                slot_code=f"{2000 + i}",
                start_time=_make_dt(2025, 9, 1),
                end_time=_make_dt(2025, 9, 5),
                uid=f"uid-concurrent-{i}",
            )
            return i, result

        results = await asyncio.gather(*[reserve(i) for i in range(5)])

        # All 5 should get unique slots
        slots = [r.slot for _, r in results]
        assert len(set(slots)) == 5

        # Verify each slot's stored data matches the guest that got it
        for i, r in results:
            assert r.slot is not None
            assert eo.get_slot_name(r.slot) == f"Guest {i}"
            override = eo.overrides[r.slot]
            assert override is not None
            assert override["slot_code"] == f"{2000 + i}"
            assert override["start_time"] == _make_dt(2025, 9, 1)
            assert override["end_time"] == _make_dt(2025, 9, 5)


# ---------------------------------------------------------------------------
# SC-002 / SC-003 — Idempotent re-delivery (T028)
# ---------------------------------------------------------------------------


class TestIdempotentRedelivery:
    """Assign guest, re-deliver with changed times, then identical times."""

    @pytest.mark.asyncio
    async def test_changed_times_updates(self) -> None:
        """Re-delivery with changed times returns times_updated=True.

        Validates SC-002.
        """
        eo = _populated_eo(start_slot=1, max_slots=3)
        uid = "uid-alice"

        # Initial assignment
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1111",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 5),
            uid=uid,
        )
        assert r1.slot is not None
        assert r1.is_new is True

        # Re-deliver with changed times
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Alice",
            slot_code="1111",
            start_time=_make_dt(2025, 8, 1),
            end_time=_make_dt(2025, 8, 7),
            uid=uid,
        )
        assert r2.slot == r1.slot
        assert r2.is_new is False
        assert r2.times_updated is True

        # Verify updated end time
        override = eo.overrides[r1.slot]
        assert override is not None
        assert override["end_time"] == _make_dt(2025, 8, 7)

    @pytest.mark.asyncio
    async def test_identical_times_noop(self) -> None:
        """Re-delivery with identical times returns times_updated=False.

        Validates SC-003.
        """
        eo = _populated_eo(start_slot=1, max_slots=3)
        uid = "uid-bob"
        start = _make_dt(2025, 8, 10)
        end = _make_dt(2025, 8, 15)

        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="2222",
            start_time=start,
            end_time=end,
            uid=uid,
        )
        assert r1.is_new is True

        # Identical re-delivery
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Bob",
            slot_code="2222",
            start_time=start,
            end_time=end,
            uid=uid,
        )
        assert r2.slot == r1.slot
        assert r2.is_new is False
        assert r2.times_updated is False

    @pytest.mark.asyncio
    async def test_update_then_noop_sequence(self) -> None:
        """Full sequence: assign → update times → noop.

        Validates SC-002 and SC-003 end-to-end.
        """
        eo = _populated_eo(start_slot=1, max_slots=3)
        uid = "uid-carol"

        # Step 1: Initial assignment
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="Carol",
            slot_code="3333",
            start_time=_make_dt(2025, 7, 1),
            end_time=_make_dt(2025, 7, 5),
            uid=uid,
        )
        assert r1.is_new is True
        original_slot = r1.slot

        # Step 2: Re-deliver with changed times
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="Carol",
            slot_code="3333",
            start_time=_make_dt(2025, 7, 1),
            end_time=_make_dt(2025, 7, 8),
            uid=uid,
        )
        assert r2.slot == original_slot
        assert r2.times_updated is True

        # Step 3: Re-deliver with now-identical times
        r3 = await eo.async_reserve_or_get_slot(
            slot_name="Carol",
            slot_code="3333",
            start_time=_make_dt(2025, 7, 1),
            end_time=_make_dt(2025, 7, 8),
            uid=uid,
        )
        assert r3.slot == original_slot
        assert r3.times_updated is False


# ---------------------------------------------------------------------------
# SC-005 — Cleanup during assignment (T029)
# ---------------------------------------------------------------------------


class TestCleanupDuringAssignment:
    """Schedule slot clear and new assignment concurrently."""

    @pytest.mark.asyncio
    async def test_clear_and_assign_no_cross_contamination(self) -> None:
        """Concurrent clear + assign complete without interference.

        Validates SC-005.
        """
        eo = _populated_eo(start_slot=1, max_slots=3)

        # Pre-populate slot 1 with a guest
        r_setup = await eo.async_reserve_or_get_slot(
            slot_name="ExistingGuest",
            slot_code="9999",
            start_time=_make_dt(2025, 6, 1),
            end_time=_make_dt(2025, 6, 5),
            uid="uid-existing",
        )
        assert r_setup.slot is not None
        existing_slot = r_setup.slot

        async def clear_slot() -> None:
            """Clear the existing guest's slot."""
            await eo.async_update(
                slot=existing_slot,
                slot_code="",
                slot_name="",
                start_time=_make_dt(2025, 6, 1),
                end_time=_make_dt(2025, 6, 5),
            )

        async def assign_new() -> ReserveResult:
            """Reserve a slot for a new guest."""
            return await eo.async_reserve_or_get_slot(
                slot_name="NewGuest",
                slot_code="8888",
                start_time=_make_dt(2025, 7, 1),
                end_time=_make_dt(2025, 7, 5),
                uid="uid-new",
            )

        _, new_result = await asyncio.gather(clear_slot(), assign_new())

        # New guest must have a slot
        assert new_result.slot is not None

        # Verify no cross-contamination: new guest's slot has correct name
        assert eo.get_slot_name(new_result.slot) == "NewGuest"

        # Existing guest's slot should be cleared (serialization ensures
        # the clear completes without interfering with the new assignment)
        cleared_override = eo.overrides.get(existing_slot)
        if new_result.slot != existing_slot:
            # Clear happened on different slot from new assignment
            assert cleared_override is None
        else:
            # If new guest took the cleared slot, it should have new data
            assert eo.get_slot_name(new_result.slot) == "NewGuest"


# ---------------------------------------------------------------------------
# SC-004 — Overflow (T030)
# ---------------------------------------------------------------------------


class TestOverflow:
    """Fill all managed slots then attempt additional reservation."""

    @pytest.mark.asyncio
    async def test_overflow_no_overwrite(self) -> None:
        """Extra reservations after all slots full return None.

        Validates SC-004: zero overwrites on overflow.
        """
        eo = _populated_eo(start_slot=1, max_slots=3)

        # Fill all 3 slots
        guests = []
        for i in range(3):
            r = await eo.async_reserve_or_get_slot(
                slot_name=f"FilledGuest {i}",
                slot_code=f"{5000 + i}",
                start_time=_make_dt(2025, 10, 1),
                end_time=_make_dt(2025, 10, 5),
                uid=f"uid-filled-{i}",
            )
            assert r.slot is not None
            assert r.is_new is True
            guests.append(r)

        # All slots occupied — next_slot should be None
        assert eo.next_slot is None

        # Attempt overflow reservations
        overflow_results = []
        for i in range(3, 6):
            r = await eo.async_reserve_or_get_slot(
                slot_name=f"OverflowGuest {i}",
                slot_code=f"{6000 + i}",
                start_time=_make_dt(2025, 10, 1),
                end_time=_make_dt(2025, 10, 5),
                uid=f"uid-overflow-{i}",
            )
            overflow_results.append(r)

        # All overflow attempts return None
        for r in overflow_results:
            assert r.slot is None
            assert r.is_new is False

        # Verify original assignments unchanged
        for g in guests:
            assert g.slot is not None
            stored = eo.overrides[g.slot]
            assert stored is not None
            assert stored["slot_name"].startswith("FilledGuest ")

    @pytest.mark.asyncio
    async def test_overflow_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Overflow reservation logs a warning message."""
        eo = _populated_eo(start_slot=1, max_slots=2)

        # Fill both slots
        for i in range(2):
            await eo.async_reserve_or_get_slot(
                slot_name=f"Guest {i}",
                slot_code=f"{7000 + i}",
                start_time=_make_dt(2025, 11, 1),
                end_time=_make_dt(2025, 11, 5),
                uid=f"uid-warn-{i}",
            )

        # Overflow attempt should log warning
        with caplog.at_level(logging.WARNING):
            r = await eo.async_reserve_or_get_slot(
                slot_name="OverflowGuest",
                slot_code="9999",
                start_time=_make_dt(2025, 11, 1),
                end_time=_make_dt(2025, 11, 5),
                uid="uid-overflow-warn",
            )

        assert r.slot is None
        assert "override slots are occupied" in caplog.text


# ---------------------------------------------------------------------------
# SC-006 — Dedup rejection (T030b)
# ---------------------------------------------------------------------------


class TestDedupRejection:
    """Pre-populate slot then invoke async_update targeting different slot."""

    @pytest.mark.asyncio
    async def test_redirect_to_existing_slot(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """async_update redirects duplicate to existing slot with warning.

        Pre-populate slot 12 with "Alice" Mon–Fri, then async_update
        targeting slot 14 with "Alice" Wed–Sun.  Expect redirect to
        slot 12 with updated times and a warning log.

        Validates SC-006.
        """
        eo = _populated_eo(start_slot=10, max_slots=5)

        # Pre-populate slot 12 with Alice Mon(4)–Fri(8)
        await eo.async_update(
            slot=12,
            slot_code="4444",
            slot_name="Alice",
            start_time=_make_dt(2025, 8, 4),  # Monday
            end_time=_make_dt(2025, 8, 8),  # Friday
        )
        assert eo.get_slot_name(12) == "Alice"

        # async_update targeting slot 14 with Alice Wed(6)–Sun(10)
        with caplog.at_level(logging.WARNING):
            await eo.async_update(
                slot=14,
                slot_code="5555",
                slot_name="Alice",
                start_time=_make_dt(2025, 8, 6),  # Wednesday
                end_time=_make_dt(2025, 8, 10),  # Sunday
            )

        # Redirect happened: slot 12 got the update
        assert eo.get_slot_name(12) == "Alice"
        override_12 = eo.overrides[12]
        assert override_12 is not None
        assert override_12["start_time"] == _make_dt(2025, 8, 6)
        assert override_12["end_time"] == _make_dt(2025, 8, 10)
        assert override_12["slot_code"] == "5555"

        # Slot 14 was NOT written (redirect took over)
        assert eo.overrides[14] is None

        # Warning about redirect was logged
        assert "Duplicate slot_name 'Alice'" in caplog.text
        assert "redirecting write" in caplog.text


# ---------------------------------------------------------------------------
# SC-007 — Single-reservation regression (T030c)
# ---------------------------------------------------------------------------


class TestSingleReservationRegression:
    """Full lifecycle: assign → update times → clear."""

    @pytest.mark.asyncio
    async def test_assign_update_clear_lifecycle(self) -> None:
        """Single guest lifecycle works identically to pre-fix behavior.

        Validates SC-007.
        """
        eo = _populated_eo(start_slot=1, max_slots=3)
        uid = "uid-lifecycle"

        # Step 1: Assign
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="LifecycleGuest",
            slot_code="1234",
            start_time=_make_dt(2025, 9, 1),
            end_time=_make_dt(2025, 9, 5),
            uid=uid,
        )
        assert r1.slot is not None
        assert r1.is_new is True
        slot = r1.slot

        # Verify stored data
        override = eo.overrides[slot]
        assert override is not None
        assert override["slot_name"] == "LifecycleGuest"
        assert override["slot_code"] == "1234"
        assert override["start_time"] == _make_dt(2025, 9, 1)
        assert override["end_time"] == _make_dt(2025, 9, 5)

        # Step 2: Update times via re-reservation
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="LifecycleGuest",
            slot_code="1234",
            start_time=_make_dt(2025, 9, 1),
            end_time=_make_dt(2025, 9, 8),
            uid=uid,
        )
        assert r2.slot == slot
        assert r2.times_updated is True

        # Verify updated times
        override = eo.overrides[slot]
        assert override is not None
        assert override["end_time"] == _make_dt(2025, 9, 8)

        # Step 3: Clear via async_update with empty name
        await eo.async_update(
            slot=slot,
            slot_code="",
            slot_name="",
            start_time=_make_dt(2025, 9, 1),
            end_time=_make_dt(2025, 9, 8),
        )

        # Verify cleared
        assert eo.overrides[slot] is None

        # Verify slot is available again
        assert eo.next_slot is not None

    @pytest.mark.asyncio
    async def test_single_slot_system(self) -> None:
        """System with exactly one managed slot works correctly.

        Edge case: ensures single-slot configs don't regress.
        """
        eo = _populated_eo(start_slot=1, max_slots=1)

        # Assign
        r1 = await eo.async_reserve_or_get_slot(
            slot_name="OnlyGuest",
            slot_code="0001",
            start_time=_make_dt(2025, 12, 1),
            end_time=_make_dt(2025, 12, 5),
            uid="uid-only",
        )
        assert r1.slot == 1
        assert r1.is_new is True
        assert eo.next_slot is None

        # Overflow
        r2 = await eo.async_reserve_or_get_slot(
            slot_name="ExtraGuest",
            slot_code="0002",
            start_time=_make_dt(2025, 12, 1),
            end_time=_make_dt(2025, 12, 5),
            uid="uid-extra",
        )
        assert r2.slot is None

        # Clear and reassign
        await eo.async_update(
            slot=1,
            slot_code="",
            slot_name="",
            start_time=_make_dt(2025, 12, 1),
            end_time=_make_dt(2025, 12, 5),
        )
        assert eo.next_slot == 1

        r3 = await eo.async_reserve_or_get_slot(
            slot_name="ReplacementGuest",
            slot_code="0003",
            start_time=_make_dt(2025, 12, 10),
            end_time=_make_dt(2025, 12, 15),
            uid="uid-replacement",
        )
        assert r3.slot == 1
        assert r3.is_new is True
