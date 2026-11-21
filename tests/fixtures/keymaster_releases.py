# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Acquire and verify pinned upstream fixtures without pytest networking."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import sys
import tempfile
import time
from types import ModuleType
from typing import Any
from typing import cast
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler
from urllib.request import Request
from urllib.request import build_opener
import zipfile

_MANIFEST = Path(__file__).with_name("upstream_artifacts.json")
_ARTIFACT_ENV = "RC_UPSTREAM_ARTIFACTS_DIR"
_TOKEN_ENV = "GH_TOKEN"
_USER_AGENT = "homeassistant-rental-control-test-fixtures"
_URL_TIMEOUT_SECONDS = 15
_MAX_RETRIES = 2
_MAX_RETRY_AFTER_SECONDS = 5.0
_RETRY_BACKOFF_SECONDS = 0.25
_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class SyntheticKeymasterEntry:
    """Represent a test-only KM entry and its asserted physical-lock identity."""

    entry_id: str
    physical_lock_id: str
    lock_name: str


@dataclass(slots=True)
class FakeKeymasterProvider:
    """Record simulated provider calls without touching a physical lock."""

    codes: dict[tuple[str, int], str] = field(default_factory=dict)
    calls: list[tuple[str, str, int, str | None]] = field(default_factory=list)

    async def async_set_code(self, physical_lock_id: str, slot: int, code: str) -> None:
        """Record a simulated code write."""
        self.calls.append(("set", physical_lock_id, slot, code))
        self.codes[(physical_lock_id, slot)] = code

    async def async_clear_code(self, physical_lock_id: str, slot: int) -> None:
        """Record a simulated code clear."""
        self.calls.append(("clear", physical_lock_id, slot, None))
        self.codes.pop((physical_lock_id, slot), None)


def synthetic_attestation_entries() -> tuple[
    SyntheticKeymasterEntry, SyntheticKeymasterEntry, SyntheticKeymasterEntry
]:
    """Return original/recreated entries on one lock and an unrelated lock."""
    return (
        SyntheticKeymasterEntry("km-original", "physical-front", "Front"),
        SyntheticKeymasterEntry("km-recreated", "physical-front", "Front"),
        SyntheticKeymasterEntry("km-unrelated", "physical-back", "Back"),
    )


def _read_manifest() -> dict[str, Any]:
    """Load the checked-in artifact pin manifest."""
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported upstream-artifact manifest")
    return cast(dict[str, Any], data)


