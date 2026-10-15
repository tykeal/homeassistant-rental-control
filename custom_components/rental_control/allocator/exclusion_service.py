# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Home Assistant service for excluding one unreadable lock slot."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall
from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from ..const import DOMAIN
from ..coordinator_helpers import exclusion_targets
from ..coordinator_helpers import slot_exclusions
from . import issuance
from .exclusions import refusal_for_exclusion

SERVICE_SET_SLOT_EXCLUSION = "set_slot_exclusion"
ATTR_LOCKNAME = "lockname"
ATTR_SLOT = "slot"
ATTR_EXCLUDED = "excluded"
ATTR_REASON = "reason"
ATTR_DRY_RUN = "dry_run"
# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExclusionRequest:
    """Parsed operator request for one slot-exclusion invocation."""

    entity_id: str | None
    lockname: str | None
    slot: int
    excluded: bool = True
    reason: str | None = None
    dry_run: bool = False


def _positive_slot(value: Any) -> int:
    """Return a positive integer slot, rejecting bools and fractional values."""
    if isinstance(value, bool):
        raise vol.Invalid("slot must be a positive integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise vol.Invalid("slot must be a positive integer")
        return int(value)
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise vol.Invalid("slot must be a positive integer")


SET_SLOT_EXCLUSION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_LOCKNAME): cv.string,
        vol.Required(ATTR_SLOT): vol.All(_positive_slot, vol.Range(min=1)),
        vol.Optional(ATTR_EXCLUDED, default=True): cv.boolean,
        vol.Optional(ATTR_REASON): cv.string,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)


def register_set_slot_exclusion_service(hass: HomeAssistant) -> None:
    """Register the slot-exclusion service once for the domain."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_SLOT_EXCLUSION):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SLOT_EXCLUSION,
        _handle_set_slot_exclusion(hass),
        schema=SET_SLOT_EXCLUSION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _handle_set_slot_exclusion(hass: HomeAssistant) -> Any:
    """Return the bound slot-exclusion service handler."""

    async def handler(call: ServiceCall) -> dict[str, Any]:
        """Handle one slot-exclusion service call."""
        return await _handle_set_slot_exclusion_call(hass, call)

    return handler


async def _handle_set_slot_exclusion_call(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    """Handle one parsed slot-exclusion service call."""
    request = ExclusionRequest(
        entity_id=call.data.get(ATTR_ENTITY_ID),
        lockname=call.data.get(ATTR_LOCKNAME),
        slot=int(call.data[ATTR_SLOT]),
        excluded=bool(call.data[ATTR_EXCLUDED]),
        reason=call.data.get(ATTR_REASON),
        dry_run=bool(call.data[ATTR_DRY_RUN]),
    )
    target, refusal = exclusion_targets.resolve_exclusion_target(
        hass,
        entity_id=request.entity_id,
        lockname=request.lockname,
        slot=request.slot,
        excluded=request.excluded,
    )
    if refusal or target is None:
        return _response(request, refusal or "missing_target", None, [])
    stored = slot_exclusions.load(hass, target.entry_id)
    if request.excluded:
        guard = await _guard_refusal(hass, target, request.slot)
        if guard is not None:
            return _response(request, guard, target, sorted(stored))
        resulting = {**stored, request.slot: request.reason}
    else:
        resulting = {
            slot: reason for slot, reason in stored.items() if slot != request.slot
        }
    changed = resulting != stored
    if request.dry_run:
        return _response(request, None, target, sorted(resulting), changed=changed)
    if changed:
        slot_exclusions.persist_excluded_slots(hass, target.entry_id, resulting)
        _LOGGER.info(
            "Rental Control entry %s %s slot %s on lock %s (reason: %s)",
            target.entry_id,
            "excluded" if request.excluded else "restored",
            request.slot,
            target.lockname or "unknown lock",
            request.reason or "none given",
        )
    return _response(request, None, target, sorted(resulting), changed=changed)


async def _guard_refusal(
    hass: HomeAssistant, target: exclusion_targets.ExclusionTarget, slot: int
) -> str | None:
    """Return why this slot may not be excluded, or None when it may."""
    from ..coordinator_helpers.code_allocation import build_cycle_observation
    from .reissue_preview import observe_managed_slots_without_mutation
    from .singleton import async_get_or_create_allocator

    coordinator = target.coordinator
    observation = build_cycle_observation(
        target.entry_id,
        target.lockname,
        observe_managed_slots_without_mutation(coordinator)
        if target.lockname is not None
        else [],
    )
    allocator = await async_get_or_create_allocator(hass)
    claimed = (
        issuance.claimed_slots(allocator, target.entry_id, target.lockname)
        if target.lockname is not None
        else frozenset()
    )
    return refusal_for_exclusion(
        slot,
        start_slot=getattr(coordinator, "start_slot", 0),
        max_events=getattr(coordinator, "max_events", 0),
        unreadable_slots=observation.unreadable_slots,
        managed_slots=observation.managed_slots,
        claimed_slots=claimed,
    )


def _response(
    request: ExclusionRequest,
    refusal: str | None,
    target: exclusion_targets.ExclusionTarget | None,
    excluded_slots: list[int],
    *,
    changed: bool = False,
) -> dict[str, Any]:
    """Build the service response for one exclusion call."""
    response: dict[str, Any] = {
        "status": "refused" if refusal else "ok",
        "entry_id": target.entry_id if target else None,
        "lockname": target.lockname if target else request.lockname,
        "slot": request.slot,
        "excluded": request.excluded,
        "changed": False if refusal else changed,
        "excluded_slots": excluded_slots,
        "dry_run": request.dry_run,
    }
    if refusal:
        response["reason"] = refusal
    return response


__all__ = [
    "SERVICE_SET_SLOT_EXCLUSION",
    "SET_SLOT_EXCLUSION_SCHEMA",
    "ExclusionRequest",
    "register_set_slot_exclusion_service",
]
