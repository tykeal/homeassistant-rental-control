# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HA-facing status adapter for the protected registry protocol."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import threading
from typing import Any
from typing import Callable

from homeassistant.components.persistent_notification import async_create
from homeassistant.components.persistent_notification import async_dismiss
from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.issue_registry import async_create_issue
from homeassistant.helpers.issue_registry import async_delete_issue

from ..const import DOMAIN
from ..const import NAME
from . import protected_associations as _protected_associations
from .association_transactions import AssociationTransaction
from .associations import AssociationTuple
from .models import ProtectionAuthorizationState
from .models import ProtectionCommitToken
from .models import ProtectionLoadResult
from .models import ProtectionLoadState
from .protected_store_commit import CommitPreconditions
from .protected_store_commit import async_commit_protected_store
from .registry_transactions import async_recover_protection_files

_LOGGER = logging.getLogger(__name__)
_NOTIFICATION_ID = f"{DOMAIN}_protected_code_registry"
_GENERAL_WARNING = (
    "Rental Control could not verify the shared door-code registry history. "
    "No independently verified, sufficiently complete marker-bearing backup "
    "has been established; a markerless v1 backup alone cannot prove "
    "completeness. Preserve the current registry, protection marker, and any "
    "quarantine evidence without editing or restoring files. This release "
    "has no supported external restore or override. Issuance, reuse, and "
    "release may remain blocked indefinitely, including on installations "
    "that never activated v2. This does not establish whether an unverified "
    "code is present, absent, visible, or reusable."
)
_SCAN_WARNING = (
    "Rental Control could not complete its bounded storage evidence scan. "
    "The incomplete scan cannot establish that quarantine evidence is absent. "
    "Preserve the current registry, protection marker, and any quarantine "
    "evidence. With Home Assistant stopped, resolve the storage scan problem "
    "without editing or restoring files, then reload Rental Control. This "
    "release has no supported external restore or override. Issuance, reuse, "
    "and release remain blocked until history can be verified."
)
_GENERAL_WARNING_FR = (
    "Rental Control n’a pas pu vérifier l’historique du registre partagé des "
    "codes de porte. Aucune sauvegarde indépendante, suffisamment complète "
    "et accompagnée d’un marqueur de protection vérifié n’a été établie ; "
    "une sauvegarde v1 sans marqueur ne prouve pas l’exhaustivité. Préservez "
    "le registre actuel, le marqueur de protection et toute preuve de mise en "
    "quarantaine, sans modifier ni restaurer ces fichiers. Cette version ne "
    "propose aucune restauration externe ni dérogation prise en charge. "
    "L’émission, la réutilisation et la libération peuvent rester bloquées "
    "indéfiniment, y compris sur les installations n’ayant jamais activé v2. "
    "Cet état ne permet pas de conclure qu’un code non vérifié est présent, "
    "absent, visible ou réutilisable."
)
_SCAN_WARNING_FR = (
    "Rental Control n’a pas pu terminer l’analyse limitée des preuves de "
    "stockage. L’analyse incomplète ne permet pas d’établir l’absence de "
    "preuves de mise en quarantaine. Préservez le registre actuel, le marqueur "
    "de protection et toute preuve de mise en quarantaine. Lorsque Home "
    "Assistant est arrêté, corrigez le problème d’analyse sans modifier ni "
    "restaurer les fichiers, puis rechargez Rental Control. Cette version ne "
    "propose aucune restauration externe ni dérogation prise en charge. "
    "L’émission, la réutilisation et la libération restent bloquées tant que "
    "l’historique ne peut pas être vérifié."
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
        self._commit_operation_serial = 0
        self._commit_in_flight: int | None = None
        self._state_lock = threading.RLock()
        self._commit_settled = asyncio.Event()
        self._commit_settled.set()

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

    def uses_async_lock(self, lock: asyncio.Lock) -> bool:
        """Return whether allocator and store operations share one lock."""
        return self._async_lock is lock

    def is_current_commit_token(self, token: ProtectionCommitToken) -> bool:
        """Check that an acknowledged transaction is still the current snapshot."""
        with self._state_lock:
            loaded = self._loaded_result
            return bool(
                self._authorization_state is ProtectionAuthorizationState.READY
                and self._commit_in_flight is None
                and loaded is not None
                and loaded.state is ProtectionLoadState.VALID_V2
                and loaded.transaction_id == token.transaction_id
                and loaded.generation == token.generation
            )

    def operation_snapshot_ready(self) -> bool:
        """Return readiness for a live operation against the latest snapshot."""
        with self._state_lock:
            loaded = self._loaded_result
            return bool(
                self._authorization_state is ProtectionAuthorizationState.READY
                and self._commit_in_flight is None
                and loaded is not None
                and loaded.state is ProtectionLoadState.VALID_V2
                and loaded.data is not None
            )

    async def async_wait_for_commit_completion(self) -> bool:
        """Wait without holding allocator locks, then recheck the sticky gate."""
        while True:
            with self._state_lock:
                if self._commit_in_flight is None:
                    return (
                        self._authorization_state is ProtectionAuthorizationState.READY
                    )
                settled = self._commit_settled
            await settled.wait()

    def association_for_entry(self, rc_entry_id: str) -> AssociationTuple | None:
        """Return one validated association only under the current READY gate."""
        return _protected_associations.association_for_entry(self, rc_entry_id)

    def associations_by_entry(self) -> dict[str, AssociationTuple] | None:
        """Return the complete validated association map under the READY gate."""
        return _protected_associations.associations_by_entry(self)

    def association_references_for_entry(
        self, rc_entry_id: str
    ) -> tuple[str, ...] | None:
        """Return salted references for provably attributable stored work."""
        return _protected_associations.association_references_for_entry(
            self, rc_entry_id
        )

    async def async_commit_association(
        self, transaction: AssociationTransaction
    ) -> ProtectionLoadResult:
        """Build and commit a typed association transaction from READY state."""
        return await _protected_associations.async_commit_association(self, transaction)

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
        precondition: Callable[[], ProtectionLoadResult | None] | None = None,
        on_locked_preflight: Callable[[], ProtectionLoadResult | None] | None = None,
        authorize_owner_transition: Callable[[], bool] | None = None,
        on_acknowledged: Callable[[ProtectionLoadResult], None] | None = None,
    ) -> ProtectionLoadResult:
        """Commit exact bytes; owner loss needs a serialized guard callback."""
        return await async_commit_protected_store(
            self,
            envelope,
            legacy_migration_verified=legacy_migration_verified,
            preconditions=CommitPreconditions(precondition, on_locked_preflight),
            authorize_owner_transition=authorize_owner_transition,
            on_acknowledged=on_acknowledged,
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

    def _begin_commit_operation(self) -> int:
        """Invalidate prior commit tokens while this serialized write is active."""
        with self._state_lock:
            self._commit_operation_serial += 1
            self._commit_in_flight = self._commit_operation_serial
            self._commit_settled.clear()
            return self._commit_operation_serial

    def _apply_commit_completion(
        self,
        result: ProtectionLoadResult,
        *,
        operation_id: int | None = None,
    ) -> None:
        """Apply a commit only if its acknowledged result proves the new state."""
        if result.state is ProtectionLoadState.PROTECTIVE_UNKNOWN:
            self._enter_protective_unknown(result, commit_operation_id=operation_id)
            return
        if result.state is not ProtectionLoadState.VALID_V2:
            with self._state_lock:
                if operation_id is not None and self._commit_in_flight != operation_id:
                    return
                self._last_result = result
                if operation_id is not None:
                    self._commit_in_flight = None
            if operation_id is not None:
                self._commit_settled.set()
            return
        blocked_result: ProtectionLoadResult | None = None
        with self._state_lock:
            if operation_id is not None and self._commit_in_flight != operation_id:
                return
            if self._external_restore_suspected:
                blocked_result = ProtectionLoadResult(
                    ProtectionLoadState.PROTECTIVE_UNKNOWN,
                    data=result.data,
                    transaction_id=result.transaction_id,
                    generation=result.generation,
                    failure_code="external_restore_suspected",
                )
                self._last_result = blocked_result
                self._sticky_unknown = blocked_result
                self._authorization_state = (
                    ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
                )
            else:
                self._last_result = result
                self._loaded_result = result
                self._sticky_unknown = None
                self._authorization_state = ProtectionAuthorizationState.READY
            if operation_id is not None:
                self._commit_in_flight = None
        if operation_id is not None:
            self._commit_settled.set()
        if blocked_result is not None:
            self._report_unknown(blocked_result.failure_code)
            return
        self._dismiss_status()

    def _enter_protective_unknown(
        self,
        result: ProtectionLoadResult,
        *,
        commit_operation_id: int | None = None,
    ) -> None:
        """Latch the safety gate without discarding earlier observations."""
        with self._state_lock:
            if (
                commit_operation_id is not None
                and self._commit_in_flight != commit_operation_id
            ):
                return
            self._last_result = result
            self._sticky_unknown = result
            self._authorization_state = ProtectionAuthorizationState.PROTECTIVE_UNKNOWN
            if commit_operation_id is not None:
                self._commit_in_flight = None
        if commit_operation_id is not None:
            self._commit_settled.set()
        self._report_unknown(result.failure_code)

    def _report_unknown(self, failure_code: str | None) -> None:
        """Persist only fixed guidance and never include failure details."""
        scan_incomplete = failure_code in {"scan_limit", "scan_failed"}
        is_french = self._hass.config.language.lower().split("-", maxsplit=1)[0] == "fr"
        message = (
            (_SCAN_WARNING_FR if scan_incomplete else _GENERAL_WARNING_FR)
            if is_french
            else (_SCAN_WARNING if scan_incomplete else _GENERAL_WARNING)
        )
        issue_id = (
            "protected_registry_scan_incomplete"
            if scan_incomplete
            else "protected_registry_unknown"
        )
        stale_issue_id = (
            "protected_registry_unknown"
            if scan_incomplete
            else "protected_registry_scan_incomplete"
        )
        try:
            async_delete_issue(self._hass, DOMAIN, stale_issue_id)
        except Exception:
            _LOGGER.warning("Unable to clear stale protected registry guidance")
        translation_key = issue_id
        try:
            async_create(
                self._hass,
                message,
                title=f"{NAME} protected code registry",
                notification_id=_NOTIFICATION_ID,
            )
        except Exception:
            _LOGGER.warning("Unable to publish protected registry status")
        try:
            async_create_issue(
                self._hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=True,
                issue_domain=DOMAIN,
                severity=IssueSeverity.WARNING,
                translation_key=translation_key,
            )
        except Exception:
            _LOGGER.warning("Unable to publish protected registry repair guidance")

    def _dismiss_status(self) -> None:
        """Dismiss stale warning without changing an unavailable/busy result."""
        try:
            async_dismiss(self._hass, _NOTIFICATION_ID)
        except Exception:
            _LOGGER.warning("Unable to clear protected registry status")
        for issue_id in (
            "protected_registry_unknown",
            "protected_registry_scan_incomplete",
        ):
            try:
                async_delete_issue(self._hass, DOMAIN, issue_id)
            except Exception:
                _LOGGER.warning("Unable to clear protected registry repair guidance")
