# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Durable fence acknowledgement and resolution for Keymaster operations."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import hashlib
import json
import secrets
from typing import Any

from homeassistant.core import HomeAssistant

from .allocator.association_references import ledger_allocation_references
from .allocator.models import ProtectionLoadResult
from .allocator.models import ProtectionLoadState
from .allocator.registry_safety_models import validate_pending_fence
from .const import DOMAIN
from .keymaster_operation_models import VerifiedOperationIntent
from .keymaster_operation_models import _FenceRequest
from .keymaster_operation_validation import _authorization_inputs
from .keymaster_operation_validation import _resolve_required_capabilities


async def _async_acknowledge_fence(
    hass: HomeAssistant,
    request: _FenceRequest,
) -> bool:
    """Commit one current-generation fence with a locked live preflight."""
    from .allocator.registry_safety_models import PendingSlotFence
    from .allocator.registry_snapshot import SnapshotCommitHooks
    from .allocator.registry_snapshot import async_commit_registry_snapshot
    from .allocator.registry_snapshot import serialize_exclusion_snapshots
    from .allocator.singleton import get_allocator

    domain_data = hass.data.get(DOMAIN)
    allocator = get_allocator(hass)
    if (
        not isinstance(domain_data, dict)
        or allocator is None
        or not request.initial.store.uses_async_lock(allocator._lock)
    ):
        return False
    exclusion_generation, exclusions = _current_exclusion_snapshot(
        hass, serialize_exclusion_snapshots
    )

    def safety_transition(
        _candidate: Any,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Append only a fence whose serialized owner matches the request."""
        target_references = ledger_allocation_references(
            data, request.rc_entry_id, request.owner_identity_key
        )
        if target_references != (
            request.owner_reference,
            request.allocation_reference,
            request.slot,
        ):
            raise ValueError("owner_changed")
        records = data.get("pending_fences")
        if not isinstance(records, list) or not all(
            validate_pending_fence(record) for record in records
        ):
            raise ValueError("invalid_fences")
        if any(
            record["original_km_entry_id"] == request.initial.keymaster_entry.entry_id
            and record["slot"] == request.slot
            and record["rc_entry_id"] == request.rc_entry_id
            and record["owner_ref"] == request.owner_reference
            and record["allocation_ref"] == request.allocation_reference
            and record["fence_type"] == request.fence_type
            for record in records
        ):
            raise ValueError("fence_already_present")
        same_slot_clear = any(
            record["original_km_entry_id"] == request.initial.keymaster_entry.entry_id
            and record["slot"] == request.slot
            and record["fence_type"] == "pending_clear"
            for record in records
        )
        if request.fence_type == "allocation" and same_slot_clear:
            raise ValueError("pending_clear")
        if request.fence_type == "pending_clear" and any(
            record["original_km_entry_id"] == request.initial.keymaster_entry.entry_id
            and record["slot"] == request.slot
            and record["fence_type"] == "pending_clear"
            and (
                record["rc_entry_id"] != request.rc_entry_id
                or record["owner_ref"] != request.owner_reference
                or record["allocation_ref"] != request.allocation_reference
            )
            for record in records
        ):
            raise ValueError("ambiguous_pending_clear")
        records.append(
            PendingSlotFence(
                fence_id=secrets.token_hex(16),
                original_km_entry_id=request.initial.keymaster_entry.entry_id,
                slot=request.slot,
                rc_entry_id=request.rc_entry_id,
                owner_ref=request.owner_reference,
                allocation_ref=request.allocation_reference,
                created_transaction_id=transaction_id,
                created_generation=generation,
                fence_type=request.fence_type,
            ).to_record()
        )

    def event_loop_preflight() -> ProtectionLoadResult | None:
        """Revalidate selected identity and store token before any file I/O."""
        current = _authorization_inputs(hass, request.rc_entry_id)
        if (
            current is None
            or current.lifecycle is not request.initial.lifecycle
            or current.lifecycle.generation != request.initial.lifecycle.generation
            or current.keymaster_entry.entry_id
            != request.initial.keymaster_entry.entry_id
            or current.association != request.initial.association
            or current.token != request.initial.token
            or ledger_allocation_references(
                current.loaded.data, request.rc_entry_id, request.owner_identity_key
            )
            != (
                request.owner_reference,
                request.allocation_reference,
                request.slot,
            )
            or _resolve_required_capabilities(
                hass,
                current,
                request.slot,
                request.required_properties,
                allow_native_empty_text=request.allow_native_empty_text,
            )
            is None
        ):
            return ProtectionLoadResult(
                ProtectionLoadState.UNAVAILABLE,
                failure_code="operation_not_current",
            )
        return None

    result = await async_commit_registry_snapshot(
        allocator,
        request.initial.store,
        lambda _registry: None,
        exclusions,
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(
            safety_transition=safety_transition,
            event_loop_preflight=event_loop_preflight,
        ),
    )
    return result.state is ProtectionLoadState.VALID_V2


def _current_exclusion_snapshot(
    hass: HomeAssistant,
    serialize: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Capture complete exclusions and a stable digest on the HA event loop."""
    exclusions = serialize(hass.config_entries.async_entries(DOMAIN))
    encoded = json.dumps(
        exclusions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    generation = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")
    return generation, exclusions


async def async_resolve_verified_clear_fence(
    intent: VerifiedOperationIntent,
) -> bool:
    """Tombstone a confirmed pending-clear fence before local slot release."""
    if intent.fence_type != "pending_clear" or not await intent.async_is_current(
        allow_native_empty_text=True
    ):
        return False
    from .allocator.registry_safety_models import resolve_fence_to_tombstone
    from .allocator.registry_snapshot import SnapshotCommitHooks
    from .allocator.registry_snapshot import async_commit_registry_snapshot
    from .allocator.registry_snapshot import serialize_exclusion_snapshots
    from .allocator.singleton import get_allocator

    hass = intent.hass
    inputs = _authorization_inputs(hass, intent.rc_entry_id)
    allocator = get_allocator(hass)
    if (
        inputs is None
        or allocator is None
        or not inputs.store.uses_async_lock(allocator._lock)
    ):
        return False
    _generation, exclusions = _current_exclusion_snapshot(
        hass, serialize_exclusion_snapshots
    )

    def safety_transition(
        _candidate: Any,
        data: dict[str, Any],
        transaction_id: str,
        generation: int,
    ) -> None:
        """Resolve only this exact fence and retain its original identity."""
        fences = data.get("pending_fences")
        tombstones = data.get("tombstones")
        if not isinstance(fences, list) or not isinstance(tombstones, list):
            raise ValueError("invalid_safety_history")
        matches = [
            fence
            for fence in fences
            if validate_pending_fence(fence)
            and fence["fence_id"] == intent.fence_id
            and fence["fence_type"] == "pending_clear"
            and fence["rc_entry_id"] == intent.rc_entry_id
            and fence["original_km_entry_id"] == intent.keymaster_entry_id
            and fence["slot"] == intent.slot
            and fence["owner_ref"] == intent.owner_reference
            and fence["allocation_ref"] == intent.allocation_reference
        ]
        if len(matches) != 1:
            raise ValueError("clear_fence_changed")
        fence = matches[0]
        fences.remove(fence)
        tombstones.append(
            resolve_fence_to_tombstone(
                fence,
                tombstone_id=secrets.token_hex(16),
                resolved_transaction_id=transaction_id,
                resolved_generation=generation,
                resolution_kind="clear_confirmed",
                retained_at=datetime.now(UTC).isoformat(),
            )
        )

    result = await async_commit_registry_snapshot(
        allocator,
        inputs.store,
        lambda _registry: None,
        exclusions,
        lambda: _current_exclusion_snapshot(hass, serialize_exclusion_snapshots),
        hooks=SnapshotCommitHooks(
            safety_transition=safety_transition,
            event_loop_preflight=lambda: (
                None
                if intent.is_current(allow_native_empty_text=True)
                and (intent.dispatch_guard is None or intent.dispatch_guard())
                else ProtectionLoadResult(
                    ProtectionLoadState.UNAVAILABLE,
                    failure_code="operation_not_current",
                )
            ),
        ),
    )
    return result.state is ProtectionLoadState.VALID_V2
