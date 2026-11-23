# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Immutable data models and allowlists for verified Keymaster operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from homeassistant.core import HomeAssistant

from .allocator.associations import AssociationTuple
from .allocator.models import ProtectionCommitToken
from .keymaster_identity import CapabilityResolution

_FENCE_TYPES = frozenset({"allocation", "pending_clear"})
_PHYSICAL_LOCK_ENTITY_KEY = "lock_entity_id"
_SERVICE_ALLOWLIST = {
    "button": frozenset({"press"}),
    "datetime": frozenset({"set_value"}),
    "switch": frozenset({"turn_off", "turn_on"}),
    "text": frozenset({"set_value"}),
}
_SET_REQUIRED_PROPERTIES = frozenset(
    {
        "switch.code_slots:{slot}.enabled",
        "switch.code_slots:{slot}.accesslimit_date_range_enabled",
        "datetime.code_slots:{slot}.accesslimit_date_range_start",
        "datetime.code_slots:{slot}.accesslimit_date_range_end",
        "text.code_slots:{slot}.pin",
        "text.code_slots:{slot}.name",
    }
)


@dataclass(frozen=True, slots=True)
class _AuthorizationInputs:
    """Validated current entry, lifecycle, store, association and token."""

    config_entry: Any = field(repr=False)
    lifecycle: Any = field(repr=False)
    store: Any = field(repr=False)
    association: AssociationTuple
    loaded: Any = field(repr=False)
    token: ProtectionCommitToken = field(repr=False)
    keymaster_entry: Any = field(repr=False)
    physical_lock_entity_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _CurrentIntentInputs:
    """Current lifecycle, association, store snapshot, and selected entry."""

    lifecycle: Any = field(repr=False)
    store: Any = field(repr=False)
    association: AssociationTuple
    loaded: Any = field(repr=False)
    keymaster_entry: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class _FenceRequest:
    """Exact owner and target identity needed to acknowledge one fence."""

    rc_entry_id: str
    slot: int
    owner_identity_key: str
    owner_reference: str
    allocation_reference: str
    fence_type: str
    allow_native_empty_text: bool
    initial: _AuthorizationInputs = field(repr=False)
    required_properties: tuple[str, ...] = field(repr=False)


class VerifiedOperationRejected(Exception):
    """A fixed-code rejection that must never trigger legacy fallback."""


@dataclass(frozen=True, slots=True)
class VerifiedOperationIntent:
    """Bind one hardware operation to exact identity, fence, and generations.

    An intent is an authorization snapshot, not a long-lived capability.
    Consumers must call :meth:`async_call` for each individual side effect;
    it waits for protected writes to settle and revalidates the association,
    selected entry, capability addresses, lifecycle generation, and exact
    durable owner/fence references before and after each service call.
    """

    hass: HomeAssistant = field(repr=False, compare=False)
    rc_entry_id: str = field(repr=False)
    keymaster_entry_id: str = field(repr=False)
    physical_lock_entity_id: str = field(repr=False)
    slot: int
    owner_identity_key: str = field(repr=False)
    association_generation: int
    lifecycle_generation: int
    protection_token: ProtectionCommitToken = field(repr=False)
    fence_id: str = field(repr=False)
    fence_type: str
    allow_native_empty_text: bool
    owner_reference: str = field(repr=False)
    allocation_reference: str = field(repr=False)
    capabilities: tuple[CapabilityResolution, ...] = field(repr=False)
    # Caller-bound synchronous check run last before every service call.
    dispatch_guard: Callable[[], bool] | None = field(
        default=None, repr=False, compare=False
    )

    def entity_id(self, property_key: str) -> str:
        """Return a previously authorized exact address without dispatching.

        This is a read-only address lookup. Every side effect and positive
        confirmation must separately pass ``async_is_current`` so an unrelated
        protected write can settle before authorization is re-evaluated.
        """
        for capability in self.capabilities:
            if (
                capability.identity is not None
                and capability.identity.property_key == property_key
                and capability.entity_id is not None
            ):
                return capability.entity_id
        raise VerifiedOperationRejected("capability_not_authorized")

    async def async_call(
        self,
        property_key: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        *,
        pre_dispatch_guard: Callable[[], bool] | None = None,
    ) -> None:
        """Dispatch one exact-entity call with point-in-time revalidation."""
        if not await self.async_is_current():
            raise VerifiedOperationRejected("operation_not_current")
        entity_id = self.entity_id(property_key)
        domain = property_key.split(".", maxsplit=1)[0]
        if service not in _SERVICE_ALLOWLIST.get(domain, frozenset()):
            raise VerifiedOperationRejected("service_not_authorized")
        if not await self.async_is_current():
            raise VerifiedOperationRejected("operation_not_current")
        if self.dispatch_guard is not None and not self.dispatch_guard():
            raise VerifiedOperationRejected("operation_not_current")
        if pre_dispatch_guard is not None and not pre_dispatch_guard():
            raise VerifiedOperationRejected("operation_not_current")
        await self.hass.services.async_call(
            domain=domain,
            service=service,
            target={"entity_id": entity_id},
            service_data=service_data or {},
            blocking=True,
        )
        if not await self.async_is_current(allow_native_empty_text=True):
            # The side effect may have completed. The durable fence remains;
            # callers must report unconfirmed and must not retry by name.
            raise VerifiedOperationRejected("operation_invalidated_after_dispatch")

    async def async_is_current(self, *, allow_native_empty_text: bool = False) -> bool:
        """Wait for any protected write, then validate its acknowledged state."""
        from .const import DOMAIN
        from .const import PROTECTED_REGISTRY_STORE

        domain_data = self.hass.data.get(DOMAIN)
        store = (
            domain_data.get(PROTECTED_REGISTRY_STORE)
            if isinstance(domain_data, dict)
            else None
        )
        waiter = getattr(store, "async_wait_for_commit_completion", None)
        if not callable(waiter):
            return False
        try:
            if not await waiter():
                return False
        except Exception:
            return False
        return self.is_current(allow_native_empty_text=allow_native_empty_text)

    def is_current(self, *, allow_native_empty_text: bool = False) -> bool:
        """Revalidate every authorization input on the HA event-loop thread."""
        from .keymaster_operation_validation import _intent_is_current

        return _intent_is_current(
            self,
            allow_native_empty_text=(
                allow_native_empty_text or self.allow_native_empty_text
            ),
        )
