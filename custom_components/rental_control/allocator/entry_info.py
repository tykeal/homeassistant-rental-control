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
from ..const import CONF_CODE_LENGTH
from ..const import DEFAULT_CODE_LENGTH

_LAST_FOUR_CODE_LENGTH = 4

__all__ = [
    "CODE_GENERATOR_LAST_FOUR",
    "code_generator",
    "configured_code_length",
    "describe_owner",
    "describe_slot",
    "entry_config",
    "entry_name",
    "retention_phrase",
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
    """Return whether an entry is configured to derive codes from phone digits.

    The generator setting alone is not enough. ``generate_slot_code``
    uses the phone digits only when the configured code length is four,
    and silently falls back to date-based codes otherwise, so an entry
    set to ``last_four`` with a six digit code is a date-based entry in
    everything but name. Mirror that condition here, or a genuine
    date-based collision between two such entries would be silenced.

    This remains a configuration read, and configuration is only ever
    half the answer. The same silent fallback happens at the right code
    length whenever the reservation text carries no phone number, which
    is now the normal state of every Booking.com backed feed, so an
    entry can report ``last_four`` here while publishing nothing but
    date-based codes. A caller deciding whether to stay silent about a
    code must pair this with
    :func:`.code_origin.owner_code_is_phone_derived`, which asks what
    the code actually was rather than what it was meant to be.
    """
    if code_generator(hass, entry_id) != CODE_GENERATOR_LAST_FOUR:
        return False
    return configured_code_length(hass, entry_id) == _LAST_FOUR_CODE_LENGTH


def configured_code_length(hass: Any, entry_id: str) -> int | None:
    """Return an entry's configured code length, or None if unknown."""
    config = entry_config(hass, entry_id)
    if config is None:
        return None
    length = config.get(CONF_CODE_LENGTH, DEFAULT_CODE_LENGTH)
    if isinstance(length, bool):
        return None
    if isinstance(length, int):
        return length
    if isinstance(length, str) and length.strip().isdecimal():
        return int(length.strip())
    return None


def describe_slot(
    hass: Any,
    entry_id: str,
    lockname: str | None,
    slot: int | None,
) -> str:
    """Return a user-facing description of one entry's lock slot.

    A user knows their configuration as a named entry, a lock and a
    slot number, so that is what notifications say. An entry that
    drives no lock has no slot to name and is described by name alone.
    """
    name = entry_name(hass, entry_id)
    if lockname is None or slot is None:
        return name
    return f"{name} on {lockname} slot {slot}"


def describe_owner(hass: Any, owner: Any) -> str:
    """Return a user-facing description of one allocation owner."""
    return describe_slot(hass, owner.entry_id, owner.lockname, owner.slot)


_RETENTION_PHRASES = {
    "adoption_conflict": "the code is in use by more than one booking",
    "unverifiable_lock": "its lock could not be read this cycle",
    "code_still_programmed": "the code is still programmed in the lock",
    "recovery_fail_closed": "the code registry is still recovering",
}


def retention_phrase(reason: str | None) -> str:
    """Return a readable explanation of a retention reason code."""
    if reason is None:
        return "it could not be released"
    return _RETENTION_PHRASES.get(reason, reason.replace("_", " "))
