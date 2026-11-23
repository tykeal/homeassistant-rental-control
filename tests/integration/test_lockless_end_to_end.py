# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""End-to-end coverage for generator-only (no-lock) config entries.

A no-lock entry publishes nothing to a lock, so the only thing its user
ever sees is the ``slot_code`` attribute on the event sensors.  Since
v3.8.0 removed the sensor-side fallback, the allocator is the sole
source of that value, which makes this the only level at which an
allocator that withholds a code is visible (issue #849).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry
import homeassistant.util.dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.rental_control.allocator import registry_commit
from custom_components.rental_control.allocator.associations import AssociationMode
from custom_components.rental_control.allocator.associations import AssociationTuple
from custom_components.rental_control.allocator.registry import AllocationRegistry
from custom_components.rental_control.allocator.singleton import get_allocator
from custom_components.rental_control.codegen import generate_date_based_code
from custom_components.rental_control.config_flow_helpers.association import (
    apply_association_selection,
)
from custom_components.rental_control.const import ASSOCIATION_CHOICE_LOCKLESS
from custom_components.rental_control.const import ASSOCIATION_CHOICE_NO_CHANGE
from custom_components.rental_control.const import CHECKIN_SENSOR
from custom_components.rental_control.const import CHECKIN_STATE_CHECKED_OUT
from custom_components.rental_control.const import CONF_ASSOCIATION_ENTRY_CHOICE
from custom_components.rental_control.const import CONF_ASSOCIATION_GENERATION
from custom_components.rental_control.const import CONF_ASSOCIATION_MODE
from custom_components.rental_control.const import CONF_ASSOCIATION_TRANSACTION_ID
from custom_components.rental_control.const import CONF_LOCK_ENTRY
from custom_components.rental_control.const import CONF_VERIFIED_KEYMASTER_ENTRY_ID
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.const import PROTECTED_REGISTRY_STORE

from tests.integration.helpers import FROZEN_START_OF_DAY
from tests.integration.helpers import FROZEN_TIME
from tests.integration.helpers import future_ics

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_URL = "https://example.com/calendar.ics"
_DAYS_AHEAD = 5
_DURATION = 5
_CODE_LENGTH = 4


def _expected_code() -> str:
    """Return the date-based code the booking in ``future_ics`` yields."""
    start = FROZEN_TIME + timedelta(days=_DAYS_AHEAD)
    end = start + timedelta(days=_DURATION)
    return generate_date_based_code(_CODE_LENGTH, start, end)


def _entry(entry_id: str, *, lock_key_present: bool = False) -> MockConfigEntry:
    """Build a date-based config entry with the lock picker left unset.

    The config flow normalizes a cleared lock picker to ``None`` before it
    persists the entry, so the two shapes a no-lock entry can really have
    are an absent key and an explicit ``None``.
    """
    data: dict[str, object] = {
        "name": "Lockless Rental",
        "url": _URL,
        "timezone": "UTC",
        "checkin": "16:00",
        "checkout": "11:00",
        "start_slot": 10,
        "max_events": 2,
        "days": 90,
        "verify_ssl": True,
        "ignore_non_reserved": False,
        "code_generation": "date_based",
        "code_length": _CODE_LENGTH,
    }
    if lock_key_present:
        data[CONF_LOCK_ENTRY] = None
    return MockConfigEntry(
        domain=DOMAIN,
        title="Lockless Rental",
        version=10,
        unique_id=f"lockless-{entry_id}",
        data=data,
        entry_id=entry_id,
    )


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, body: str | None = None
) -> None:
    """Set the entry up against a frozen clock and a stubbed calendar."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    with (
        aioresponses() as mock_session,
        pytest.MonkeyPatch.context() as patcher,
    ):
        patcher.setattr(dt_util, "now", lambda *_args: FROZEN_TIME)
        patcher.setattr(
            dt_util, "start_of_local_day", lambda *_args: FROZEN_START_OF_DAY
        )
        mock_session.get(
            _URL,
            status=200,
            body=(
                body
                if body is not None
                else future_ics(days_ahead=_DAYS_AHEAD, duration=_DURATION)
            ),
            repeat=True,
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _refresh(
    hass: HomeAssistant, entry: MockConfigEntry, body: str | None = None
) -> None:
    """Drive one more coordinator refresh against the same frozen clock."""
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    with (
        aioresponses() as mock_session,
        pytest.MonkeyPatch.context() as patcher,
    ):
        patcher.setattr(dt_util, "now", lambda *_args: FROZEN_TIME)
        patcher.setattr(
            dt_util, "start_of_local_day", lambda *_args: FROZEN_START_OF_DAY
        )
        mock_session.get(
            _URL,
            status=200,
            body=(
                body
                if body is not None
                else future_ics(days_ahead=_DAYS_AHEAD, duration=_DURATION)
            ),
            repeat=True,
        )
        await coordinator.async_refresh()
        await hass.async_block_till_done()


def _event_sensor_code(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """Return the ``slot_code`` published by the entry's event_0 sensor."""
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    sensor = next(
        entity
        for entity in entities
        if entity.domain == "sensor" and entity.entity_id.endswith("event_0")
    )
    state = hass.states.get(sensor.entity_id)
    assert state is not None
    code = state.attributes.get("slot_code")
    return str(code) if code is not None else None


