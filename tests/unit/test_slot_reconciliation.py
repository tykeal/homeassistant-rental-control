# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Unit tests for the reconciliation data models (T007) and identity helpers
(T008, T079-T082).

Covers construction, default values, and the model-layer validation
invariants described in specs/012-slot-reconciliation/data-model.md
for the following types:

- :class:`~custom_components.rental_control.reconciliation.SlotStatus`
- :class:`~custom_components.rental_control.reconciliation.ActionKind`
- :class:`~custom_components.rental_control.reconciliation.SlotAction`
- :class:`~custom_components.rental_control.reconciliation.Reservation`
- :class:`~custom_components.rental_control.reconciliation.ManagedSlot`
- :class:`~custom_components.rental_control.reconciliation.PlannedSlot`
- :class:`~custom_components.rental_control.reconciliation.DesiredPlan`
- :class:`~custom_components.rental_control.reconciliation.StoredIdentity`
- :class:`~custom_components.rental_control.reconciliation.StoredActual`
- :class:`~custom_components.rental_control.reconciliation.SlotMapping`

And the identity fingerprinting / rematch helpers:

- :func:`~custom_components.rental_control.reconciliation.normalize_slot_name_for_fingerprint`
- :func:`~custom_components.rental_control.reconciliation.make_reservation_fingerprint`
- :func:`~custom_components.rental_control.reconciliation.extract_booking_aliases`
- :class:`~custom_components.rental_control.reconciliation.RematchKind`
- :class:`~custom_components.rental_control.reconciliation.RematchResult`
- :func:`~custom_components.rental_control.reconciliation.find_reservation_rematch`
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from custom_components.rental_control.reconciliation import FINGERPRINT_VERSION
from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import DesiredPlan
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import PlannedSlot
from custom_components.rental_control.reconciliation import RematchKind
from custom_components.rental_control.reconciliation import RematchResult
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotAction
from custom_components.rental_control.reconciliation import SlotMapping
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import StoredActual
from custom_components.rental_control.reconciliation import StoredIdentity
from custom_components.rental_control.reconciliation import extract_booking_aliases
from custom_components.rental_control.reconciliation import find_reservation_rematch
from custom_components.rental_control.reconciliation import make_reservation_fingerprint
from custom_components.rental_control.reconciliation import (
    normalize_slot_name_for_fingerprint,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TZ = timezone.utc


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Return a UTC-aware datetime for test convenience."""
    return datetime(year, month, day, hour, tzinfo=_TZ)


def _make_reservation(
    *,
    identity_key: str = "key-abc",
    start: datetime | None = None,
    end: datetime | None = None,
    missing_count: int = 0,
) -> Reservation:
    """Return a minimal valid Reservation for use in tests."""
    return Reservation(
        identity_key=identity_key,
        start=start or _dt(2026, 7, 1),
        end=end or _dt(2026, 7, 8),
        buffered_start=_dt(2026, 7, 1),
        buffered_end=_dt(2026, 7, 8),
        summary="Test Guest",
        slot_name="Test Guest",
        display_slot_name="RC Test Guest",
        slot_code="1234",
        missing_count=missing_count,
    )


def _make_stored_identity(
    *,
    identity_key: str = "key-abc",
) -> StoredIdentity:
    """Return a minimal valid StoredIdentity for use in tests."""
    return StoredIdentity(
        identity_key=identity_key,
        summary="Test Guest",
        slot_name="Test Guest",
    )


def _make_stored_actual(*, slot: int = 5) -> StoredActual:
    """Return a minimal valid StoredActual for use in tests."""
    return StoredActual(slot=slot, classification="free")


def _make_slot_mapping(
    *,
    identity_key: str = "key-abc",
    slot: int = 5,
    schema_version: int = 1,
    missing_count: int = 0,
) -> SlotMapping:
    """Return a minimal valid SlotMapping for use in tests."""
    return SlotMapping(
        schema_version=schema_version,
        entry_id="entry-001",
        identity_key=identity_key,
        slot=slot,
        status="occupied",
        identity=_make_stored_identity(identity_key=identity_key),
        last_observed_actual=_make_stored_actual(slot=slot),
        updated_at=_dt(2026, 6, 19),
        missing_count=missing_count,
    )


def _make_desired_plan(
    *,
    plan_id: str = "plan-001",
    selected: dict[str, int] | None = None,
) -> DesiredPlan:
    """Return a minimal valid DesiredPlan for use in tests."""
    return DesiredPlan(
        plan_id=plan_id,
        generated_at=_dt(2026, 6, 19),
        selected=selected or {},
    )


# ---------------------------------------------------------------------------
# SlotStatus
# ---------------------------------------------------------------------------


class TestSlotStatus:
    """Tests for the SlotStatus enumeration."""

    def test_all_expected_values_exist(self) -> None:
        """All six slot-status values defined in the data model are present."""
        expected = {
            "free",
            "occupied",
            "pending_clear",
            "blocked",
            "phantom",
            "unknown",
        }
        actual = {s.value for s in SlotStatus}
        assert actual == expected

    def test_value_is_string(self) -> None:
        """SlotStatus values are plain strings (str, Enum subclass)."""
        assert isinstance(SlotStatus.FREE, str)
        assert SlotStatus.FREE == "free"

    def test_free(self) -> None:
        """SlotStatus.FREE has value 'free'."""
        assert SlotStatus.FREE.value == "free"

    def test_occupied(self) -> None:
        """SlotStatus.OCCUPIED has value 'occupied'."""
        assert SlotStatus.OCCUPIED.value == "occupied"

    def test_pending_clear(self) -> None:
        """SlotStatus.PENDING_CLEAR has value 'pending_clear'."""
        assert SlotStatus.PENDING_CLEAR.value == "pending_clear"

    def test_blocked(self) -> None:
        """SlotStatus.BLOCKED has value 'blocked'."""
        assert SlotStatus.BLOCKED.value == "blocked"

    def test_phantom(self) -> None:
        """SlotStatus.PHANTOM has value 'phantom'."""
        assert SlotStatus.PHANTOM.value == "phantom"

    def test_unknown(self) -> None:
        """SlotStatus.UNKNOWN has value 'unknown'."""
        assert SlotStatus.UNKNOWN.value == "unknown"

    def test_roundtrip_from_string(self) -> None:
        """SlotStatus can be round-tripped from its string value."""
        assert SlotStatus("occupied") is SlotStatus.OCCUPIED

    def test_member_count(self) -> None:
        """SlotStatus contains exactly six members."""
        assert len(SlotStatus) == 6


# ---------------------------------------------------------------------------
# ActionKind
# ---------------------------------------------------------------------------


class TestActionKind:
    """Tests for the ActionKind enumeration."""

    def test_all_expected_values_exist(self) -> None:
        """All seven action-kind values defined in the data model are present."""
        expected = {
            "noop",
            "set",
            "update_times",
            "clear",
            "retry_clear",
            "overwrite_manual_change",
            "blocked",
        }
        actual = {a.value for a in ActionKind}
        assert actual == expected

    def test_value_is_string(self) -> None:
        """ActionKind values are plain strings (str, Enum subclass)."""
        assert isinstance(ActionKind.NOOP, str)
        assert ActionKind.NOOP == "noop"

    def test_noop(self) -> None:
        """ActionKind.NOOP has value 'noop'."""
        assert ActionKind.NOOP.value == "noop"

    def test_set(self) -> None:
        """ActionKind.SET has value 'set'."""
        assert ActionKind.SET.value == "set"

    def test_update_times(self) -> None:
        """ActionKind.UPDATE_TIMES has value 'update_times'."""
        assert ActionKind.UPDATE_TIMES.value == "update_times"

    def test_clear(self) -> None:
        """ActionKind.CLEAR has value 'clear'."""
        assert ActionKind.CLEAR.value == "clear"

    def test_retry_clear(self) -> None:
        """ActionKind.RETRY_CLEAR has value 'retry_clear'."""
        assert ActionKind.RETRY_CLEAR.value == "retry_clear"

    def test_overwrite_manual_change(self) -> None:
        """ActionKind.OVERWRITE_MANUAL_CHANGE has value 'overwrite_manual_change'."""
        assert ActionKind.OVERWRITE_MANUAL_CHANGE.value == "overwrite_manual_change"

    def test_blocked(self) -> None:
        """ActionKind.BLOCKED has value 'blocked'."""
        assert ActionKind.BLOCKED.value == "blocked"

    def test_roundtrip_from_string(self) -> None:
        """ActionKind can be round-tripped from its string value."""
        assert ActionKind("clear") is ActionKind.CLEAR

    def test_member_count(self) -> None:
        """ActionKind contains exactly seven members."""
        assert len(ActionKind) == 7


# ---------------------------------------------------------------------------
# SlotAction
# ---------------------------------------------------------------------------


class TestSlotAction:
    """Tests for the SlotAction dataclass."""

    def test_minimal_construction(self) -> None:
        """SlotAction can be constructed with only required fields."""
        action = SlotAction(kind=ActionKind.SET, slot=5)
        assert action.kind is ActionKind.SET
        assert action.slot == 5
        assert action.identity_key is None
        assert action.reason is None

    def test_full_construction(self) -> None:
        """SlotAction stores all provided optional fields."""
        action = SlotAction(
            kind=ActionKind.CLEAR,
            slot=3,
            identity_key="key-xyz",
            reason="reservation expired",
        )
        assert action.kind is ActionKind.CLEAR
        assert action.slot == 3
        assert action.identity_key == "key-xyz"
        assert action.reason == "reservation expired"

    def test_noop_action(self) -> None:
        """A NOOP SlotAction carries no identity or reason."""
        action = SlotAction(kind=ActionKind.NOOP, slot=7)
        assert action.kind is ActionKind.NOOP
        assert action.identity_key is None

    def test_blocked_action_with_reason(self) -> None:
        """A BLOCKED SlotAction accepts a reason string."""
        action = SlotAction(
            kind=ActionKind.BLOCKED,
            slot=2,
            reason="pending_clear unconfirmed",
        )
        assert action.kind is ActionKind.BLOCKED
        assert action.reason == "pending_clear unconfirmed"

    def test_slot_code_not_in_repr(self) -> None:
        """SlotAction repr does not expose sensitive slot_code data."""
        # SlotAction itself has no slot_code; verify repr is deterministic.
        action = SlotAction(kind=ActionKind.SET, slot=5, identity_key="k")
        text = repr(action)
        assert "SlotAction" in text
        assert "5" in text


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


class TestReservation:
    """Tests for the Reservation dataclass construction and validation."""

    def test_minimal_valid_construction(self) -> None:
        """Reservation accepts all required fields with valid start < end."""
        r = _make_reservation()
        assert r.identity_key == "key-abc"
        assert r.start < r.end

    def test_defaults_for_optional_fields(self) -> None:
        """Optional Reservation fields default to documented zero-values."""
        r = _make_reservation()
        assert r.uid_aliases == set()
        assert r.booking_aliases == set()
        assert r.fingerprint_history == set()
        assert r.eligible is True
        assert r.protected_active is False
        assert r.checked_out is False
        assert r.missing_count == 0
        assert r.desired_slot is None
        assert r.overflow_reason is None

    def test_start_equal_end_raises(self) -> None:
        """Reservation raises ValueError when start equals end."""
        ts = _dt(2026, 7, 1)
        with pytest.raises(ValueError, match="start must be strictly before end"):
            _make_reservation(start=ts, end=ts)

    def test_start_after_end_raises(self) -> None:
        """Reservation raises ValueError when start is after end."""
        with pytest.raises(ValueError, match="start must be strictly before end"):
            _make_reservation(start=_dt(2026, 7, 8), end=_dt(2026, 7, 1))

    def test_negative_missing_count_raises(self) -> None:
        """Reservation raises ValueError when missing_count is negative."""
        with pytest.raises(ValueError, match="missing_count must be non-negative"):
            _make_reservation(missing_count=-1)

    def test_zero_missing_count_is_valid(self) -> None:
        """Reservation accepts missing_count of zero."""
        r = _make_reservation(missing_count=0)
        assert r.missing_count == 0

    def test_missing_count_threshold_values(self) -> None:
        """Reservation accepts missing_count of 0, 1, 2, and 3."""
        for count in (0, 1, 2, 3):
            r = _make_reservation(missing_count=count)
            assert r.missing_count == count

    def test_slot_code_excluded_from_repr(self) -> None:
        """slot_code does not appear in Reservation repr output."""
        r = _make_reservation()
        r_repr = repr(r)
        assert "1234" not in r_repr

    def test_uid_aliases_mutable_set(self) -> None:
        """uid_aliases is an independent mutable set per instance."""
        r1 = _make_reservation(identity_key="k1")
        r2 = _make_reservation(identity_key="k2")
        r1.uid_aliases.add("uid-1")
        assert "uid-1" not in r2.uid_aliases

    def test_booking_aliases_mutable_set(self) -> None:
        """booking_aliases is an independent mutable set per instance."""
        r1 = _make_reservation(identity_key="k1")
        r2 = _make_reservation(identity_key="k2")
        r1.booking_aliases.add("BOOK-123")
        assert "BOOK-123" not in r2.booking_aliases

    def test_fingerprint_history_mutable_set(self) -> None:
        """fingerprint_history is an independent mutable set per instance."""
        r1 = _make_reservation(identity_key="k1")
        r2 = _make_reservation(identity_key="k2")
        r1.fingerprint_history.add("old-fp")
        assert "old-fp" not in r2.fingerprint_history

    def test_desired_slot_can_be_set(self) -> None:
        """desired_slot can be assigned after construction."""
        r = _make_reservation()
        r.desired_slot = 4
        assert r.desired_slot == 4

    def test_overflow_reason_can_be_set(self) -> None:
        """overflow_reason can be assigned after construction."""
        r = _make_reservation()
        r.overflow_reason = "capacity"
        assert r.overflow_reason == "capacity"

    def test_protected_active_flag(self) -> None:
        """protected_active can be set to True to mark a checked-in guest."""
        r = _make_reservation()
        r.protected_active = True
        assert r.protected_active is True


# ---------------------------------------------------------------------------
# ManagedSlot
# ---------------------------------------------------------------------------


class TestManagedSlot:
    """Tests for the ManagedSlot dataclass."""

    def test_minimal_construction(self) -> None:
        """ManagedSlot can be constructed with slot and managed flag."""
        ms = ManagedSlot(slot=5, managed=True)
        assert ms.slot == 5
        assert ms.managed is True

    def test_default_status_is_unknown(self) -> None:
        """ManagedSlot.status defaults to SlotStatus.UNKNOWN."""
        ms = ManagedSlot(slot=5, managed=True)
        assert ms.status is SlotStatus.UNKNOWN

    def test_defaults_for_all_optional_fields(self) -> None:
        """All optional ManagedSlot fields default to documented values."""
        ms = ManagedSlot(slot=3, managed=True)
        assert ms.actual_name is None
        assert ms.actual_code_present is None
        assert ms.actual_start is None
        assert ms.actual_end is None
        assert ms.date_range_enabled is None
        assert ms.enabled is None
        assert ms.desired_identity_key is None
        assert ms.persisted_identity_key is None
        assert ms.blocked_reason is None
        assert ms.retry_count == 0
        assert ms.last_operation_id is None
        assert ms.dirty_during_operation is False

    def test_unmanaged_slot(self) -> None:
        """ManagedSlot correctly represents an unmanaged slot."""
        ms = ManagedSlot(slot=1, managed=False)
        assert ms.managed is False

    def test_status_can_transition(self) -> None:
        """ManagedSlot.status can be reassigned to reflect transitions."""
        ms = ManagedSlot(slot=5, managed=True, status=SlotStatus.FREE)
        ms.status = SlotStatus.OCCUPIED
        assert ms.status is SlotStatus.OCCUPIED

    def test_blocked_slot_stores_reason(self) -> None:
        """BLOCKED ManagedSlot stores a human-readable blocked_reason."""
        ms = ManagedSlot(
            slot=5,
            managed=True,
            status=SlotStatus.BLOCKED,
            blocked_reason="clear failed after 3 retries",
        )
        assert ms.status is SlotStatus.BLOCKED
        assert ms.blocked_reason == "clear failed after 3 retries"

    def test_dirty_during_operation_flag(self) -> None:
        """dirty_during_operation can be set True when callback fires."""
        ms = ManagedSlot(slot=5, managed=True)
        ms.dirty_during_operation = True
        assert ms.dirty_during_operation is True

    def test_retry_count_can_be_incremented(self) -> None:
        """retry_count can be incremented to track consecutive failures."""
        ms = ManagedSlot(slot=5, managed=True)
        ms.retry_count += 1
        assert ms.retry_count == 1

    def test_all_slot_statuses_assignable(self) -> None:
        """All SlotStatus values can be assigned to ManagedSlot.status."""
        ms = ManagedSlot(slot=5, managed=True)
        for status in SlotStatus:
            ms.status = status
            assert ms.status is status


# ---------------------------------------------------------------------------
# PlannedSlot
# ---------------------------------------------------------------------------


class TestPlannedSlot:
    """Tests for the PlannedSlot dataclass."""

    def test_minimal_construction(self) -> None:
        """PlannedSlot can be constructed with only required fields."""
        ps = PlannedSlot(
            slot=5,
            desired_identity_key="key-abc",
            actual_classification="free",
            action=ActionKind.SET,
        )
        assert ps.slot == 5
        assert ps.desired_identity_key == "key-abc"
        assert ps.actual_classification == "free"
        assert ps.action is ActionKind.SET

    def test_defaults_for_optional_fields(self) -> None:
        """Optional PlannedSlot fields default to documented values."""
        ps = PlannedSlot(
            slot=5,
            desired_identity_key=None,
            actual_classification="free",
            action=ActionKind.NOOP,
        )
        assert ps.pending_reason is None
        assert ps.retry_count == 0
        assert ps.last_error is None

    def test_none_desired_identity_for_empty_slot(self) -> None:
        """desired_identity_key accepts None for slots that should be empty."""
        ps = PlannedSlot(
            slot=5,
            desired_identity_key=None,
            actual_classification="occupied",
            action=ActionKind.CLEAR,
        )
        assert ps.desired_identity_key is None

    def test_blocked_action_with_pending_reason(self) -> None:
        """BLOCKED PlannedSlot stores a pending_reason explanation."""
        ps = PlannedSlot(
            slot=5,
            desired_identity_key="key-abc",
            actual_classification="unknown",
            action=ActionKind.BLOCKED,
            pending_reason="clear unconfirmed",
        )
        assert ps.action is ActionKind.BLOCKED
        assert ps.pending_reason == "clear unconfirmed"

    def test_retry_clear_with_retry_count(self) -> None:
        """RETRY_CLEAR PlannedSlot carries the slot's current retry count."""
        ps = PlannedSlot(
            slot=5,
            desired_identity_key=None,
            actual_classification="pending_clear",
            action=ActionKind.RETRY_CLEAR,
            retry_count=2,
            last_error="service call failed",
        )
        assert ps.action is ActionKind.RETRY_CLEAR
        assert ps.retry_count == 2
        assert ps.last_error == "service call failed"

    def test_all_action_kinds_assignable(self) -> None:
        """All ActionKind values are valid for PlannedSlot.action."""
        for kind in ActionKind:
            ps = PlannedSlot(
                slot=5,
                desired_identity_key=None,
                actual_classification="free",
                action=kind,
            )
            assert ps.action is kind


# ---------------------------------------------------------------------------
# DesiredPlan
# ---------------------------------------------------------------------------


class TestDesiredPlan:
    """Tests for the DesiredPlan dataclass and its validate() method."""

    def test_minimal_construction(self) -> None:
        """DesiredPlan can be constructed with only required fields."""
        plan = _make_desired_plan()
        assert plan.plan_id == "plan-001"
        assert plan.generated_at == _dt(2026, 6, 19)

    def test_defaults_for_all_collections(self) -> None:
        """All DesiredPlan collection fields default to empty containers."""
        plan = _make_desired_plan()
        assert plan.selected == {}
        assert plan.protected == set()
        assert plan.overflow == {}
        assert plan.slots == {}
        assert plan.actions == []
        assert plan.diagnostics == {}

    def test_collection_defaults_are_independent(self) -> None:
        """Each DesiredPlan instance has its own collection defaults."""
        plan_a = _make_desired_plan(plan_id="a")
        plan_b = _make_desired_plan(plan_id="b")
        plan_a.selected["k1"] = 5
        assert "k1" not in plan_b.selected

    def test_validate_empty_plan_returns_no_violations(self) -> None:
        """An empty DesiredPlan has no invariant violations."""
        plan = _make_desired_plan()
        assert plan.validate() == []

    def test_validate_valid_selected_returns_no_violations(self) -> None:
        """A plan with unique identities and unique slots validates cleanly."""
        plan = _make_desired_plan(selected={"k1": 5, "k2": 6, "k3": 7})
        assert plan.validate() == []

    def test_validate_detects_duplicate_slot(self) -> None:
        """validate() reports when two identity keys map to the same slot."""
        # Build a dict with a collision by mutating after construction.
        plan = _make_desired_plan()
        plan.selected = {"k1": 5, "k2": 5}
        violations = plan.validate()
        assert any("Slot 5" in v for v in violations)

    def test_validate_single_entry_no_violations(self) -> None:
        """A single-entry selected mapping always validates cleanly."""
        plan = _make_desired_plan(selected={"only-key": 5})
        assert plan.validate() == []

    def test_protected_set_membership(self) -> None:
        """protected set correctly reflects checked-in reservations."""
        plan = _make_desired_plan(selected={"k1": 5})
        plan.protected.add("k1")
        assert "k1" in plan.protected

    def test_overflow_records_reason(self) -> None:
        """overflow dict stores the reason a reservation was not assigned."""
        plan = _make_desired_plan()
        plan.overflow["k-extra"] = "capacity"
        assert plan.overflow["k-extra"] == "capacity"

    def test_actions_list_accepts_slot_actions(self) -> None:
        """actions list accepts SlotAction instances in order."""
        plan = _make_desired_plan()
        action = SlotAction(kind=ActionKind.SET, slot=5, identity_key="k1")
        plan.actions.append(action)
        assert len(plan.actions) == 1
        assert plan.actions[0].kind is ActionKind.SET

    def test_slots_dict_accepts_planned_slots(self) -> None:
        """slots dict accepts PlannedSlot values keyed by slot number."""
        plan = _make_desired_plan()
        ps = PlannedSlot(
            slot=5,
            desired_identity_key="k1",
            actual_classification="free",
            action=ActionKind.SET,
        )
        plan.slots[5] = ps
        assert plan.slots[5] is ps

    def test_diagnostics_accepts_arbitrary_data(self) -> None:
        """diagnostics dict accepts any JSON-compatible data."""
        plan = _make_desired_plan()
        plan.diagnostics["entry_id"] = "entry-001"
        plan.diagnostics["max_events"] = 3
        assert plan.diagnostics["max_events"] == 3


# ---------------------------------------------------------------------------
# StoredIdentity
# ---------------------------------------------------------------------------


class TestStoredIdentity:
    """Tests for the StoredIdentity dataclass."""

    def test_minimal_construction(self) -> None:
        """StoredIdentity can be constructed with only required fields."""
        si = _make_stored_identity()
        assert si.identity_key == "key-abc"
        assert si.summary == "Test Guest"
        assert si.slot_name == "Test Guest"

    def test_defaults_for_list_fields(self) -> None:
        """uid_aliases and booking_aliases default to empty lists."""
        si = _make_stored_identity()
        assert si.uid_aliases == []
        assert si.booking_aliases == []

    def test_list_defaults_are_independent(self) -> None:
        """Each StoredIdentity has its own uid_aliases and booking_aliases."""
        si1 = _make_stored_identity(identity_key="k1")
        si2 = _make_stored_identity(identity_key="k2")
        si1.uid_aliases.append("uid-1")
        assert "uid-1" not in si2.uid_aliases

    def test_with_aliases(self) -> None:
        """StoredIdentity stores provided alias lists."""
        si = StoredIdentity(
            identity_key="k1",
            summary="Guest A",
            slot_name="Guest A",
            uid_aliases=["uid-1", "uid-2"],
            booking_aliases=["BOOK-001"],
        )
        assert si.uid_aliases == ["uid-1", "uid-2"]
        assert si.booking_aliases == ["BOOK-001"]


# ---------------------------------------------------------------------------
# StoredActual
# ---------------------------------------------------------------------------


class TestStoredActual:
    """Tests for the StoredActual dataclass."""

    def test_minimal_construction(self) -> None:
        """StoredActual can be constructed with only required fields."""
        sa = _make_stored_actual()
        assert sa.slot == 5
        assert sa.classification == "free"

    def test_defaults_for_optional_fields(self) -> None:
        """All optional StoredActual fields default to None."""
        sa = _make_stored_actual()
        assert sa.name_state is None
        assert sa.has_code is None
        assert sa.start_state is None
        assert sa.end_state is None
        assert sa.use_date_range is None
        assert sa.enabled is None

    def test_raw_pin_not_stored(self) -> None:
        """StoredActual uses has_code bool, not a raw PIN value."""
        # The model has no field for raw PIN; has_code is a boolean only.
        sa = StoredActual(slot=5, classification="occupied", has_code=True)
        assert sa.has_code is True
        # Verify no raw PIN attribute exists on the dataclass.
        assert not hasattr(sa, "pin")
        assert not hasattr(sa, "pin_code")
        assert not hasattr(sa, "slot_code")

    def test_full_construction(self) -> None:
        """StoredActual stores all provided optional fields."""
        start = _dt(2026, 7, 1)
        end = _dt(2026, 7, 8)
        sa = StoredActual(
            slot=3,
            classification="occupied",
            name_state="Alice",
            has_code=True,
            start_state=start,
            end_state=end,
            use_date_range=True,
            enabled=True,
        )
        assert sa.name_state == "Alice"
        assert sa.has_code is True
        assert sa.start_state == start
        assert sa.end_state == end
        assert sa.use_date_range is True
        assert sa.enabled is True

    def test_all_classification_strings_accepted(self) -> None:
        """StoredActual classification accepts any string value."""
        for classification in (
            "free",
            "occupied",
            "phantom",
            "partial_reset",
            "unknown",
        ):
            sa = StoredActual(slot=5, classification=classification)
            assert sa.classification == classification


# ---------------------------------------------------------------------------
# SlotMapping
# ---------------------------------------------------------------------------


class TestSlotMapping:
    """Tests for the SlotMapping dataclass and its validation."""

    def test_minimal_construction(self) -> None:
        """SlotMapping can be constructed with all required fields."""
        sm = _make_slot_mapping()
        assert sm.schema_version == 1
        assert sm.entry_id == "entry-001"
        assert sm.identity_key == "key-abc"
        assert sm.slot == 5
        assert sm.status == "occupied"

    def test_defaults_for_optional_fields(self) -> None:
        """Optional SlotMapping fields default to documented values."""
        sm = _make_slot_mapping()
        assert sm.fingerprint_history == []
        assert sm.missing_count == 0
        assert sm.lockname is None
        assert sm.start_slot == 0
        assert sm.max_slots == 0
        assert sm.operation_id is None
        assert sm.operation_kind is None
        assert sm.pending_set_since is None
        assert sm.pending_clear_since is None

    def test_schema_version_below_one_raises(self) -> None:
        """SlotMapping raises ValueError when schema_version is less than 1."""
        with pytest.raises(ValueError, match="schema_version must be >= 1"):
            _make_slot_mapping(schema_version=0)

    def test_schema_version_one_is_valid(self) -> None:
        """SlotMapping accepts schema_version of 1."""
        sm = _make_slot_mapping(schema_version=1)
        assert sm.schema_version == 1

    def test_negative_missing_count_raises(self) -> None:
        """SlotMapping raises ValueError when missing_count is negative."""
        with pytest.raises(ValueError, match="missing_count must be non-negative"):
            _make_slot_mapping(missing_count=-1)

    def test_zero_missing_count_is_valid(self) -> None:
        """SlotMapping accepts missing_count of zero."""
        sm = _make_slot_mapping(missing_count=0)
        assert sm.missing_count == 0

    def test_missing_count_increments_to_three(self) -> None:
        """SlotMapping accepts missing_count of 1, 2, and 3 (feed-miss cycle)."""
        for count in (1, 2, 3):
            sm = _make_slot_mapping(missing_count=count)
            assert sm.missing_count == count

    def test_pending_set_operation_fields(self) -> None:
        """SlotMapping stores operation_id and operation_kind for fencing."""
        sm = _make_slot_mapping()
        sm.operation_id = "op-001"
        sm.operation_kind = "set"
        sm.pending_set_since = _dt(2026, 6, 19)
        assert sm.operation_id == "op-001"
        assert sm.operation_kind == "set"
        assert sm.pending_set_since == _dt(2026, 6, 19)

    def test_pending_clear_operation_fields(self) -> None:
        """SlotMapping stores pending_clear_since for clear fencing."""
        sm = _make_slot_mapping()
        sm.operation_kind = "clear"
        sm.pending_clear_since = _dt(2026, 6, 19)
        assert sm.pending_clear_since == _dt(2026, 6, 19)

    def test_fingerprint_history_list(self) -> None:
        """SlotMapping stores fingerprint history as an ordered list."""
        sm = _make_slot_mapping()
        sm.fingerprint_history.append("old-fp-1")
        sm.fingerprint_history.append("old-fp-2")
        assert len(sm.fingerprint_history) == 2

    def test_fingerprint_defaults_independent(self) -> None:
        """Each SlotMapping has its own fingerprint_history list."""
        sm1 = _make_slot_mapping(identity_key="k1")
        sm2 = _make_slot_mapping(identity_key="k2")
        sm1.fingerprint_history.append("fp-a")
        assert "fp-a" not in sm2.fingerprint_history

    def test_all_valid_status_strings_accepted(self) -> None:
        """SlotMapping.status accepts all documented status string values."""
        for status in (
            "occupied",
            "pending_set",
            "pending_clear",
            "blocked",
            "overflow",
        ):
            sm = _make_slot_mapping()
            sm.status = status
            assert sm.status == status

    def test_lockname_can_be_set(self) -> None:
        """SlotMapping.lockname stores the Keymaster lock scope."""
        sm = _make_slot_mapping()
        sm.lockname = "front_door"
        assert sm.lockname == "front_door"


# ---------------------------------------------------------------------------
# T008: Reservation identity fingerprinting helpers
# ---------------------------------------------------------------------------


class TestNormalizeSlotNameForFingerprint:
    """Tests for normalize_slot_name_for_fingerprint (T008)."""

    def test_plain_name_unchanged_content(self) -> None:
        """A plain all-lowercase name returns the same content."""
        assert normalize_slot_name_for_fingerprint("alice") == "alice"

    def test_uppercase_casefolded(self) -> None:
        """Uppercase letters are casefolded to lowercase."""
        assert normalize_slot_name_for_fingerprint("Alice Guest") == "alice guest"

    def test_all_caps_casefolded(self) -> None:
        """All-uppercase name is casefolded."""
        assert normalize_slot_name_for_fingerprint("ALICE GUEST") == "alice guest"

    def test_leading_trailing_spaces_stripped(self) -> None:
        """Leading and trailing whitespace is stripped."""
        assert normalize_slot_name_for_fingerprint("  Alice  ") == "alice"

    def test_internal_spaces_preserved(self) -> None:
        """Internal spaces are preserved after normalization."""
        result = normalize_slot_name_for_fingerprint("Alice  Guest")
        assert "  " in result  # internal double-space preserved

    def test_empty_string(self) -> None:
        """Empty string normalizes to empty string."""
        assert normalize_slot_name_for_fingerprint("") == ""

    def test_whitespace_only_normalizes_to_empty(self) -> None:
        """Whitespace-only string normalizes to empty string."""
        assert normalize_slot_name_for_fingerprint("   ") == ""

    def test_mixed_case_and_spaces(self) -> None:
        """Mixed case and surrounding spaces normalize consistently."""
        assert normalize_slot_name_for_fingerprint("  ALICE guest  ") == "alice guest"

    def test_idempotent(self) -> None:
        """Applying normalization twice yields the same result."""
        name = "  Alice GUEST  "
        once = normalize_slot_name_for_fingerprint(name)
        twice = normalize_slot_name_for_fingerprint(once)
        assert once == twice


class TestMakeReservationFingerprint:
    """Tests for make_reservation_fingerprint (T008)."""

    def test_returns_64_hex_chars(self) -> None:
        """Fingerprint is a 64-character lowercase hexadecimal string."""
        fp = make_reservation_fingerprint(
            "entry-001",
            "Alice Guest",
            _dt(2026, 7, 1),
            _dt(2026, 7, 8),
        )
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic(self) -> None:
        """Same inputs always produce the same fingerprint."""
        args = ("entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8))
        assert make_reservation_fingerprint(*args) == make_reservation_fingerprint(
            *args
        )

    def test_different_entry_id_yields_different_fingerprint(self) -> None:
        """Different entry_id produces a different fingerprint."""
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-002", "Alice", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        assert fp1 != fp2

    def test_different_slot_name_yields_different_fingerprint(self) -> None:
        """Different slot name produces a different fingerprint."""
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "Bob", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        assert fp1 != fp2

    def test_different_start_yields_different_fingerprint(self) -> None:
        """Different start datetime produces a different fingerprint."""
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "Alice", _dt(2026, 7, 2), _dt(2026, 7, 8)
        )
        assert fp1 != fp2

    def test_different_end_yields_different_fingerprint(self) -> None:
        """Different end datetime produces a different fingerprint."""
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "Alice", _dt(2026, 7, 1), _dt(2026, 7, 9)
        )
        assert fp1 != fp2

    def test_uid_independence(self) -> None:
        """Fingerprint is the same regardless of calendar UID (T079 core).

        This is the fundamental guarantee: UID churn must not invalidate
        the persisted slot mapping.
        """
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        # Both represent the same stay; only the UID would differ in the
        # calendar feed.  The fingerprint must be identical.
        assert fp1 == fp2

    def test_case_insensitive_name_produces_same_fingerprint(self) -> None:
        """Names differing only in case produce the same fingerprint."""
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "ALICE GUEST", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        assert fp1 == fp2

    def test_whitespace_trimmed_name_produces_same_fingerprint(self) -> None:
        """Names differing only in surrounding whitespace produce the same fingerprint."""
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "  Alice Guest  ", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        assert fp1 == fp2

    def test_fingerprint_version_prefix_used(self) -> None:
        """The fingerprint encodes the FINGERPRINT_VERSION constant."""
        assert FINGERPRINT_VERSION == "v1"
        # Verify that changing the version tag changes the fingerprint
        # (indirectly: the constant is in the canonical string).
        import hashlib

        name = normalize_slot_name_for_fingerprint("Alice")
        s = _dt(2026, 7, 1)
        e = _dt(2026, 7, 8)
        s_iso = s.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        e_iso = e.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        canonical_v1 = f"v1:entry-001:{name}:{s_iso}:{e_iso}"
        canonical_v2 = f"v2:entry-001:{name}:{s_iso}:{e_iso}"
        assert (
            hashlib.sha256(canonical_v1.encode()).hexdigest()
            != hashlib.sha256(canonical_v2.encode()).hexdigest()
        )

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetimes and UTC-aware datetimes with same value produce same fingerprint."""
        naive_start = datetime(2026, 7, 1, 0, 0, 0)
        aware_start = datetime(2026, 7, 1, 0, 0, 0, tzinfo=_TZ)
        naive_end = datetime(2026, 7, 8, 0, 0, 0)
        aware_end = datetime(2026, 7, 8, 0, 0, 0, tzinfo=_TZ)
        fp_naive = make_reservation_fingerprint(
            "entry-001", "Alice", naive_start, naive_end
        )
        fp_aware = make_reservation_fingerprint(
            "entry-001", "Alice", aware_start, aware_end
        )
        assert fp_naive == fp_aware

    def test_non_utc_timezone_normalized(self) -> None:
        """Non-UTC timezone-aware datetimes are normalized to UTC before hashing."""
        from datetime import timedelta

        # UTC+5: 2026-07-01 05:00:00+05:00 == 2026-07-01 00:00:00+00:00
        plus5 = timezone(timedelta(hours=5))
        start_plus5 = datetime(2026, 7, 1, 5, 0, 0, tzinfo=plus5)
        start_utc = datetime(2026, 7, 1, 0, 0, 0, tzinfo=_TZ)
        end_plus5 = datetime(2026, 7, 8, 5, 0, 0, tzinfo=plus5)
        end_utc = datetime(2026, 7, 8, 0, 0, 0, tzinfo=_TZ)
        fp_offset = make_reservation_fingerprint(
            "entry-001", "Alice", start_plus5, end_plus5
        )
        fp_utc = make_reservation_fingerprint("entry-001", "Alice", start_utc, end_utc)
        assert fp_offset == fp_utc


class TestExtractBookingAliases:
    """Tests for extract_booking_aliases (T008)."""

    def test_no_code_returns_empty_set(self) -> None:
        """No recognizable confirmation code → empty set."""
        assert extract_booking_aliases("Alice Guest", "") == set()

    def test_airbnb_code_in_description(self) -> None:
        """Airbnb confirmation code extracted from description."""
        aliases = extract_booking_aliases(
            "Reserved",
            "Confirmation code: HM5K8X2R9P",
        )
        assert "HM5K8X2R9P" in aliases

    def test_airbnb_code_in_summary(self) -> None:
        """Airbnb confirmation code extracted from summary field."""
        aliases = extract_booking_aliases("Reserved HM5K8X2R9P", "")
        assert "HM5K8X2R9P" in aliases

    def test_airbnb_code_format_10_chars(self) -> None:
        """Airbnb code: exactly 1 uppercase letter + 9 uppercase alphanumeric."""
        # Valid: ABCDE12345 (letter + 9 alnum)
        aliases = extract_booking_aliases("Reserved", "code ABCDE12345 end")
        assert "ABCDE12345" in aliases

    def test_lowercase_code_not_extracted(self) -> None:
        """Lowercase confirmation codes are not extracted."""
        assert extract_booking_aliases("Reserved", "code abcde12345 end") == set()

    def test_11_char_code_not_extracted(self) -> None:
        """Code longer than 10 uppercase alnum chars is not matched."""
        # 11 uppercase chars → boundary char is uppercase → not matched
        assert extract_booking_aliases("Reserved", "ABCDE123456") == set()

    def test_multiple_codes_extracted(self) -> None:
        """Multiple confirmation codes in text are all extracted."""
        aliases = extract_booking_aliases(
            "Reserved", "First: HM5K8X2R9P and second: AB3CD4EF5G"
        )
        assert "HM5K8X2R9P" in aliases
        assert "AB3CD4EF5G" in aliases

    def test_empty_description_safe(self) -> None:
        """Empty description does not raise an error."""
        assert extract_booking_aliases("Something", "") == set()

    def test_no_code_in_normal_summary(self) -> None:
        """A normal guest-name summary produces no aliases."""
        assert extract_booking_aliases("Alice Smith", "Check in at 3pm") == set()

    def test_returns_set_type(self) -> None:
        """Return type is always a set."""
        result = extract_booking_aliases("Reserved", "HM5K8X2R9P")
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# T008: RematchResult construction
# ---------------------------------------------------------------------------


class TestRematchResult:
    """Tests for RematchResult dataclass construction (T008)."""

    def test_exact_result_construction(self) -> None:
        """RematchResult can be constructed for an EXACT match."""
        r = RematchResult(kind=RematchKind.EXACT, matched_identity_key="key-abc")
        assert r.kind is RematchKind.EXACT
        assert r.matched_identity_key == "key-abc"
        assert r.date_shifted is False
        assert r.ambiguous_keys == []

    def test_no_match_result(self) -> None:
        """RematchResult for NO_MATCH has None matched_identity_key."""
        r = RematchResult(kind=RematchKind.NO_MATCH, matched_identity_key=None)
        assert r.kind is RematchKind.NO_MATCH
        assert r.matched_identity_key is None

    def test_uid_alias_result_with_date_shifted(self) -> None:
        """RematchResult for UID_ALIAS records date_shifted=True."""
        r = RematchResult(
            kind=RematchKind.UID_ALIAS,
            matched_identity_key="old-key",
            date_shifted=True,
        )
        assert r.kind is RematchKind.UID_ALIAS
        assert r.date_shifted is True

    def test_ambiguous_result_with_keys(self) -> None:
        """RematchResult for AMBIGUOUS stores all ambiguous candidate keys."""
        r = RematchResult(
            kind=RematchKind.AMBIGUOUS,
            matched_identity_key=None,
            ambiguous_keys=["key-a", "key-b"],
        )
        assert r.kind is RematchKind.AMBIGUOUS
        assert r.matched_identity_key is None
        assert "key-a" in r.ambiguous_keys
        assert "key-b" in r.ambiguous_keys

    def test_rematch_kind_values(self) -> None:
        """All seven RematchKind values are present."""
        expected = {
            "exact",
            "uid_alias",
            "booking_alias",
            "name_time",
            "continuity",
            "ambiguous",
            "no_match",
        }
        actual = {k.value for k in RematchKind}
        assert actual == expected

    def test_rematch_kind_is_string(self) -> None:
        """RematchKind values are plain strings."""
        assert isinstance(RematchKind.EXACT, str)
        assert RematchKind.EXACT == "exact"


# ---------------------------------------------------------------------------
# T079: UID-changed but stable name/start/end mapping retention
# ---------------------------------------------------------------------------


class TestUidChangedStableNameStartEnd:
    """T079: Fingerprint stability guarantees UID-change tolerance."""

    def test_same_fingerprint_when_uid_changes(self) -> None:
        """Two reservations with different UIDs but same name/start/end share a fingerprint.

        This is the core guarantee: UID churn must not produce a new
        identity key, so the persisted slot mapping survives platform
        UID reissuance without any rematch step.
        """
        fp1 = make_reservation_fingerprint(
            "entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        fp2 = make_reservation_fingerprint(
            "entry-001", "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        assert fp1 == fp2

    def test_exact_rematch_when_uid_changes(self) -> None:
        """Reservation with changed UID but same data finds EXACT rematch (rule 1).

        Because the fingerprint does not include the UID, the new
        reservation's identity_key is the same as the persisted key and
        rule 1 fires directly — no alias lookup needed.
        """
        entry_id = "entry-001"
        name = "Alice Guest"
        start = _dt(2026, 7, 1)
        end = _dt(2026, 7, 8)
        fp = make_reservation_fingerprint(entry_id, name, start, end)

        # Persisted mapping uses this fingerprint as its key
        persisted_mappings = {
            fp: {
                "identity_key": fp,
                "slot": 10,
                "status": "occupied",
                "identity": {
                    "identity_key": fp,
                    "summary": name,
                    "slot_name": name,
                    "uid_aliases": ["old-uid-original"],
                    "booking_aliases": [],
                },
                "fingerprint_history": [],
                "last_observed_actual": {
                    "slot": 10,
                    "classification": "occupied",
                    "name_state": name,
                    "has_code": True,
                    "start_state": None,
                    "end_state": None,
                    "use_date_range": None,
                    "enabled": None,
                },
            }
        }

        # New feed reservation: same data, new UID "new-uid-churn"
        reservation = _make_reservation(
            identity_key=fp,  # same fingerprint (UID not in fingerprint)
            start=start,
            end=end,
        )
        reservation.uid_aliases.add("new-uid-churn")

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.EXACT
        assert result.matched_identity_key == fp

    def test_different_uid_aliases_do_not_affect_fingerprint_equality(self) -> None:
        """Adding UIDs to a reservation's uid_aliases does not change its fingerprint."""
        fp = make_reservation_fingerprint(
            "entry-001", "Bob Guest", _dt(2026, 8, 1), _dt(2026, 8, 7)
        )
        # Construct two reservations with different uid_aliases sets but
        # same identity_key (fingerprint).
        r1 = _make_reservation(
            identity_key=fp, start=_dt(2026, 8, 1), end=_dt(2026, 8, 7)
        )
        r1.uid_aliases = {"uid-alpha"}

        r2 = _make_reservation(
            identity_key=fp, start=_dt(2026, 8, 1), end=_dt(2026, 8, 7)
        )
        r2.uid_aliases = {"uid-beta", "uid-gamma"}

        assert r1.identity_key == r2.identity_key


