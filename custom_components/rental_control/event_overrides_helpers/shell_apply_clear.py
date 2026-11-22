# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verified and legacy clear application helpers."""

from __future__ import annotations

from typing import Any

from ..keymaster_operations import async_resolve_verified_clear_fence
from ..keymaster_operations import prepare_verified_intent
from ..keymaster_operations import verified_lifecycle_active
from ..keymaster_slot_operations import async_slot_operation
from .apply_clear import decide_clear_result_mutation


async def apply_clear(
    self: Any,
    coordinator: Any,
    slot: int,
    *,
    preflight_read: bool = False,
    owner_identity_key: str | None = None,
):
    """Serialize intent, reset dispatch, and local-clear reconciliation."""
    async with async_slot_operation(coordinator, slot):
        return await _apply_clear_locked(
            self,
            coordinator,
            slot,
            preflight_read=preflight_read,
            owner_identity_key=owner_identity_key,
        )


async def _apply_clear_locked(
    self: Any,
    coordinator: Any,
    slot: int,
    *,
    preflight_read: bool = False,
    owner_identity_key: str | None = None,
):
    """Apply a CLEAR, RETRY_CLEAR, or RESET action for one slot."""
    operation_id = str(self._module.uuid.uuid4())
    expected_name = self.get_slot_name(slot) or None
    verified = verified_lifecycle_active(coordinator)
    intent = await _prepare_clear_intent(
        coordinator, slot, owner_identity_key, verified
    )
    if verified and intent is None:
        return self._operation_result_type(kind="clear", slot=slot, unconfirmed=True)
    if preflight_read:
        preflight_result = await _resolve_clear_preflight(
            self, coordinator, slot, expected_name, operation_id, intent
        )
        if preflight_result is not None:
            return preflight_result
    await _set_pending_clear(self, slot, operation_id)
    result = await _dispatch_clear(self, coordinator, slot, expected_name, intent)
    return await _apply_clear_result(self, slot, operation_id, intent, result)


async def _prepare_clear_intent(
    coordinator: Any,
    slot: int,
    owner_identity_key: str | None,
    verified: bool,
) -> Any:
    """Prepare a pending-clear fence only for an acknowledged owner."""
    if not verified or owner_identity_key is None:
        return None
    return await prepare_verified_intent(
        coordinator,
        slot,
        owner_identity_key,
        (
            f"button.code_slots:{slot}.reset",
            f"text.code_slots:{slot}.name",
            f"text.code_slots:{slot}.pin",
        ),
        "pending_clear",
    )


async def _resolve_clear_preflight(
    self: Any,
    coordinator: Any,
    slot: int,
    expected_name: str | None,
    operation_id: str,
    intent: Any,
) -> Any:
    """Resolve an already-empty slot without repeating the reset command."""
    result = await self._preflight_clear_result(
        coordinator, slot, expected_name, intent=intent
    )
    if result is None or not result.confirmed or intent is None:
        return result
    if not await async_resolve_verified_clear_fence(intent):
        await _set_pending_clear(self, slot, operation_id)
        return self._operation_result_type(kind="clear", slot=slot, unconfirmed=True)
    async with self._lock:
        self.release_pending_clear_slot(slot)
    return result


async def _set_pending_clear(self: Any, slot: int, operation_id: str) -> None:
    """Keep local slot state occupied while reset evidence is unresolved."""
    async with self._lock:
        self._pending_fences[slot] = operation_id
        self._pending_clear_slots[slot] = operation_id


async def _dispatch_clear(
    self: Any,
    coordinator: Any,
    slot: int,
    expected_name: str | None,
    intent: Any,
) -> Any:
    """Dispatch reset through the exact verified intent when it is active."""
    if intent is None:
        return await self._module.async_fire_clear_code(
            coordinator, slot, expected_name=expected_name
        )
    return await self._module.async_fire_clear_code(
        coordinator,
        slot,
        expected_name=expected_name,
        verified_intent=intent,
    )


async def _apply_clear_result(
    self: Any,
    slot: int,
    operation_id: str,
    intent: Any,
    result: Any,
) -> Any:
    """Apply clear evidence while retaining the pending fence on uncertainty."""
    async with self._lock:
        if intent is not None and not await intent.async_is_current(
            allow_native_empty_text=True
        ):
            return self._operation_result_type(
                kind="clear", slot=slot, unconfirmed=True
            )
        if self._pending_fences.get(slot) != operation_id:
            self._logger.warning(
                "Stale clear token for slot %d (expected %s, got %s)",
                slot,
                operation_id,
                self._pending_fences.get(slot),
            )
            return self._operation_result_type(
                kind="clear", slot=slot, unconfirmed=True
            )
        mutation = decide_clear_result_mutation(result)
        if mutation["record_error"]:
            _log_clear_not_confirmed(self, slot, result)
            self._record_slot_error(slot, mutation["record_error"])
        elif not mutation["clear_slot"]:
            self._logger.debug(
                "Clear unconfirmed for slot %d; slot remains pending-clear", slot
            )
    if not mutation["clear_slot"]:
        return result
    if intent is not None and not await async_resolve_verified_clear_fence(intent):
        return self._operation_result_type(kind="clear", slot=slot, unconfirmed=True)
    async with self._lock:
        if self._pending_fences.get(slot) != operation_id:
            return self._operation_result_type(
                kind="clear", slot=slot, unconfirmed=True
            )
        self._logger.debug("Clear confirmed for slot %d; marking free", slot)
        self._pending_fences.pop(slot, None)
        self._pending_clear_slots.pop(slot, None)
        self._clear_assignment(slot)
        self._clear_slot_error(slot)
    return result


def _log_clear_not_confirmed(self: Any, slot: int, result: Any) -> None:
    """Log the original failed or lingering clear messages."""
    if result.failed:
        self._logger.warning(
            "Clear failed for slot %d (error: %s); slot remains pending-clear",
            slot,
            result.error,
        )
        return
    self._logger.warning(
        "Clear not fully confirmed for slot %d (lingering_name=%s, lingering_pin=%s); slot remains pending-clear",
        slot,
        result.lingering_name,
        result.lingering_pin,
    )
