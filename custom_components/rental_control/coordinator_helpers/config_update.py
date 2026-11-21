# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Pure configuration-update decisions.

The coordinator shell parses the raw config entry (which requires Home
Assistant validators) and performs Store/entity side effects. This module
holds the pure decisions derived from a config change: whether overrides
are stale, whether buffers changed, and how to recover unbuffered times.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..allocator.association_commit import add_association_transaction_to_envelope
from ..allocator.association_transactions import AssociationReplayState
from ..allocator.association_transactions import AssociationTransaction
from ..allocator.association_transactions import replay_association_chain
from ..allocator.associations import AssociationTuple
from ..allocator.models import ProtectionLoadResult
from ..allocator.models import ProtectionLoadState
from ..const import CONF_ASSOCIATION_GENERATION
from ..const import CONF_ASSOCIATION_MODE
from ..const import CONF_ASSOCIATION_TRANSACTION_ID
from ..const import CONF_LOCK_ENTRY
from ..const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from ..helpers import shift_datetime_by_elapsed


class AssociationApplyState(StrEnum):
    """Sanitized outcome of committing and projecting an association change."""

    CONFIG_MISSING = "config_missing"
    CONFIG_CHANGED = "config_changed"
    INVALID_TARGET = "invalid_target"
    STORAGE_NOT_ACKNOWLEDGED = "storage_not_acknowledged"
    CONFIG_WRITE_SCHEDULED = "config_write_scheduled"
    CONFIG_ALREADY_CURRENT = "config_already_current"
    CONFIG_REPLAY_REQUIRED = "config_replay_required"


ConfigEntryUpdater = Callable[..., None]


