# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Loaded-only Keymaster version support diagnosis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Final

from awesomeversion import AwesomeVersion
from awesomeversion.strategy import AwesomeVersionStrategy
from homeassistant import loader
from homeassistant.core import HomeAssistant

_KEYMASTER_DOMAIN: Final = "keymaster"
_KEYMASTER_VERSION_FLOOR: Final = AwesomeVersion("v0.6.1")
_KEYMASTER_RELEASE: Final = re.compile(
    r"^v?(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)"
    r"(?P<suffix>(?:[-+._]?[A-Za-z][0-9A-Za-z.-]*|[-+][0-9A-Za-z.-]+))?$"
)


class KeymasterVersionStatus(StrEnum):
    """Loaded Keymaster support diagnosis, independent from capabilities."""

    SUPPORTED = "supported"
    OUTSIDE_SUPPORT = "outside_support"
    DEVELOPMENT = "development"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class KeymasterVersionDiagnosis:
    """Allowlisted loaded-version diagnosis with no capability inference."""

    status: KeymasterVersionStatus
    version: str | None
    support_floor: str
    reason: str


def diagnose_keymaster_version(hass: HomeAssistant) -> KeymasterVersionDiagnosis:
    """Classify only already-loaded Keymaster manifest metadata.

    The version is descriptive evidence only. Callers must continue to apply
    the same identity, capability, current-state, and protection checks for
    every diagnosis status.
    """
    try:
        integration = loader.async_get_loaded_integration(hass, _KEYMASTER_DOMAIN)
    except loader.IntegrationNotLoaded:
        return _unknown_version_diagnosis("integration_not_loaded")
    except Exception:
        return _unknown_version_diagnosis("loaded_metadata_unavailable")

    try:
        raw_version = integration.manifest.get("version")
    except Exception:
        return _unknown_version_diagnosis("loaded_metadata_invalid")
    if not isinstance(raw_version, str) or not raw_version:
        return _unknown_version_diagnosis("version_metadata_missing")
    if raw_version in {"0.0.0", "v0.0.0"}:
        return KeymasterVersionDiagnosis(
            KeymasterVersionStatus.DEVELOPMENT,
            raw_version,
            str(_KEYMASTER_VERSION_FLOOR),
            "development_placeholder",
        )
    match = _KEYMASTER_RELEASE.fullmatch(raw_version)
    if match is None:
        return _unknown_version_diagnosis("release_shape_unrecognized")
    try:
        comparator_version = integration.version
    except Exception:
        return _unknown_version_diagnosis("version_comparator_unavailable")
    if comparator_version is None:
        return _unknown_version_diagnosis("version_comparator_unavailable")
    strategy = getattr(comparator_version, "strategy", None)
    if strategy not in {
        AwesomeVersionStrategy.SEMVER,
        AwesomeVersionStrategy.PEP440,
    }:
        return _unknown_version_diagnosis("version_comparator_unrecognized")

    try:
        below_floor = comparator_version < _KEYMASTER_VERSION_FLOOR
        at_or_above_floor = comparator_version >= _KEYMASTER_VERSION_FLOOR
    except Exception:
        return _unknown_version_diagnosis("version_comparison_failed")
    safe_version = raw_version if len(raw_version) <= 64 else None
    if below_floor:
        return KeymasterVersionDiagnosis(
            KeymasterVersionStatus.OUTSIDE_SUPPORT,
            safe_version,
            str(_KEYMASTER_VERSION_FLOOR),
            "below_support_floor",
        )
    if at_or_above_floor:
        return KeymasterVersionDiagnosis(
            KeymasterVersionStatus.SUPPORTED,
            safe_version,
            str(_KEYMASTER_VERSION_FLOOR),
            "meets_support_floor",
        )
    return _unknown_version_diagnosis("version_incomparable", version=safe_version)


def _unknown_version_diagnosis(
    reason: str, *, version: str | None = None
) -> KeymasterVersionDiagnosis:
    """Build an unknown-version result without arbitrary metadata text."""
    return KeymasterVersionDiagnosis(
        KeymasterVersionStatus.UNKNOWN,
        version,
        str(_KEYMASTER_VERSION_FLOOR),
        reason,
    )
