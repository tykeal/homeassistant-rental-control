# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Home Assistant services for the shared door-code allocator."""

from __future__ import annotations

from dataclasses import asdict
import logging
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall
from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv

from ..const import COORDINATOR
from ..const import DOMAIN
from ..const import NAME
from . import reissue as allocator_reissue
from . import reissue_service
from .models import CycleObservation
from .models import ForcedReissueDirective
from .models import OrphanCleanupReport
from .models import ReissueOutcome

SERVICE_CLEAR_ORPHANED_CODES = "clear_orphaned_codes"
ATTR_DRY_RUN = "dry_run"
ATTR_FORCE_REISSUED_HOLDS = "force_reissued_holds"
_LOGGER = logging.getLogger(__name__)
_ORPHAN_NOTIFICATION_ID = f"{DOMAIN}_code_registry_orphans"
_FORCED_HOLD_NOTIFICATION_ID = f"{DOMAIN}_forced_reissue_holds"


def register_allocator_services(hass: HomeAssistant) -> None:
    """Register allocator services once for the integration domain."""
    if not hasattr(hass, "services"):
        return
    reissue_service.register_force_reissue_service(hass)
    if hass.services.has_service(DOMAIN, SERVICE_CLEAR_ORPHANED_CODES):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ORPHANED_CODES,
        _handle_clear_orphaned_codes(hass),
        schema=cv.vol.Schema(
            {
                cv.vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
                cv.vol.Optional(ATTR_FORCE_REISSUED_HOLDS, default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


def _handle_clear_orphaned_codes(hass: HomeAssistant) -> Any:
    """Return the bound clear-orphaned-codes service handler."""

    async def handler(call: ServiceCall) -> dict[str, Any]:
        """Clear orphaned shared code allocations for one service call."""
        from .singleton import async_get_or_create_allocator

        allocator = await async_get_or_create_allocator(hass)
        known_entry_ids = {
            entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)
        }
        report = await allocator.async_clear_orphans(
            known_entry_ids,
            _collect_observations(hass),
            dry_run=bool(call.data[ATTR_DRY_RUN]),
            force_reissued_holds=bool(call.data[ATTR_FORCE_REISSUED_HOLDS]),
            loaded_entry_ids=_loaded_entry_ids(hass),
        )
        return {
            "dry_run": report.dry_run,
            "cleared": [_outcome_dict(outcome) for outcome in report.cleared],
            "retained": [_outcome_dict(outcome) for outcome in report.retained],
        }

    return handler


def _loaded_entry_ids(hass: HomeAssistant) -> set[str]:
    """Return Rental Control entries with loaded integration data."""
    return {
        entry_id
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items()
        if isinstance(entry_id, str)
        and isinstance(entry_data, dict)
        and COORDINATOR in entry_data
    }


def _collect_observations(hass: HomeAssistant) -> list[CycleObservation]:
    """Return current observations from loaded Rental Control coordinators."""
    from ..coordinator_helpers.code_allocation import build_cycle_observation

    observations: list[CycleObservation] = []
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_id, str) or not isinstance(entry_data, dict):
            continue
        coordinator = entry_data.get(COORDINATOR)
        if coordinator is None or getattr(coordinator, "lockname", None) is None:
            continue
        slots = coordinator._observe_managed_slots()
        observations.append(
            build_cycle_observation(entry_id, coordinator.lockname, slots)
        )
    return observations


def _outcome_dict(outcome: Any) -> dict[str, Any]:
    """Return a cleanup outcome response without empty optional fields."""
    data = asdict(outcome)
    for key in ("reason", "lockname", "slot"):
        if data.get(key) is None:
            data.pop(key, None)
    return data


def report_orphan_cleanup(hass: HomeAssistant, report: OrphanCleanupReport) -> None:
    """Log and notify the operator about orphan cleanup results."""
    _LOGGER.info(
        "Shared code orphan cleanup dry_run=%s cleared=%s retained=%s",
        report.dry_run,
        [outcome.code_ref for outcome in report.cleared],
        [
            {"code_ref": outcome.code_ref, "reason": outcome.reason}
            for outcome in report.retained
        ],
    )
    if report.dry_run:
        return
    if not report.retained:
        async_dismiss(hass, _ORPHAN_NOTIFICATION_ID)
        return
    message = "Shared code orphan cleanup retained allocations: " + ", ".join(
        f"{outcome.code_ref}:{outcome.reason}" for outcome in report.retained
    )
    async_create(
        hass,
        message,
        title=f"{NAME} code orphan cleanup",
        notification_id=_ORPHAN_NOTIFICATION_ID,
    )


def has_forced_holds(allocator: Any) -> bool:
    """Return whether the registry contains any forced-release hold."""
    return any(
        allocator_reissue.is_forced_release_hold(owner.identity_key)
        for record in allocator._registry.records.values()
        for owner in record.owners
    )


def report_forced_hold_deferrals(
    allocator: Any,
    current_directives: tuple[ForcedReissueDirective, ...],
    observations: list[CycleObservation] | None = None,
) -> None:
    """Reconcile notifications for all outstanding forced-release holds."""
    hass = allocator.hass
    all_hold_rows = [
        (record, owner)
        for record in allocator._registry.records.values()
        for owner in record.owners
        if allocator_reissue.is_forced_release_hold(owner.identity_key)
    ]
    if not all_hold_rows:
        if hasattr(hass, "bus"):
            async_dismiss(hass, _FORCED_HOLD_NOTIFICATION_ID)
        return
    observations = (
        observations if observations is not None else _collect_observations(hass)
    )
    observed_entry_ids = {observation.entry_id for observation in observations}
    hold_rows = [
        (record, owner)
        for record, owner in all_hold_rows
        if not observed_entry_ids or owner.entry_id in observed_entry_ids
    ]
    if not hold_rows:
        return
    deferred = []
    for record, owner in hold_rows:
        outcome = ReissueOutcome(
            owner.entry_id,
            owner.identity_key.split(":", 1)[0],
            owner.lockname,
            owner.slot,
            record.code_ref,
            None,
            None,
            "held_pending_release",
            allocator_reissue.forced_hold_retention_reason(
                allocator, record, owner, observations
            ),
        )
        if outcome.retention_reason in (None, "adoption_conflict"):
            continue
        if not _matches_current_directive(outcome, current_directives):
            deferred.append(outcome)
    if not deferred:
        if len(hold_rows) == len(all_hold_rows) and hasattr(hass, "bus"):
            async_dismiss(hass, _FORCED_HOLD_NOTIFICATION_ID)
        return
    message = "Forced re-issue hold releases remain deferred: " + ", ".join(
        f"{outcome.replaced_code_ref}:{outcome.retention_reason}"
        for outcome in deferred
    )
    _LOGGER.warning(message)
    if not hasattr(hass, "bus"):
        return
    async_create(
        hass,
        message,
        title=f"{NAME} forced re-issue holds",
        notification_id=_FORCED_HOLD_NOTIFICATION_ID,
    )


def _matches_current_directive(
    outcome: ReissueOutcome, directives: tuple[ForcedReissueDirective, ...]
) -> bool:
    """Return whether a deferral belongs to this cycle's new directive."""
    return any(
        outcome.entry_id == directive.entry_id
        and outcome.lockname == directive.lockname
        and outcome.slot == directive.slot
        and (
            directive.identity_key is None
            or outcome.identity_key == directive.identity_key
        )
        for directive in directives
    )