def _is_github_api_origin(url: str) -> bool:
    """Return whether URL has exactly the HTTPS GitHub API origin."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "api.github.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _build_request(url: str) -> Request:
    """Build public requests; scope an optional token to the API origin."""
    headers = {"User-Agent": _USER_AGENT}
    token = os.environ.get(_TOKEN_ENV)
    if token and _is_github_api_origin(url):
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers)


class _NoAuthenticatedRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects on authenticated requests to prevent token leakage."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        """Do not forward Authorization to any redirect target."""
        if request.has_header("Authorization"):
            return None
        return cast(
            Request | None,
            super().redirect_request(
                request, file_pointer, code, message, headers, new_url
            ),
        )


def _open_url(request: Request, *, timeout: float) -> Any:
    """Open a URL with redirects disabled only for authenticated requests."""
    return build_opener(_NoAuthenticatedRedirectHandler()).open(
        request, timeout=timeout
    )


def _retry_after_seconds(value: str) -> float | None:
    """Parse a bounded Retry-After value expressed as seconds or HTTP date."""
    try:
        delay = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at is None:
                return None
            retry_at = cast(datetime, retry_at)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except TypeError, ValueError, OverflowError:
            return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return delay


def _http_retry_delay(error: HTTPError, attempt: int) -> float | None:
    """Choose a bounded retry delay only for transient or rate-limit errors."""
    headers = error.headers
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        if error.code not in _TRANSIENT_HTTP_STATUSES | {403}:
            return None
        delay = _retry_after_seconds(retry_after)
        if delay is None or delay > _MAX_RETRY_AFTER_SECONDS:
            return None
        return delay

    if error.code == 403:
        if headers is None or headers.get("X-RateLimit-Remaining") != "0":
            return None
        reset = headers.get("X-RateLimit-Reset")
        if reset is None:
            return None
        try:
            delay = max(0.0, float(reset) - time.time())
        except ValueError:
            return None
        if not math.isfinite(delay) or delay > _MAX_RETRY_AFTER_SECONDS:
            return None
        return delay

    if error.code not in _TRANSIENT_HTTP_STATUSES:
        return None
    return _backoff_delay(attempt)


def _backoff_delay(attempt: int) -> float:
    """Return bounded exponential backoff for one retry attempt."""
    delay = float(_RETRY_BACKOFF_SECONDS * (2**attempt))
    if delay > _MAX_RETRY_AFTER_SECONDS:
        return _MAX_RETRY_AFTER_SECONDS
    return delay


def _read_url(url: str) -> bytes:
    """Fetch a pinned URL with scoped auth and bounded transient retries."""
    request = _build_request(url)
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with _open_url(request, timeout=_URL_TIMEOUT_SECONDS) as response:
                return cast(bytes, response.read())
        except HTTPError as error:
            delay = _http_retry_delay(error, attempt)
            if delay is None or attempt == _MAX_RETRIES:
                error.close()
                raise
            error.close()
        except TimeoutError, URLError:
            if attempt == _MAX_RETRIES:
                raise
            delay = _backoff_delay(attempt)
        time.sleep(delay)
    raise AssertionError("bounded URL retry loop exited unexpectedly")


def _artifact_specs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all package, tagged-source and legacy artifact pins."""
    specs = [
        dict(item, artifact_kind="keymaster_package") for item in manifest["keymaster"]
    ]
    specs.extend(
        {
            "tag": item["tag"],
            "tag_commit": item["tag_commit"],
            "filename": item["source_filename"],
            "url": item["source_url"],
            "size": item["source_size"],
            "sha256": item["source_sha256"],
            "manifest_version": item["source_manifest_version"],
            "artifact_kind": "keymaster_source",
        }
        for item in manifest["keymaster"]
    )
    specs.append(dict(manifest["rental_control"], artifact_kind="rental_control"))
    return specs


