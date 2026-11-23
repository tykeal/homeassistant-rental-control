# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Smoke-test verified upstream packages in isolated HA test runtimes."""

from __future__ import annotations

from email.message import Message
from importlib import import_module
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from typing import cast
from unittest.mock import patch
from urllib.error import HTTPError

from homeassistant.util import slugify
import pytest
from pytest_homeassistant.common import MockConfigEntry

from tests.fixtures import keymaster_releases
from tests.fixtures.keymaster_releases import FakeKeymasterProvider
from tests.fixtures.keymaster_releases import artifact_paths
from tests.fixtures.keymaster_releases import extract_legacy_rental_control
from tests.fixtures.keymaster_releases import import_keymaster_entity_module
from tests.fixtures.keymaster_releases import import_keymaster_release
from tests.fixtures.keymaster_releases import import_legacy_registry_store
from tests.fixtures.keymaster_releases import remove_imported_package
from tests.fixtures.keymaster_releases import synthetic_attestation_entries
from tests.fixtures.keymaster_releases import verify_artifacts

_RELEASES = ("v0.6.1", "v0.6.5")


@pytest.fixture(scope="module")
def verified_artifact_paths() -> dict[str, Path]:
    """Require upstream artifacts to be acquired before pytest starts."""
    return artifact_paths()


@pytest.mark.parametrize("release", _RELEASES)
async def test_packaged_keymaster_release_loads_and_uses_ha_slugify(
    hass,
    tmp_path: Path,
    release: str,
    verified_artifact_paths: dict[str, Path],  # noqa: ARG001
) -> None:
    """Load each actual release entity and characterize its unique ID."""
    module_name = "custom_components.keymaster"
    assert module_name not in sys.modules
    entity_module = import_keymaster_entity_module(
        release, tmp_path / release, module_name
    )
    try:
        upstream_entity = cast(Any, entity_module)
        assert upstream_entity.DOMAIN == "keymaster"
        entry = MockConfigEntry(
            domain="keymaster",
            title="Front",
            entry_id=f"km-{release}",
        )
        lock = SimpleNamespace(lock_name="Front")
        coordinator = SimpleNamespace(
            sync_get_lock_by_config_entry_id=lambda _entry_id: lock
        )
        property_key = "text.code_slots:21.pin"
        description = upstream_entity.KeymasterEntityDescription(
            hass=hass,
            config_entry=entry,
            coordinator=coordinator,
            key=property_key,
            name="PIN",
        )
        with patch.object(
            upstream_entity.CoordinatorEntity, "__init__", lambda *args: None
        ):
            entity = upstream_entity.KeymasterEntity(description)
        assert entity.unique_id == f"{entry.entry_id}_{slugify(property_key)}"
        assert (
            json.loads(
                (tmp_path / release / "manifest.json").read_text(encoding="utf-8")
            )["version"]
            == release
        )
    finally:
        remove_imported_package(module_name)


@pytest.mark.parametrize("release", _RELEASES)
async def test_supported_package_import_characterizes_fake_provider_boundary(
    hass,  # noqa: ARG001
    tmp_path: Path,
    release: str,
    verified_artifact_paths: dict[str, Path],  # noqa: ARG001
) -> None:
    """Exercise each supported package with the disposable fake provider."""
    module_name = "custom_components.keymaster"
    import_keymaster_release(release, tmp_path / release, module_name)
    try:
        providers = cast(Any, import_module(f"{module_name}.providers"))
        provider = providers.create_provider()
        assert isinstance(provider, FakeKeymasterProvider)
        await provider.async_set_code("physical-front", 21, "2468")
        assert provider.codes[("physical-front", 21)] == "2468"
    finally:
        remove_imported_package(module_name)


def test_v060_packaged_integration_exposes_missing_required_constant(
    tmp_path: Path,
    verified_artifact_paths: dict[str, Path],  # noqa: ARG001
) -> None:
    """Characterize the official v0.6.0 asset's current import failure."""
    with pytest.raises(ImportError, match="LARGE_LOCK_ACK_STORE_VERSION"):
        import_keymaster_release(
            "v0.6.0", tmp_path / "v0.6.0", "custom_components.keymaster"
        )


def test_release_pins_distinguish_supported_floor_from_rejected_package() -> None:
    """Keep the broken v0.6.0 fixture separate from supported test releases."""
    manifest = keymaster_releases._read_manifest()
    support_status = {
        package["version"]: package["support_status"]
        for package in manifest["keymaster"]
    }

    assert support_status == {
        "v0.6.0": "rejected-import-characterization",
        "v0.6.1": "supported",
        "v0.6.5": "supported",
    }
    assert _RELEASES == ("v0.6.1", "v0.6.5")


