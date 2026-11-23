# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for forced re-issue target resolution."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.rental_control.const import CHECKIN_SENSOR
from custom_components.rental_control.const import COORDINATOR
from custom_components.rental_control.const import DOMAIN
from custom_components.rental_control.coordinator_helpers import reissue
from custom_components.rental_control.reconciliation import make_reservation_fingerprint

_START = datetime(2026, 9, 17, 16, tzinfo=dt_util.UTC)
_END = datetime(2026, 9, 20, 11, tzinfo=dt_util.UTC)


def test_entity_form_resolves_reservation_sensor(hass: HomeAssistant) -> None:
    """Entity targeting resolves to the reservation fingerprint identity."""
    identity = make_reservation_fingerprint("entry-a", "Guest", _START, _END)
    coordinator = _coordinator(entry_id="entry-a", latest={identity: object()})
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: coordinator}}
    hass.states.async_set("sensor.guest", "Guest", _attrs("Guest"))

    target, reason = reissue.resolve_entity_target(hass, "sensor.guest")

    assert reason == ""
    assert target is not None
    assert target.entry_id == "entry-a"
    assert target.identity_key == identity
    assert target.target_key == identity


def test_non_reservation_entity_is_refused(hass: HomeAssistant) -> None:
    """Check-in tracking and unrelated entities are not reservation targets."""
    hass.data[DOMAIN] = {
        "entry-a": {
            COORDINATOR: _coordinator(entry_id="entry-a"),
            CHECKIN_SENSOR: SimpleNamespace(state="checked_in"),
        }
    }
    hass.states.async_set("sensor.check_in", "checked_in", {})

    target, reason = reissue.resolve_entity_target(hass, "sensor.check_in")

    assert target is None
    assert reason == "not_a_reservation_sensor"


def test_slot_inside_one_entry_resolves(hass: HomeAssistant) -> None:
    """Slot targeting resolves when exactly one entry manages the slot."""
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: _coordinator(entry_id="entry-a")}}

    target, reason = reissue.resolve_slot_target(hass, "front", 11)

    assert reason == ""
    assert target is not None
    assert target.entry_id == "entry-a"
    assert target.identity_key is None
    assert target.target_key == "slot:front:11"


def test_slot_outside_every_range_is_refused(hass: HomeAssistant) -> None:
    """A known lock with no entry managing the slot is refused."""
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: _coordinator(entry_id="entry-a")}}

    target, reason = reissue.resolve_slot_target(hass, "front", 20)

    assert target is None
    assert reason == "slot_not_managed"


def test_slot_claimed_by_two_entries_is_ambiguous(hass: HomeAssistant) -> None:
    """Shared lock overlap is refused instead of guessed."""
    hass.data[DOMAIN] = {
        "entry-a": {COORDINATOR: _coordinator(entry_id="entry-a")},
        "entry-b": {COORDINATOR: _coordinator(entry_id="entry-b")},
    }

    target, reason = reissue.resolve_slot_target(hass, "front", 10)

    assert target is None
    assert reason == "ambiguous_lock"


def test_slot_target_attaches_live_reservation(hass: HomeAssistant) -> None:
    """A slot occupied by a live reservation resolves to that identity."""
    latest_plan = SimpleNamespace(selected={"identity-a": 10})
    coordinator = _coordinator(
        entry_id="entry-a",
        latest={"identity-a": object()},
        latest_plan=latest_plan,
        observed={"identity-a": (10, "1234")},
    )
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: coordinator}}

    target, reason = reissue.resolve_slot_target(hass, "front", 10)

    assert reason == ""
    assert target is not None
    assert target.identity_key == "identity-a"
    assert target.observed_code == "1234"


def test_slot_target_ignores_persisted_ghost(hass: HomeAssistant) -> None:
    """A persisted ghost mapping does not turn a bare slot into identity mode."""
    coordinator = _coordinator(
        entry_id="entry-a",
        latest={"identity-a": SimpleNamespace(missing_count=1)},
        latest_plan=SimpleNamespace(selected={"identity-a": 10}),
    )
    coordinator._slot_mappings = {"mappings": {"identity-a": {"slot": 10}}}
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: coordinator}}

    target, reason = reissue.resolve_slot_target(hass, "front", 10)

    assert reason == ""
    assert target is not None
    assert target.identity_key is None
    assert target.target_key == "slot:front:10"


def test_checked_in_flag_matches_target_identity(hass: HomeAssistant) -> None:
    """A future reservation is not checked in just because the entry is."""
    identity = make_reservation_fingerprint("entry-a", "Guest", _START, _END)
    coordinator = _coordinator(entry_id="entry-a", latest={identity: object()})
    hass.data[DOMAIN] = {
        "entry-a": {
            COORDINATOR: coordinator,
            CHECKIN_SENSOR: SimpleNamespace(
                state="checked_in",
                extra_state_attributes={
                    "guest_name": "Other",
                    "start": _START,
                    "end": _END,
                },
            ),
        }
    }
    hass.states.async_set("sensor.guest", "Guest", _attrs("Guest"))

    target, reason = reissue.resolve_entity_target(hass, "sensor.guest")

    assert reason == ""
    assert target is not None
    assert target.checked_in is False


