# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config entry facts the allocator needs to talk about an entry.

The allocator only ever holds an ``entry_id``, which is a 32 character
UUID.  That is the right handle for the log and the wrong one for a
notification, where a user recognises their entry by its name.  It is
also not enough to decide whether a shared code is a problem, because
that depends on how the owning entry generates codes.

Both answers live on the config entry, so they are resolved here, once,
with the same defensive reading.  Home Assistant exposes
``ConfigEntry.data`` as a read-only ``MappingProxyType`` rather than a
``dict``: testing for ``dict`` made every real entry look empty and
silently disabled a safety mechanism in production (issue #850), so
every read here accepts any :class:`~collections.abc.Mapping`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..const import CODE_GENERATOR_LAST_FOUR
from ..const import CONF_CODE_GENERATION

__all__ = [
    "CODE_GENERATOR_LAST_FOUR",
    "code_generator",
    "entry_config",
    "entry_name",
    "uses_last_four",
]


def _config_entry(hass: Any, entry_id: str) -> Any | None:
    """Return one config entry, or None when it cannot be looked up."""
    entries = getattr(hass, "config_entries", None)
    getter = getattr(entries, "async_get_entry", None)
    if getter is None:
        return None
    try:
        return getter(entry_id)
    except Exception:  # pragma: no cover - defensive
        return None


def entry_config(hass: Any, entry_id: str) -> Mapping[str, Any] | None:
    """Return an entry's effective configuration mapping.

    Options are normally folded back into ``data`` by the update
    listener, so ``data`` is authoritative; ``options`` is consulted
    only for a key ``data`` does not carry, which covers an entry read
    between a reconfigure and that fold.
    """
    entry = _config_entry(hass, entry_id)
    if entry is None:
        return None
    data = getattr(entry, "data", None)
    options = getattr(entry, "options", None)
    merged: dict[str, Any] = {}
    if isinstance(options, Mapping):
        merged.update(options)
    if isinstance(data, Mapping):
        merged.update(data)
    return merged if merged else None


def entry_name(hass: Any, entry_id: str) -> str:
    """Return the entry's friendly title for user-facing text.

    A missing title or a deleted entry falls back to a short prefix of
    the entry id.  It is still an identifier, but it is eight characters
    rather than thirty-two, and it keeps two unnamed entries in one
    message distinguishable.
    """
    entry = _config_entry(hass, entry_id)
    title = getattr(entry, "title", None) if entry is not None else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return f"unnamed entry {entry_id[:8]}"


def code_generator(hass: Any, entry_id: str) -> str | None:
    """Return an entry's configured code generator, or None if unknown.

    ``None`` means "could not be determined", not "default".  Callers
    use it to fail toward reporting rather than toward silence.
    """
    config = entry_config(hass, entry_id)
    if config is None:
        return None
    generator = config.get(CONF_CODE_GENERATION)
    if isinstance(generator, str) and generator.strip():
        return generator.strip()
    return None


def uses_last_four(hass: Any, entry_id: str) -> bool:
    """Return whether an entry derives codes from guest phone digits."""
    return code_generator(hass, entry_id) == CODE_GENERATOR_LAST_FOUR
