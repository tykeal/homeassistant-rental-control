# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Home Assistant service for forcing one door-code re-issue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall
from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv

from ..const import COORDINATOR
from ..const import DOMAIN
from ..coordinator_helpers import reissue as coordinator_reissue
from ..reconciliation import SlotStatus
from .models import ReissueOutcome
from .models import ReissuePreview

SERVICE_FORCE_REISSUE = "force_reissue"
ATTR_LOCKNAME = "lockname"
ATTR_SLOT = "slot"
ATTR_FORCE = "force"
ATTR_DRY_RUN = "dry_run"
_LOGGER = logging.getLogger(__name__)
_PREVIEW_REFUSALS = {
    "exhausted",
    "adoption_pending",
    "unaccounted_slots",
    "recovery_fail_closed",
    "no_existing_allocation",
}
_REAL_REFUSALS = {*_PREVIEW_REFUSALS, "no_existing_allocation"}


@dataclass(frozen=True, slots=True)
class ReissueRequest:
    """Parsed operator request for one force-reissue invocation."""

    entity_id: str | None
    lockname: str | None
    slot: int | None
    force: bool = False
    dry_run: bool = False


def _positive_slot(value: Any) -> int:
    """Return a positive integer slot, rejecting bools and fractional values."""
    if isinstance(value, bool):
        raise cv.vol.Invalid("slot must be a positive integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise cv.vol.Invalid("slot must be a positive integer")
        return int(value)
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise cv.vol.Invalid("slot must be a positive integer")


FORCE_REISSUE_SCHEMA = cv.vol.Schema(
    {
        cv.vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        cv.vol.Optional(ATTR_LOCKNAME): cv.string,
        cv.vol.Optional(ATTR_SLOT): cv.vol.All(_positive_slot, cv.vol.Range(min=1)),
        cv.vol.Optional(ATTR_FORCE, default=False): cv.boolean,
        cv.vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)


def register_force_reissue_service(hass: HomeAssistant) -> None:
    """Register the force-reissue service once for the domain."""
    if hass.services.has_service(DOMAIN, SERVICE_FORCE_REISSUE):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_REISSUE,
        _handle_force_reissue(hass),
        schema=FORCE_REISSUE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _handle_force_reissue(hass: HomeAssistant) -> Any:
    """Return the bound force-reissue service handler."""

    async def handler(call: ServiceCall) -> dict[str, Any]:
        """Handle one forced re-issue service call."""
        return await _handle_force_reissue_call(hass, call)

    return handler


async def _handle_force_reissue_call(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    """Handle one parsed forced re-issue service call."""
    from .singleton import async_get_or_create_allocator

    request = ReissueRequest(
        entity_id=call.data.get(ATTR_ENTITY_ID),
        lockname=call.data.get(ATTR_LOCKNAME),
        slot=call.data.get(ATTR_SLOT),
        force=bool(call.data[ATTR_FORCE]),
        dry_run=bool(call.data[ATTR_DRY_RUN]),
    )
    target, refusal = _resolve_and_validate_target(hass, request)
    if refusal:
        return _refusal(refusal, request.dry_run)
    assert target is not None
    coordinator = _coordinator_for_target(hass, target)
    if coordinator is None:
        return _refusal("not_a_reservation_sensor", request.dry_run)
    allocator = await async_get_or_create_allocator(hass)
    from . import reissue_preview

    if request.dry_run:
        _preview_request, preview = await reissue_preview.preview_request_and_result(
            allocator, coordinator, target
        )
        if preview.reason in _PREVIEW_REFUSALS:
            return _refusal(preview.reason, request.dry_run)
        return _preview_response(target, request.force, preview)
    async with coordinator_reissue.reissue_lock(coordinator):
        repeat = _repeat_outcome(coordinator, allocator, target)
        if repeat is not None:
            return _accepted_response(target, request.force, repeat)
        _preview_request, preview = await reissue_preview.preview_request_and_result(
            allocator, coordinator, target
        )
        if preview.reason in _REAL_REFUSALS:
            return _refusal(preview.reason, request.dry_run)
        if target.identity_key is not None and not _target_has_existing_owner(
            allocator, target
        ):
            return _refusal("no_existing_allocation", request.dry_run)
        pending = _record_pending_reissue(coordinator, target, preview, call)
    _log_acceptance(target, request, preview, call)
    await coordinator.async_request_refresh()
    outcome = coordinator_reissue.completed_reissues(coordinator).get(target.target_key)
    if outcome is None:
        outcome = _pending_outcome(target, pending)
    return _accepted_response(target, request.force, outcome)


def _record_pending_reissue(
    coordinator: Any,
    target: coordinator_reissue.ReissueTarget,
    preview: ReissuePreview,
    call: ServiceCall,
) -> coordinator_reissue.PendingReissue:
    """Record a pending re-issue for the next coordinator cycle."""
    pending = coordinator_reissue.PendingReissue(
        target_key=target.target_key,
        identity_key=target.identity_key,
        lockname=target.lockname,
        slot=target.slot,
        replaced_code_ref=preview.replaced_code_ref,
        requested_at=datetime.now(UTC).isoformat(),
        invoker=getattr(call.context, "user_id", None),
    )
    coordinator_reissue.pending_reissues(coordinator)[target.target_key] = pending
    return pending


def _log_acceptance(
    target: coordinator_reissue.ReissueTarget,
    request: ReissueRequest,
    preview: ReissuePreview,
    call: ServiceCall,
) -> None:
    """Log an accepted invocation using masked code refs only."""
    _LOGGER.info(
        "Accepted forced re-issue entry=%s target=%s force=%s dry_run=%s "
        "replaced_code_ref=%s user_id=%s origin=%s",
        target.entry_id,
        _target_label(target),
        request.force,
        request.dry_run,
        preview.replaced_code_ref,
        getattr(call.context, "user_id", None),
        getattr(call.context, "origin", None),
    )


def _pending_outcome(
    target: coordinator_reissue.ReissueTarget,
    pending: coordinator_reissue.PendingReissue,
) -> ReissueOutcome:
    """Build a response outcome from still-pending in-memory state."""
    return ReissueOutcome(
        target.entry_id,
        target.identity_key,
        target.lockname,
        target.slot,
        pending.replaced_code_ref,
        pending.replacement_code_ref,
        None,
        "accepted",
        pending.terminal_reason,
    )


def _resolve_and_validate_target(
    hass: HomeAssistant, request: ReissueRequest
) -> tuple[coordinator_reissue.ReissueTarget | None, str]:
    """Resolve the request and apply ordered handler-level validation."""
    half_slot = (request.lockname is None) != (request.slot is None)
    if half_slot:
        return None, "incomplete_slot_target"
    has_entity = request.entity_id is not None
    has_slot = request.lockname is not None and request.slot is not None
    if has_entity and has_slot:
        return None, "ambiguous_target"
    if not has_entity and not has_slot:
        return None, "missing_target"
    if request.entity_id is not None:
        target, reason = coordinator_reissue.resolve_entity_target(
            hass, request.entity_id
        )
    else:
        assert request.lockname is not None and request.slot is not None
        target, reason = coordinator_reissue.resolve_slot_target(
            hass, request.lockname, request.slot
        )
    if target is None:
        return None, reason
    reason = _physical_refusal(hass, target)
    if reason:
        return None, reason
    if target.checked_in and not request.force:
        return None, "checked_in_requires_force"
    return target, ""


def _physical_refusal(
    hass: HomeAssistant, target: coordinator_reissue.ReissueTarget
) -> str:
    """Return a physical-state refusal reason, or empty string."""
    if target.lockname is None:
        return ""
    coordinator = _coordinator_for_target(hass, target)
    if coordinator is None or target.slot is None:
        return "lock_unavailable"
    from . import reissue_preview

    for slot in reissue_preview.observe_managed_slots_without_mutation(coordinator):
        if slot.slot != target.slot:
            continue
        if slot.status is SlotStatus.UNKNOWN:
            return "slot_unreadable"
        return ""
    return "lock_unavailable"


def _repeat_outcome(
    coordinator: Any,
    allocator: Any,
    target: coordinator_reissue.ReissueTarget,
) -> ReissueOutcome | None:
    """Return an idempotent prior outcome for a repeated invocation."""
    pending = coordinator_reissue.pending_reissues(coordinator).get(target.target_key)
    if pending is not None:
        return ReissueOutcome(
            target.entry_id,
            target.identity_key,
            target.lockname,
            target.slot,
            pending.replaced_code_ref,
            pending.replacement_code_ref,
            None,
            pending.phase.value,
            pending.terminal_reason,
        )
    completed = coordinator_reissue.completed_reissues(coordinator).get(
        target.target_key
    )
    if completed is not None:
        expected_ref = completed.replacement_code_ref or completed.replaced_code_ref
        if (
            target.observed_code is not None
            and expected_ref is not None
            and allocator.code_ref(target.observed_code) != expected_ref
        ):
            coordinator_reissue.completed_reissues(coordinator).pop(
                target.target_key, None
            )
            return None
        return completed
    from . import reissue_preview

    return reissue_preview.forced_hold_matches_target(
        allocator, target.entry_id, target.identity_key, target.lockname, target.slot
    )


def _target_has_existing_owner(
    allocator: Any, target: coordinator_reissue.ReissueTarget
) -> bool:
    """Return whether the target has an allocation owner that can be held."""
    if target.identity_key is not None and allocator._registry.code_for_identity(
        target.identity_key
    ):
        return True
    return any(
        owner.entry_id == target.entry_id
        and owner.lockname == target.lockname
        and owner.slot == target.slot
        for record in allocator._registry.records.values()
        for owner in record.owners
    )


def _coordinator_for_target(
    hass: HomeAssistant, target: coordinator_reissue.ReissueTarget
) -> Any | None:
    """Return the loaded coordinator for a resolved target."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    entry_data = domain_data.get(target.entry_id)
    if not isinstance(entry_data, dict):
        return None
    return entry_data.get(COORDINATOR)


def _accepted_response(
    target: coordinator_reissue.ReissueTarget,
    forced: bool,
    outcome: ReissueOutcome,
) -> dict[str, Any]:
    """Build a non-dry-run response with masked code references only."""
    return {
        "status": "accepted",
        "dry_run": False,
        "entry_id": target.entry_id,
        "identity_key": target.identity_key,
        "lockname": target.lockname,
        "slot": target.slot,
        "forced_checked_in_override": forced,
        "replaced_code_ref": outcome.replaced_code_ref,
        "replacement_code_ref": outcome.replacement_code_ref,
        "origin": outcome.origin.value if outcome.origin is not None else None,
        "replaced_disposition": outcome.disposition,
        "retention_reason": outcome.retention_reason,
    }


def _preview_response(
    target: coordinator_reissue.ReissueTarget,
    forced: bool,
    preview: ReissuePreview,
) -> dict[str, Any]:
    """Build a dry-run response whose only raw code field is replacement_code."""
    return {
        "status": "preview",
        "dry_run": True,
        "entry_id": target.entry_id,
        "identity_key": target.identity_key,
        "lockname": target.lockname,
        "slot": target.slot,
        "forced_checked_in_override": forced,
        "replaced_code_ref": preview.replaced_code_ref,
        "replacement_code_ref": preview.replacement_code_ref,
        "origin": preview.origin.value if preview.origin is not None else None,
        "replaced_disposition": preview.reason or "held_pending_release",
        "retention_reason": None,
        "replacement_code": preview.replacement_code,
    }


def _refusal(reason: str, dry_run: bool) -> dict[str, Any]:
    """Build a structured handler-level refusal response."""
    return {"status": "refused", "reason": reason, "dry_run": dry_run}


def _target_label(target: coordinator_reissue.ReissueTarget) -> str:
    """Return an audit-safe target label without raw codes."""
    if target.identity_key is not None:
        return f"identity:{target.identity_key}"
    return f"slot:{target.lockname}:{target.slot}"


__all__ = [
    "ATTR_DRY_RUN",
    "ATTR_FORCE",
    "ATTR_LOCKNAME",
    "ATTR_SLOT",
    "FORCE_REISSUE_SCHEMA",
    "ReissueRequest",
    "SERVICE_FORCE_REISSUE",
    "register_force_reissue_service",
]