@pytest.mark.parametrize("lock_key_present", [False, True])
async def test_no_lock_entry_publishes_its_date_based_code(
    hass: HomeAssistant,
    lock_key_present: bool,
) -> None:
    """A generator-only entry publishes the code its dates imply."""
    entry = _entry("lockless_entry", lock_key_present=lock_key_present)

    await _setup(hass, entry)

    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator.lockname is None
    assert _event_sensor_code(hass, entry) == _expected_code()


async def test_no_lock_calendar_remains_available_under_protective_unknown(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected-store failure withholds access, not calendar-only tracking."""
    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    Path(hass.config.path(".storage")).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        registry_commit,
        "_atomic_write_acknowledged",
        lambda _path, _data, _maximum: False,
    )
    entry = _entry("lockless_protective_unknown")

    await _setup(hass, entry)

    allocator = get_allocator(hass)
    assert allocator is not None
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert store.authorization_state.value == "protective_unknown"
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator.last_update_success is True
    assert coordinator.lockname is None
    assert coordinator._latest_res_by_key
    assert _event_sensor_code(hass, entry) is None

    await _refresh(hass, entry)

    assert coordinator._latest_res_by_key
    assert _event_sensor_code(hass, entry) is None
    assert coordinator.last_update_success is True


async def test_no_lock_entry_keeps_its_code_across_refreshes(
    hass: HomeAssistant,
) -> None:
    """The published code is stable, so a guest's code does not churn."""
    entry = _entry("lockless_stable")

    await _setup(hass, entry)
    first = _event_sensor_code(hass, entry)
    await _refresh(hass, entry)

    assert first == _expected_code()
    assert _event_sensor_code(hass, entry) == first


async def test_committed_lockless_retained_selector_remains_calendar_only(
    hass: HomeAssistant,
) -> None:
    """A retained selector cannot disable lockless allocation or enable hardware."""
    entry = _entry("committed_lockless")
    await _setup(hass, entry)
    flow = SimpleNamespace(hass=hass, config_entry=entry)
    result = await apply_association_selection(
        flow,
        {
            CONF_ASSOCIATION_ENTRY_CHOICE: ASSOCIATION_CHOICE_LOCKLESS,
            CONF_LOCK_ENTRY: "Front Door",
        },
    )
    assert not result.errors
    association = AssociationTuple.from_config(entry.data)
    assert association is not None
    assert association.mode is AssociationMode.EXPLICIT_LOCKLESS
    assert association.legacy_lock_selection_raw == "Front Door"
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(coordinator, "async_request_refresh", AsyncMock())
        await coordinator.update_config({**entry.data, "refresh_frequency": 2})
    await _refresh(hass, entry)
    assert coordinator.lockname is None
    assert coordinator.event_overrides is None
    assert not coordinator.keymaster_runtime_enabled
    assert _event_sensor_code(hass, entry) == _expected_code()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator.lockname is None
    assert coordinator.event_overrides is None
    assert _event_sensor_code(hass, entry) == _expected_code()
    allocator = get_allocator(hass)
    assert allocator is not None
    before = deepcopy(allocator._registry)
    inconsistent = replace(
        association,
        mode=AssociationMode.VERIFIED,
        verified_keymaster_entry_id="replacement-entry",
    )
    hass.config_entries.async_update_entry(
        entry, data=inconsistent.merge_into_config(entry.data)
    )
    await coordinator._run_lockless_allocation(coordinator.data)
    assert allocator._registry == before


async def test_unresolved_lock_cannot_use_lockless_allocation(
    hass: HomeAssistant,
) -> None:
    """A legacy lock selector cannot issue codes via the lockless path."""
    entry = _entry("unresolved_lockless_cycle")
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    allocator = get_allocator(hass)
    assert allocator is not None
    before_registry = deepcopy(allocator._registry)
    before_reservations = deepcopy(coordinator._latest_res_by_key)
    assert before_reservations
    identity = next(iter(before_reservations))
    assert coordinator.get_slot_code(identity) == _expected_code()

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LOCK_ENTRY: "Front Door"}
    )
    await coordinator._run_lockless_allocation(coordinator.data)
    assert allocator._registry == before_registry
    assert coordinator._latest_res_by_key == before_reservations
    assert coordinator.get_slot_code(identity) is None
    assert coordinator.data


