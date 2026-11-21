# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Tests for config-flow validation and step helper parity."""

from __future__ import annotations

from typing import Any

from aioresponses import aioresponses
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator.allocator import DoorCodeAllocator
from custom_components.rental_control.allocator.models import AllocationRequest
from custom_components.rental_control.allocator.models import (
    ProtectionAuthorizationState,
)
from custom_components.rental_control.allocator.models import ProtectionLoadResult
from custom_components.rental_control.allocator.models import ProtectionLoadState
from custom_components.rental_control.config_flow import RentalControlOptionsFlow
from custom_components.rental_control.config_flow import _handle_association_selection
from custom_components.rental_control.config_flow import _start_config_flow
from custom_components.rental_control.config_flow_helpers.models import (
    ConfigFormContext,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    apply_successful_conversions,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    normalize_lock_entry,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    validate_code_length_change,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    validate_name_length,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    validate_name_trimming,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    validate_scalar_fields,
)
from custom_components.rental_control.config_flow_helpers.validation import (
    validate_submitted_data,
)
from custom_components.rental_control.config_flow_helpers.validation import validate_url
from custom_components.rental_control.const import ALLOCATOR
from custom_components.rental_control.const import CONF_ASSOCIATION_ATTEST_SAME_LOCK
from custom_components.rental_control.const import CONF_ASSOCIATION_ENTRY_CHOICE
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_CHECKIN
from custom_components.rental_control.const import CONF_CHECKOUT
from custom_components.rental_control.const import CONF_CODE_GENERATION
from custom_components.rental_control.const import CONF_CODE_LENGTH
from custom_components.rental_control.const import CONF_CREATION_DATETIME
from custom_components.rental_control.const import CONF_DAYS
from custom_components.rental_control.const import CONF_EVENT_PREFIX
from custom_components.rental_control.const import CONF_GENERATE
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_MAX_EVENTS
from custom_components.rental_control.const import CONF_MAX_NAME_LENGTH
from custom_components.rental_control.const import CONF_REFRESH_FREQUENCY
from custom_components.rental_control.const import CONF_SHOULD_UPDATE_CODE
from custom_components.rental_control.const import CONF_START_SLOT
from custom_components.rental_control.const import CONF_TIMEZONE
from custom_components.rental_control.const import CONF_TRIM_NAMES
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from custom_components.rental_control.const import DEFAULT_CHECKIN
from custom_components.rental_control.const import DEFAULT_CHECKOUT
from custom_components.rental_control.const import DEFAULT_CODE_LENGTH
from custom_components.rental_control.const import DEFAULT_DAYS
from custom_components.rental_control.const import DEFAULT_MAX_EVENTS
from custom_components.rental_control.const import DEFAULT_REFRESH_FREQUENCY
from custom_components.rental_control.const import DEFAULT_START_SLOT
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import LOCK_MANAGER
from custom_components.rental_control.const import MIN_NAME_LENGTH
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE

from tests.fixtures import calendar_data

if False:
    from homeassistant.core import HomeAssistant


class _Flow:
    """Flow stub used by helper tests."""

    def __init__(self, hass: "HomeAssistant") -> None:
        """Initialize the flow stub."""
        self.hass = hass
        self.created = "2026-06-29T00:00:00+00:00"

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        """Return create-entry kwargs."""
        return {"type": FlowResultType.CREATE_ENTRY, **kwargs}

    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        """Return show-form kwargs."""
        return {"type": FlowResultType.FORM, **kwargs}


class _OptionsFlow(_Flow):
    """Options-flow stub without created timestamp."""

    def __init__(self, hass: "HomeAssistant") -> None:
        """Initialize the options-flow stub."""
        self.hass = hass
        self.config_entry: Any = None