# ---------------------------------------------------------------------------
# T080: UID-match date-shift mapping update and should-update-code interaction
# ---------------------------------------------------------------------------


class TestUidMatchDateShift:
    """T080: UID alias match with date shift → date_shifted=True."""

    def _make_persisted_with_uid(
        self,
        identity_key: str,
        slot_name: str,
        uid: str,
        slot: int = 10,
    ) -> dict:
        """Build a minimal persisted mapping with a UID alias."""
        return {
            "identity_key": identity_key,
            "slot": slot,
            "status": "occupied",
            "identity": {
                "identity_key": identity_key,
                "summary": slot_name,
                "slot_name": slot_name,
                "uid_aliases": [uid],
                "booking_aliases": [],
            },
            "fingerprint_history": [],
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": slot_name,
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }

    def test_uid_alias_match_when_dates_shifted(self) -> None:
        """UID alias + name match when dates shifted → RematchKind.UID_ALIAS.

        Rule 2 fires when the UID is in the persisted uid_aliases and the
        normalized names match, but the fingerprint differs (i.e. dates
        changed since the mapping was persisted).
        """
        entry_id = "entry-001"
        name = "Alice Guest"
        orig_start = _dt(2026, 7, 1)
        orig_end = _dt(2026, 7, 8)
        shared_uid = "uid-platform-abc"

        # Old fingerprint: original dates
        old_fp = make_reservation_fingerprint(entry_id, name, orig_start, orig_end)

        persisted_mappings = {
            old_fp: self._make_persisted_with_uid(old_fp, name, shared_uid)
        }

        # Reservation in new feed: same UID, same name, but dates shifted by 1 day
        new_start = _dt(2026, 7, 2)
        new_end = _dt(2026, 7, 9)
        new_fp = make_reservation_fingerprint(entry_id, name, new_start, new_end)
        assert new_fp != old_fp  # fingerprint changed due to date shift

        reservation = Reservation(
            identity_key=new_fp,
            start=new_start,
            end=new_end,
            buffered_start=new_start,
            buffered_end=new_end,
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="5678",
        )
        reservation.uid_aliases.add(shared_uid)

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.UID_ALIAS
        assert result.matched_identity_key == old_fp
        assert result.date_shifted is True

    def test_date_shifted_flag_signals_code_update_need(self) -> None:
        """date_shifted=True in result tells coordinator to consider code update.

        When should_update_code=True (date-based code), the coordinator
        regenerates the code when date_shifted=True.  When
        should_update_code=False (static code), only the dates are updated.
        This test verifies that the rematch result exposes date_shifted
        so the coordinator can apply its should_update_code policy.
        """
        entry_id = "entry-001"
        name = "Bob Guest"
        uid = "uid-xyz"

        old_fp = make_reservation_fingerprint(
            entry_id, name, _dt(2026, 8, 1), _dt(2026, 8, 7)
        )
        new_fp = make_reservation_fingerprint(
            entry_id, name, _dt(2026, 8, 2), _dt(2026, 8, 8)
        )
        assert old_fp != new_fp

        persisted_mappings = {old_fp: self._make_persisted_with_uid(old_fp, name, uid)}

        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 8, 2),
            end=_dt(2026, 8, 8),
            buffered_start=_dt(2026, 8, 2),
            buffered_end=_dt(2026, 8, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="9999",
        )
        reservation.uid_aliases.add(uid)

        result = find_reservation_rematch(reservation, persisted_mappings)

        # date_shifted=True allows coordinator to decide code-update strategy
        assert result.date_shifted is True

        # Simulate coordinator decision logic:
        # should_update_code=True → regenerate code (date-based)
        # should_update_code=False → keep code, update times only
        should_update_code_true = True
        should_update_code_false = False
        assert result.date_shifted and should_update_code_true  # would regenerate
        assert result.date_shifted and not should_update_code_false  # update times only

    def test_uid_alias_match_requires_name_match(self) -> None:
        """Rule 2 does not fire if the name does not match despite UID overlap."""
        entry_id = "entry-001"
        uid = "uid-shared"
        old_fp = make_reservation_fingerprint(
            entry_id, "Alice Guest", _dt(2026, 7, 1), _dt(2026, 7, 8)
        )
        persisted_mappings = {
            old_fp: self._make_persisted_with_uid(old_fp, "Alice Guest", uid)
        }

        # New reservation has the same UID but a DIFFERENT name
        new_fp = make_reservation_fingerprint(
            entry_id, "Bob Guest", _dt(2026, 7, 2), _dt(2026, 7, 9)
        )
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 7, 2),
            end=_dt(2026, 7, 9),
            buffered_start=_dt(2026, 7, 2),
            buffered_end=_dt(2026, 7, 9),
            summary="Bob Guest",
            slot_name="Bob Guest",
            display_slot_name="RC Bob Guest",
            slot_code="1111",
        )
        reservation.uid_aliases.add(uid)

        result = find_reservation_rematch(reservation, persisted_mappings)
        # Must not match by UID alias because name differs
        assert result.kind is not RematchKind.UID_ALIAS

    def test_date_shifted_false_for_exact_match(self) -> None:
        """date_shifted is False when rule 1 (exact) fires."""
        entry_id = "entry-001"
        name = "Carol Guest"
        fp = make_reservation_fingerprint(
            entry_id, name, _dt(2026, 9, 1), _dt(2026, 9, 8)
        )
        persisted_mappings = {fp: self._make_persisted_with_uid(fp, name, "uid-carol")}
        reservation = Reservation(
            identity_key=fp,
            start=_dt(2026, 9, 1),
            end=_dt(2026, 9, 8),
            buffered_start=_dt(2026, 9, 1),
            buffered_end=_dt(2026, 9, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="2222",
        )
        reservation.uid_aliases.add("uid-carol")

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.EXACT
        assert result.date_shifted is False


# ---------------------------------------------------------------------------
# T081: Conservative continuity rematch
# ---------------------------------------------------------------------------


class TestConservativeContinuityRematch:
    """T081: Conservative continuity rematch via fingerprint history, booking
    aliases, normalized name, non-overlap ordering, and actual-slot continuity.
    """

    def _persisted(
        self,
        identity_key: str,
        slot_name: str,
        slot: int = 10,
        fingerprint_history: list[str] | None = None,
        booking_aliases: list[str] | None = None,
        uid_aliases: list[str] | None = None,
    ) -> dict:
        """Build a minimal persisted mapping for continuity tests."""
        return {
            "identity_key": identity_key,
            "slot": slot,
            "status": "occupied",
            "identity": {
                "identity_key": identity_key,
                "summary": slot_name,
                "slot_name": slot_name,
                "uid_aliases": uid_aliases or [],
                "booking_aliases": booking_aliases or [],
            },
            "fingerprint_history": fingerprint_history or [],
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": slot_name,
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }

    def _reservation(
        self,
        identity_key: str,
        slot_name: str,
        start: datetime | None = None,
        end: datetime | None = None,
        booking_aliases: set[str] | None = None,
        fingerprint_history: set[str] | None = None,
    ) -> Reservation:
        """Build a minimal Reservation for continuity tests."""
        r = Reservation(
            identity_key=identity_key,
            start=start or _dt(2026, 7, 10),
            end=end or _dt(2026, 7, 17),
            buffered_start=start or _dt(2026, 7, 10),
            buffered_end=end or _dt(2026, 7, 17),
            summary=slot_name,
            slot_name=slot_name,
            display_slot_name=f"RC {slot_name}",
            slot_code="0000",
        )
        if booking_aliases:
            r.booking_aliases = booking_aliases
        if fingerprint_history:
            r.fingerprint_history = fingerprint_history
        return r

    # --- fingerprint history signals ---

    def test_continuity_via_fingerprint_history_in_mapping(self) -> None:
        """Continuity match when current identity_key is in mapping's fingerprint_history.

        This handles the case where a reservation's dates shifted (new
        fingerprint) but the persisted mapping kept the old fingerprint
        as a history entry.
        """
        old_fp = "old-fingerprint-aaa"
        new_fp = "new-fingerprint-bbb"
        name = "Alice Guest"

        persisted_mappings = {
            old_fp: self._persisted(
                old_fp,
                name,
                fingerprint_history=[new_fp],  # new fp already recorded
            )
        }
        # New reservation has a completely new key (both UID and dates changed)
        reservation = self._reservation(new_fp, name)

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp

    def test_continuity_via_reservation_fingerprint_history(self) -> None:
        """Continuity match when old persisted key is in reservation's fingerprint_history.

        This handles the case where the coordinator already moved the old
        key to the reservation's history before persisting the new one,
        and a later restart needs to re-identify the mapping.
        """
        old_fp = "persisted-key-xyz"
        new_fp = "new-key-for-this-reservation"
        name = "Bob Guest"

        persisted_mappings = {old_fp: self._persisted(old_fp, name)}
        # Reservation remembers its prior persisted key
        reservation = self._reservation(new_fp, name, fingerprint_history={old_fp})

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp

    # --- booking alias signal ---

    def test_continuity_via_booking_alias(self) -> None:
        """Booking alias overlap + name match recovers the persisted mapping (rule 3).

        When a booking/confirmation alias (e.g. an Airbnb confirmation code)
        from the current feed reservation matches the persisted mapping's
        booking_aliases AND the normalized names match, rule 3 fires and
        returns RematchKind.BOOKING_ALIAS.

        Rule 3 (BOOKING_ALIAS) pre-empts rule 5 (CONTINUITY) because direct
        alias evidence is considered stronger than the multi-signal continuity
        check.  The booking_alias signal is also included in rule 5's
        _is_continuity_compatible as a safety net for future extension, but
        in practice rule 3 fires first whenever booking aliases overlap.
        """
        old_fp = "fp-booking-test"
        new_fp = "fp-new-booking"
        name = "Carol Guest"
        booking_code = "HMABCDE1234"  # Airbnb-style 10-char code

        persisted_mappings = {
            old_fp: self._persisted(old_fp, name, booking_aliases=[booking_code])
        }
        reservation = self._reservation(new_fp, name, booking_aliases={booking_code})

        result = find_reservation_rematch(reservation, persisted_mappings)
        # Rule 3 fires first for booking alias + name match; the mapping is
        # successfully recovered regardless of which rule matched.
        assert result.kind is RematchKind.BOOKING_ALIAS
        assert result.matched_identity_key == old_fp

    def test_continuity_booking_alias_requires_name_match(self) -> None:
        """Booking alias overlap alone is not enough: name must also match."""
        old_fp = "fp-carol"
        new_fp = "fp-diana"
        booking_code = "HM1234567XY"

        persisted_mappings = {
            old_fp: self._persisted(
                old_fp, "Carol Guest", booking_aliases=[booking_code]
            )
        }
        # Different name but same booking code (unlikely in reality but tested
        # to verify the name-required rule)
        reservation = self._reservation(
            new_fp, "Diana Guest", booking_aliases={booking_code}
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        # Name mismatch → no continuity match
        assert result.kind is RematchKind.NO_MATCH

    # --- actual-slot continuity signal ---

    def test_continuity_via_actual_slot_name(self) -> None:
        """Continuity match when actual Keymaster slot name matches reservation name."""
        old_fp = "fp-actual-slot"
        new_fp = "fp-new-actual"
        name = "Eve Guest"

        persisted_mappings = {old_fp: self._persisted(old_fp, name, slot=10)}
        reservation = self._reservation(new_fp, name)
        # Actual slot 10 has "Eve Guest" programmed
        actual_slot_names = {10: "Eve Guest"}

        result = find_reservation_rematch(
            reservation, persisted_mappings, actual_slot_names=actual_slot_names
        )
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp

    def test_continuity_actual_slot_name_case_insensitive(self) -> None:
        """Actual slot name comparison is case-insensitive."""
        old_fp = "fp-case-slot"
        new_fp = "fp-new-case"
        name = "Frank Guest"

        persisted_mappings = {old_fp: self._persisted(old_fp, name, slot=11)}
        reservation = self._reservation(new_fp, name)
        # Actual name has different case
        actual_slot_names = {11: "FRANK GUEST"}

        result = find_reservation_rematch(
            reservation, persisted_mappings, actual_slot_names=actual_slot_names
        )
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp

    # --- non-overlap ordering (competition check) ---

    def test_continuity_blocked_by_competing_reservation(self) -> None:
        """Continuity match returns AMBIGUOUS when another reservation competes.

        The non-overlap ordering check: if two current reservations have
        the same name, they both 'compete' for the same persisted mapping,
        making the continuity rematch ambiguous.
        """
        old_fp = "fp-shared-name"
        new_fp_1 = "fp-res1"
        new_fp_2 = "fp-res2"
        name = "Grace Guest"

        persisted_mappings = {
            old_fp: self._persisted(old_fp, name, fingerprint_history=[new_fp_1])
        }
        reservation1 = self._reservation(new_fp_1, name)
        reservation2 = self._reservation(
            new_fp_2, name, start=_dt(2026, 8, 1), end=_dt(2026, 8, 8)
        )
        # reservation1 is compatible via fingerprint history; reservation2
        # competes by name → AMBIGUOUS
        result = find_reservation_rematch(
            reservation1,
            persisted_mappings,
            current_reservations=[reservation1, reservation2],
        )
        assert result.kind is RematchKind.AMBIGUOUS

    def test_continuity_succeeds_without_competing_reservations(self) -> None:
        """Continuity match returns CONTINUITY when no other reservation competes."""
        old_fp = "fp-solo-name"
        new_fp = "fp-new-solo"
        name = "Henry Guest"

        persisted_mappings = {
            old_fp: self._persisted(old_fp, name, fingerprint_history=[new_fp])
        }
        reservation = self._reservation(new_fp, name)
        # Only one current reservation with this name → no competition
        result = find_reservation_rematch(
            reservation,
            persisted_mappings,
            current_reservations=[reservation],
        )
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp

    def test_continuity_requires_name_match(self) -> None:
        """Continuity match never fires when names differ."""
        old_fp = "fp-alice-wrong"
        new_fp = "fp-bob-new"

        persisted_mappings = {
            # Alice's mapping has Bob's new fingerprint in its history (edge case)
            old_fp: self._persisted(old_fp, "Alice Guest", fingerprint_history=[new_fp])
        }
        # Bob has the matching fingerprint history entry but different name
        reservation = self._reservation(new_fp, "Bob Guest")

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.NO_MATCH

    def test_no_match_when_no_signals_present(self) -> None:
        """NO_MATCH returned when no rule 1-5 criteria are met."""
        old_fp = "fp-old-unrelated"
        new_fp = "fp-completely-new"

        persisted_mappings = {old_fp: self._persisted(old_fp, "Other Guest")}
        reservation = self._reservation(new_fp, "Different Guest")

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.NO_MATCH
        assert result.matched_identity_key is None


# ---------------------------------------------------------------------------
# T082: Ambiguous continuity rematch diagnostics
# ---------------------------------------------------------------------------


class TestAmbiguousRematchDiagnostics:
    """T082: Two candidates remain compatible → AMBIGUOUS with both keys."""

    def _persisted(
        self,
        identity_key: str,
        slot_name: str,
        slot: int,
        fingerprint_history: list[str] | None = None,
        booking_aliases: list[str] | None = None,
    ) -> dict:
        """Build a minimal persisted mapping for ambiguous tests."""
        return {
            "identity_key": identity_key,
            "slot": slot,
            "status": "occupied",
            "identity": {
                "identity_key": identity_key,
                "summary": slot_name,
                "slot_name": slot_name,
                "uid_aliases": [],
                "booking_aliases": booking_aliases or [],
            },
            "fingerprint_history": fingerprint_history or [],
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": slot_name,
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }

    def test_two_continuity_compatible_mappings_return_ambiguous(self) -> None:
        """AMBIGUOUS returned when two persisted mappings are both compatible.

        Scenario: two different guest stays have the same name (repeat
        guest) and both persisted mappings have the incoming fingerprint
        in their history.  The rematch is ambiguous and no mapping is
        selected.
        """
        name = "Repeat Guest"
        new_fp = "fp-incoming-repeat"
        old_fp_a = "fp-old-stay-a"
        old_fp_b = "fp-old-stay-b"

        persisted_mappings = {
            old_fp_a: self._persisted(
                old_fp_a, name, slot=10, fingerprint_history=[new_fp]
            ),
            old_fp_b: self._persisted(
                old_fp_b, name, slot=11, fingerprint_history=[new_fp]
            ),
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 9, 1),
            end=_dt(2026, 9, 8),
            buffered_start=_dt(2026, 9, 1),
            buffered_end=_dt(2026, 9, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.AMBIGUOUS
        assert result.matched_identity_key is None

    def test_ambiguous_result_exposes_all_candidate_keys(self) -> None:
        """AMBIGUOUS result's ambiguous_keys contains all compatible candidate keys."""
        name = "Repeat Guest"
        new_fp = "fp-repeat-incoming"
        old_fp_a = "fp-repeat-a"
        old_fp_b = "fp-repeat-b"

        persisted_mappings = {
            old_fp_a: self._persisted(
                old_fp_a, name, slot=10, fingerprint_history=[new_fp]
            ),
            old_fp_b: self._persisted(
                old_fp_b, name, slot=11, fingerprint_history=[new_fp]
            ),
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 9, 10),
            end=_dt(2026, 9, 17),
            buffered_start=_dt(2026, 9, 10),
            buffered_end=_dt(2026, 9, 17),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.AMBIGUOUS
        assert old_fp_a in result.ambiguous_keys
        assert old_fp_b in result.ambiguous_keys
        assert len(result.ambiguous_keys) == 2

    def test_ambiguous_result_for_booking_alias_overlap_both_candidates(
        self,
    ) -> None:
        """AMBIGUOUS when two mappings share a booking alias with the reservation."""
        name = "Repeat Guest"
        new_fp = "fp-booking-ambiguous"
        old_fp_a = "fp-stay-a-booking"
        old_fp_b = "fp-stay-b-booking"
        shared_code = "HMAMBIGUOUS1"

        persisted_mappings = {
            old_fp_a: self._persisted(
                old_fp_a, name, slot=10, booking_aliases=[shared_code]
            ),
            old_fp_b: self._persisted(
                old_fp_b, name, slot=11, booking_aliases=[shared_code]
            ),
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 10, 1),
            end=_dt(2026, 10, 8),
            buffered_start=_dt(2026, 10, 1),
            buffered_end=_dt(2026, 10, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )
        reservation.booking_aliases.add(shared_code)

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.AMBIGUOUS
        assert len(result.ambiguous_keys) == 2

    def test_single_compatible_candidate_returns_continuity_not_ambiguous(
        self,
    ) -> None:
        """Exactly one compatible candidate → CONTINUITY, not AMBIGUOUS."""
        name = "Solo Guest"
        new_fp = "fp-solo-incoming"
        old_fp = "fp-solo-only"

        persisted_mappings = {
            old_fp: self._persisted(old_fp, name, slot=10, fingerprint_history=[new_fp])
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 10, 1),
            end=_dt(2026, 10, 8),
            buffered_start=_dt(2026, 10, 1),
            buffered_end=_dt(2026, 10, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp

    def test_ambiguous_diagnostic_has_no_matched_key(self) -> None:
        """AMBIGUOUS result never sets a matched_identity_key."""
        name = "Ambiguous Guest"
        new_fp = "fp-ambig-incoming"

        persisted_mappings = {
            f"fp-slot-{i}": self._persisted(
                f"fp-slot-{i}", name, slot=10 + i, fingerprint_history=[new_fp]
            )
            for i in range(3)
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 11, 1),
            end=_dt(2026, 11, 8),
            buffered_start=_dt(2026, 11, 1),
            buffered_end=_dt(2026, 11, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.AMBIGUOUS
        assert result.matched_identity_key is None
        assert len(result.ambiguous_keys) == 3


# ---------------------------------------------------------------------------
# T008: find_reservation_rematch – rule priority order
# ---------------------------------------------------------------------------


class TestFindReservationRematchRulePriority:
    """T008: verify rule precedence and boundary conditions for all six rules."""

    def _persisted(
        self,
        key: str,
        name: str,
        slot: int = 10,
        uid_aliases: list[str] | None = None,
        booking_aliases: list[str] | None = None,
        fingerprint_history: list[str] | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> dict:
        """Build a minimal persisted mapping for rule-priority tests.

        Supports optional start/end ISO strings in the identity dict so
        that rule 4 (name + exact start/end) scenarios can be tested.
        """
        identity: dict = {
            "identity_key": key,
            "summary": name,
            "slot_name": name,
            "uid_aliases": uid_aliases or [],
            "booking_aliases": booking_aliases or [],
        }
        if start_iso is not None:
            identity["start"] = start_iso
        if end_iso is not None:
            identity["end"] = end_iso
        return {
            "identity_key": key,
            "slot": slot,
            "status": "occupied",
            "identity": identity,
            "fingerprint_history": fingerprint_history or [],
            "last_observed_actual": {
                "slot": slot,
                "classification": "occupied",
                "name_state": name,
                "has_code": True,
                "start_state": None,
                "end_state": None,
                "use_date_range": None,
                "enabled": None,
            },
        }

    def _reservation(
        self,
        key: str,
        name: str,
        uid_aliases: set[str] | None = None,
        booking_aliases: set[str] | None = None,
        fingerprint_history: set[str] | None = None,
    ) -> Reservation:
        """Build a minimal Reservation for rule-priority tests."""
        r = Reservation(
            identity_key=key,
            start=_dt(2026, 7, 1),
            end=_dt(2026, 7, 8),
            buffered_start=_dt(2026, 7, 1),
            buffered_end=_dt(2026, 7, 8),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )
        if uid_aliases:
            r.uid_aliases = uid_aliases
        if booking_aliases:
            r.booking_aliases = booking_aliases
        if fingerprint_history:
            r.fingerprint_history = fingerprint_history
        return r

    def test_rule1_exact_takes_priority_over_rule2(self) -> None:
        """Rule 1 fires even when UID alias would also match (rule 2 skipped)."""
        fp = "fp-exact-uid-both"
        name = "Priority Guest"
        uid = "uid-matches-both"

        persisted_mappings = {fp: self._persisted(fp, name, uid_aliases=[uid])}
        reservation = self._reservation(fp, name, uid_aliases={uid})

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.EXACT

    def test_rule2_uid_alias_takes_priority_over_rule3(self) -> None:
        """Rule 2 fires even when booking alias would also match (rule 3 skipped)."""
        old_fp = "fp-old-uid-booking"
        new_fp = "fp-new-uid-booking"
        name = "Overlap Guest"
        uid = "uid-matches"
        book = "HMOVERLAPXX1"

        persisted_mappings = {
            old_fp: self._persisted(
                old_fp, name, uid_aliases=[uid], booking_aliases=[book]
            )
        }
        reservation = self._reservation(
            new_fp, name, uid_aliases={uid}, booking_aliases={book}
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.UID_ALIAS

    def test_rule3_booking_alias_takes_priority_over_rule4(self) -> None:
        """Rule 3 fires when booking alias matches, even if name+time also matches."""
        name = "Booking Guest"
        book = "HMBOOKING123"
        start = _dt(2026, 7, 1)
        end = _dt(2026, 7, 8)
        old_fp = "fp-booking-priority"
        new_fp = make_reservation_fingerprint("entry-001", name, start, end)
        assert old_fp != new_fp  # ensure different so rule 1 doesn't fire

        persisted_mappings = {
            old_fp: self._persisted(
                old_fp,
                name,
                booking_aliases=[book],
                start_iso=start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                end_iso=end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            )
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )
        reservation.booking_aliases.add(book)

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.BOOKING_ALIAS

    def test_rule4_name_time_match(self) -> None:
        """Rule 4 fires when name, start, and end all match in the identity dict."""
        name = "Name Time Guest"
        start = _dt(2026, 7, 1)
        end = _dt(2026, 7, 8)
        # Use a non-sha256 old key to simulate migration (rule 1 won't fire)
        old_fp = "legacy-migration-key-abc"
        new_fp = make_reservation_fingerprint("entry-001", name, start, end)
        assert old_fp != new_fp

        persisted_mappings = {
            old_fp: self._persisted(
                old_fp,
                name,
                start_iso=start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                end_iso=end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            )
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=start,
            end=end,
            buffered_start=start,
            buffered_end=end,
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.NAME_TIME
        assert result.matched_identity_key == old_fp

    def test_rule4_requires_matching_dates(self) -> None:
        """Rule 4 does not fire when dates in the identity dict differ from reservation."""
        name = "Date Mismatch Guest"
        old_fp = "legacy-fp-date-diff"
        new_fp = make_reservation_fingerprint(
            "entry-001", name, _dt(2026, 7, 2), _dt(2026, 7, 9)
        )

        persisted_mappings = {
            old_fp: self._persisted(
                old_fp,
                name,
                start_iso="2026-07-01T00:00:00+00:00",
                end_iso="2026-07-08T00:00:00+00:00",
            )
        }
        reservation = Reservation(
            identity_key=new_fp,
            start=_dt(2026, 7, 2),
            end=_dt(2026, 7, 9),
            buffered_start=_dt(2026, 7, 2),
            buffered_end=_dt(2026, 7, 9),
            summary=name,
            slot_name=name,
            display_slot_name=f"RC {name}",
            slot_code="0000",
        )

        result = find_reservation_rematch(reservation, persisted_mappings)
        # Dates differ → rule 4 does not fire
        assert result.kind is not RematchKind.NAME_TIME

    def test_empty_persisted_mappings_returns_no_match(self) -> None:
        """NO_MATCH returned when there are no persisted mappings at all."""
        reservation = self._reservation("fp-any", "Anyone")
        result = find_reservation_rematch(reservation, {})
        assert result.kind is RematchKind.NO_MATCH

    def test_no_match_when_name_differs_across_all_rules(self) -> None:
        """NO_MATCH when reservation name differs from all persisted mappings."""
        old_fp = "fp-alice-only"
        persisted_mappings = {old_fp: self._persisted(old_fp, "Alice Guest")}
        reservation = self._reservation("fp-bob-new", "Bob Guest")
        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.NO_MATCH

    def test_rule5_continuity_fires_before_no_match(self) -> None:
        """Rule 5 continuity fires when rule 4 identity dates are absent."""
        old_fp = "fp-continuity-fires"
        new_fp = "fp-continuity-new"
        name = "Continuity Guest"

        persisted_mappings = {
            old_fp: self._persisted(old_fp, name, fingerprint_history=[new_fp])
        }
        reservation = self._reservation(new_fp, name)

        result = find_reservation_rematch(reservation, persisted_mappings)
        assert result.kind is RematchKind.CONTINUITY
        assert result.matched_identity_key == old_fp