def _write_verified_file(directory: Path, filename: str, data: bytes) -> Path:
    """Atomically save bytes only after their pinned digest and size match."""
    manifest = _read_manifest()
    spec = next(
        item for item in _artifact_specs(manifest) if item["filename"] == filename
    )
    if len(data) != spec["size"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError(f"upstream artifact integrity check failed: {filename}")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    target = directory / filename
    temp_path.replace(target)
    return target


def _verify_zip_member_paths(archive: zipfile.ZipFile) -> None:
    """Reject absolute or parent-traversing paths before archive extraction."""
    for member in archive.namelist():
        path = PurePosixPath(member)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe path in pinned upstream archive")


def verify_artifacts(directory: Path) -> dict[str, Path]:
    """Verify local artifact bytes and their embedded release manifests."""
    manifest = _read_manifest()
    specs = _artifact_specs(manifest)
    verified: dict[str, Path] = {}
    for spec in specs:
        path = directory / spec["filename"]
        if not path.is_file():
            raise FileNotFoundError(
                f"required upstream artifact is absent: {path.name}"
            )
        data = path.read_bytes()
        if (
            len(data) != spec["size"]
            or hashlib.sha256(data).hexdigest() != spec["sha256"]
        ):
            raise ValueError(f"upstream artifact integrity check failed: {path.name}")
        try:
            with zipfile.ZipFile(path) as archive:
                _verify_zip_member_paths(archive)
                if archive.testzip() is not None:
                    raise ValueError(f"invalid upstream archive: {path.name}")
                if spec["artifact_kind"] == "keymaster_package":
                    release_manifest = json.loads(archive.read("manifest.json"))
                    if release_manifest.get("version") != spec["version"]:
                        raise ValueError(f"Keymaster version mismatch: {path.name}")
                elif spec["artifact_kind"] == "keymaster_source":
                    prefix = f"FutureTense-keymaster-{spec['tag_commit'][:7]}"
                    manifest_path = (
                        f"{prefix}/custom_components/keymaster/manifest.json"
                    )
                    release_manifest = json.loads(archive.read(manifest_path))
                    if release_manifest.get("version") != spec["manifest_version"]:
                        raise ValueError("tagged-source Keymaster manifest mismatch")
                    if f"{prefix}/LICENSE" not in archive.namelist():
                        raise ValueError("tagged source archive is missing its license")
                    package_spec = next(
                        item
                        for item in manifest["keymaster"]
                        if item["tag"] == spec["tag"]
                    )
                    package_path = directory / package_spec["filename"]
                    with zipfile.ZipFile(package_path) as package_archive:
                        package_files = _zip_files(package_archive)
                    source_files = _zip_files(
                        archive,
                        prefix=f"{prefix}/custom_components/keymaster/",
                    )
                    if set(package_files) != set(source_files):
                        raise ValueError("tagged source and package file sets differ")
                    changed = {
                        name
                        for name in package_files
                        if package_files[name] != source_files[name]
                    }
                    if changed != {"const.py", "manifest.json"}:
                        raise ValueError("unexpected tagged source/package differences")
                else:
                    prefix = f"tykeal-homeassistant-rental-control-{spec['commit'][:7]}"
                    manifest_path = (
                        f"{prefix}/custom_components/rental_control/manifest.json"
                    )
                    release_manifest = json.loads(archive.read(manifest_path))
                    if release_manifest.get("version") != spec["manifest_version"]:
                        raise ValueError("legacy tagged-source manifest mismatch")
                    if f"{prefix}/LICENSE" not in archive.namelist():
                        raise ValueError("legacy source archive is missing its license")
        except (KeyError, json.JSONDecodeError, zipfile.BadZipFile) as err:
            raise ValueError(f"invalid upstream archive contents: {path.name}") from err
        verified[path.stem] = path
    return verified


def _zip_files(archive: zipfile.ZipFile, *, prefix: str = "") -> dict[str, bytes]:
    """Return regular archive members keyed by paths relative to a prefix."""
    return {
        name.removeprefix(prefix): archive.read(name)
        for name in archive.namelist()
        if name.startswith(prefix)
        and not name.endswith("/")
        and (not prefix or name != prefix.rstrip("/"))
    }


def artifact_paths() -> dict[str, Path]:
    """Return verified local fixtures; never access the network from pytest."""
    directory = os.environ.get(_ARTIFACT_ENV)
    if not directory:
        raise RuntimeError(f"{_ARTIFACT_ENV} must point to pre-acquired fixtures")
    return verify_artifacts(Path(directory))


def extract_keymaster_release(version: str, destination: Path) -> Path:
    """Extract one verified packaged Keymaster release into a test directory."""
    manifest = _read_manifest()
    spec = next(item for item in manifest["keymaster"] if item["version"] == version)
    archive_path = artifact_paths()[Path(spec["filename"]).stem]
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        _verify_zip_member_paths(archive)
        archive.extractall(destination)
    return destination


def import_keymaster_release(
    version: str, destination: Path, module_name: str
) -> ModuleType:
    """Load an actual verified release package under an isolated module name."""
    package_root = extract_keymaster_release(version, destination)
    providers = ModuleType(f"{module_name}.providers")
    providers.__dict__["CodeSlot"] = object
    providers.__dict__["create_provider"] = lambda *_args, **_kwargs: (
        FakeKeymasterProvider()
    )
    providers.__dict__["is_platform_supported"] = lambda *_args, **_kwargs: True
    providers.__dict__["refresh_pass_verify_budget"] = lambda *_args, **_kwargs: 0
    sys.modules[providers.__name__] = providers
    init_file = package_root / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(package_root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("unable to create upstream Keymaster package loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        remove_imported_package(module_name)
        raise
    return module


def _read_import_requirements() -> dict[str, str]:
    """Read exact optional HA component requirements for the real-import probe."""
    requirements_path = Path(__file__).with_name("keymaster_import_requirements.txt")
    requirements: dict[str, str] = {}
    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        requirement = line.partition("#")[0].strip()
        if not requirement:
            continue
        name, separator, version = requirement.partition("==")
        if not separator or not name or not version or name in requirements:
            raise ValueError("invalid or duplicate Keymaster import requirement")
        requirements[name] = version
    return requirements


def _verify_authentic_import_environment() -> None:
    """Require the locked HA line and each manifest-pinned provider dependency."""
    homeassistant_version = importlib.metadata.version("homeassistant")
    if homeassistant_version != "2026.11.0":
        raise RuntimeError(
            "authentic Keymaster imports require HA 2026.11.0, "
            f"found {homeassistant_version}"
        )
    requirements = _read_import_requirements()
    for package, expected_version in requirements.items():
        actual_version = importlib.metadata.version(package)
        if actual_version != expected_version:
            raise RuntimeError(
                f"authentic import dependency mismatch: {package} "
                f"{actual_version} != {expected_version}"
            )


def probe_authentic_keymaster_import(version: str) -> None:
    """Import a pinned package with real providers, or verify v0.6.0 rejection.

    Run this command in a fresh HA process with the exact optional integration
    requirements installed. The only namespace initialized here is
    ``custom_components``; no Keymaster/provider/constant module is replaced.
    """
    if version not in {"v0.6.0", "v0.6.1"}:
        raise ValueError("authentic import probe only covers v0.6.0 and v0.6.1")
    _verify_authentic_import_environment()
    module_name = "custom_components.keymaster"
    if any(
        name == module_name or name.startswith(f"{module_name}.")
        for name in sys.modules
    ):
        raise RuntimeError(
            "Keymaster modules were loaded before the fresh-process probe"
        )

    package_root = extract_keymaster_release(
        version, Path(tempfile.mkdtemp(prefix=f"keymaster-{version}-"))
    )
    namespace = ModuleType("custom_components")
    namespace.__path__ = []
    sys.modules["custom_components"] = namespace
    spec = importlib.util.spec_from_file_location(
        module_name,
        package_root / "__init__.py",
        submodule_search_locations=[str(package_root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("unable to create authentic upstream Keymaster loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as error:
        if (
            version == "v0.6.0"
            and "LARGE_LOCK_ACK_STORE_VERSION" in str(error)
            and str(package_root / "const.py") in str(error)
        ):
            print(
                "IMPORT=EXPECTED-REJECTION",
                version,
                "missing LARGE_LOCK_ACK_STORE_VERSION",
                package_root / "const.py",
            )
            return
        raise
    if version == "v0.6.0":
        raise RuntimeError("v0.6.0 unexpectedly imported successfully")

    provider_modules = (
        "custom_components.keymaster.providers",
        "custom_components.keymaster.providers.zha",
        "custom_components.keymaster.providers.zwave_js",
    )
    provider_origins: list[Path] = []
    for name in provider_modules:
        imported = sys.modules.get(name)
        origin = Path(getattr(imported, "__file__", "")).resolve()
        if not origin.is_relative_to(package_root.resolve()):
            raise RuntimeError(f"provider did not load from pinned package: {name}")
        provider_origins.append(origin)
    print("IMPORT=PASS", version, module.__file__)
    for origin in provider_origins:
        print("PROVIDER", origin)


def import_keymaster_entity_module(
    version: str, destination: Path, module_name: str
) -> ModuleType:
    """Load an actual release entity module with inert HA/provider dependencies."""
    package_root = extract_keymaster_release(version, destination)
    package = ModuleType(module_name)
    package.__dict__["__path__"] = [str(package_root)]
    package.__dict__["__package__"] = module_name
    sys.modules[module_name] = package
    coordinator = ModuleType(f"{module_name}.coordinator")
    coordinator.__dict__["KeymasterLockCoordinator"] = type(
        "KeymasterLockCoordinator", (), {}
    )
    lock = ModuleType(f"{module_name}.lock")
    lock.__dict__["KeymasterLock"] = type("KeymasterLock", (), {})
    sys.modules[coordinator.__name__] = coordinator
    sys.modules[lock.__name__] = lock
    try:
        from importlib import import_module

        return import_module(f"{module_name}.entity")
    except Exception:
        remove_imported_package(module_name)
        raise


def extract_legacy_rental_control(destination: Path) -> Path:
    """Extract the pinned v4.0.3 source tree into an isolated test directory."""
    manifest = _read_manifest()
    spec = manifest["rental_control"]
    archive_path = artifact_paths()[Path(spec["filename"]).stem]
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        _verify_zip_member_paths(archive)
        source_root = next(
            name.split("/", 1)[0]
            for name in archive.namelist()
            if name.endswith("/custom_components/rental_control/manifest.json")
        )
        for member in archive.namelist():
            if member.startswith(f"{source_root}/custom_components/rental_control/"):
                archive.extract(member, destination)
        archive.extract(f"{source_root}/LICENSE", destination)
    return destination / source_root / "custom_components" / "rental_control"


def import_legacy_registry_store(source_root: Path, module_name: str) -> ModuleType:
    """Import the actual old Store wrapper under an isolated package namespace."""
    package = ModuleType(module_name)
    package.__dict__["__path__"] = [str(source_root)]
    package.__package__ = module_name
    sys.modules[module_name] = package
    try:
        from importlib import import_module

        return import_module(f"{module_name}.allocator.store")
    except Exception:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(f"{module_name}."):
                del sys.modules[name]
        raise


def remove_imported_package(module_name: str) -> None:
    """Remove an isolated upstream package and its submodules from sys.modules."""
    for name in tuple(sys.modules):
        if name == module_name or name.startswith(f"{module_name}."):
            del sys.modules[name]


def _verify_repository_license(repository: str, expected: str) -> None:
    """Confirm GitHub reports the expected upstream repository license."""
    url = f"https://api.github.com/repos/{repository}"
    metadata = json.loads(_read_url(url))
    actual = (metadata.get("license") or {}).get("spdx_id")
    if actual != expected:
        raise ValueError(f"unexpected upstream license for {repository}")


def acquire_artifacts(directory: Path) -> dict[str, Path]:
    """Acquire pinned files after verifying GitHub release and commit metadata."""
    manifest = _read_manifest()
    keymaster_repo = "FutureTense/keymaster"
    rc_repo = "tykeal/homeassistant-rental-control"
    _verify_repository_license(keymaster_repo, "MIT")
    _verify_repository_license(rc_repo, "Apache-2.0")

    acquired: list[Path] = []
    for spec in manifest["keymaster"]:
        tag_url = (
            f"https://api.github.com/repos/{keymaster_repo}/git/ref/tags/{spec['tag']}"
        )
        tag_ref = json.loads(_read_url(tag_url))
        if tag_ref.get("object", {}).get("sha") != spec["tag_commit"]:
            raise ValueError(f"Keymaster tag provenance mismatch: {spec['tag']}")
        release_url = (
            f"https://api.github.com/repos/{keymaster_repo}/releases/tags/{spec['tag']}"
        )
        release = json.loads(_read_url(release_url))
        assets = [
            asset
            for asset in release.get("assets", [])
            if asset["id"] == spec["asset_id"]
        ]
        if (
            release.get("id") != spec["release_id"]
            or release.get("tag_name") != spec["tag"]
            or len(assets) != 1
            or assets[0].get("name") != spec["asset_name"]
            or assets[0].get("size") != spec["size"]
            or assets[0].get("digest") != f"sha256:{spec['sha256']}"
            or assets[0].get("browser_download_url") != spec["url"]
        ):
            raise ValueError(f"Keymaster release provenance mismatch: {spec['tag']}")
        acquired.append(
            _write_verified_file(directory, spec["filename"], _read_url(spec["url"]))
        )
        acquired.append(
            _write_verified_file(
                directory, spec["source_filename"], _read_url(spec["source_url"])
            )
        )

    legacy = manifest["rental_control"]
    commit = json.loads(_read_url(legacy["commit_api_url"]))
    if commit.get("sha") != legacy["commit"]:
        raise ValueError("legacy Rental Control commit provenance mismatch")
    acquired.append(
        _write_verified_file(
            directory, legacy["filename"], _read_url(legacy["archive_url"])
        )
    )
    verify_artifacts(directory)
    return {path.stem: path for path in acquired}


def _main() -> None:
    """Run the pre-pytest artifact acquisition or local verification command."""
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    acquire = actions.add_parser("acquire")
    acquire.add_argument("--output-dir", type=Path, required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("--directory", type=Path, required=True)
    probe = actions.add_parser("probe-import")
    probe.add_argument("--release", choices=("v0.6.0", "v0.6.1"), required=True)
    args = parser.parse_args()
    if args.action == "acquire":
        paths = acquire_artifacts(args.output_dir)
    elif args.action == "verify":
        paths = verify_artifacts(args.directory)
        for path in paths.values():
            print(path)
    else:
        probe_authentic_keymaster_import(args.release)


if __name__ == "__main__":
    _main()
