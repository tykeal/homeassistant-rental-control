# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Policy for issuing codes while the shared registry is unrecovered."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING

from ..const import CONF_LOCK_ENTRY
from ..const import CONF_MAX_EVENTS
from ..const import CONF_START_SLOT
from ..const import DOMAIN
from ..helpers import configured_lock_entry
from ..helpers import configured_lock_slug
from . import entry_info
from .models import AllocationOrigin
from .models import AllocationRequest
from .models import AllocationResult
from .models import ObservedLock

if TYPE_CHECKING:
    from .allocator import DoorCodeAllocator

_LOGGER = logging.getLogger(__name__)


def entry_has_lock(data: object) -> bool:
    """Return whether config entry data references a Keymaster lock.

    Home Assistant exposes ``ConfigEntry.data`` as a read-only
    ``MappingProxyType`` rather than a ``dict``, so this accepts any
    mapping.  Testing for ``dict`` made every real entry look lockless.
    """
    if not isinstance(data, Mapping):
        return False
    return configured_lock_entry(data.get(CONF_LOCK_ENTRY)) is not None


def configured_lockname(data: object) -> str | None:
    """Return the slugified lock an entry is currently configured for."""
    if not isinstance(data, Mapping):
        return None
    return configured_lock_slug(data.get(CONF_LOCK_ENTRY))


def configured_slots(data: object) -> frozenset[int] | None:
    """Return the managed slot range an entry is configured for.

    ``None`` means the range cannot be read, which no adoption covers.
    """
    if not isinstance(data, Mapping):
        return None
    try:
        start = int(str(data.get(CONF_START_SLOT)))
        count = int(str(data.get(CONF_MAX_EVENTS)))
    except ValueError:
        return None
    return frozenset(range(start, start + count))


def is_observed(adopted: ObservedLock | None, data: object) -> bool:
    """Return whether an adoption covers the entry's current lock and range.

    The lock must be the one read, and every configured slot must have
    been read: widening or moving the range exposes slots that may hold
    codes the registry never saw (#856). Narrowing it does not.
    """
    lockname = configured_lockname(data)
    slots = configured_slots(data)
    return (
        adopted is not None
        and lockname is not None
        and adopted.lockname == lockname
        and slots is not None
        and slots <= adopted.slots
    )


def gate_entries(hass: object, *, include_disabled: bool) -> set[str]:
    """Return lock-backed entries the adoption gate must wait for.

    A disabled entry runs no cycles, but unloading it clears no lock
    slot, so its codes stay programmed. While the registry is intact it
    still records them; once the registry is lost nothing does, and the
    gate must also wait for disabled entries (#854).
    """
    entries = getattr(hass, "config_entries", None)
    if entries is None:
        return set()
    return {
        entry.entry_id
        for entry in entries.async_entries(DOMAIN)
        if entry_has_lock(getattr(entry, "data", {}))
        and (include_disabled or getattr(entry, "disabled_by", None) is None)
    }


def entry_is_disabled(hass: object, entry_id: str) -> bool:
    """Return whether a configured entry is currently disabled."""
    entries = getattr(hass, "config_entries", None)
    get_entry = getattr(entries, "async_get_entry", None)
    entry = get_entry(entry_id) if callable(get_entry) else None
    return entry is not None and getattr(entry, "disabled_by", None) is not None


def describe_pending_entry(
    allocator: DoorCodeAllocator, entry_id: str, *, friendly: bool = False
) -> str:
    """Return a pending entry label annotated with its blocking slots.

    The log is addressed to a maintainer and keeps the entry id; a
    notification is addressed to the user, who knows the entry by
    the name they gave it.
    """
    label = entry_info.entry_name(allocator.hass, entry_id) if friendly else entry_id
    if entry_is_disabled(allocator.hass, entry_id):
        return (
            f"{label} (disabled: enable it so its lock is read, or clear "
            "its codes from the lock and delete it)"
        )
    slots = sorted(allocator._pending_adoption_slots.get(entry_id, ()))
    if not slots:
        return label
    noun = "slots" if len(slots) > 1 else "slot"
    return f"{label} (unreadable {noun} {', '.join(map(str, slots))})"


def keeps_gate_closed(allocator: DoorCodeAllocator, entry_id: str) -> bool:
    """Return whether an unloading entry must stay pending adoption.

    Disabling an entry unloads it. If the registry is lost and the entry
    has not adopted, the codes in its lock are recorded nowhere, so it
    stays in the gate until it is enabled and adopts, or is deleted.
    """
    return (
        allocator._registry_lost
        and entry_id in gate_entries(allocator.hass, include_disabled=True)
        and entry_is_disabled(allocator.hass, entry_id)
    )


def continuity_required(request: AllocationRequest, reissuing: frozenset[str]) -> bool:
    """Return whether a request must prove it keeps the guest's code.

    A generator-only identity that was published but has no registry
    record lost it with the registry: recovery flags clear on the first
    sound cycle of any entry, which may come before this identity is
    restored or after it was refused. Its code is then recorded nowhere,
    so it is bound to its proven preference on every cycle, not just
    while the flags are set. A deliberate re-issue is exempt, since the
    operator asked for a new code.
    """
    return (
        request.previously_published
        and request.lockname is None
        and request.slot is None
        and request.identity_key not in reissuing
    )


