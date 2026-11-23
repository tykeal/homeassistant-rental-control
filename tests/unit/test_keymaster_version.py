# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Loaded-only Keymaster version classification."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from awesomeversion import AwesomeVersion
from homeassistant import loader
import pytest

from custom_components.rental_control.keymaster_version import KeymasterVersionStatus
from custom_components.rental_control.keymaster_version import (
    diagnose_keymaster_version,
)

_VERSION_CASES = (
    ("v0.6.0", KeymasterVersionStatus.OUTSIDE_SUPPORT),
    ("v0.6.1", KeymasterVersionStatus.SUPPORTED),
    ("v0.6.5", KeymasterVersionStatus.SUPPORTED),
    ("0.0.0", KeymasterVersionStatus.DEVELOPMENT),
    ("v0.0.0", KeymasterVersionStatus.DEVELOPMENT),
    ("v0.0.0-dev", KeymasterVersionStatus.OUTSIDE_SUPPORT),
    ("v0.6.1rc1", KeymasterVersionStatus.OUTSIDE_SUPPORT),
    ("v0.6.2rc1", KeymasterVersionStatus.SUPPORTED),
    ("v0.6.1+local", KeymasterVersionStatus.UNKNOWN),
    ("7", KeymasterVersionStatus.UNKNOWN),
    ("2026.11", KeymasterVersionStatus.UNKNOWN),
    ("20260101", KeymasterVersionStatus.UNKNOWN),
    ("0.6", KeymasterVersionStatus.UNKNOWN),
    ("0.0", KeymasterVersionStatus.UNKNOWN),
    ("v.0.0.71", KeymasterVersionStatus.UNKNOWN),
)


def _integration(version: str) -> SimpleNamespace:
    """Build the loaded manifest and HA-provided comparator pair."""
    return SimpleNamespace(
        manifest={"version": version},
        version=AwesomeVersion(version),
    )


@pytest.mark.parametrize(("version", "expected"), _VERSION_CASES)
def test_loaded_keymaster_version_matrix(
    hass,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    expected: KeymasterVersionStatus,
) -> None:
    """Apply release-shape rules before HA comparator results."""
    loaded_lookup = Mock(return_value=_integration(version))
    monkeypatch.setattr(loader, "async_get_loaded_integration", loaded_lookup)

    diagnosis = diagnose_keymaster_version(hass)

    assert diagnosis.status is expected
    assert diagnosis.support_floor == "v0.6.1"
    if version in {
        "v0.6.0",
        "v0.6.1",
        "v0.6.5",
        "0.0.0",
        "v0.0.0",
        "v0.0.0-dev",
        "v0.6.1rc1",
        "v0.6.2rc1",
        "v0.6.1+local",
    }:
        assert diagnosis.version == version
    else:
        assert diagnosis.version is None
    loaded_lookup.assert_called_once_with(hass, "keymaster")


@pytest.mark.parametrize(
    "version",
    ["7", "2026.11", "20260101", "0.6", "0.0", "v.0.0.71", "v0.6.1 "],
)
def test_non_release_shapes_do_not_export_untrusted_metadata(
    hass, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Unknown arbitrary metadata is not copied into operator-facing output."""
    monkeypatch.setattr(
        loader,
        "async_get_loaded_integration",
        Mock(return_value=_integration(version)),
    )

    diagnosis = diagnose_keymaster_version(hass)

    assert diagnosis.status is KeymasterVersionStatus.UNKNOWN
    assert diagnosis.version is None


def test_missing_metadata_is_unknown(hass, monkeypatch: pytest.MonkeyPatch) -> None:
    """A loaded integration without a manifest version remains unknown."""
    integration = SimpleNamespace(manifest={}, version=None)
    monkeypatch.setattr(
        loader,
        "async_get_loaded_integration",
        Mock(return_value=integration),
    )

    diagnosis = diagnose_keymaster_version(hass)

    assert diagnosis.status is KeymasterVersionStatus.UNKNOWN
    assert diagnosis.version is None
    assert diagnosis.reason == "version_metadata_missing"


def test_unloaded_integration_is_unknown_without_loading_it(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use HA's loaded-only lookup and never initiate metadata loading."""
    loaded_lookup = Mock(side_effect=loader.IntegrationNotLoaded("keymaster"))
    monkeypatch.setattr(loader, "async_get_loaded_integration", loaded_lookup)

    diagnosis = diagnose_keymaster_version(hass)

    assert diagnosis.status is KeymasterVersionStatus.UNKNOWN
    assert diagnosis.version is None
    assert diagnosis.reason == "integration_not_loaded"
    loaded_lookup.assert_called_once_with(hass, "keymaster")


def test_comparator_failure_is_unknown(hass, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not convert missing, invalid, or incomparable comparator data to support."""

    class BrokenVersionIntegration:
        """Integration object whose comparator property is unavailable."""

        manifest = {"version": "v0.6.1"}

        @property
        def version(self):
            """Raise to model a broken HA version comparator."""
            raise RuntimeError("sensitive comparator failure")

    monkeypatch.setattr(
        loader,
        "async_get_loaded_integration",
        Mock(return_value=BrokenVersionIntegration()),
    )

    diagnosis = diagnose_keymaster_version(hass)

    assert diagnosis.status is KeymasterVersionStatus.UNKNOWN
    assert diagnosis.version is None
    assert diagnosis.reason == "version_comparator_unavailable"
