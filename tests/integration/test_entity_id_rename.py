# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for renaming entity_ids through the entity registry.

Home Assistant 2026.11 changes an entity_id in place and calls
``async_entity_id_changed``; older releases remove and re-add the entity.
Either way the renamed entities must keep working under their new id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.const import CHECKIN_SENSOR
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import EARLY_CHECKOUT_EXPIRY_SWITCH
from custom_components.rental_control.const import KEYMASTER_MONITORING_SWITCH
from custom_components.rental_control.coordinator import RentalControlCoordinator
from custom_components.rental_control.sensors.calsensor import RentalControlCalSensor
from custom_components.rental_control.sensors.checkinsensor import CheckinTrackingSensor
from custom_components.rental_control.switch import EarlyCheckoutExpirySwitch
from custom_components.rental_control.switch import KeymasterMonitoringSwitch

from tests.integration.helpers import future_ics

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant

_URL = "https://example.com/calendar.ics"
_ENTITY_CLASSES: list[type[Entity]] = [
    RentalControlCalSensor,
    CheckinTrackingSensor,
    KeymasterMonitoringSwitch,
    EarlyCheckoutExpirySwitch,
]
_SUPPORTS_IN_PLACE = hasattr(Entity, "async_entity_id_changed")


@pytest.fixture
def frozen_calendar() -> Iterator[None]:
    """Serve a calendar with one upcoming booking and skip the slot store."""

    async def _noop_store(_coordinator: RentalControlCoordinator) -> None:
        """Skip the slot store so the test needs no persisted data."""

    with (
        aioresponses() as mock_session,
        patch.object(
            RentalControlCoordinator, "async_load_slot_store", new=_noop_store
        ),
        patch.object(
            RentalControlCoordinator, "async_save_slot_store", new=_noop_store
        ),
    ):
        # A real-clock booking tomorrow keeps the auto check-in timer pending.
        body = future_ics(days_ahead=1, base_time=dt_util.utcnow())
        mock_session.get(_URL, status=200, body=body, repeat=True)
        yield


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a lock-backed entry so every affected entity is created."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rename Rental",
        version=10,
        unique_id="rename-rental",
        data={
            "name": "Rename Rental",
            "url": _URL,
            "timezone": "UTC",
            "checkin": "16:00",
            "checkout": "11:00",
            "start_slot": 10,
            "max_events": 1,
            "days": 90,
            "verify_ssl": True,
            "ignore_non_reserved": False,
            "honor_event_times": False,
            CONF_LOCK_ENTRY: "front_door",
            "code_buffer_before": 0,
            "code_buffer_after": 0,
        },
        entry_id="rename_entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _live_entity(hass: HomeAssistant, entity_class: type[Entity]) -> Any:
    """Return the single live entity of *entity_class* for the entry."""
    for component in ("sensor", "switch"):
        for entity in hass.data["entity_components"][component].entities:
            if type(entity) is entity_class:
                return entity
    raise AssertionError(f"no live {entity_class.__name__}")


async def _rename(hass: HomeAssistant, entity: Entity) -> tuple[str, str]:
    """Rename *entity* through the registry and return (old, new) ids."""
    old_id = entity.entity_id
    domain = old_id.split(".", 1)[0]
    new_id = f"{domain}.renamed_{type(entity).__name__.lower()}"
    er.async_get(hass).async_update_entity(old_id, new_entity_id=new_id)
    await hass.async_block_till_done()
    return old_id, new_id


@pytest.mark.usefixtures("frozen_calendar")
@pytest.mark.parametrize("entity_class", _ENTITY_CLASSES, ids=lambda c: c.__name__)
async def test_renamed_entity_keeps_working(
    hass: HomeAssistant, entity_class: type[Entity]
) -> None:
    """A renamed entity publishes its state under the new id only."""
    entry = await _setup_entry(hass)
    entity = _live_entity(hass, entity_class)
    old_state = hass.states.get(entity.entity_id)
    assert old_state is not None
    timer_before = getattr(entity, "_timer_manager", None)
    scheduled_before = timer_before.scheduled if timer_before else None

    old_id, new_id = await _rename(hass, entity)

    assert hass.states.get(old_id) is None
    new_state = hass.states.get(new_id)
    assert new_state is not None
    assert new_state.state == old_state.state
    live = _live_entity(hass, entity_class)
    assert live.entity_id == new_id

    entry_data = hass.data[DOMAIN][entry.entry_id]
    if entity_class is KeymasterMonitoringSwitch:
        assert entry_data[KEYMASTER_MONITORING_SWITCH] is live
    elif entity_class is EarlyCheckoutExpirySwitch:
        assert entry_data[EARLY_CHECKOUT_EXPIRY_SWITCH] is live
    elif entity_class is CheckinTrackingSensor:
        assert entry_data[CHECKIN_SENSOR] is live
        assert scheduled_before is not None
        assert live._tracked_event_start == scheduled_before.target_time
        if _SUPPORTS_IN_PLACE:
            # The pre-2026.11 remove/re-add fallback cancels the timer.
            scheduled_after = live._timer_manager.scheduled
            assert scheduled_after is not None
            assert scheduled_after.purpose == scheduled_before.purpose
            assert scheduled_after.target_time == scheduled_before.target_time


@pytest.mark.skipif(
    not _SUPPORTS_IN_PLACE, reason="in-place entity_id changes need HA 2026.11+"
)
@pytest.mark.usefixtures("frozen_calendar")
@pytest.mark.parametrize("entity_class", _ENTITY_CLASSES, ids=lambda c: c.__name__)
async def test_rename_updates_entity_in_place(
    hass: HomeAssistant, entity_class: type[Entity]
) -> None:
    """HA detects our opt-in and renames without removing or re-adding."""
    await _setup_entry(hass)
    entity = _live_entity(hass, entity_class)
    timer = getattr(entity, "_timer_manager", None)
    scheduled_before = timer.scheduled if timer else None

    with (
        patch.object(entity_class, "async_added_to_hass") as added,
        patch.object(entity_class, "async_will_remove_from_hass") as removed,
    ):
        _, new_id = await _rename(hass, entity)

    added.assert_not_called()
    removed.assert_not_called()
    assert _live_entity(hass, entity_class) is entity
    assert entity.entity_id == new_id
    assert hass.states.get(new_id) is not None
    if timer is not None:
        assert scheduled_before is not None
        assert timer.scheduled is scheduled_before
