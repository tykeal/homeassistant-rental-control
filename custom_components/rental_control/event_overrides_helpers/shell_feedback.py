# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Feedback fencing and date-mutation methods for the EventOverrides shell."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing import cast


def get_feedback_generation(self, slot: int) -> int:
    """Return the generation that fences delayed feedback for ``slot``."""
    return cast("int", self._feedback_generations.get(slot, 0))


def begin_feedback_event(self, slot: int, entity_id: str) -> int:
    """Reserve an ordering token for a non-suppressed Keymaster event."""
    key = (slot, entity_id)
    version = cast("int", self._feedback_event_versions.get(key, 0)) + 1
    self._feedback_event_versions[key] = version
    return version


async def async_update_times(
    self,
    slot: int,
    start_time: Any,
    end_time: Any,
    *,
    expected_generation: int | None = None,
    expected_times: tuple[Any, Any] | None = None,
    guard: Callable[[], bool] | None = None,
) -> bool:
    """Apply confirmed buffer dates to the current mapping under its lock."""
    async with self._lock:
        if (
            expected_generation is not None
            and expected_generation != self._feedback_generations.get(slot, 0)
        ):
            return False
        if guard is not None and not guard():
            return False
        override = self._overrides.get(slot)
        if override is None:
            return False
        if (
            expected_times is not None
            and (
                override["start_time"],
                override["end_time"],
            )
            != expected_times
        ):
            return False
        override["start_time"] = start_time
        override["end_time"] = end_time
        self._advance_feedback_generation(slot)
        return True


def advance_feedback_generation(self, slot: int) -> None:
    """Fence queued feedback after an authoritative runtime mutation.

    Generations are equality fences, not operation counts; multiple advances
    during a compound reconciliation are harmless.
    """
    self._feedback_generations[slot] = self._feedback_generations.get(slot, 0) + 1
