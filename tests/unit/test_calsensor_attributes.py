# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Focused parity tests for calendar sensor attributes and slot helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import MagicMock

from freezegun import freeze_time

from custom_components.rental_control.sensors.calsensor_helpers import attributes
from custom_components.rental_control.sensors.calsensor_helpers import slots
from custom_components.rental_control.sensors.calsensor_helpers import state
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    CalendarSensorRenderResult,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    DoorCodeRequest,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    EtaSnapshot,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    EventAttributeSnapshot,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    ParsedReservationAttributes,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    SlotAssignmentContext,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    SlotReadContext,
)
from custom_components.rental_control.sensors.calsensor_helpers.models import (
    SlotReadResult,
)


def _event(uid: str | None = "uid-1") -> MagicMock:
    """Build a representative calendar event."""
    event = MagicMock()
    event.summary = "Reserved - Jane Doe"
    event.description = "Email: jane@example.com"
    event.location = "123 Main St"
    event.start = datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc)
    event.end = datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc)
    event.uid = uid
    return event


def test_model_conversion_preserves_attribute_keys() -> None:
    """Verify model helpers produce the existing dictionary keys."""
    snapshot = EventAttributeSnapshot(
        summary="No reservation",
        description=None,
        location=None,
        start=None,
        end=None,
        uid=None,
        eta_days=None,
        eta_hours=None,
        eta_minutes=None,
        slot_name=None,
        slot_code=None,
        slot_number=None,
    )
    parsed = ParsedReservationAttributes(
        last_four="1234",
        dynamic={"custom_field": "value"},
    )

    assert list(snapshot.as_dict()) == [
        "summary",
        "description",
        "location",
        "start",
        "end",
        "uid",
        "eta_days",
        "eta_hours",
        "eta_minutes",
        "slot_name",
        "slot_code",
        "slot_number",
    ]
    assert parsed.as_dict() == {"last_four": "1234", "custom_field": "value"}


def test_context_models_carry_legacy_values() -> None:
    """Verify grouped slot context values keep the legacy fields."""
    start = datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc)
    end = datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc)
    request = DoorCodeRequest("date_based", 4, start, end, "uid", "desc", None)
    read = SlotReadContext(
        entry_id="entry",
        summary="summary",
        description="description",
        event_prefix="prefix",
        start=start,
        end=end,
        event_overrides_present=True,
        get_slot_name=lambda _summary, _description, _prefix: "slot",
        make_reservation_fingerprint=lambda _entry, _slot, _start, _end: "key",
    )
    assignment = SlotAssignmentContext("slot", "1234", start, end, None, "", 5)

    assert request.code_length == 4
    assert read.entry_id == "entry"
    assert assignment.eta_days == 5


def test_no_reservation_attributes_match_legacy() -> None:
    """Verify no-reservation summaries and cleared attributes."""
    attrs = attributes.build_no_reservation_attributes("Rental")
    assert attrs["summary"] == "Rental No reservation"
    assert attrs["slot_name"] is None
    assert attrs["slot_code"] is None
    assert attrs["slot_number"] is None
    assert attributes.build_no_reservation_summary("") == "No reservation"


@freeze_time("2025-03-10T12:00:00+00:00")
def test_eta_and_event_attributes_match_legacy() -> None:
    """Verify ETA and base event attributes for a future event."""
    event = _event(uid="  uid-1  ")
    eta = attributes.calculate_eta(event.start)
    slot = SlotReadResult("Jane Doe", 4, "9876")
    attrs = attributes.build_event_attributes(event, eta, slot)

    assert eta == EtaSnapshot(eta_days=5, eta_hours=124, eta_minutes=7440)
    assert attrs["uid"] == "uid-1"
    assert attrs["slot_name"] == "Jane Doe"
    assert attrs["slot_number"] == 4
    assert attrs["slot_code"] == "9876"


@freeze_time("2025-03-20T12:00:00+00:00")
def test_past_eta_fields_are_none() -> None:
    """Verify past events produce cleared ETA fields."""
    assert attributes.calculate_eta(_event().start) == EtaSnapshot(None, None, None)


def test_read_slot_uses_patchable_dependencies_and_same_key() -> None:
    """Verify slot lookup uses supplied functions and one identity key."""
    coordinator = MagicMock()
    coordinator.get_slot_assignment.return_value = 12
    coordinator.get_slot_code.return_value = "5555"
    context = SlotReadContext(
        entry_id="entry-1",
        summary="Reserved - Jane Doe",
        description="desc",
        event_prefix="",
        start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
        end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        event_overrides_present=True,
        get_slot_name=MagicMock(return_value="Jane Doe"),
        make_reservation_fingerprint=MagicMock(return_value="identity-key"),
    )

    result = slots.read_slot(context, coordinator)

    assert result == SlotReadResult("Jane Doe", 12, "5555")
    coordinator.get_slot_assignment.assert_called_once_with("identity-key")
    coordinator.get_slot_code.assert_called_once_with("identity-key")


def test_read_slot_skips_fingerprint_without_overrides_or_slot() -> None:
    """Verify reconciliation lookup is skipped when inputs are absent."""
    coordinator = MagicMock()
    make_fingerprint = MagicMock(return_value="identity-key")
    context = SlotReadContext(
        entry_id="entry-1",
        summary="Blocked",
        description=None,
        event_prefix="",
        start=datetime(2025, 3, 15, 16, 0, tzinfo=timezone.utc),
        end=datetime(2025, 3, 20, 11, 0, tzinfo=timezone.utc),
        event_overrides_present=False,
        get_slot_name=MagicMock(return_value=None),
        make_reservation_fingerprint=make_fingerprint,
    )

    assert slots.read_slot(context, coordinator) == SlotReadResult(None, None, None)
    make_fingerprint.assert_not_called()
    coordinator.get_slot_assignment.assert_not_called()
    coordinator.get_slot_code.assert_not_called()


def test_state_helpers_select_and_render_results() -> None:
    """Verify state helpers preserve selection and render formatting."""
    event = _event()
    result = state.render_event_result(
        event,
        {"summary": event.summary},
        {"guest_email": "jane@example.com"},
    )
    no_reservation = state.render_no_reservation("")

    assert state.select_event([event], 0) is event
    assert state.select_event([], 0) is None
    assert result == CalendarSensorRenderResult(
        state="Reserved - Jane Doe - 15 March 2025 16:00",
        event_attributes={"summary": event.summary},
        parsed_attributes={"guest_email": "jane@example.com"},
    )
    assert no_reservation.state == "No reservation"