class _AcknowledgedAssociationStore:
    """Minimal durable-acknowledgement fake for options-flow ordering tests."""

    authorization_state = ProtectionAuthorizationState.READY

    def __init__(self) -> None:
        """Initialize an empty, current v2 snapshot."""
        self.envelopes: list[dict[str, Any]] = []
        self.loaded_result = ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            transaction_id="a" * 32,
            generation=1,
            data={
                "transaction_id": "a" * 32,
                "generation": 1,
                "schema_version": 1,
                "ledger": {
                    "schema_version": 1,
                    "updated_at": "2026-11-21T00:00:00+00:00",
                    "code_ref_salt": "persisted-test-salt",
                    "records": [],
                    "announced_conflicts": {},
                },
                "associations": [],
                "attestations": [],
                "pending_fences": [],
                "holds": [],
                "tombstones": [],
                "pending_config": [],
            },
        )

    def association_references_for_entry(self, _entry_id: str) -> tuple[str, ...]:
        """Report no legacy owners for this isolated first-association test."""
        return ()

    async def async_commit(self, envelope: dict[str, Any]) -> ProtectionLoadResult:
        """Record the acknowledged target and return its exact transaction."""
        self.envelopes.append(envelope)
        data = envelope["data"]
        return ProtectionLoadResult(
            ProtectionLoadState.VALID_V2,
            transaction_id=data["transaction_id"],
            generation=data["generation"],
            data=data,
        )


def _valid_input(url: str = "https://example.com/calendar.ics") -> dict[str, Any]:
    """Return a complete valid config-flow input."""
    return {
        CONF_NAME: "Test Rental",
        CONF_URL: url,
        CONF_VERIFY_SSL: True,
        CONF_LOCK_ENTRY: "(none)",
        CONF_REFRESH_FREQUENCY: DEFAULT_REFRESH_FREQUENCY,
        CONF_TIMEZONE: "UTC",
        CONF_EVENT_PREFIX: "",
        CONF_CHECKIN: DEFAULT_CHECKIN,
        CONF_CHECKOUT: DEFAULT_CHECKOUT,
        CONF_DAYS: DEFAULT_DAYS,
        CONF_MAX_EVENTS: DEFAULT_MAX_EVENTS,
        CONF_START_SLOT: DEFAULT_START_SLOT,
        CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH,
        CONF_CODE_GENERATION: "Start/End Date",
        CONF_SHOULD_UPDATE_CODE: True,
    }


def _add_lock_manager(hass: "HomeAssistant") -> None:
    """Register a mock lock-manager config entry."""
    hass.data[LOCK_MANAGER] = {}
    MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        data={"lockname": "lock.front_door"},
        entry_id="lock-entry",
    ).add_to_hass(hass)


def test_lock_normalization_values() -> None:
    """Test lock-entry normalization keeps current edge cases."""
    assert normalize_lock_entry(None) == "(none)"
    assert normalize_lock_entry("") == "(none)"
    assert normalize_lock_entry("  ") == "(none)"
    assert normalize_lock_entry("Front Door") == "Front Door"


