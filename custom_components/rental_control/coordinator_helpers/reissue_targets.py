# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Target resolution helpers for forced door-code re-issue."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import cast

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import CHECKIN_SENSOR
from ..const import CHECKIN_STATE_CHECKED_IN
from ..const import COORDINATOR
from ..const import DOMAIN
from ..reconciliation import make_reservation_fingerprint
from . import keymaster_observation
from .reissue import ReissueTarget
from .reissue import target_key


def resolve_entity_target(
    hass: Any, entity_id: str
) -> tuple[ReissueTarget | None, str]:
    """Resolve a reservation sensor entity into a re-issue target."""
    if entity_id.split(".", 1)[0] != SENSOR_DOMAIN:
        return None, "not_a_reservation_sensor"
    state = hass.states.get(entity_id)
    if state is None:
        return None, "not_a_reservation_sensor"
    registry_entry = er.async_get(hass).async_get(entity_id)
    entry_id = getattr(registry_entry, "config_entry_id", None)
    if entry_id is None:
        entry_id = _entry_from_sensor_state(hass, state.attributes)
    if not isinstance(entry_id, str):
        return None, "not_a_reservation_sensor"
    coordinator = _coordinator_for_entry(hass, entry_id)
    if coordinator is None:
        return None, "not_a_reservation_sensor"
    slot_name = state.attributes.get("slot_name")
    start = _coerce_datetime(state.attributes.get("start"))
    end = _coerce_datetime(state.attributes.get("end"))
    if not isinstance(slot_name, str) or start is None or end is None:
        return None, "not_a_reservation_sensor"
    identity_key = make_reservation_fingerprint(entry_id, slot_name, start, end)
    slot = state.attributes.get("slot_number")
    if not isinstance(slot, int) or isinstance(slot, bool):
        slot = coordinator.get_slot_assignment(identity_key)
    observed_code = (
        coordinator._observed_slot_codes.get(identity_key, (None, None))[1]
        if hasattr(coordinator, "_observed_slot_codes")
        else None
    )
    return (
        ReissueTarget(
            entry_id,
            identity_key,
            coordinator.lockname,
            slot,
            target_key(identity_key, coordinator.lockname, slot),
            _is_checked_in(hass, entry_id, identity_key),
            observed_code,
        ),
        "",
    )


def resolve_slot_target(
    hass: Any, lockname: str, slot: int
) -> tuple[ReissueTarget | None, str]:
    """Resolve an explicit lock and slot into a re-issue target."""
    matches: list[tuple[str, Any]] = []
    lock_matches: list[tuple[str, Any]] = []
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_id, str) or not isinstance(entry_data, dict):
            continue
        coordinator = entry_data.get(COORDINATOR)
        if coordinator is None or getattr(coordinator, "lockname", None) != lockname:
            continue
        lock_matches.append((entry_id, coordinator))
        managed = range(
            coordinator.start_slot, coordinator.start_slot + coordinator.max_events
        )
        if slot in managed:
            matches.append((entry_id, coordinator))
    if not lock_matches:
        return None, "unknown_lock"
    if not matches:
        return None, "slot_not_managed"
    if len(matches) > 1:
        return None, "ambiguous_lock"
    entry_id, coordinator = matches[0]
    identity_key = _identity_for_slot(hass, entry_id, coordinator, slot)
    observed_code = _observed_code_for_slot(coordinator, slot)
    return (
        ReissueTarget(
            entry_id,
            identity_key,
            lockname,
            slot,
            target_key(identity_key, lockname, slot),
            _is_checked_in(hass, entry_id, identity_key)
            if identity_key is not None
            else False,
            observed_code,
        ),
        "",
    )


def _coordinator_for_entry(hass: Any, entry_id: str | None) -> Any | None:
    """Return the loaded coordinator for a config entry id."""
    if entry_id is None:
        return None
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, dict):
        return None
    return entry_data.get(COORDINATOR)


def _entry_from_sensor_state(hass: Any, attributes: dict[str, Any]) -> str | None:
    """Best-effort entry lookup for unit tests without entity registry rows."""
    slot_name = attributes.get("slot_name")
    start = _coerce_datetime(attributes.get("start"))
    end = _coerce_datetime(attributes.get("end"))
    if not isinstance(slot_name, str) or start is None or end is None:
        return None
    matches: list[str] = []
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_id, str) or not isinstance(entry_data, dict):
            continue
        coordinator = entry_data.get(COORDINATOR)
        if coordinator is None:
            continue
        identity = make_reservation_fingerprint(entry_id, slot_name, start, end)
        if identity in getattr(coordinator, "_latest_res_by_key", {}):
            matches.append(entry_id)
    return matches[0] if len(matches) == 1 else None


