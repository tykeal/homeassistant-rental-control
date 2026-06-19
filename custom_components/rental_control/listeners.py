# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>

"""Keymaster listener registration for Rental Control."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import CHECKIN_SENSOR
from .const import CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS
from .const import COORDINATOR
from .const import DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS
from .const import DOMAIN
from .const import KEYMASTER_MONITORING_SWITCH
from .const import UNSUB_LISTENERS
from .util import get_entry_data

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _KeymasterEventContext:
    """Context for handling one monitored Keymaster event."""

    hass: HomeAssistant
    config_entry: ConfigEntry
    coordinator: Any
    raw_lockname: Any
    event_lockname: str
    event_data: dict[str, Any]
    code_slot_num: Any
    diagnostics_enabled: bool


@callback
def async_register_keymaster_listener(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> None:
    """Register keymaster event bus listener for unlock detection (T024/T026).

    Listens for ``keymaster_lock_state_changed`` events and forwards
    matching unlock events to the check-in tracking sensor.

    The listener validates:
    - ``lockname`` is in ``coordinator.monitored_locknames`` (parent + children)
    - ``state`` is ``"unlocked"``
    - ``code_slot_num != 0`` (FR-017)
    - ``code_slot_num`` is in ``[start_slot, start_slot + max_events)``

    Args:
        hass: Home Assistant instance.
        config_entry: The integration config entry.
    """
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]
    start_slot = coordinator.start_slot
    max_events = coordinator.max_events

    @callback
    def _handle_event(event: Event) -> None:
        """Handle a keymaster lock-state event."""
        _handle_keymaster_event(
            hass=hass,
            config_entry=config_entry,
            event=event,
            coordinator=coordinator,
            start_slot=start_slot,
            max_events=max_events,
        )

    unsub = hass.bus.async_listen("keymaster_lock_state_changed", _handle_event)
    hass.data[DOMAIN][config_entry.entry_id][UNSUB_LISTENERS].append(unsub)
    _LOGGER.debug(
        "Registered keymaster event bus listener for monitored locknames=%s",
        sorted(coordinator.monitored_locknames),
    )


def _handle_keymaster_event(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    event: Event,
    coordinator: Any,
    start_slot: int,
    max_events: int,
) -> None:
    """Handle a keymaster_lock_state_changed event."""
    event_data = event.data

    raw_lockname, event_lockname = _normalized_event_lockname(event_data)

    # Drop unmonitored-lock events as early as possible: in
    # deployments with many RC integrations, every integration
    # sees every event on the HA bus, and the unmonitored case
    # is by far the hottest path. Recording these events with
    # disposition "rejected_not_monitored" would flood the
    # 10-entry diagnostics ring buffer with noise from other
    # integrations' locks. The "rejected_not_monitored"
    # disposition string is retained as a reserved value for
    # schema stability with downstream consumers but is no
    # longer emitted.
    if event_lockname not in coordinator.monitored_locknames:
        return

    code_slot_num = event_data.get("code_slot_num")
    diagnostics_enabled = config_entry.data.get(
        CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
        DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS,
    )
    context = _KeymasterEventContext(
        hass=hass,
        config_entry=config_entry,
        coordinator=coordinator,
        raw_lockname=raw_lockname,
        event_lockname=event_lockname,
        event_data=event_data,
        code_slot_num=code_slot_num,
        diagnostics_enabled=diagnostics_enabled,
    )

    if _event_should_be_rejected(
        context,
        start_slot,
        max_events,
    ):
        return

    _forward_keymaster_unlock(context)


def _normalized_event_lockname(event_data: dict[str, Any]) -> tuple[Any, str]:
    """Return the raw and slugified Keymaster lock name."""
    raw_lockname = event_data.get("lockname", "")
    event_lockname = (
        slugify(raw_lockname) if raw_lockname and isinstance(raw_lockname, str) else ""
    )
    return raw_lockname, event_lockname


def _event_should_be_rejected(
    context: _KeymasterEventContext,
    start_slot: int,
    max_events: int,
) -> bool:
    """Return whether a monitored Keymaster event should be rejected."""
    disposition: str | None = None
    if context.event_data.get("state") != "unlocked":
        disposition = "rejected_state"
    # FR-017: Ignore code_slot_num == 0
    elif context.code_slot_num is None or context.code_slot_num == 0:
        disposition = "rejected_slot_zero"
    elif not (start_slot <= context.code_slot_num < start_slot + max_events):
        disposition = "rejected_out_of_range"

    if disposition is None:
        return False

    _record_if_enabled(context, disposition)
    return True


def _record_if_enabled(
    context: _KeymasterEventContext,
    disposition: str,
) -> None:
    """Record a Keymaster event disposition when diagnostics are enabled."""
    if context.diagnostics_enabled:
        _record_keymaster_event_disposition(context, disposition)


def _record_keymaster_event_disposition(
    context: _KeymasterEventContext,
    disposition: str,
) -> None:
    """Append a diagnostic entry and refresh the sensor state."""
    context.coordinator.keymaster_event_diagnostics.append(
        {
            "timestamp": dt_util.utcnow().isoformat(),
            "lockname": str(context.raw_lockname),
            "lockname_slug": context.event_lockname,
            "state": context.event_data.get("state"),
            "code_slot_num": context.code_slot_num,
            "disposition": disposition,
        }
    )
    _refresh_checkin_sensor_state(context.hass, context.config_entry)


def _refresh_checkin_sensor_state(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
) -> None:
    """Refresh the check-in sensor after diagnostics change."""
    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.get(config_entry.entry_id, {})
    sensor = entry_data.get(CHECKIN_SENSOR)
    if sensor is not None and sensor.hass is not None:
        sensor.async_write_ha_state()


def _forward_keymaster_unlock(context: _KeymasterEventContext) -> bool:
    """Forward an accepted Keymaster unlock to the check-in sensor."""
    entry_data = _entry_data_for_unlock(context)
    if entry_data is None:
        return False

    checkin_sensor = _checkin_sensor_for_unlock(
        entry_data,
        context,
    )
    if checkin_sensor is None:
        return False

    if not _monitoring_switch_allows_unlock(
        entry_data,
        context,
    ):
        return False

    _LOGGER.debug(
        "Forwarding keymaster unlock (slot=%d, lock=%s) to checkin sensor",
        context.code_slot_num,
        context.raw_lockname,
    )
    _record_if_enabled(context, "accepted")
    checkin_sensor.async_handle_keymaster_unlock(
        code_slot_num=context.code_slot_num,
        lock_name=context.raw_lockname,
    )
    return True


def _entry_data_for_unlock(context: _KeymasterEventContext) -> dict[str, Any] | None:
    """Return entry data for forwarding a Keymaster unlock."""
    entry_data = get_entry_data(context.hass, context.config_entry.entry_id)
    if entry_data is None:
        _LOGGER.debug(
            "Keymaster unlock event received but entry data not available for entry %s",
            context.config_entry.entry_id,
        )
    return entry_data


def _checkin_sensor_for_unlock(
    entry_data: dict[str, Any],
    context: _KeymasterEventContext,
) -> Any | None:
    """Return the check-in sensor for forwarding a Keymaster unlock."""
    checkin_sensor = entry_data.get(CHECKIN_SENSOR)
    if checkin_sensor is not None:
        return checkin_sensor

    _LOGGER.debug(
        "Keymaster unlock event received but checkin sensor "
        "not yet available for entry %s",
        context.config_entry.entry_id,
    )
    _record_if_enabled(context, "rejected_no_checkin_sensor")
    return None


def _monitoring_switch_allows_unlock(
    entry_data: dict[str, Any],
    context: _KeymasterEventContext,
) -> bool:
    """Return whether the monitoring switch allows unlock forwarding."""
    monitoring_switch = entry_data.get(KEYMASTER_MONITORING_SWITCH)
    if monitoring_switch is None:
        _LOGGER.debug(
            "Keymaster unlock event received but monitoring "
            "switch not yet available for entry %s",
            context.config_entry.entry_id,
        )
    elif monitoring_switch.is_on:
        return True
    else:
        _LOGGER.debug(
            "Ignoring keymaster unlock: monitoring switch is off",
        )

    _record_if_enabled(context, "rejected_monitoring_off")
    return False