async def test_url_validation_error_parity(hass: "HomeAssistant") -> None:
    """Test URL validation errors and SSL-disabled HTTP behavior."""
    flow = _OptionsFlow(hass)
    errors: dict[str, str] = {}
    await validate_url(flow, _valid_input("ftp://example.com/calendar.ics"), errors)
    assert errors[CONF_URL] == "invalid_url"

    errors = {}
    await validate_url(flow, _valid_input("http://example.com/calendar.ics"), errors)
    assert errors[CONF_URL] == "https_required"

    with aioresponses() as mock_aiohttp:
        url = "http://example.com/calendar.ics"
        mock_aiohttp.get(
            url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        user_input = _valid_input(url)
        user_input[CONF_VERIFY_SSL] = False
        errors = {}
        await validate_url(flow, user_input, errors)
    assert errors == {}


async def test_url_response_error_parity(hass: "HomeAssistant") -> None:
    """Test non-200 and non-calendar response URL errors."""
    flow = _OptionsFlow(hass)
    with aioresponses() as mock_aiohttp:
        url = "https://example.com/missing.ics"
        mock_aiohttp.get(url, status=404, reason="Missing")
        errors: dict[str, str] = {}
        await validate_url(flow, _valid_input(url), errors)
    assert errors[CONF_URL] == "unknown"

    with aioresponses() as mock_aiohttp:
        url = "https://example.com/plain.txt"
        mock_aiohttp.get(
            url, status=200, body="plain", headers={"content-type": "text/plain"}
        )
        errors = {}
        await validate_url(flow, _valid_input(url), errors)
    assert errors[CONF_URL] == "bad_ics"


def test_scalar_and_trim_validation_errors() -> None:
    """Test scalar validation error keys and trim-name base error."""
    user_input = _valid_input()
    user_input[CONF_REFRESH_FREQUENCY] = 1441
    user_input[CONF_CHECKIN] = "bad"
    user_input[CONF_CHECKOUT] = "bad"
    user_input[CONF_DAYS] = 0
    user_input[CONF_MAX_EVENTS] = 0
    user_input[CONF_CODE_LENGTH] = 5
    user_input[CONF_MAX_NAME_LENGTH] = MIN_NAME_LENGTH - 1
    errors: dict[str, str] = {}

    validate_scalar_fields(user_input, errors)
    validate_name_length(user_input, errors)

    assert errors[CONF_REFRESH_FREQUENCY] == "bad_refresh"
    assert errors[CONF_CHECKIN] == "bad_time"
    assert errors[CONF_CHECKOUT] == "bad_time"
    assert errors[CONF_DAYS] == "bad_minimum"
    assert errors[CONF_MAX_EVENTS] == "bad_minimum"
    assert errors[CONF_CODE_LENGTH] == "bad_code_length"
    assert errors[CONF_MAX_NAME_LENGTH] == "bad_max_name_length"

    user_input = _valid_input()
    user_input[CONF_TRIM_NAMES] = True
    user_input[CONF_EVENT_PREFIX] = "LongPrefix"
    user_input[CONF_MAX_NAME_LENGTH] = 8
    errors = {}
    validate_name_trimming(user_input, errors)
    assert errors["base"] == "prefix_too_long_for_trim"


async def test_generator_conversion_before_error_rerender(
    hass: "HomeAssistant",
) -> None:
    """Test code-generator conversion happens before later error render."""
    with aioresponses() as mock_aiohttp:
        url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        user_input = _valid_input(url)
        user_input[CONF_DAYS] = 0
        result = await validate_submitted_data(_OptionsFlow(hass), user_input)

    assert result.errors[CONF_DAYS] == "bad_minimum"
    assert result.user_input[CONF_CODE_GENERATION] == "date_based"


async def test_options_reject_code_length_with_allocations(
    hass: "HomeAssistant",
) -> None:
    """Test options cannot change code length while allocations exist."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**_valid_input(), CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH},
        entry_id="entry-a",
    )
    flow = _OptionsFlow(hass)
    flow.config_entry = entry
    allocator = DoorCodeAllocator(hass)
    allocator._store = type(
        "StoreStub",
        (),
        {"async_save": staticmethod(lambda _registry: None)},
    )()
    hass.data.setdefault(DOMAIN, {})[ALLOCATOR] = allocator
    await allocator.async_allocate(
        AllocationRequest(
            entry_id="entry-a",
            identity_key="identity-a",
            preferred_code="1234",
            code_length=DEFAULT_CODE_LENGTH,
        )
    )

    user_input = _valid_input()
    user_input[CONF_CODE_LENGTH] = DEFAULT_CODE_LENGTH + 2
    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            user_input[CONF_URL],
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        result = await validate_submitted_data(flow, user_input)

    assert result.errors[CONF_CODE_LENGTH] == "active_allocations"


def test_bad_code_length_error_is_not_overwritten(
    hass: "HomeAssistant",
) -> None:
    """Test invalid code length remains the primary validation error."""
    flow = _OptionsFlow(hass)
    flow.config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH},
        entry_id="entry-a",
    )
    allocator = DoorCodeAllocator(hass)
    allocator._registry_lost = True
    hass.data.setdefault(DOMAIN, {})[ALLOCATOR] = allocator
    user_input = _valid_input()
    user_input[CONF_CODE_LENGTH] = DEFAULT_CODE_LENGTH + 1
    errors: dict[str, str] = {}

    validate_scalar_fields(user_input, errors)
    validate_code_length_change(flow, user_input, errors)

    assert errors[CONF_CODE_LENGTH] == "bad_code_length"


def test_registry_loss_blocks_code_length_change(
    hass: "HomeAssistant",
) -> None:
    """Test lost registry state blocks code-length changes."""
    flow = _OptionsFlow(hass)
    flow.config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CODE_LENGTH: DEFAULT_CODE_LENGTH},
        entry_id="entry-a",
    )
    allocator = DoorCodeAllocator(hass)
    allocator._registry_lost = True
    hass.data.setdefault(DOMAIN, {})[ALLOCATOR] = allocator
    user_input = _valid_input()
    user_input[CONF_CODE_LENGTH] = DEFAULT_CODE_LENGTH + 2
    errors: dict[str, str] = {}

    validate_code_length_change(flow, user_input, errors)

    assert errors[CONF_CODE_LENGTH] == "active_allocations"


async def test_successful_lock_and_metadata_conversion(
    hass: "HomeAssistant",
) -> None:
    """Test successful lock conversion, generation flag, and creation metadata."""
    _add_lock_manager(hass)
    with aioresponses() as mock_aiohttp:
        url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        user_input = _valid_input(url)
        user_input[CONF_LOCK_ENTRY] = "Front Door"
        flow = _Flow(hass)
        result = await validate_submitted_data(flow, user_input)

    assert result.errors == {}
    apply_successful_conversions(flow, result.user_input)
    assert result.user_input[CONF_LOCK_ENTRY] == "lock.front_door"
    assert result.user_input[CONF_CREATION_DATETIME] == flow.created
    assert result.user_input[CONF_GENERATE] is True


async def test_start_config_flow_initial_context(
    hass: "HomeAssistant", monkeypatch: Any
) -> None:
    """Test initial render passes grouped ConfigFormContext."""
    seen: dict[str, Any] = {}

    def renderer(flow: Any, context: ConfigFormContext) -> dict[str, Any]:
        """Capture the initial form context."""
        seen["context"] = context
        return {"type": FlowResultType.FORM, "step_id": context.step_id}

    monkeypatch.setattr(
        "custom_components.rental_control.config_flow._show_config_form", renderer
    )
    result = await _start_config_flow(_OptionsFlow(hass), "user", "", None, {}, None)

    assert result["step_id"] == "user"
    assert seen["context"].user_input is None
    assert seen["context"].errors == {}


async def test_start_config_flow_error_context(
    hass: "HomeAssistant", monkeypatch: Any
) -> None:
    """Test error render preserves input, errors, defaults, and entry id."""
    seen: dict[str, Any] = {}

    def renderer(flow: Any, context: ConfigFormContext) -> dict[str, Any]:
        """Capture the error form context."""
        seen["context"] = context
        return {"type": FlowResultType.FORM, "errors": context.errors}

    monkeypatch.setattr(
        "custom_components.rental_control.config_flow._show_config_form", renderer
    )
    with aioresponses() as mock_aiohttp:
        url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        user_input = _valid_input(url)
        user_input[CONF_DAYS] = 0
        result = await _start_config_flow(
            _OptionsFlow(hass), "init", "", user_input, {}, "entry-id"
        )

    assert result["errors"] == {CONF_DAYS: "bad_minimum"}
    assert seen["context"].entry_id == "entry-id"
    assert seen["context"].defaults == {}
    assert seen["context"].user_input[CONF_CODE_GENERATION] == "date_based"


async def test_start_config_flow_create_entry(hass: "HomeAssistant") -> None:
    """Test successful transition returns unchanged create-entry title and data."""
    with aioresponses() as mock_aiohttp:
        url = "https://example.com/calendar.ics"
        mock_aiohttp.get(
            url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        result = await _start_config_flow(
            _Flow(hass), "user", "Test Rental", _valid_input(url), {}, None
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Rental"
    assert result["data"][CONF_LOCK_ENTRY] is None
    assert result["data"][CONF_CREATION_DATETIME] == "2026-06-29T00:00:00+00:00"


async def test_start_config_flow_options_create_entry(hass: "HomeAssistant") -> None:
    """Test options transition saves validated data without creation metadata."""
    with aioresponses() as mock_aiohttp:
        url = "https://example.com/options.ics"
        mock_aiohttp.get(
            url,
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
        )
        result = await _start_config_flow(
            _OptionsFlow(hass), "init", "", _valid_input(url), {}, "entry-id"
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_CREATION_DATETIME not in result["data"]
    assert result["data"][CONF_CODE_GENERATION] == "date_based"


async def test_options_association_waits_for_validation_and_strips_ui_fields(
    hass: "HomeAssistant", monkeypatch: Any
) -> None:
    """Only valid options reach the association commit; selectors are UI-only."""
    from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID

    lock_entry = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        data={"lockname": "lock.front_door"},
        entry_id="km-options-flow",
    )
    lock_entry.add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rental",
        data={CONF_LOCK_ENTRY: "lock.front_door"},
        entry_id="rc-options-flow",
    )
    entry.add_to_hass(hass)
    flow = _OptionsFlow(hass)
    flow.config_entry = entry
    store = _AcknowledgedAssociationStore()
    hass.data.setdefault(DOMAIN, {})[PROTECTED_REGISTRY_STORE] = store

    def render_form(_flow: Any, context: ConfigFormContext) -> dict[str, Any]:
        """Return validation errors without requiring a frontend."""
        return {"type": FlowResultType.FORM, "errors": context.errors}

    monkeypatch.setattr(
        "custom_components.rental_control.config_flow._show_config_form", render_form
    )
    invalid_input = _valid_input()
    invalid_input.update(
        {
            CONF_LOCK_ENTRY: "Front Door",
            CONF_ASSOCIATION_ENTRY_CHOICE: lock_entry.entry_id,
            CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            CONF_DAYS: 0,
        }
    )
    with aioresponses() as mock_aiohttp:
        mock_aiohttp.get(
            "https://example.com/calendar.ics",
            status=200,
            body=calendar_data.AIRBNB_ICS_CALENDAR,
            headers={"content-type": "text/calendar"},
            repeat=True,
        )
        invalid_result = await _start_config_flow(
            flow,
            "init",
            "",
            invalid_input,
            {},
            entry.entry_id,
            validated_input_handler=_handle_association_selection,
        )
        assert invalid_result["type"] == FlowResultType.FORM
        assert not store.envelopes

        valid_input = _valid_input()
        valid_input.update(
            {
                CONF_LOCK_ENTRY: "Front Door",
                CONF_ASSOCIATION_ENTRY_CHOICE: lock_entry.entry_id,
                CONF_ASSOCIATION_ATTEST_SAME_LOCK: False,
            }
        )
        result = await _start_config_flow(
            flow,
            "init",
            "",
            valid_input,
            {},
            entry.entry_id,
            validated_input_handler=_handle_association_selection,
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert len(store.envelopes) == 1
    assert CONF_ASSOCIATION_ENTRY_CHOICE not in result["data"]
    assert CONF_ASSOCIATION_ATTEST_SAME_LOCK not in result["data"]
    assert CONF_VERIFIED_KEYMASTER_ENTRY_ID not in result["data"]
    assert result["data"][CONF_LOCK_ENTRY] == "lock.front_door"


async def test_committed_selector_edit_requires_protected_repair(hass, monkeypatch):
    """A missing backend cannot silently ignore edits to a committed tuple."""
    from custom_components.rental_control.allocator.associations import AssociationMode

    front = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Front Door",
        data={"lockname": "lock.front_door"},
        entry_id="km-front",
    )
    back = MockConfigEntry(
        domain=LOCK_MANAGER,
        title="Back Door",
        data={"lockname": "lock.back_door"},
        entry_id="km-back",
    )
    front.add_to_hass(hass)
    back.add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Rental",
        entry_id="rc-committed-association",
        data={
            CONF_LOCK_ENTRY: "lock.front_door",
            CONF_VERIFIED_KEYMASTER_ENTRY_ID: front.entry_id,
            CONF_ASSOCIATION_GENERATION: 3,
            CONF_ASSOCIATION_TRANSACTION_ID: "c" * 32,
            CONF_ASSOCIATION_MODE: AssociationMode.VERIFIED.value,
        },
    )
    entry.add_to_hass(hass)
    flow = _OptionsFlow(hass)
    flow.config_entry = entry

    async def dispatch_handler(
        target_flow,
        _step_id,
        _title,
        user_input,
        _defaults,
        _entry_id,
        *,
        validated_input_handler,
    ):
        """Exercise the actual options-flow handler selection."""
        assert validated_input_handler is not None
        return await validated_input_handler(target_flow, user_input)

    monkeypatch.setattr(
        "custom_components.rental_control.config_flow._start_config_flow",
        dispatch_handler,
    )

    changed = await RentalControlOptionsFlow.async_step_init(
        flow,
        {
            **_valid_input(),
            CONF_LOCK_ENTRY: "Back Door",
        },
    )
    assert changed == (
        {
            **_valid_input(),
            CONF_LOCK_ENTRY: "Back Door",
        },
        {CONF_LOCK_ENTRY: "association_repair_required"},
    )
    assert entry.data[CONF_LOCK_ENTRY] == "lock.front_door"

    unchanged = await RentalControlOptionsFlow.async_step_init(
        flow,
        {
            **_valid_input(),
            CONF_LOCK_ENTRY: "Front Door",
        },
    )
    assert unchanged[1] == {}