def test_real_import_requirements_match_ha_component_manifests() -> None:
    """Pin only the exact runtime requirements declared by HA 2026.11.0."""
    homeassistant = import_module("homeassistant")
    component_root = Path(cast(str, homeassistant.__file__)).parent / "components"
    components = (
        "mqtt",
        "zha",
        "zwave_js",
        "usb",
        "homeassistant_hardware",
        "hassio",
    )
    expected = {
        requirement
        for component in components
        for requirement in json.loads(
            (component_root / component / "manifest.json").read_text(encoding="utf-8")
        ).get("requirements", [])
    }
    requirements_path = (
        Path(__file__).parents[1] / "fixtures" / "keymaster_import_requirements.txt"
    )
    actual = {
        line.partition("#")[0].strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.partition("#")[0].strip()
    }

    assert actual == expected


def test_authentic_import_probe_lock_pins_manifest_requirements_with_hashes() -> None:
    """Keep the disposable probe install on its generated resolved lock."""
    fixtures = Path(__file__).parents[1] / "fixtures"
    lock_lines = (
        (fixtures / "keymaster_import_probe.lock")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    packages: dict[str, tuple[str, bool]] = {}
    current: str | None = None
    for line in lock_lines:
        if line.startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if current is not None and "--hash=sha256:" in line:
                version, _ = packages[current]
                packages[current] = (version, True)
            continue
        package, separator, constraint = line.partition("==")
        if separator:
            version, marker_separator, marker_value = constraint.partition(";")
            normalized = package.lower().replace("_", "-")
            packages[normalized] = (version.removesuffix("\\").strip(), False)
            current = normalized
            if marker_separator and not marker_value.removesuffix("\\").strip():
                raise AssertionError("probe lock has an empty package marker")

    requirements = keymaster_releases._read_import_requirements()
    expected = {
        name.lower().replace("_", "-"): version
        for name, version in requirements.items()
    }
    expected["homeassistant"] = "2026.11.0"
    for package, version in expected.items():
        assert package in packages
        assert packages[package] == (version, True)


async def test_actual_v403_registry_loader_runs_against_ha_store(
    hass,
    tmp_path: Path,
    verified_artifact_paths: dict[str, Path],  # noqa: ARG001
) -> None:
    """Execute the pinned old RC loader with the installed HA Store."""
    legacy_root = extract_legacy_rental_control(tmp_path / "legacy-source")
    module_name = "rental_control_v403_characterization"
    module = import_legacy_registry_store(legacy_root, module_name)
    try:
        assert Path(cast(str, module.__file__)).is_relative_to(legacy_root)
        result = await cast(Any, module).RegistryStore(hass).async_load()
        assert result.registry_lost is True
        assert result.registry_missing is True
    finally:
        remove_imported_package(module_name)


def test_synthetic_attestation_entries_distinguish_lock_continuity() -> None:
    """Provide same-lock recreation and unrelated-lock test identities."""
    original, recreated, unrelated = synthetic_attestation_entries()

    assert original.entry_id != recreated.entry_id
    assert original.physical_lock_id == recreated.physical_lock_id
    assert unrelated.physical_lock_id != original.physical_lock_id


async def test_fake_provider_never_touches_a_real_lock() -> None:
    """Exercise the disposable provider used by future routing tests."""
    provider = FakeKeymasterProvider()
    await provider.async_set_code("physical-front", 21, "2468")

    assert provider.codes == {("physical-front", 21): "2468"}
    assert provider.calls == [("set", "physical-front", 21, "2468")]


def test_artifact_verification_fails_closed_for_absent_or_modified_files(
    tmp_path: Path,
    verified_artifact_paths: dict[str, Path],
) -> None:
    """Never skip missing artifacts or accept byte changes."""
    with pytest.raises(FileNotFoundError):
        verify_artifacts(tmp_path)

    copied = tmp_path / "artifacts"
    copied.mkdir()
    source = next(iter(verified_artifact_paths.values()))
    (copied / source.name).write_bytes(source.read_bytes()[:-1] + b"x")
    with pytest.raises(ValueError, match="integrity check failed"):
        verify_artifacts(copied)


def test_github_token_is_scoped_to_exact_api_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never attach the optional token to public artifacts or lookalike hosts."""
    monkeypatch.setenv("GH_TOKEN", "test-token")
    api_request = keymaster_releases._build_request(
        "https://api.github.com/repos/FutureTense/keymaster"
    )
    assert api_request.get_header("Authorization") == "Bearer test-token"

    for url in (
        "https://codeload.github.com/FutureTense/keymaster/legacy.zip/commit",
        "https://api.github.com.attacker.invalid/repos/FutureTense/keymaster",
        "http://api.github.com/repos/FutureTense/keymaster",
        "https://api.github.com:444/repos/FutureTense/keymaster",
    ):
        assert (
            keymaster_releases._build_request(url).get_header("Authorization") is None
        )

    monkeypatch.delenv("GH_TOKEN")
    assert (
        keymaster_releases._build_request(
            "https://api.github.com/repos/FutureTense/keymaster"
        ).get_header("Authorization")
        is None
    )


def test_authenticated_redirect_is_refused_and_public_redirect_is_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject token-bearing redirects; public artifact redirects carry no token."""
    monkeypatch.setenv("GH_TOKEN", "test-token")
    handler = keymaster_releases._NoAuthenticatedRedirectHandler()
    authenticated = keymaster_releases._build_request(
        "https://api.github.com/repos/FutureTense/keymaster"
    )
    assert (
        handler.redirect_request(
            authenticated,
            None,
            302,
            "Found",
            Message(),
            "https://attacker.invalid/collect",
        )
        is None
    )

    public = keymaster_releases._build_request(
        "https://codeload.github.com/FutureTense/keymaster/archive/commit.zip"
    )
    redirected = handler.redirect_request(
        public,
        None,
        302,
        "Found",
        Message(),
        "https://objects.githubusercontent.com/archive.zip",
    )
    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_transient_http_error_retries_with_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a transient server error once and return only a successful body."""
    url = "https://api.github.com/repos/FutureTense/keymaster"
    outcomes: list[HTTPError | BytesIO] = [
        HTTPError(url, 503, "Service Unavailable", Message(), BytesIO()),
        BytesIO(b"metadata"),
    ]
    requests = []
    delays: list[float] = []

    def fake_open(request, *, timeout):
        """Return one transient error before a successful response."""
        requests.append((request, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, HTTPError):
            raise outcome
        return outcome

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(keymaster_releases, "_open_url", fake_open)
    monkeypatch.setattr(
        keymaster_releases,
        "time",
        SimpleNamespace(sleep=delays.append),
    )

    assert keymaster_releases._read_url(url) == b"metadata"
    assert len(requests) == 2
    assert requests[0][1] == keymaster_releases._URL_TIMEOUT_SECONDS
    assert delays == [keymaster_releases._RETRY_BACKOFF_SECONDS]


def test_rate_limit_retry_after_is_honored_within_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honor a short GitHub rate-limit Retry-After without exceeding policy."""
    url = "https://api.github.com/repos/FutureTense/keymaster"
    headers = Message()
    headers["Retry-After"] = "2"
    outcomes: list[HTTPError | BytesIO] = [
        HTTPError(url, 429, "Too Many Requests", headers, BytesIO()),
        BytesIO(b"metadata"),
    ]
    attempts = []
    delays: list[float] = []

    def fake_open(request, *, timeout):
        """Return one rate-limit error before a successful response."""
        attempts.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, HTTPError):
            raise outcome
        return outcome

    monkeypatch.setattr(keymaster_releases, "_open_url", fake_open)
    monkeypatch.setattr(
        keymaster_releases,
        "time",
        SimpleNamespace(sleep=delays.append),
    )

    assert keymaster_releases._read_url(url) == b"metadata"
    assert len(attempts) == 2
    assert delays == [2.0]