@pytest.mark.parametrize("selector", [123, ["front"], {"lock": "front"}])
async def test_malformed_legacy_selector_blocks_issuance_and_raises_repair(
    hass: HomeAssistant, selector: object
) -> None:
    """Malformed legacy selectors never become lockless, including at startup."""
    entry = _entry("malformed_legacy_selector")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LOCK_ENTRY: selector}
    )
    with patch.object(
        type(hass.services), "async_call", new=AsyncMock()
    ) as service_calls:
        await _setup(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        allocator = get_allocator(hass)
        assert allocator is not None
        store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
        assert store.authorization_state.value == "ready"
        assert coordinator.data
        assert coordinator.last_update_success
        assert not coordinator.keymaster_runtime_enabled
        assert not allocator._registry.records
        assert _event_sensor_code(hass, entry) is None
        issue = async_get_issue_registry(hass).async_get_issue(
            DOMAIN, f"keymaster_association_invalid_{entry.entry_id}"
        )
        assert issue is not None
        assert not issue.is_fixable
        before_registry = deepcopy(allocator._registry)
        before_store = deepcopy(store.loaded_result.data)

        await coordinator._run_lockless_allocation(coordinator.data)
        await _refresh(hass, entry)
        assert allocator._registry == before_registry
        assert store.loaded_result.data == before_store
        assert entry.data[CONF_LOCK_ENTRY] == selector
        assert _event_sensor_code(hass, entry) is None
        service_calls.assert_not_called()


async def test_malformed_legacy_selector_withholds_existing_protected_code(
    hass: HomeAssistant,
) -> None:
    """A malformed selector preserves old ownership but withholds the code."""
    entry = _entry("malformed_legacy_with_owner")
    await _setup(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    allocator = get_allocator(hass)
    assert allocator is not None
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    assert allocator._registry.records
    assert _event_sensor_code(hass, entry) == _expected_code()
    before_registry = deepcopy(allocator._registry)
    before_store = deepcopy(store.loaded_result.data)
    reservations = deepcopy(coordinator._latest_res_by_key)
    identity = next(iter(reservations))
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_LOCK_ENTRY: {"bad": "selector"}}
    )
    with patch.object(
        type(hass.services), "async_call", new=AsyncMock()
    ) as service_calls:
        await coordinator._run_lockless_allocation(coordinator.data)
        await _refresh(hass, entry)
        assert allocator._registry == before_registry
        assert store.loaded_result.data == before_store
        assert coordinator._latest_res_by_key == reservations
        assert coordinator.get_slot_code(identity) is None
        assert _event_sensor_code(hass, entry) is None
        assert coordinator.data
        service_calls.assert_not_called()
    assert entry.data[CONF_LOCK_ENTRY] == {"bad": "selector"}
    assert async_get_issue_registry(hass).async_get_issue(
        DOMAIN, f"keymaster_association_invalid_{entry.entry_id}"
    )


