# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Slot date/time accessor methods for EventOverrides."""

from __future__ import annotations

from datetime import time as dt_time


def get_slot_start_date(self, slot: int):
    """Return the start date of ``slot`` or today when empty."""
    override = self._overrides.get(slot)
    return (
        override["start_time"].date()
        if override and "start_time" in override
        else self._today_date()
    )


def get_slot_start_time(self, slot: int):
    """Return the start time of ``slot`` or midnight when empty."""
    override = self._overrides.get(slot)
    return (
        override["start_time"].time()
        if override and "start_time" in override
        else dt_time()
    )


def get_slot_end_date(self, slot: int):
    """Return the end date of ``slot`` or today when empty."""
    override = self._overrides.get(slot)
    return (
        override["end_time"].date()
        if override and "end_time" in override
        else self._today_date()
    )


def get_slot_end_time(self, slot: int):
    """Return the end time of ``slot`` or midnight when empty."""
    override = self._overrides.get(slot)
    return (
        override["end_time"].time()
        if override and "end_time" in override
        else dt_time()
    )
