# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HA-facing status adapter for the dormant protected registry protocol."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import threading
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..const import NAME
from .models import ProtectionAuthorizationState
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .registry_commit import async_commit_protection_transaction
from .registry_transactions import async_recover_protection_files

_LOGGER = logging.getLogger(__name__)
_NOTIFICATION_ID = f"{DOMAIN}_protected_code_registry"
_GENERAL_WARNING = (
    "Rental Control could not verify the shared door-code registry history. "
    "Preserve the current registry, protection marker, and any quarantine "
    "evidence. Do not restore or edit these files. External recovery and an "
    "operator override are not supported in this release. Shared code "
    "issuance, reuse, and release remain blocked. This does not establish "
    "whether any unverified code is present, absent, visible, or reusable."
)
_SCAN_WARNING = (
    "Rental Control could not complete its bounded storage evidence scan. "
    "Preserve the current registry, protection marker, and any quarantine "
    "evidence, and inspect the storage directory with Home Assistant stopped. "
    "External recovery and an operator override are not supported in this "
    "release. Shared code issuance, reuse, and release remain blocked."
)


class ProtectedRegistryStore:
    """Expose typed storage outcomes and fixed privacy-safe repair guidance."""

    def __init__(
        self,
        hass: HomeAssistant,
        async_lock: asyncio.Lock,
        *,
        storage_dir: Path | None = None,
    ) -> None:
        """Bind to HA's private storage path and allocator-wide async lock."""
        self._hass = hass
        self._async_lock = async_lock
        self._storage_dir = storage_dir or Path(hass.config.path(".storage"))
        self._last_result: ProtectionLoadResult | None = None
        self._loaded_result: ProtectionLoadResult | None = None
        self._authorization_state = ProtectionAuthorizationState.UNVERIFIED
        self._sticky_unknown: ProtectionLoadResult | None = None
        self._external_restore_suspected = False
        self._state_lock = threading.RLock()

    @property
    def last_result(self) -> ProtectionLoadResult | None:
        """Return the most recent attempt outcome, not the authorization gate."""
        with self._state_lock:
            return self._last_result

    @property
    def loaded_result(self) -> ProtectionLoadResult | None:
        """Return retained observations; callers must separately check the gate."""
        with self._state_lock:
            return self._loaded_result

    @property
    def authorization_state(self) -> ProtectionAuthorizationState:
        """Return the sticky typed authorization gate, not loaded snapshot state."""
        with self._state_lock:
            return self._authorization_state

    async def async_load(self) -> ProtectionLoadResult:
        """Read/recover protected state without the legacy HA Store loader."""
        rejection = self._load_operation_preflight()
        if rejection is not None:
            self._apply_load_completion(rejection)
            return rejection
        return await async_recover_protection_files(
            self._async_lock,
            self._storage_dir,
            on_completion=self._apply_load_completion,
            operation_preflight=self._load_operation_preflight,
        )

    async def async_commit(
        self,
        envelope: dict[str, Any],
        *,
        legacy_migration_verified: bool = False,
    ) -> ProtectionLoadResult:
        """Commit exact bytes; report ambiguity without exposing raw errors."""
        rejection = self._commit_operation_preflight()
        if rejection is not None:
            self._apply_commit_completion(rejection)
            return rejection
        return await async_commit_protection_transaction(
            self._async_lock,
            self._storage_dir,
            envelope,
            legacy_migration_verified=legacy_migration_verified,
            on_completion=self._apply_commit_completion,
            operation_preflight=self._commit_operation_preflight,
        )

    def mark_external_restore_suspected(self) -> None:
        """Latch known restore evidence; matching bytes cannot clear this gate."""
        with self._state_lock:
            self._external_restore_suspected = True
        self._enter_protective_unknown(
            ProtectionLoadResult(
                ProtectionLoadState.PROTECTIVE_UNKNOWN,
                failure_code="external_restore_suspected",
            )
        )

    def _load_operation_preflight(self) -> ProtectionLoadResult | None:
        """Check restore evidence immediately before locked recovery I/O."""
        with self._state_lock:
            if not self._external_restore_suspected:
                return None
        return ProtectionLoadResult(
            ProtectionLoadState.PROTECTIVE_UNKNOWN,
            failure_code="external_restore_suspected",
        )

    def _commit_operation_preflight(self) -> ProtectionLoadResult | None:
        """Recheck authorization under both locks before any disk operation."""
        with self._state_lock:
            if self._external_restore_suspected:
                return ProtectionLoadResult(
                    ProtectionLoadState.PROTECTIVE_UNKNOWN,
                    failure_code="external_restore_suspected",
                )
            if (
                self._authorization_state
                is ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
            ):
                return self._sticky_unknown or ProtectionLoadResult(
                    ProtectionLoadState.PROTECTIVE_UNKNOWN,
                    failure_code="operation_failed",
                )
            if self._authorization_state is not ProtectionAuthorizationState.READY:
                return ProtectionLoadResult(
                    ProtectionLoadState.UNAVAILABLE,
                    failure_code="authorization_required",
                )
        return None

    def _apply_load_completion(self, result: ProtectionLoadResult) -> None:
        """Apply recovery state before cancellation propagates."""
        if result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN:
            self._enter_protective_unknown(result)
            return
        if result.state in {
            ProtectionLoadState.ABSENT,
            ProtectionLoadState.LEGACY_V1,
            ProtectionLoadState.VALID_V2,
        }:
            with self._state_lock:
                if self._external_restore_suspected:
                    blocked = True
                else:
                    blocked = False
                    self._last_result = result
                    self._loaded_result = result
                    self._sticky_unknown = None
                    self._authorization_state = ProtectionAuthorizationState.READY
            if blocked:
                self._enter_protective_unknown(
                    ProtectionLoadResult(
                        ProtectionLoadState.PROTECTIVE_UNKNOWN,
                        failure_code="external_restore_suspected",
                    )
                )
            else:
                self._dismiss_status()
            return
        with self._state_lock:
            self._last_result = result

    def _apply_commit_completion(self, result: ProtectionLoadResult) -> None:
        """Apply a commit only if its acknowledged result proves the new state."""
        if result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN:
            self._enter_protective_unknown(result)
            return
        if result.state is not ProtectionLoadState.VALID_V2:
            with self._state_lock:
                self._last_result = result
            return
        with self._state_lock:
            if self._external_restore_suspected:
                blocked = True
            else:
                blocked = False
                self._last_result = result
                self._loaded_result = result
                self._sticky_unknown = None
                self._authorization_state = ProtectionAuthorizationState.READY
        if blocked:
            self._enter_protective_unknown(
                ProtectionLoadResult(
                    ProtectionLoadState.PROTECTIVE_UNKNOWN,
                    data=result.data,
                    transaction_id=result.transaction_id,
                    generation=result.generation,
                    failure_code="external_restore_suspected",
                )
            )
            return
        self._dismiss_status()

    def _enter_protective_unknown(self, result: ProtectionLoadResult) -> None:
        """Latch the safety gate without discarding earlier observations."""
        with self._state_lock:
            self._last_result = result
            self._sticky_unknown = result
            self._authorization_state = ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
        self._report_unknown(result.failure_code)

    def _report_unknown(self, failure_code: str | None) -> None:
        """Persist only fixed guidance and never include failure details."""
        message = (
            _SCAN_WARNING
            if failure_code in {"scan_limit", "scan_failed"}
            else _GENERAL_WARNING
        )
        try:
            async_create(
                self._hass,
                message,
                title=f"{NAME} protected code registry",
                notification_id=_NOTIFICATION_ID,
            )
        except Exception:
            _LOGGER.warning("Unable to publish protected registry status")

    def _dismiss_status(self) -> None:
        """Dismiss stale warning without changing an unavailable/busy result."""
        try:
            async_dismiss(self._hass, _NOTIFICATION_ID)
        except Exception:
            _LOGGER.warning("Unable to clear protected registry status")