def test_primary_rate_limit_reset_is_honored_within_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honor GitHub's primary rate-limit reset header when it is near."""
    url = "https://api.github.com/repos/FutureTense/keymaster"
    headers = Message()
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "102"
    outcomes: list[HTTPError | BytesIO] = [
        HTTPError(url, 403, "API rate limit exceeded", headers, BytesIO()),
        BytesIO(b"metadata"),
    ]
    attempts = []
    delays: list[float] = []

    def fake_open(request, *, timeout):
        """Return one primary rate-limit error before a successful response."""
        attempts.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, HTTPError):
            raise outcome
        return outcome

    monkeypatch.setattr(keymaster_releases, "_open_url", fake_open)
    monkeypatch.setattr(
        keymaster_releases,
        "time",
        SimpleNamespace(time=lambda: 100.0, sleep=delays.append),
    )

    assert keymaster_releases._read_url(url) == b"metadata"
    assert len(attempts) == 2
    assert delays == [2.0]


@pytest.mark.parametrize(
    ("status", "retry_after"),
    [(401, None), (403, None), (429, "30")],
)
def test_permanent_or_overlong_http_errors_fail_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    retry_after: str | None,
) -> None:
    """Do not retry auth failures or exceed the bounded server delay."""
    url = "https://api.github.com/repos/FutureTense/keymaster"
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    error = HTTPError(url, status, "Request rejected", headers, BytesIO())
    attempts = []
    delays: list[float] = []

    def fake_open(request, *, timeout):
        """Raise the permanent or overlong response error."""
        attempts.append(request)
        raise error

    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(keymaster_releases, "_open_url", fake_open)
    monkeypatch.setattr(
        keymaster_releases,
        "time",
        SimpleNamespace(sleep=delays.append),
    )

    with pytest.raises(HTTPError):
        keymaster_releases._read_url(url)

    assert len(attempts) == 1
    assert attempts[0].get_header("Authorization") == "Bearer test-token"
    assert delays == []