def test_entity_target_rejects_bad_domain_state_attributes_and_owner(hass) -> None:
    """Malformed or unattributable sensor state never becomes a target."""
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: _coordinator(entry_id="entry-a")}}

    assert reissue.resolve_entity_target(hass, "binary_sensor.guest") == (
        None,
        "not_a_reservation_sensor",
    )
    assert reissue.resolve_entity_target(hass, "sensor.missing") == (
        None,
        "not_a_reservation_sensor",
    )

    hass.states.async_set(
        "sensor.bad_dates", "Guest", _attrs("Guest") | {"start": "bad"}
    )
    assert reissue.resolve_entity_target(hass, "sensor.bad_dates") == (
        None,
        "not_a_reservation_sensor",
    )
    hass.states.async_set("sensor.bad_shape", "Guest", {"slot_name": "Guest"})
    assert reissue.resolve_entity_target(hass, "sensor.bad_shape") == (
        None,
        "not_a_reservation_sensor",
    )
    hass.states.async_set("sensor.unknown_owner", "Guest", _attrs("Guest"))
    hass.data[DOMAIN].clear()
    assert reissue.resolve_entity_target(hass, "sensor.unknown_owner") == (
        None,
        "not_a_reservation_sensor",
    )


def test_entity_target_fallback_refuses_ambiguous_reservation_identity(hass) -> None:
    """Entity attributes do not choose between multiple matching entries."""
    identity_a = make_reservation_fingerprint("entry-a", "Guest", _START, _END)
    identity_b = make_reservation_fingerprint("entry-b", "Guest", _START, _END)
    hass.data[DOMAIN] = {
        "entry-a": {
            COORDINATOR: _coordinator(entry_id="entry-a", latest={identity_a: object()})
        },
        "entry-b": {
            COORDINATOR: _coordinator(entry_id="entry-b", latest={identity_b: object()})
        },
    }
    hass.states.async_set(
        "sensor.ambiguous",
        "Guest",
        _attrs("Guest") | {"start": _START.isoformat(), "end": _END.isoformat()},
    )

    target, reason = reissue.resolve_entity_target(hass, "sensor.ambiguous")

    assert target is None
    assert reason == "not_a_reservation_sensor"


def test_slot_target_uses_live_mapping_then_checked_in_name_confirmation(
    hass: HomeAssistant,
) -> None:
    """Only live mappings or an exact checked-in physical name supply identity."""
    coordinator = _coordinator(
        entry_id="entry-a",
        latest={"mapped-identity": object()},
    )
    coordinator._slot_mappings = {"mappings": {"mapped-identity": {"slot": 10}}}
    hass.data[DOMAIN] = {"entry-a": {COORDINATOR: coordinator}}

    mapped, reason = reissue.resolve_slot_target(hass, "front", 10)

    assert reason == ""
    assert mapped is not None
    assert mapped.identity_key == "mapped-identity"

    identity = make_reservation_fingerprint("entry-a", "Guest", _START, _END)
    coordinator._latest_res_by_key.clear()
    coordinator._slot_mappings = {"mappings": {}}
    coordinator.event_prefix = "Cabin"
    coordinator.event_overrides = None
    coordinator._read_slot_snapshot = lambda slot: SimpleNamespace(
        slot=slot,
        name_state="Guest",
        pin_state="1234",
        use_date_range_state="off",
        enabled_state="on",
        start_state=None,
        end_state=None,
    )
    coordinator._physical_slot_name_matches_name = lambda observed, guest, display: (
        observed == guest and display == "Cabin Guest"
    )
    hass.data[DOMAIN]["entry-a"][CHECKIN_SENSOR] = SimpleNamespace(
        state="checked_in",
        extra_state_attributes={
            "guest_name": "Guest",
            "start": _START.isoformat(),
            "end": _END.isoformat(),
        },
    )
    coordinator._observed_slot_codes = {"other": (10, "4321")}

    checked_in, reason = reissue.resolve_slot_target(hass, "front", 10)

    assert reason == ""
    assert checked_in is not None
    assert checked_in.identity_key == identity
    assert checked_in.checked_in
    assert checked_in.observed_code == "4321"

    coordinator._physical_slot_name_matches_name = lambda *_args: False
    refused, reason = reissue.resolve_slot_target(hass, "front", 10)
    assert reason == ""
    assert refused is not None
    assert refused.identity_key is None


def test_slot_target_refuses_unknown_lock_and_filters_malformed_entries(
    hass: HomeAssistant,
) -> None:
    """Malformed domain rows cannot create a lock or slot match."""
    hass.data[DOMAIN] = {
        None: {COORDINATOR: _coordinator(entry_id="entry-a")},
        "malformed": None,
        "entry-a": {COORDINATOR: _coordinator(entry_id="entry-a")},
    }

    target, reason = reissue.resolve_slot_target(hass, "unknown", 10)

    assert target is None
    assert reason == "unknown_lock"


def _attrs(slot_name: str) -> dict[str, object]:
    """Return reservation sensor attributes."""
    return {
        "slot_name": slot_name,
        "slot_number": 10,
        "start": _START,
        "end": _END,
    }


def _coordinator(
    *,
    entry_id: str,
    latest: dict[str, object] | None = None,
    latest_plan: object | None = None,
    observed: dict[str, tuple[int, str]] | None = None,
) -> SimpleNamespace:
    """Return a lightweight coordinator for target tests."""
    return SimpleNamespace(
        _entry_id=entry_id,
        lockname="front",
        start_slot=10,
        max_events=3,
        _latest_res_by_key={} if latest is None else latest,
        _latest_plan=latest_plan,
        _observed_slot_codes={} if observed is None else observed,
        get_slot_assignment=lambda _identity: 10,
    )
