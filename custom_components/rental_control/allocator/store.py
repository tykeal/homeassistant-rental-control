# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Home Assistant Store wrapper for the shared code registry.

Persisted door codes use Keymaster-style salted base64 obfuscation.  This
is not encryption and is not a security boundary; the salt is persisted
beside the encoded value and anyone with file access can recover the code.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import hashlib
import logging
import secrets
from typing import Any

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store

from ..const import CODE_REGISTRY_SCHEMA_VERSION
from ..const import DOMAIN
from ..const import NAME
from ..const import STORE_CODE_REGISTRY_KEY
from .models import AllocationOrigin
from .models import AllocationOwner
from .models import AllocationRecord
from .registry import AllocationRegistry

_LOGGER = logging.getLogger(__name__)
_NOTIFICATION_ID = f"{DOMAIN}_code_registry_warning"
_SAVE_DELAY = 1


@dataclass(frozen=True, slots=True)
class RegistryLoadResult:
    """Result of loading the persisted registry."""

    registry: AllocationRegistry
    registry_lost: bool
    registry_missing: bool = False


def encode_code(code: str, salt: str) -> str:
    """Obfuscate a door code with a salt prefix and base64 encoding."""
    return base64.b64encode(salt.encode("utf-8") + code.encode("utf-8")).decode("utf-8")


def decode_code(encoded_code: str, salt: str) -> str:
    """Recover an obfuscated door code using its stored salt value."""
    salt_bytes = salt.encode("utf-8")
    raw = base64.b64decode(encoded_code, validate=True)
    if not raw.startswith(salt_bytes):
        raise ValueError("encoded code does not match stored salt")
    return raw[len(salt_bytes) :].decode("utf-8")


def code_ref_for(code: str, salt: str) -> str:
    """Return the masked diagnostic reference for a plain code."""
    return hashlib.sha256(f"{salt}{code}".encode("utf-8")).hexdigest()[:8]