def preserve_committed_association_tuple(
    config_data: Mapping[str, Any], option_data: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep tuple metadata out of options unless copied from entry data.

    The legacy lock selector remains an ordinary option until a protected
    association tuple has been durably acknowledged. Once any tuple metadata
    exists, the tuple and its selector are copied only from config-entry data.
    """
    merged = dict(option_data)
    tuple_keys = (
        CONF_VERIFIED_KEYMASTER_ENTRY_ID,
        CONF_ASSOCIATION_GENERATION,
        CONF_ASSOCIATION_TRANSACTION_ID,
        CONF_ASSOCIATION_MODE,
    )
    for key in tuple_keys:
        merged.pop(key, None)

    if any(key in config_data for key in tuple_keys):
        for key in (*tuple_keys, CONF_LOCK_ENTRY):
            if key in config_data:
                merged[key] = config_data[key]
            else:
                merged.pop(key, None)
    else:
        if CONF_LOCK_ENTRY not in merged and CONF_LOCK_ENTRY in config_data:
            merged[CONF_LOCK_ENTRY] = config_data[CONF_LOCK_ENTRY]
    return merged


async def async_commit_association_transaction(
    hass: HomeAssistant,
    protected_store: Any,
    base_envelope: dict[str, Any],
    transaction: AssociationTransaction,
    *,
    entry_updater: ConfigEntryUpdater | None = None,
) -> AssociationApplyState:
    """Acknowledge shared state before changing any RC config entry.

    The protected transaction and HA config-entry update have separate
    durability boundaries. The recorded tuple chain is retained even after
    the config update is scheduled because HA exposes no durable-save
    acknowledgement or writer barrier.
    """
    entries: list[tuple[ConfigEntry, AssociationTuple, AssociationTuple]] = []
    for change in transaction.changes:
        entry = hass.config_entries.async_get_entry(change.rc_entry_id)
        if entry is None:
            return AssociationApplyState.CONFIG_MISSING
        current = AssociationTuple.from_config(entry.data)
        if current != change.old:
            return AssociationApplyState.CONFIG_CHANGED
        entries.append((entry, change.old, change.new))

    target_envelope = add_association_transaction_to_envelope(
        base_envelope, transaction
    )
    if target_envelope is None:
        return AssociationApplyState.INVALID_TARGET

    result: ProtectionLoadResult = await protected_store.async_commit(target_envelope)
    if (
        result.state is not ProtectionLoadState.VALID_V2
        or result.transaction_id != transaction.transaction_id
        or result.generation != transaction.generation
    ):
        return AssociationApplyState.STORAGE_NOT_ACKNOWLEDGED

    for entry, old, _new in entries:
        current_entry = hass.config_entries.async_get_entry(entry.entry_id)
        if (
            current_entry is None
            or AssociationTuple.from_config(current_entry.data) != old
        ):
            return AssociationApplyState.CONFIG_CHANGED

    updater = entry_updater or hass.config_entries.async_update_entry
    try:
        for entry, _old, new in entries:
            current_entry = hass.config_entries.async_get_entry(entry.entry_id)
            if current_entry is None:
                return AssociationApplyState.CONFIG_REPLAY_REQUIRED
            updater(
                current_entry,
                data=new.merge_into_config(current_entry.data),
            )
    except Exception:
        return AssociationApplyState.CONFIG_REPLAY_REQUIRED
    return AssociationApplyState.CONFIG_WRITE_SCHEDULED


def replay_pending_association_transactions(
    hass: HomeAssistant,
    transactions: tuple[AssociationTransaction, ...],
    *,
    entry_updater: ConfigEntryUpdater | None = None,
) -> AssociationApplyState:
    """Replay exact recorded prior tuples without pretending to re-acknowledge.

    All affected entries are validated before any update. A tuple that is
    absent from the durable chain remains unresolved; unrelated config data
    and options are merged from the current in-memory entry.
    """
    changes_by_entry: dict[str, list[Any]] = {}
    seen_transactions: set[str] = set()
    previous_generation = 0
    for transaction in transactions:
        if (
            transaction.transaction_id in seen_transactions
            or transaction.generation <= previous_generation
        ):
            return AssociationApplyState.CONFIG_CHANGED
        seen_transactions.add(transaction.transaction_id)
        previous_generation = transaction.generation
        for change in transaction.changes:
            changes_by_entry.setdefault(change.rc_entry_id, []).append(change)

    replay: list[tuple[ConfigEntry, AssociationTuple]] = []
    changed = False
    for entry_id, changes in changes_by_entry.items():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return AssociationApplyState.CONFIG_MISSING
        current = AssociationTuple.from_config(entry.data)
        state, target = replay_association_chain(current, tuple(changes))
        if state is AssociationReplayState.UNKNOWN or target is None:
            return AssociationApplyState.CONFIG_CHANGED
        if state is AssociationReplayState.REPLAY_PRIOR:
            changed = True
            replay.append((entry, target))
    if not changed:
        return AssociationApplyState.CONFIG_ALREADY_CURRENT

    updater = entry_updater or hass.config_entries.async_update_entry
    try:
        for entry, target in replay:
            current_entry = hass.config_entries.async_get_entry(entry.entry_id)
            if current_entry is None:
                return AssociationApplyState.CONFIG_REPLAY_REQUIRED
            updater(
                current_entry,
                data=target.merge_into_config(current_entry.data),
            )
    except Exception:
        return AssociationApplyState.CONFIG_REPLAY_REQUIRED
    return AssociationApplyState.CONFIG_WRITE_SCHEDULED


def overrides_are_stale(
    overrides_missing: bool,
    lockname_changed: bool,
    max_events: int,
    previous_max_events: int,
    start_slot: int,
    previous_start_slot: int,
) -> bool:
    """Return whether the event-override map must be rebuilt."""
    return (
        overrides_missing
        or lockname_changed
        or max_events != previous_max_events
        or start_slot != previous_start_slot
    )


def buffer_changed(
    before: int,
    after: int,
    previous_before: int,
    previous_after: int,
) -> bool:
    """Return whether either code buffer value changed."""
    return before != previous_before or after != previous_after


def unbuffer_window(
    start: datetime,
    end: datetime,
    old_before: int,
    old_after: int,
) -> tuple[datetime, datetime]:
    """Reverse a previously-applied buffer to recover unbuffered times.

    Keymaster stores already-buffered datetimes. To re-apply a new buffer
    without double counting, the old buffer must first be reversed.
    """
    if old_before:
        start = shift_datetime_by_elapsed(start, timedelta(minutes=old_before))
    if old_after:
        end = shift_datetime_by_elapsed(end, -timedelta(minutes=old_after))
    return start, end
