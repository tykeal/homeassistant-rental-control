# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared door-code allocator package public API."""

from __future__ import annotations

from .allocator import DoorCodeAllocator
from .singleton import async_get_or_create_allocator
from .singleton import get_allocator

__all__ = ["DoorCodeAllocator", "async_get_or_create_allocator", "get_allocator"]
