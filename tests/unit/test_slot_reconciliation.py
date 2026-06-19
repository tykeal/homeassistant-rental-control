# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Unit tests for the reconciliation data models (T007).

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
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from custom_components.rental_control.reconciliation import ActionKind
from custom_components.rental_control.reconciliation import DesiredPlan
from custom_components.rental_control.reconciliation import ManagedSlot
from custom_components.rental_control.reconciliation import PlannedSlot
from custom_components.rental_control.reconciliation import Reservation
from custom_components.rental_control.reconciliation import SlotAction
from custom_components.rental_control.reconciliation import SlotMapping
from custom_components.rental_control.reconciliation import SlotStatus
from custom_components.rental_control.reconciliation import StoredActual
from custom_components.rental_control.reconciliation import StoredIdentity

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