class RegistryStore:
    """Load and save the shared allocator registry in Home Assistant storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the Home Assistant storage wrapper."""
        self._hass = hass
        self._store: Store = Store(
            hass,
            CODE_REGISTRY_SCHEMA_VERSION,
            STORE_CODE_REGISTRY_KEY,
        )

    async def async_load(self) -> RegistryLoadResult:
        """Load and validate the registry, fail-closed on corruption."""
        try:
            payload = await self._store.async_load()
        except Exception as err:
            raise ConfigEntryNotReady("Unable to load shared code registry") from err
        if payload is None:
            self._warn_empty("code registry store is absent")
            return RegistryLoadResult(
                self._empty_registry(),
                registry_lost=True,
                registry_missing=True,
            )
        try:
            registry = self._registry_from_payload(payload)
        except ValueError as err:
            self._warn_empty(f"invalid code registry payload: {err}")
            return RegistryLoadResult(self._empty_registry(), registry_lost=True)
        return RegistryLoadResult(registry, registry_lost=False)

    def async_save(self, registry: AllocationRegistry) -> None:
        """Schedule a delayed save of the current registry payload."""
        payload = self._payload_from_registry(registry)
        self._store.async_delay_save(lambda: payload, _SAVE_DELAY)

    def _empty_registry(self) -> AllocationRegistry:
        """Return an empty registry with a fresh diagnostic salt."""
        return AllocationRegistry(code_ref_salt=secrets.token_hex(8))

    def _warn_empty(self, message: str) -> None:
        """Log and notify that the registry could not be used."""
        _LOGGER.warning("Starting with an empty code registry: %s", message)
        async_create(
            self._hass,
            f"Starting with an empty shared code registry: {message}",
            title=f"{NAME} code registry warning",
            notification_id=_NOTIFICATION_ID,
        )

    def _registry_from_payload(self, payload: Any) -> AllocationRegistry:
        """Convert a storage payload into a validated allocation registry."""
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        schema_version = payload.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != CODE_REGISTRY_SCHEMA_VERSION
        ):
            raise ValueError("schema_version is not supported")
        records_payload = payload.get("records")
        if not isinstance(records_payload, list):
            raise ValueError("records is not a list")
        code_ref_salt = payload.get("code_ref_salt")
        if not isinstance(code_ref_salt, str) or not code_ref_salt:
            code_ref_salt = secrets.token_hex(8)
        records: dict[str, AllocationRecord] = {}
        identities: set[str] = set()
        for item in records_payload:
            record = self._record_from_payload(item, code_ref_salt)
            if record.code in records:
                raise ValueError("two records decode to the same code")
            for owner in record.owners:
                if owner.identity_key in identities:
                    raise ValueError("identity_key owned by two records")
                identities.add(owner.identity_key)
            records[record.code] = record
        return AllocationRegistry(records=records, code_ref_salt=code_ref_salt)

    def _record_from_payload(self, item: Any, code_ref_salt: str) -> AllocationRecord:
        """Convert one storage record into an allocation record."""
        if not isinstance(item, dict):
            raise ValueError("record is not an object")
        code_length = item.get("code_length")
        if (
            not isinstance(code_length, int)
            or isinstance(code_length, bool)
            or code_length < 1
        ):
            raise ValueError("record code_length is invalid")
        encoded_code = item.get("encoded_code")
        salt_value = item.get("encoding_salt_value")
        salt_source = item.get("encoding_salt_source")
        if not isinstance(encoded_code, str) or not encoded_code:
            raise ValueError("record encoded_code is missing")
        if salt_source != "entry_id":
            raise ValueError("record encoding_salt_source is invalid")
        if not isinstance(salt_value, str) or not salt_value:
            raise ValueError("record encoding_salt_value is missing")
        try:
            code = decode_code(encoded_code, salt_value)
        except Exception as err:
            raise ValueError("record encoded_code is undecodable") from err
        owners_payload = item.get("owners")
        if not isinstance(owners_payload, list) or not owners_payload:
            raise ValueError("record owners must be a non-empty list")
        owners = [self._owner_from_payload(owner) for owner in owners_payload]
        return AllocationRecord(
            code=code,
            code_ref=code_ref_for(code, code_ref_salt),
            encoding_salt_source=salt_source,
            encoding_salt_value=salt_value,
            code_length=code_length,
            owners=owners,
            created_at=self._string_field(item, "created_at"),
            updated_at=self._string_field(item, "updated_at"),
        )

    def _owner_from_payload(self, item: Any) -> AllocationOwner:
        """Convert one storage owner into an allocation owner."""
        if not isinstance(item, dict):
            raise ValueError("owner is not an object")
        origin_value = item.get("origin")
        if not isinstance(origin_value, str):
            raise ValueError("owner origin is invalid")
        try:
            origin = AllocationOrigin(origin_value)
        except ValueError as err:
            raise ValueError("owner origin is invalid") from err
        slot = item.get("slot")
        if slot is not None and (not isinstance(slot, int) or isinstance(slot, bool)):
            raise ValueError("owner slot is invalid")
        lockname = item.get("lockname")
        if lockname is not None and not isinstance(lockname, str):
            raise ValueError("owner lockname is invalid")
        lock_observed = item.get("lock_observed", False)
        if not isinstance(lock_observed, bool):
            raise ValueError("owner lock_observed is invalid")
        return AllocationOwner(
            entry_id=self._string_field(item, "entry_id"),
            identity_key=self._string_field(item, "identity_key"),
            origin=origin,
            lockname=lockname,
            slot=slot,
            lock_observed=lock_observed,
            first_seen=self._string_field(item, "first_seen"),
            last_seen=self._string_field(item, "last_seen"),
        )

    @staticmethod
    def _string_field(item: dict[str, Any], key: str) -> str:
        """Return a required string field from a payload object."""
        value = item.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    def _payload_from_registry(self, registry: AllocationRegistry) -> dict[str, Any]:
        """Convert a registry into the persisted storage payload."""
        code_ref_salt = registry.code_ref_salt or secrets.token_hex(8)
        registry.code_ref_salt = code_ref_salt
        return {
            "schema_version": CODE_REGISTRY_SCHEMA_VERSION,
            "updated_at": datetime.now(UTC).isoformat(),
            "code_ref_salt": code_ref_salt,
            "records": [
                self._record_to_payload(record)
                for record in sorted(
                    registry.records.values(), key=lambda item: item.code
                )
            ],
        }

    def _record_to_payload(self, record: AllocationRecord) -> dict[str, Any]:
        """Convert one allocation record into the stored schema shape."""
        return {
            "encoded_code": encode_code(record.code, record.encoding_salt_value),
            "encoding_salt_source": record.encoding_salt_source,
            "encoding_salt_value": record.encoding_salt_value,
            "code_length": record.code_length,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "owners": [self._owner_to_payload(owner) for owner in record.owners],
        }

    @staticmethod
    def _owner_to_payload(owner: AllocationOwner) -> dict[str, Any]:
        """Convert one owner into the stored schema shape."""
        return {
            "entry_id": owner.entry_id,
            "identity_key": owner.identity_key,
            "origin": owner.origin.value,
            "lockname": owner.lockname,
            "slot": owner.slot,
            "lock_observed": owner.lock_observed,
            "first_seen": owner.first_seen,
            "last_seen": owner.last_seen,
        }
