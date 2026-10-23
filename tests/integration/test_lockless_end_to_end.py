# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""End-to-end coverage for generator-only (no-lock) config entries.

A no-lock entry publishes nothing to a lock, so the only thing its user
ever sees is the ``slot_code`` attribute on the event sensors.  Since
v3.8.0 removed the sensor-side fallback, the allocator is the sole
source of that value, which makes this the only level at which an
allocator that withholds a code is visible (issue #849).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from aioresponses import aioresponses
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.singleton import get_allocator
from custom_components.rental_control.codegen import generate_date_based_code
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN

from tests.integration.helpers import FROZEN_START_OF_DAY
from tests.integration.helpers import FROZEN_TIME
from tests.integration.helpers import future_ics

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_URL = "https://example.com/calendar.ics"
_DAYS_AHEAD = 5
_DURATION = 5
_CODE_LENGTH = 4


def _expected_code() -> str:
    """Return the date-based code the booking in ``future_ics`` yields."""
    start = FROZEN_TIME + timedelta(days=_DAYS_AHEAD)
    end = start + timedelta(days=_DURATION)
    return generate_date_based_code(_CODE_LENGTH, start, end)


def _entry(entry_id: str, *, lock_key_present: bool = False) -> MockConfigEntry:
    """Build a date-based config entry with the lock picker left unset.

    The config flow normalizes a cleared lock picker to ``None`` before it
    persists the entry, so the two shapes a no-lock entry can really have
    are an absent key and an explicit ``None``.
    """
    data: dict[str, object] = {
        "name": "Lockless Rental",
        "url": _URL,
        "timezone": "UTC",
        "checkin": "16:00",
        "checkout": "11:00",
        "start_slot": 10,
        "max_events": 2,
        "days": 90,
        "verify_ssl": True,
        "ignore_non_reserved": False,
        "code_generation": "date_based",
        "code_length": _CODE_LENGTH,
    }
    if lock_key_present:
        data[CONF_LOCK_ENTRY] = None
    return MockConfigEntry(
        domain=DOMAIN,
        title="Lockless Rental",
        version=10,
        unique_id=f"lockless-{entry_id}",
        data=data,
        entry_id=entry_id,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the entry up against a frozen clock and a stubbed calendar."""
    entry.add_to_hass(hass)
    with (
        aioresponses() as mock_session,
        pytest.MonkeyPatch.context() as patcher,
    ):
        patcher.setattr(dt_util, "now", lambda *_args: FROZEN_TIME)
        patcher.setattr(
            dt_util, "start_of_local_day", lambda *_args: FROZEN_START_OF_DAY
        )
        mock_session.get(
            _URL,
            status=200,
            body=future_ics(days_ahead=_DAYS_AHEAD, duration=_DURATION),
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Drive one more coordinator refresh against the same frozen clock."""
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    with (
        aioresponses() as mock_session,
        pytest.MonkeyPatch.context() as patcher,
    ):
        patcher.setattr(dt_util, "now", lambda *_args: FROZEN_TIME)
        patcher.setattr(
            dt_util, "start_of_local_day", lambda *_args: FROZEN_START_OF_DAY
        )
        mock_session.get(
            _URL,
            status=200,
            body=future_ics(days_ahead=_DAYS_AHEAD, duration=_DURATION),
            repeat=True,
        )
        await coordinator.async_refresh()
        await hass.async_block_till_done()


def _event_sensor_code(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """Return the ``slot_code`` published by the entry's event_0 sensor."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    sensor = next(
        entity
        for entity in entities
        if entity.domain == "sensor" and entity.entity_id.endswith("event_0")
    )
    state = hass.states.get(sensor.entity_id)
    assert state is not None
    code = state.attributes.get("slot_code")
    return str(code) if code is not None else None


@pytest.mark.parametrize("lock_key_present", [False, True])
async def test_no_lock_entry_publishes_its_date_based_code(
    hass: HomeAssistant,
    lock_key_present: bool,
) -> None:
    """A generator-only entry publishes the code its dates imply."""
    entry = _entry("lockless_entry", lock_key_present=lock_key_present)

    await _setup(hass, entry)

    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator.lockname is None
    assert _event_sensor_code(hass, entry) == _expected_code()


async def test_no_lock_entry_keeps_its_code_across_refreshes(
    hass: HomeAssistant,
) -> None:
    """The published code is stable, so a guest's code does not churn."""
    entry = _entry("lockless_stable")

    await _setup(hass, entry)
    first = _event_sensor_code(hass, entry)
    await _refresh(hass, entry)

    assert first == _expected_code()
    assert _event_sensor_code(hass, entry) == first


async def test_no_lock_entry_republishes_after_registry_loss(
    hass: HomeAssistant,
) -> None:
    """Losing the shared registry does not blank a no-lock entry's codes.

    This is the user-visible shape of issue #845: the entry has already
    published a code, the shared registry then goes away, and there is no
    lock whose contents could be adopted to rebuild it.
    """
    entry = _entry("lockless_recovery")

    await _setup(hass, entry)
    assert _event_sensor_code(hass, entry) == _expected_code()

    allocator = get_allocator(hass)
    assert allocator is not None
    allocator._registry = AllocationRegistry(code_ref_salt="recovery-salt")
    allocator._registry_lost = True
    allocator._registry_missing = True

    await _refresh(hass, entry)

    assert _event_sensor_code(hass, entry) == _expected_code()
    assert allocator._registry_lost is False
