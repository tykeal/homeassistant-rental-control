# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Policy for issuing codes while the shared registry is unrecovered."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING

from ..const import CONF_LOCK_ENTRY
from ..const import DOMAIN
from ..helpers import configured_lock_entry
from ..helpers import configured_lock_slug
from .models import AllocationRequest
from .models import AllocationResult

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


def unobserved_lock_entries(allocator: DoorCodeAllocator) -> frozenset[str]:
    """Return configured lock-backed entries that have not adopted.

    An empty pending-adoption set does not prove every lock has been
    read. ``_seed_pending_adoption`` never enrolls a disabled entry, and
    ``async_setup_entry`` unregisters an entry whose setup fails, so
    both leave the gate empty while their codes stay programmed in a
    lock. Adoption is therefore tracked positively in
    ``_adopted_locks``, from cycles that actually completed, and against
    the lock that was read: an entry whose options later point at a
    different lock has not observed that one, and an entry whose latest
    cycle was incomplete has stopped observing its own.
    """
    entries = getattr(allocator.hass, "config_entries", None)
    if entries is None:
        return frozenset()
    adopted: Mapping[str, str] = getattr(allocator, "_adopted_locks", {})
    return frozenset(
        entry.entry_id
        for entry in entries.async_entries(DOMAIN)
        if (lockname := configured_lockname(getattr(entry, "data", {}))) is not None
        and adopted.get(entry.entry_id) != lockname
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
    "configured_lockname",
    "entry_has_lock",
    "issuance_block",
    "lockless_recovery_allowed",
    "unobserved_lock_entries",
]