async def test_malformed_legacy_selector_options_cannot_clear_it(
    hass: HomeAssistant,
) -> None:
    """A real options submission cannot turn malformed history into locklessness."""
    entry = _entry("malformed_legacy_options")
    await _setup(hass, entry)
    damaged = {**entry.data, CONF_LOCK_ENTRY: {"bad": "selector"}}
    hass.config_entries.async_update_entry(entry, data=damaged)
    inputs = {
        "name": "Lockless Rental",
        "url": _URL,
        "verify_ssl": True,
        CONF_LOCK_ENTRY: "(none)",
        "refresh_frequency": 30,
        "timezone": "UTC",
        "event_prefix": "",
        "checkin": "16:00",
        "checkout": "11:00",
        "days": 90,
        "max_events": 2,
        "start_slot": 10,
        "code_length": 4,
        "code_generation": "Start/End Date",
        "should_update_code": True,
        CONF_ASSOCIATION_ENTRY_CHOICE: ASSOCIATION_CHOICE_NO_CHANGE,
    }
    with aioresponses() as mock_session:
        mock_session.get(
            _URL,
            status=200,
            body=future_ics(days_ahead=_DAYS_AHEAD, duration=_DURATION),
            headers={"content-type": "text/calendar"},
            repeat=True,
        )
        form = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], user_input=inputs
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_LOCK_ENTRY: "association_invalid_history"}
    assert dict(entry.data) == damaged
    await _refresh(hass, entry)
    assert _event_sensor_code(hass, entry) is None


async def test_options_preserve_invalid_association_without_authorizing_hardware(
    hass: HomeAssistant,
) -> None:
    """Unrelated options remain usable without rewriting malformed evidence."""
    entry = _entry("invalid_association_options")
    await _setup(hass, entry)
    hass.data["keymaster"] = {}
    for entry_id, title, lockname in (
        ("invalid-km-front", "Front Door", "lock.front_door"),
        ("invalid-km-back", "Back Door", "lock.back_door"),
    ):
        MockConfigEntry(
            domain="keymaster",
            title=title,
            entry_id=entry_id,
            data={"lockname": lockname},
        ).add_to_hass(hass)
    partial = {
        **entry.data,
        CONF_LOCK_ENTRY: "lock.front_door",
        CONF_ASSOCIATION_MODE: "verified",
        CONF_VERIFIED_KEYMASTER_ENTRY_ID: "invalid-km-front",
        CONF_ASSOCIATION_TRANSACTION_ID: "a" * 32,
    }
    partial.pop(CONF_ASSOCIATION_GENERATION, None)
    hass.config_entries.async_update_entry(entry, data=partial)
    await hass.async_block_till_done()
    issue = async_get_issue_registry(hass).async_get_issue(
        DOMAIN, f"keymaster_association_invalid_{entry.entry_id}"
    )
    assert issue is not None
    assert not issue.is_fixable
    evidence_keys = (
        CONF_LOCK_ENTRY,
        CONF_ASSOCIATION_MODE,
        CONF_VERIFIED_KEYMASTER_ENTRY_ID,
        CONF_ASSOCIATION_TRANSACTION_ID,
        CONF_ASSOCIATION_GENERATION,
    )
    evidence = {key: entry.data[key] for key in evidence_keys if key in entry.data}
    store = hass.data[DOMAIN][PROTECTED_REGISTRY_STORE]
    before_store = deepcopy(store.loaded_result.data)
    allocator = get_allocator(hass)
    assert allocator is not None
    before_registry = deepcopy(allocator._registry)
    inputs = {
        "name": "Lockless Rental",
        "url": _URL,
        "verify_ssl": True,
        CONF_LOCK_ENTRY: "Front Door",
        "refresh_frequency": 30,
        "timezone": "UTC",
        "event_prefix": "",
        "checkin": "16:00",
        "checkout": "11:00",
        "days": 90,
        "max_events": 2,
        "start_slot": 10,
        "code_length": 4,
        "code_generation": "Start/End Date",
        "should_update_code": True,
        CONF_ASSOCIATION_ENTRY_CHOICE: ASSOCIATION_CHOICE_NO_CHANGE,
    }
    with (
        aioresponses() as mock_session,
        patch.object(
            type(hass.services), "async_call", new=AsyncMock()
        ) as service_calls,
    ):
        mock_session.get(
            _URL,
            status=200,
            body=future_ics(days_ahead=_DAYS_AHEAD, duration=_DURATION),
            headers={"content-type": "text/calendar"},
            repeat=True,
        )
        form = await hass.config_entries.options.async_init(entry.entry_id)
        schema = form["data_schema"]
        assert schema is not None
        choice_key = next(
            key
            for key in schema.schema
            if str(key.schema) == CONF_ASSOCIATION_ENTRY_CHOICE
        )
        assert choice_key.default() == ASSOCIATION_CHOICE_NO_CHANGE
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], user_input=inputs
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.data["refresh_frequency"] == 30
        assert {key: entry.data[key] for key in evidence_keys if key in entry.data} == (
            evidence
        )
        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        assert not coordinator.keymaster_runtime_enabled
        assert coordinator._observed_pin(10) is None
        assert coordinator.data

        for changes, error_field in (
            (
                {CONF_ASSOCIATION_ENTRY_CHOICE: ASSOCIATION_CHOICE_LOCKLESS},
                CONF_ASSOCIATION_ENTRY_CHOICE,
            ),
            ({CONF_LOCK_ENTRY: "Back Door"}, CONF_LOCK_ENTRY),
        ):
            form = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                form["flow_id"], user_input={**inputs, **changes}
            )
            assert result["type"] is FlowResultType.FORM
            assert result["errors"] == {error_field: "association_invalid_history"}

        service_calls.assert_not_awaited()
    assert {key: entry.data[key] for key in evidence_keys if key in entry.data} == (
        evidence
    )
    assert store.loaded_result.data == before_store
    assert allocator._registry == before_registry


