# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for calendar refresh cycles.

These tests verify that the coordinator refresh pipeline works end-to-end:
initial data load, scheduled refresh, sensor/calendar state propagation,
door-code generation, and independent multi-entry updates.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

from aioresponses import aioresponses
import homeassistant.util.dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

from tests.fixtures import calendar_data
from tests.integration.helpers import FROZEN_TIME
from tests.integration.helpers import future_ics

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ---------------------------------------------------------------------------
# T112 – initial data load
# ---------------------------------------------------------------------------


async def test_initial_data_load(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify first refresh fetches and processes calendar data.

    After integration setup the coordinator should have loaded the ICS
    feed and populated its calendar list with parsed events.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
    assert coordinator.calendar_loaded is True
    assert len(coordinator.calendar) > 0


# ---------------------------------------------------------------------------
# T113 – scheduled refresh
# ---------------------------------------------------------------------------


async def test_scheduled_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify automatic refresh happens after the refresh interval elapses.

    Calls coordinator.update() twice: once to seed, then after advancing
    past next_refresh to confirm a second fetch occurs.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
        first_next_refresh = coordinator.next_refresh

    # Advance past the refresh interval and trigger update
    future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(dt_util, "start_of_local_day", return_value=future),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            repeat=True,
        )

        await coordinator.update()
        await hass.async_block_till_done()

    assert coordinator.next_refresh > first_next_refresh


# ---------------------------------------------------------------------------
# T114 – sensor state updates on refresh
# ---------------------------------------------------------------------------


async def test_sensor_updates_on_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify sensor entity states reflect data after coordinator refresh.

    After setup, sensors are created but not yet updated with event data.
    A subsequent coordinator update (with time advanced past next_refresh)
    triggers sensor updates so their state includes the guest name.
    """
    mock_config_entry.add_to_hass(hass)

    ics_body = future_ics()

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]
        assert len(coordinator.event_sensors) == mock_config_entry.data["max_events"]

        # Advance past next_refresh so a second update triggers a full refresh
        # which also calls async_update on all event sensors
        future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(dt_util, "start_of_local_day", return_value=future),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        await coordinator.update()
        await hass.async_block_till_done()

    first_sensor = coordinator.event_sensors[0]
    assert first_sensor.state is not None
    assert "Test Guest" in first_sensor.state


# ---------------------------------------------------------------------------
# T115 – calendar entity reflects new events
# ---------------------------------------------------------------------------


async def test_calendar_updates_on_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Verify calendar entity reflects events after refresh.

    The coordinator.event should be set to the next upcoming event
    after the initial data load.
    """
    mock_config_entry.add_to_hass(hass)

    ics_body = future_ics()

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            mock_config_entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][mock_config_entry.entry_id][COORDINATOR]

    assert coordinator.event is not None
    assert coordinator.event.summary == "Reserved: Test Guest"


# ---------------------------------------------------------------------------
# T116 – door code generation on refresh
# ---------------------------------------------------------------------------


async def test_door_code_generation_on_refresh(
    hass: HomeAssistant,
) -> None:
    """Verify door codes are generated during refresh when configured.

    Uses a config entry with code_generation enabled. After the initial
    setup, a second coordinator update (past next_refresh) triggers
    sensor updates which generate door codes from event data.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Code Test",
        data={
            "name": "Code Test",
            "url": "https://example.com/calendar.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 3,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "code_generation": "date_based",
            "code_length": 4,
        },
        entry_id="test_code_entry",
    )
    entry.add_to_hass(hass)

    ics_body = future_ics()

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(
            entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]

        # Trigger a second refresh to populate sensors with event data
        future = FROZEN_TIME + timedelta(minutes=coordinator.refresh_frequency + 1)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=future),
        patch.object(dt_util, "start_of_local_day", return_value=future),
    ):
        mock_session.get(
            entry.data["url"],
            status=200,
            body=ics_body,
            repeat=True,
        )

        await coordinator.update()
        await hass.async_block_till_done()

    first_sensor = coordinator.event_sensors[0]

    # Sensor with an event should have generated a door code (stored as slot_code)
    attrs = first_sensor.extra_state_attributes
    assert attrs.get("slot_code") is not None
    assert len(attrs["slot_code"]) == 4
    assert attrs["slot_code"].isdigit()


# ---------------------------------------------------------------------------
# T117 – concurrent calendar updates (multiple entries)
# ---------------------------------------------------------------------------


async def test_concurrent_calendar_updates(
    hass: HomeAssistant,
) -> None:
    """Verify multiple config entries update independently.

    Sets up two separate integration entries, each with its own calendar
    URL and ICS data, and confirms they maintain independent state.
    Version is set to 7 to skip migrations that would overwrite
    unique_id with gen_uuid(dt.now()) and cause a collision.
    """
    entry_a = MockConfigEntry(
        domain=DOMAIN,
        title="Rental A",
        unique_id="unique_rental_a",
        version=7,
        data={
            "name": "Rental A",
            "url": "https://example.com/a.ics",
            "timezone": "America/New_York",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 2,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "creation_datetime": "2025-01-01T00:00:00",
        },
        entry_id="entry_a",
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        title="Rental B",
        unique_id="unique_rental_b",
        version=7,
        data={
            "name": "Rental B",
            "url": "https://example.com/b.ics",
            "timezone": "America/Chicago",
            "checkin": "15:00",
            "checkout": "10:00",
            "start_slot": 20,
            "max_events": 3,
            "days": 180,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "creation_datetime": "2025-02-01T00:00:00",
        },
        entry_id="entry_b",
    )

    ics_a = future_ics(summary="Reserved: Guest A")
    ics_b = future_ics(summary="Reserved: Guest B", days_ahead=10)

    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    with (
        aioresponses() as mock_session,
        patch.object(dt_util, "now", return_value=FROZEN_TIME),
        patch.object(dt_util, "start_of_local_day", return_value=FROZEN_TIME),
    ):
        mock_session.get(entry_a.data["url"], status=200, body=ics_a, repeat=True)
        mock_session.get(entry_b.data["url"], status=200, body=ics_b, repeat=True)

        # HA component setup auto-loads all registered config entries for the
        # domain.  Calling async_setup on entry_a triggers async_setup_component
        # which in turn sets up every entry added to hass for this domain.
        assert await hass.config_entries.async_setup(entry_a.entry_id)
        await hass.async_block_till_done()

    coord_a = hass.data[DOMAIN][entry_a.entry_id][COORDINATOR]
    coord_b = hass.data[DOMAIN][entry_b.entry_id][COORDINATOR]

    assert coord_a.name == "Rental A"
    assert coord_b.name == "Rental B"
    assert coord_a.max_events == 2
    assert coord_b.max_events == 3
    assert len(coord_a.event_sensors) == 2
    assert len(coord_b.event_sensors) == 3

    # Each coordinator loaded its own calendar independently
    assert coord_a.calendar_loaded is True
    assert coord_b.calendar_loaded is True
    assert coord_a.event.summary == "Reserved: Guest A"
    assert coord_b.event.summary == "Reserved: Guest B"
