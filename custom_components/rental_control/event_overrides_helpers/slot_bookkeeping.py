# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure slot-ordering helpers and request normalizers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import SlotReservationRequest
from .models import SlotUpdateRequest

_RESERVATION_FIELDS = ("slot_name", "slot_code", "start_time", "end_time")
_RESERVATION_POSITIONAL = _RESERVATION_FIELDS + ("uid", "prefix")
_RESERVATION_KW = _RESERVATION_POSITIONAL
_UPDATE_FIELDS = ("slot", "slot_code", "slot_name", "start_time", "end_time")
_UPDATE_POSITIONAL = _UPDATE_FIELDS + ("prefix",)
_UPDATE_KW = _UPDATE_POSITIONAL


def get_slots_with_values(overrides: Mapping[int, object | None]) -> list[int]:
    """Return sorted occupied slot numbers."""
    return sorted(slot for slot, override in overrides.items() if override is not None)


def get_slots_without_values(
    overrides: Mapping[int, object | None], max_slot: int = 0
) -> list[int]:
    """Return sorted free slot numbers greater than ``max_slot``."""
    return sorted(
        slot
        for slot, override in overrides.items()
        if override is None and slot > max_slot
    )


def compute_next_slot(
    overrides: Mapping[int, object | None], start_slot: int, max_slots: int
) -> int | None:
    """Return the next free greedy slot, or ``None`` when full/unready."""
    if len(overrides) != max_slots:
        return None
    occupied = get_slots_with_values(overrides)
    if len(occupied) == max_slots:
        return None
    max_slot = occupied[-1] if occupied else start_slot - 1
    higher = get_slots_without_values(overrides, max_slot)
    if higher:
        return higher[0]
    free_any = get_slots_without_values(overrides)
    return free_any[0] if free_any else None


def normalize_reservation_request(
    *values: Any, **legacy: Any
) -> SlotReservationRequest:
    """Normalize accepted ``async_reserve_or_get_slot`` call styles."""
    request = legacy.pop("request", None)
    if isinstance(request, SlotReservationRequest):
        if values or legacy:
            msg = "SlotReservationRequest cannot be combined with extra values"
            raise TypeError(msg)
        return request
    positional = ([request] if request is not None else []) + list(values)
    resolved = _resolve_arguments(
        positional,
        legacy,
        positional_fields=_RESERVATION_POSITIONAL,
        keyword_fields=_RESERVATION_KW,
        context="async_reserve_or_get_slot",
    )
    resolved.setdefault("uid", None)
    resolved.setdefault("prefix", None)
    return SlotReservationRequest(**resolved)


def normalize_update_request(*values: Any, **legacy: Any) -> SlotUpdateRequest:
    """Normalize accepted ``async_update`` and ``update`` call styles."""
    request = legacy.pop("update", None) or legacy.pop("request", None)
    if isinstance(request, SlotUpdateRequest):
        if values or legacy:
            msg = "SlotUpdateRequest cannot be combined with extra values"
            raise TypeError(msg)
        return request
    positional = ([request] if request is not None else []) + list(values)
    resolved = _resolve_arguments(
        positional,
        legacy,
        positional_fields=_UPDATE_POSITIONAL,
        keyword_fields=_UPDATE_KW,
        context="event_override_update",
    )
    resolved.setdefault("prefix", None)
    return SlotUpdateRequest(**resolved)


def _resolve_arguments(
    positional: list[Any],
    legacy: dict[str, Any],
    *,
    positional_fields: tuple[str, ...],
    keyword_fields: tuple[str, ...],
    context: str,
) -> dict[str, Any]:
    """Resolve positional and keyword arguments into one dict."""
    unknown = set(legacy) - set(keyword_fields)
    if unknown:
        msg = f"Unknown {context} arguments: {sorted(unknown)}"
        raise TypeError(msg)
    if len(positional) > len(positional_fields):
        msg = f"Too many positional values for {context}"
        raise TypeError(msg)
    resolved = dict(zip(positional_fields, positional, strict=False))
    for name, value in legacy.items():
        if name in resolved:
            msg = f"Duplicate value for {context} argument {name!r}"
            raise TypeError(msg)
        resolved[name] = value
    required = (
        positional_fields[:4]
        if context == "async_reserve_or_get_slot"
        else positional_fields[:5]
    )
    missing = [name for name in required if name not in resolved]
    if missing:
        msg = f"Missing required {context} arguments: {missing}"
        raise TypeError(msg)
    return resolved
