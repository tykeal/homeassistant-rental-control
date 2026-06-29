# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
##############################################################################
# COPYRIGHT 2022 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control utils compatibility shell."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.components.persistent_notification import async_dismiss as pn_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt

from . import helpers as _helpers
from . import keymaster_services as _keymaster_services
from . import state_handlers as _state_handlers
from .helpers import EventIdentity
from .helpers import OperationResult
from .helpers import _ensure_datetime
from .helpers import _keymaster_text_state_token
from .helpers import _raise_first_gather_exception
from .helpers import add_call
from .helpers import apply_buffer
from .helpers import async_reload_package_platforms
from .helpers import check_gather_results
from .helpers import compute_early_expiry_time
from .helpers import delete_folder
from .helpers import delete_rc_and_base_folder
from .helpers import gen_uuid
from .helpers import get_entry_data
from .helpers import get_slot_name
from .helpers import is_cleared_keymaster_text_state
from .helpers import is_unreadable_keymaster_text_state
from .helpers import normalize_keymaster_text_state
from .helpers import normalize_uid
from .helpers import trim_name

_LOGGER = logging.getLogger(__name__)
_SET_CODE_CONFIRMATION_TIMEOUT = 5.0
__all__ = [
    "EventIdentity",
    "OperationResult",
    "_SET_CODE_CONFIRMATION_TIMEOUT",
    "_async_wait_for_expected_datetime",
    "_async_wait_for_expected_name",
    "_ensure_datetime",
    "_keymaster_text_state_token",
    "_raise_first_gather_exception",
    "_state_has_non_string_value",
    "_state_matches_expected_datetime",
    "_state_matches_expected_name",
    "add_call",
    "apply_buffer",
    "async_fire_clear_code",
    "async_fire_set_code",
    "async_fire_update_times",
    "async_reload_package_platforms",
    "async_track_state_change_event",
    "check_gather_results",
    "compute_early_expiry_time",
    "delete_folder",
    "delete_rc_and_base_folder",
    "dt",
    "gen_uuid",
    "get_entry_data",
    "get_event_identities",
    "get_event_names",
    "get_slot_name",
    "handle_state_change",
    "is_cleared_keymaster_text_state",
    "is_unreadable_keymaster_text_state",
    "normalize_keymaster_text_state",
    "normalize_uid",
    "pn_create",
    "pn_dismiss",
    "trim_name",
]


def _keymaster_deps() -> _keymaster_services.KeymasterServiceDeps:
    """Build Keymaster service dependencies from current util attributes."""
    return _keymaster_services.KeymasterServiceDeps(
        sleep=asyncio.sleep,
        track_state_change=async_track_state_change_event,
        confirmation_timeout=_SET_CODE_CONFIRMATION_TIMEOUT,
        create_notification=pn_create,
        dismiss_notification=pn_dismiss,
        logger=_LOGGER,
        add_call=add_call,
        check_gather_results=check_gather_results,
        raise_first_gather_exception=_raise_first_gather_exception,
        apply_buffer=apply_buffer,
        ensure_datetime=_ensure_datetime,
        trim_name=trim_name,
        is_cleared_text=is_cleared_keymaster_text_state,
        is_unreadable_text=is_unreadable_keymaster_text_state,
    )


def _state_deps() -> _state_handlers.StateHandlerDeps:
    """Build state handler dependencies from current util attributes."""
    return _state_handlers.StateHandlerDeps(
        sleep=asyncio.sleep,
        logger=_LOGGER,
        normalize_keymaster_text_state=normalize_keymaster_text_state,
        trim_name=trim_name,
    )


async def async_fire_clear_code(
    coordinator: Any, slot: int, expected_name: str | None = None
) -> OperationResult:
    """Fire a clear_code signal."""
    return await _keymaster_services.async_fire_clear_code(
        coordinator, slot, expected_name, _keymaster_deps()
    )


async def async_fire_set_code(
    coordinator: Any, event: Any, slot: int
) -> OperationResult:
    """Set codes into a slot."""
    return await _keymaster_services.async_fire_set_code(
        coordinator, event, slot, _keymaster_deps()
    )


async def async_fire_update_times(
    coordinator: Any, event: Any, slot: int
) -> OperationResult:
    """Update times on slot."""
    return await _keymaster_services.async_fire_update_times(
        coordinator, event, slot, _keymaster_deps()
    )


async def _async_wait_for_expected_name(
    hass: HomeAssistant, entity_id: str, name: str, timeout: float
) -> bool:
    """Wait briefly for one name entity to match the expected slot name."""
    return await _keymaster_services._async_wait_for_expected_name(
        hass, entity_id, name, timeout, _keymaster_deps()
    )


async def _async_wait_for_expected_datetime(
    hass: HomeAssistant, entity_id: str, expected: Any, timeout: float
) -> bool:
    """Wait briefly for one datetime entity to match the expected value."""
    return await _keymaster_services._async_wait_for_expected_datetime(
        hass, entity_id, expected, timeout, _keymaster_deps()
    )


def _state_matches_expected_name(
    hass: HomeAssistant, entity_id: str, name: str
) -> bool:
    """Return whether the entity state currently matches the expected name."""
    return _keymaster_services._state_matches_expected_name(hass, entity_id, name)


def _state_has_non_string_value(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether the entity exists with a non-HA state value."""
    return _keymaster_services._state_has_non_string_value(hass, entity_id)


def _state_matches_expected_datetime(
    hass: HomeAssistant, entity_id: str, expected: Any
) -> bool:
    """Return whether the entity state matches the expected datetime."""
    return _keymaster_services._state_matches_expected_datetime(
        hass, entity_id, expected
    )


def get_event_identities(
    rc: Any, calendar: list[Any] | None = None
) -> list[EventIdentity]:
    """Get structured event identities for slot reconciliation."""
    return _helpers.get_event_identities(rc, calendar=calendar)


def get_event_names(rc: Any, calendar: list[Any] | None = None) -> list[str]:
    """Get the current event names from coordinator data."""
    return [eid.name for eid in get_event_identities(rc, calendar=calendar)]


async def handle_state_change(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    event: Event[EventStateChangedData],
) -> None:
    """Listener to track state changes of Keymaster input entities."""
    await _state_handlers.handle_state_change(hass, config_entry, event, _state_deps())