async def test_verified_without_selector_preserves_calendar_and_opens_repair(
    hass: HomeAssistant,
) -> None:
    """A contradictory saved tuple does not become an active lockless issuer."""
    entry = _entry("verified_missing_selector")
    association = AssociationTuple(
        legacy_lock_selection_present=True,
        legacy_lock_selection_raw=None,
        verified_keymaster_entry_id="missing-keymaster-entry",
        association_generation=1,
        association_transaction_id="a" * 32,
        mode=AssociationMode.VERIFIED,
    )
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data=association.merge_into_config(entry.data)
    )
    await _setup(hass, entry)

    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    issue = async_get_issue_registry(hass).async_get_issue(
        DOMAIN, f"keymaster_selector_repair_{entry.entry_id}"
    )
    assert issue is not None
    assert coordinator.last_update_success
    assert coordinator.data
    assert not coordinator.keymaster_runtime_enabled
    assert not coordinator.monitored_locknames
    assert _event_sensor_code(hass, entry) is None


async def test_no_lock_entry_withholds_code_when_protected_snapshot_is_lost(
    hass: HomeAssistant,
) -> None:
    """A missing in-memory baseline cannot be used to issue a protected code.

    This simulates lost in-memory state while the durable snapshot still
    exists. The lockless calendar remains trackable, but the mismatch prevents
    a new protected allocation from being acknowledged.
    """
    entry = _entry("lockless_recovery")

    await _setup(hass, entry)
    assert _event_sensor_code(hass, entry) == _expected_code()

    allocator = get_allocator(hass)
    assert allocator is not None
    allocator._registry = AllocationRegistry(code_ref_salt="recovery-salt")
    allocator._registry_lost = True
    allocator._registry_missing = True

    await _refresh(hass, entry)

    assert _event_sensor_code(hass, entry) is None
    assert allocator._registry_lost is True
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    assert coordinator._latest_res_by_key
    assert coordinator.last_update_success is True


async def test_no_lock_entry_keeps_a_checked_out_code_through_checkout_day(
    hass: HomeAssistant,
) -> None:
    """A checked-out stay keeps its code while its event is still listed.

    The stay ended at 11:00 and the clock reads 12:00 on the same day,
    so the event is retained until local midnight. A no-lock entry has
    no slot to release at checkout, so the code must stay published
    for as long as the event does (issue #847). Once the booking has
    left the feed the entry stops tracking it as usual.
    """
    entry = _entry("lockless_checked_out")
    departing = future_ics(days_ahead=-3, duration=3)

    await _setup(hass, entry, departing)
    code = _event_sensor_code(hass, entry)
    assert code is not None

    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    (reservation,) = coordinator._latest_res_by_key.values()
    hass.data[DOMAIN][entry.entry_id][CHECKIN_SENSOR] = SimpleNamespace(
        state=CHECKIN_STATE_CHECKED_OUT,
        extra_state_attributes={
            "guest_name": reservation.slot_name,
            "start": reservation.start,
            "end": reservation.end,
            "summary": reservation.summary,
        },
    )

    await _refresh(hass, entry, departing)

    assert reservation.identity_key in coordinator._latest_res_by_key
    assert coordinator._latest_res_by_key[reservation.identity_key].checked_out
    assert _event_sensor_code(hass, entry) == code

    arriving = future_ics(summary="Reserved: Next Guest")
    for _ in range(3):
        await _refresh(hass, entry, arriving)

    assert reservation.identity_key not in coordinator._latest_res_by_key
    assert reservation.identity_key not in coordinator._slot_mappings["mappings"]
