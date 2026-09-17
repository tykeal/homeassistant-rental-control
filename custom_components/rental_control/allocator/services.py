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
from . import reissue_service
from .models import CycleObservation
from .models import OrphanCleanupReport

SERVICE_CLEAR_ORPHANED_CODES = "clear_orphaned_codes"
ATTR_DRY_RUN = "dry_run"
_LOGGER = logging.getLogger(__name__)
_ORPHAN_NOTIFICATION_ID = f"{DOMAIN}_code_registry_orphans"


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
            {cv.vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean}
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
        )
        return {
            "dry_run": report.dry_run,
            "cleared": [_outcome_dict(outcome) for outcome in report.cleared],
            "retained": [_outcome_dict(outcome) for outcome in report.retained],
        }

    return handler


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
    """Return a cleanup outcome response without empty reason fields."""
    data = asdict(outcome)
    if data.get("reason") is None:
        data.pop("reason", None)
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