def origin_proven(request: AllocationRequest) -> bool:
    """Return whether recovery may restore this request's preference.

    Two lockless entries can prefer the same code; the second was then
    issued a collision-resolved alternative. After a registry loss the
    preference may look free to either, so a published identity is
    given it back only if the per-entry store recorded that it was
    originally issued its preference (#855). Anything else would
    silently change a code the guest holds.
    """
    if not request.previously_published:
        return True
    if request.issued_origin is AllocationOrigin.PREFERRED:
        return True
    _LOGGER.warning(
        "Declined recovery for generator-only entry %s:%s: the code it was "
        "last issued is not recorded as its preferred code, so restoring "
        "the preference could change a code the guest already holds",
        request.entry_id,
        request.identity_key,
    )
    return False


def unobserved_lock_entries(allocator: DoorCodeAllocator) -> frozenset[str]:
    """Return configured lock-backed entries that have not adopted.

    An empty pending-adoption set does not prove every lock has been
    read. ``_seed_pending_adoption`` enrolls a disabled entry only once
    the registry is known to be lost, and ``async_setup_entry``
    unregisters an entry whose setup fails, so the gate can be empty
    while codes stay programmed in a lock. Adoption is therefore tracked
    positively in ``_adopted_locks``, from cycles that actually
    completed, and against the lock and slots that were read: an entry
    whose options later point at a different lock or a wider range has
    not observed it, and an entry whose latest cycle was incomplete has
    stopped observing its own.
    """
    entries = getattr(allocator.hass, "config_entries", None)
    if entries is None:
        return frozenset()
    adopted: Mapping[str, ObservedLock] = getattr(allocator, "_adopted_locks", {})
    return frozenset(
        entry.entry_id
        for entry in entries.async_entries(DOMAIN)
        if entry_has_lock(data := getattr(entry, "data", {}))
        and not is_observed(adopted.get(entry.entry_id), data)
    )


def lockless_recovery_allowed(
    allocator: DoorCodeAllocator,
    request: AllocationRequest,
    *,
    lockless_cycle: bool,
) -> bool:
    """Return whether a lost registry may still issue for this request.

    Fail-closed exists because a lost registry cannot prove a code is
    unused, and re-issuing one that is already programmed into a lock
    would hand two guests the same code. A generator-only entry programs
    no lock and observes no slots, so it has nothing to adopt and would
    otherwise wait forever for an adoption that can never happen.

    Recovery is therefore allowed only when every one of these holds:

    * the whole cycle is lockless and the request claims no physical
      slot, so nothing this entry issues can reach a lock;
    * no entry is still pending its first adoption, so every enabled
      lock-backed entry has re-registered the codes its locks hold;
    * every configured lock-backed entry has actually completed an
      adoption cycle, so no lock holds codes ``select_code`` cannot see;
    * the request carries a real preferred code rather than a
      placeholder, so recovery restores a code a guest already has
      instead of inventing one.
    """
    return (
        lockless_cycle
        and request.lockname is None
        and request.slot is None
        and request.preferred_code_known
        and not allocator._pending_adoption
        and not unobserved_lock_entries(allocator)
    )


def issuance_block(
    allocator: DoorCodeAllocator,
    request: AllocationRequest,
    *,
    lockless_cycle: bool,
) -> tuple[AllocationResult | None, bool]:
    """Return any result blocking issuance, and whether this is recovery.

    A ``None`` result means issuance may proceed. The boolean reports
    whether it proceeds as recovery from a lost registry, which binds
    the caller to the preferred code.
    """
    if request.previously_published and not request.preferred_code_known:
        _LOGGER.warning(
            "Withholding a code for %s:%s: it has been published before but "
            "no code is recorded for it, so any code issued now would be one "
            "the guest has never been given",
            request.entry_id,
            request.identity_key,
        )
        return AllocationResult(code=None, reason="published_code_unknown"), False
    if not request.issuance_allowed:
        return AllocationResult(code=None, reason="unaccounted_slots"), False
    if allocator._pending_adoption:
        return AllocationResult(code=None, reason="adoption_pending"), False
    if not allocator._registry_lost or not (
        request.previously_published or not allocator._registry_missing
    ):
        return None, False
    if not lockless_recovery_allowed(allocator, request, lockless_cycle=lockless_cycle):
        _LOGGER.warning(
            "Declined allocation for %s:%s after registry loss",
            request.entry_id,
            request.identity_key,
        )
        return AllocationResult(code=None, reason="recovery_fail_closed"), False
    _LOGGER.info(
        "Re-issuing a code for generator-only entry %s:%s after registry "
        "loss; this entry drives no lock, so there is nothing to adopt "
        "and no physical slot a re-issued code could collide with",
        request.entry_id,
        request.identity_key,
    )
    return None, True


__all__ = [
    "continuity_required",
    "configured_lockname",
    "configured_slots",
    "describe_pending_entry",
    "is_observed",
    "entry_has_lock",
    "entry_is_disabled",
    "gate_entries",
    "keeps_gate_closed",
    "origin_proven",
    "issuance_block",
    "lockless_recovery_allowed",
    "unobserved_lock_entries",
]
