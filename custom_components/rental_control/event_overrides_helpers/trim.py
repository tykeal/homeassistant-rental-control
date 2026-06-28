# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure trim and prefix helpers for EventOverrides."""

from __future__ import annotations

from ..util import trim_name
from .models import TrimConfig


def strip_prefix(slot_name: str, prefix: str) -> str:
    """Remove a leading ``prefix + ' '`` from ``slot_name``."""
    candidate = f"{prefix} "
    return (
        slot_name[len(candidate) :]
        if prefix and slot_name.startswith(candidate)
        else slot_name
    )


def is_trimmed_match(name_a: str, name_b: str, guest_max: int) -> bool:
    """Return whether one name is the trimmed form of the other."""
    if name_a == name_b or guest_max <= 0:
        return False
    shorter, longer = sorted((name_a, name_b), key=len)
    return trim_name(longer, guest_max) == shorter


def make_trim_config(
    trim_names: bool,
    max_name_length: int,
    event_prefix: str,
    prefix_length: int,
) -> TrimConfig:
    """Build a :class:`TrimConfig` from shell configuration."""
    return TrimConfig(trim_names, max_name_length, event_prefix, prefix_length)


def names_match(stored_name: str, expected_name: str, config: TrimConfig) -> bool:
    """Return whether a stored slot name still belongs to ``expected_name``."""
    if stored_name == expected_name:
        return True
    if not config.trim_names or config.max_name_length <= 0:
        return False
    normalized = strip_prefix(stored_name, config.event_prefix)
    if normalized == expected_name:
        return True
    return len(expected_name) > len(normalized) and is_trimmed_match(
        normalized, expected_name, config.guest_max
    )


def restored_name(stored_name: str, event_name: str, guest_max: int) -> str | None:
    """Return the longer event name when trim matching should restore it."""
    if len(event_name) > len(stored_name) and is_trimmed_match(
        stored_name, event_name, guest_max
    ):
        return event_name
    return None
