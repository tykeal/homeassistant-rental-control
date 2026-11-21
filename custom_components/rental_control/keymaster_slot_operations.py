# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared per-Keymaster-entry and slot operation serialization."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .keymaster_lifecycle import get_keymaster_lifecycle

_LOCKS_KEY = "_keymaster_verified_slot_operation_locks"
_HELD_KEYS: ContextVar[frozenset[tuple[int, int, str, int]]] = ContextVar(
    "rental_control_held_slot_operation_locks", default=frozenset()
)


@asynccontextmanager
async def async_slot_operation(coordinator: Any, slot: int) -> AsyncIterator[None]:
    """Serialize a full consumer pipeline for one selected lock slot.

    The operation lock lives in hass.data, so separate RC entries targeting
    the same verified Keymaster entry and slot share one lock. A synchronous
    local-pending count is published before the first await; nested calls in
    the same task are reentrant. The allocator-wide lock is never held here.
    """
    hass = getattr(coordinator, "hass", None)
    rc_entry_id = _coordinator_entry_id(coordinator)
    lifecycle = (
        get_keymaster_lifecycle(hass, rc_entry_id)
        if isinstance(hass, HomeAssistant) and rc_entry_id is not None
        else None
    )
    if not isinstance(hass, HomeAssistant) or lifecycle is None:
        yield
        return
    event_overrides = getattr(coordinator, "event_overrides", None)
    pending = getattr(event_overrides, "_slot_operations_pending", None)
    if not isinstance(pending, dict):
        raise RuntimeError("verified slot pending state unavailable")
    async with async_keymaster_slot_operation(
        hass,
        lifecycle.keymaster_entry_id,
        slot,
        pending=pending,
    ):
        yield


@asynccontextmanager
async def async_keymaster_slot_operation(
    hass: HomeAssistant,
    keymaster_entry_id: str,
    slot: int,
    *,
    pending: dict[int, int] | None = None,
) -> AsyncIterator[None]:
    """Acquire one shared physical-entry/slot lock without allocator locking."""
    if (
        isinstance(slot, bool)
        or not isinstance(slot, int)
        or slot < 1
        or not isinstance(keymaster_entry_id, str)
        or not keymaster_entry_id
    ):
        raise ValueError("invalid verified Keymaster slot")
    key = (keymaster_entry_id, slot)
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("verified slot operation requires an asyncio task")
    # Context variables propagate into child tasks. Include task identity so a
    # spawned task cannot mistake its inherited context for lock ownership.
    context_key = (id(hass), id(task), key[0], key[1])
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not isinstance(domain_data, dict):
        raise RuntimeError("verified slot operation lock unavailable")
    locks = domain_data.setdefault(_LOCKS_KEY, {})
    if not isinstance(locks, dict):
        raise RuntimeError("verified slot operation lock unavailable")
    lock = locks.setdefault(key, asyncio.Lock())
    if not isinstance(lock, asyncio.Lock):
        raise RuntimeError("verified slot operation lock unavailable")

    if pending is not None:
        pending[slot] = pending.get(slot, 0) + 1

    held = _HELD_KEYS.get()
    token = None
    acquired = False
    try:
        if context_key not in held:
            await lock.acquire()
            acquired = True
            token = _HELD_KEYS.set(held | {context_key})
        yield
    finally:
        if token is not None:
            _HELD_KEYS.reset(token)
        if acquired:
            lock.release()
        if pending is not None:
            remaining = pending.get(slot, 0) - 1
            if remaining > 0:
                pending[slot] = remaining
            else:
                pending.pop(slot, None)


def _coordinator_entry_id(coordinator: Any) -> str | None:
    """Return one exact RC entry ID without inferring Keymaster identity."""
    config_entry = getattr(coordinator, "config_entry", None)
    entry_id = getattr(config_entry, "entry_id", None) or getattr(
        coordinator, "_entry_id", None
    )
    return entry_id if isinstance(entry_id, str) and entry_id else None
