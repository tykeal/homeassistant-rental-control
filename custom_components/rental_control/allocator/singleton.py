# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Singleton accessors for the shared door-code allocator."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant

from ..const import ALLOCATOR
from ..const import DOMAIN
from .allocator import DoorCodeAllocator

_CREATE_LOCK = asyncio.Lock()


def get_allocator(hass: HomeAssistant) -> DoorCodeAllocator | None:
    """Return the shared allocator when it has already been created."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    allocator = domain_data.get(ALLOCATOR)
    return allocator if isinstance(allocator, DoorCodeAllocator) else None


async def async_get_or_create_allocator(hass: HomeAssistant) -> DoorCodeAllocator:
    """Create or return the one allocator shared by all config entries."""
    hass.data.setdefault(DOMAIN, {})
    existing = get_allocator(hass)
    if existing is not None:
        return existing
    async with _CREATE_LOCK:
        existing = get_allocator(hass)
        if existing is not None:
            return existing
        allocator = DoorCodeAllocator(hass)
        await allocator.async_load()
        hass.data[DOMAIN][ALLOCATOR] = allocator
        return allocator
