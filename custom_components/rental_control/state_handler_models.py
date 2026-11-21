# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed state-listener context and runtime dependencies."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import Event
from homeassistant.core import EventStateChangedData
from homeassistant.core import HomeAssistant

from .keymaster_identity import CapabilityIdentity


@dataclass(frozen=True, slots=True)
class StateHandlerDeps:
    """Runtime dependencies supplied by the util compatibility shell."""

    sleep: Callable[..., Awaitable[Any]]
    logger: logging.Logger
    normalize_keymaster_text_state: Callable[[Any], str | None]
    trim_name: Callable[[str, int], str]


@dataclass(frozen=True, slots=True)
class StateChangeContext:
    """Resolved state-change context for one Keymaster entity event."""

    hass: HomeAssistant
    event: Event[EventStateChangedData]
    coordinator: Any
    lockname: str
    entity_id: str
    event_new_value: Any
    event_has_new_value: bool
    slot_num: int
    existing_override: dict[str, Any] | None
    property_key: str | None = None
    capability_entity_ids: dict[str, str] | None = None
    lifecycle_generation: int | None = None
    capability_identity: CapabilityIdentity | None = None
    config_entry_id: str | None = None


@dataclass(frozen=True, slots=True)
class SlotStateSnapshot:
    """Home Assistant states needed to normalize a slot update."""

    slot_code_entity_id: str
    slot_name_entity_id: str
    start_time_entity_id: str
    end_time_entity_id: str
    slot_code: Any
    slot_name: Any
    g_start_time: Any
    g_end_time: Any


VERIFIED_FEEDBACK_PROPERTIES = frozenset(
    {
        "button.code_slots:{slot}.reset",
        "datetime.code_slots:{slot}.accesslimit_date_range_end",
        "datetime.code_slots:{slot}.accesslimit_date_range_start",
        "switch.code_slots:{slot}.accesslimit_date_range_enabled",
        "switch.code_slots:{slot}.enabled",
        "text.code_slots:{slot}.name",
        "text.code_slots:{slot}.pin",
    }
)
