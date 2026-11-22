# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Loaded Keymaster version issue reporting and selected-entry lifecycle."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryChange
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.issue_registry import async_create_issue
from homeassistant.helpers.issue_registry import async_delete_issue
from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry

from ..allocator.associations import AssociationMode
from ..allocator.associations import AssociationTuple
from ..const import DOMAIN
from ..const import LOCK_MANAGER
from ..const import PROTECTED_REGISTRY_STORE
from ..keymaster_version import KeymasterVersionDiagnosis
from ..keymaster_version import KeymasterVersionStatus
from ..keymaster_version import diagnose_keymaster_version

_LOGGER = logging.getLogger(__name__)
_TRACKER_KEY: Final = "_keymaster_version_issue_tracker"
_ISSUE_PREFIX: Final = "keymaster_version_"


def report_keymaster_version(hass: HomeAssistant, entry_id: str) -> None:
    """Report already-loaded version metadata independently from capabilities."""
    _report_diagnosis(hass, entry_id, diagnose_keymaster_version(hass))


def _report_diagnosis(
    hass: HomeAssistant,
    keymaster_entry_id: str,
    diagnosis: KeymasterVersionDiagnosis,
    *,
    log: bool = True,
) -> None:
    """Upsert one issue against the selected Keymaster config entry."""
    issue_id = f"{_ISSUE_PREFIX}{keymaster_entry_id}"
    if diagnosis.status is KeymasterVersionStatus.OUTSIDE_SUPPORT:
        version = diagnosis.version or "unknown"
        if log:
            _LOGGER.warning(
                "Loaded Keymaster version %s is below the tested support floor %s; "
                "version status alone does not block or authorize lock operations.",
                version,
                diagnosis.support_floor,
            )
        async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            config_entry_id=keymaster_entry_id,
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=IssueSeverity.WARNING,
            translation_key="keymaster_version_outside_support",
            translation_placeholders={
                "version": version,
                "support_floor": diagnosis.support_floor,
            },
        )
        return
    if diagnosis.status is KeymasterVersionStatus.UNKNOWN:
        if log:
            _LOGGER.warning(
                "Loaded Keymaster version could not be classified (%s); "
                "version status alone does not block or authorize lock operations.",
                diagnosis.reason,
            )
        async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            config_entry_id=keymaster_entry_id,
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=IssueSeverity.WARNING,
            translation_key="keymaster_version_unknown",
            translation_placeholders={
                "support_floor": diagnosis.support_floor,
            },
        )
        return
    async_delete_issue(hass, DOMAIN, issue_id)
    if log and diagnosis.status is KeymasterVersionStatus.DEVELOPMENT:
        _LOGGER.info(
            "Loaded Keymaster version is the allowed development placeholder; "
            "slot operations still require all independent safety checks."
        )