def _coerce_datetime(value: Any) -> datetime | None:
    """Return a datetime from a sensor attribute value."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return cast(datetime | None, dt_util.parse_datetime(value))
    return None


def _is_checked_in(hass: Any, entry_id: str, identity_key: str) -> bool:
    """Return whether the check-in sensor tracks this reservation identity."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return False
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, dict):
        return False
    sensor = entry_data.get(CHECKIN_SENSOR)
    if getattr(sensor, "state", None) != CHECKIN_STATE_CHECKED_IN:
        return False
    attrs = getattr(sensor, "extra_state_attributes", {})
    guest = attrs.get("guest_name") or attrs.get("slot_name")
    start = _coerce_datetime(attrs.get("start"))
    end = _coerce_datetime(attrs.get("end"))
    if not isinstance(guest, str) or start is None or end is None:
        return False
    return make_reservation_fingerprint(entry_id, guest, start, end) == identity_key


def _identity_for_slot(
    hass: Any, entry_id: str, coordinator: Any, slot: int
) -> str | None:
    """Return the live reservation identity currently selected for a slot."""
    latest = getattr(coordinator, "_latest_plan", None)
    selected = getattr(latest, "selected", {}) if latest is not None else {}
    latest_reservations = getattr(coordinator, "_latest_res_by_key", {})
    for identity_key, selected_slot in selected.items():
        if selected_slot == slot and _is_live_reservation(
            latest_reservations.get(identity_key)
        ):
            return str(identity_key)
    mapped = _mapped_identity_for_slot(coordinator, slot)
    if mapped is not None:
        return mapped
    return _checked_in_identity_for_slot(hass, entry_id, coordinator, slot)


def _mapped_identity_for_slot(coordinator: Any, slot: int) -> str | None:
    """Return a live reservation identity from persisted slot mappings."""
    slot_mappings = getattr(coordinator, "_slot_mappings", {})
    mappings = (
        slot_mappings.get("mappings", {}) if isinstance(slot_mappings, dict) else {}
    )
    latest_reservations = getattr(coordinator, "_latest_res_by_key", {})
    if not isinstance(mappings, dict) or not isinstance(latest_reservations, dict):
        return None
    for identity_key, mapping in mappings.items():
        if (
            isinstance(identity_key, str)
            and isinstance(mapping, dict)
            and mapping.get("slot") == slot
            and _is_live_reservation(latest_reservations.get(identity_key))
        ):
            return identity_key
    return None


def _is_live_reservation(reservation: Any) -> bool:
    """Return whether a cached reservation came from the live feed."""
    return reservation is not None and getattr(reservation, "missing_count", 0) == 0


def _checked_in_identity_for_slot(
    hass: Any, entry_id: str, coordinator: Any, slot: int
) -> str | None:
    """Return the checked-in identity when the physical slot matches it."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return None
    entry_data = domain_data.get(entry_id)
    if not isinstance(entry_data, dict):
        return None
    sensor = entry_data.get(CHECKIN_SENSOR)
    if getattr(sensor, "state", None) != CHECKIN_STATE_CHECKED_IN:
        return None
    attrs = getattr(sensor, "extra_state_attributes", {})
    guest = attrs.get("guest_name") or attrs.get("slot_name")
    start = _coerce_datetime(attrs.get("start"))
    end = _coerce_datetime(attrs.get("end"))
    if not isinstance(guest, str) or start is None or end is None:
        return None
    if getattr(coordinator, "_physical_slot_name_matches_name", None) is not None:
        prefix = f"{coordinator.event_prefix} " if coordinator.event_prefix else ""
        display = f"{prefix}{guest}"
        observed_name = _observed_name_for_slot(coordinator, slot)
        if not coordinator._physical_slot_name_matches_name(
            observed_name, guest, display
        ):
            return None
    return make_reservation_fingerprint(entry_id, guest, start, end)


def _observed_name_for_slot(coordinator: Any, slot: int) -> str | None:
    """Return the current physical slot name when it is readable."""
    last_error = None
    if coordinator.event_overrides is not None:
        last_error = coordinator.event_overrides.get_last_slot_error(slot)
    managed_slot, _actual_state = keymaster_observation.classify_slot(
        coordinator._read_slot_snapshot(slot), last_error
    )
    return managed_slot.actual_name


def _observed_code_for_slot(coordinator: Any, slot: int) -> str | None:
    """Return the last observed code for a selected physical slot."""
    for observed_slot, observed_code in getattr(
        coordinator, "_observed_slot_codes", {}
    ).values():
        if observed_slot == slot:
            return str(observed_code)
    return None