class KeymasterVersionIssueTracker:
    """Reconcile version issues only for active RC entries selecting Keymaster."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Subscribe to config-entry changes for load, unload, and selection."""
        self._hass = hass
        self._rc_entry_ids: set[str] = set()
        self._last_diagnoses: dict[
            str, tuple[KeymasterVersionStatus, str | None, str]
        ] = {}
        self._unsubscribe = async_dispatcher_connect(
            hass,
            SIGNAL_CONFIG_ENTRY_CHANGED,
            self._handle_config_entry_change,
        )

    def register(self, rc_entry_id: str) -> None:
        """Track an RC entry after its runtime coordinator is prepared."""
        self._rc_entry_ids.add(rc_entry_id)
        self.reconcile()

    def unregister(self, rc_entry_id: str) -> None:
        """Stop considering an RC entry and clear no-longer-selected issues."""
        self._rc_entry_ids.discard(rc_entry_id)
        self.reconcile()

    def stop(self) -> None:
        """Remove the shared config-entry listener."""
        self._unsubscribe()
        self._rc_entry_ids.clear()

    @callback
    def _handle_config_entry_change(
        self, _change: ConfigEntryChange, _entry: ConfigEntry
    ) -> None:
        """Reconcile on setup/load/unload and association data changes."""
        self.reconcile()

    def reconcile(self) -> None:
        """Diagnose only loaded Keymaster entries selected by loaded RC entries."""
        domain_data = self._hass.data.get(DOMAIN)
        if not isinstance(domain_data, dict):
            return

        selected_entry_ids: set[str] = set()
        for rc_entry_id in tuple(self._rc_entry_ids):
            rc_entry = self._hass.config_entries.async_get_entry(rc_entry_id)
            runtime = domain_data.get(rc_entry_id)
            if (
                rc_entry is None
                or rc_entry.state is not ConfigEntryState.LOADED
                or not isinstance(runtime, dict)
            ):
                continue
            selected = _selected_loaded_keymaster_entry(self._hass, rc_entry)
            if selected is not None:
                selected_entry_ids.add(selected.entry_id)

        issue_registry = async_get_issue_registry(self._hass)
        for issue_domain, issue_id in tuple(issue_registry.issues):
            if (
                issue_domain == DOMAIN
                and issue_id.startswith(_ISSUE_PREFIX)
                and issue_id[len(_ISSUE_PREFIX) :] not in selected_entry_ids
            ):
                async_delete_issue(self._hass, DOMAIN, issue_id)

        if not selected_entry_ids:
            self._last_diagnoses.clear()
            return
        diagnosis = diagnose_keymaster_version(self._hass)
        current_diagnosis = (
            diagnosis.status,
            diagnosis.version,
            diagnosis.reason,
        )
        for stale_entry_id in self._last_diagnoses.keys() - selected_entry_ids:
            self._last_diagnoses.pop(stale_entry_id, None)
        for keymaster_entry_id in sorted(selected_entry_ids):
            log = self._last_diagnoses.get(keymaster_entry_id) != current_diagnosis
            _report_diagnosis(self._hass, keymaster_entry_id, diagnosis, log=log)
            self._last_diagnoses[keymaster_entry_id] = current_diagnosis


def _selected_loaded_keymaster_entry(
    hass: HomeAssistant, rc_entry: ConfigEntry
) -> ConfigEntry | None:
    """Resolve the acknowledged selection without capability/readiness checks."""
    association = AssociationTuple.from_config(rc_entry.data)
    if (
        association is None
        or association.mode is not AssociationMode.VERIFIED
        or association.verified_keymaster_entry_id is None
    ):
        return None
    domain_data = hass.data.get(DOMAIN)
    store = (
        domain_data.get(PROTECTED_REGISTRY_STORE)
        if isinstance(domain_data, dict)
        else None
    )
    association_reader = getattr(store, "association_for_entry", None)
    if not callable(association_reader):
        return None
    try:
        if association_reader(rc_entry.entry_id) != association:
            return None
    except Exception:
        return None
    selected = hass.config_entries.async_get_entry(
        association.verified_keymaster_entry_id
    )
    if (
        selected is None
        or selected.domain != LOCK_MANAGER
        or selected.disabled_by is not None
        or selected.state is not ConfigEntryState.LOADED
    ):
        return None
    return selected


def register_keymaster_version_tracking(hass: HomeAssistant, rc_entry_id: str) -> None:
    """Register one loaded Rental Control entry with a shared issue tracker."""
    config_entries = getattr(hass, "config_entries", None)
    if not callable(getattr(config_entries, "async_get_entry", None)):
        return
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not isinstance(domain_data, dict):
        return
    tracker = domain_data.get(_TRACKER_KEY)
    if not isinstance(tracker, KeymasterVersionIssueTracker):
        tracker = KeymasterVersionIssueTracker(hass)
        domain_data[_TRACKER_KEY] = tracker
    tracker.register(rc_entry_id)


def unregister_keymaster_version_tracking(
    hass: HomeAssistant, rc_entry_id: str
) -> None:
    """Unregister an unloaded or removed RC entry and stop an unused tracker."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return
    tracker = domain_data.get(_TRACKER_KEY)
    if not isinstance(tracker, KeymasterVersionIssueTracker):
        return
    tracker.unregister(rc_entry_id)
    if not tracker._rc_entry_ids:
        tracker.stop()
        if domain_data.get(_TRACKER_KEY) is tracker:
            domain_data.pop(_TRACKER_KEY)
