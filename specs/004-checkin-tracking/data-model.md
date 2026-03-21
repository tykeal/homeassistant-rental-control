<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Guest Check-in/Check-out Tracking

**Feature Branch**: `004-checkin-tracking`
**Date**: 2025-07-15

## Entities

### CheckinTrackingSensor

**Type**: `SensorEntity` + `CoordinatorEntity` + `RestoreEntity`
**Domain**: `sensor`
**One per**: Integration instance (config entry)

| Field | Type | Description |
|-------|------|-------------|
| `_attr_native_value` | `str` | Current state: `no_reservation`, `awaiting_checkin`, `checked_in`, `checked_out` |
| `_tracked_event_summary` | `str \| None` | Summary of the currently tracked event |
| `_tracked_event_start` | `datetime \| None` | Start time of tracked event |
| `_tracked_event_end` | `datetime \| None` | Original end time of tracked event (frozen at association) |
| `_tracked_event_slot_name` | `str \| None` | Guest name/reservation ID from slot extraction |
| `_checkin_source` | `str \| None` | How check-in occurred: `keymaster`, `automatic`, or `None` |
| `_checkout_source` | `str \| None` | How check-out occurred: `manual`, `automatic`, or `None` |
| `_checkout_time` | `datetime \| None` | Actual time the checkout transition occurred |
| `_transition_target_time` | `datetime \| None` | Scheduled time for next state transition |
| `_checked_out_event_key` | `str \| None` | Identity key of the event we checked out from (for FR-007) |

**State Attributes** (exposed via `extra_state_attributes`):

| Attribute | Type | Description |
|-----------|------|-------------|
| `state` | `str` | Redundant with native_value for automation access |
| `summary` | `str \| None` | Tracked event summary |
| `start` | `str \| None` | Event start time (ISO 8601) |
| `end` | `str \| None` | Event end time (ISO 8601) |
| `guest_name` | `str \| None` | Extracted guest name / slot name |
| `checkin_source` | `str \| None` | Source of last check-in |
| `checkout_source` | `str \| None` | Source of last check-out |
| `checkout_time` | `str \| None` | When checkout occurred (ISO 8601) |
| `next_transition` | `str \| None` | When next state transition is scheduled (ISO 8601) |

**Unique ID**: `gen_uuid(f"{coordinator.unique_id} checkin_tracking")`
**Entity ID**: `sensor.rental_control_{calendar_name}_checkin`
**Device**: Linked to existing integration device

**Restore State Data** (persisted via `RestoreEntity`):

| Field | Type | Description |
|-------|------|-------------|
| `state` | `str` | Last known state |
| `tracked_event_summary` | `str \| None` | Event being tracked |
| `tracked_event_start` | `str \| None` | ISO 8601 start time |
| `tracked_event_end` | `str \| None` | ISO 8601 original end time |
| `tracked_event_slot_name` | `str \| None` | Guest name |
| `checkin_source` | `str \| None` | Last checkin source |
| `checkout_source` | `str \| None` | Last checkout source |
| `checkout_time` | `str \| None` | ISO 8601 checkout time |
| `transition_target_time` | `str \| None` | ISO 8601 next transition |
| `checked_out_event_key` | `str \| None` | Event identity key |

---

### KeymasterMonitoringSwitch

**Type**: `SwitchEntity` + `RestoreEntity`
**Domain**: `switch`
**One per**: Integration instance (only when keymaster configured)
**Condition**: `coordinator.lockname` is not empty

| Field | Type | Description |
|-------|------|-------------|
| `_attr_is_on` | `bool` | Whether keymaster unlock monitoring is active |

**Unique ID**: `gen_uuid(f"{coordinator.unique_id} keymaster_monitoring")`
**Entity ID**: `switch.rental_control_{calendar_name}_keymaster_monitoring`
**Device**: Linked to existing integration device
**Default**: `False` (off)

---

### EarlyCheckoutExpirySwitch

**Type**: `SwitchEntity` + `RestoreEntity`
**Domain**: `switch`
**One per**: Integration instance (only when keymaster configured)
**Condition**: `coordinator.lockname` is not empty

| Field | Type | Description |
|-------|------|-------------|
| `_attr_is_on` | `bool` | Whether early checkout triggers lock code expiry |

**Unique ID**: `gen_uuid(f"{coordinator.unique_id} early_checkout_expiry")`
**Entity ID**: `switch.rental_control_{calendar_name}_early_checkout_expiry`
**Device**: Linked to existing integration device
**Default**: `False` (off)

---

## State Machine

### States

| State | Value | Description |
|-------|-------|-------------|
| No Reservation | `no_reservation` | No relevant event; property idle |
| Awaiting Check-in | `awaiting_checkin` | Event identified; waiting for guest |
| Checked In | `checked_in` | Guest has arrived |
| Checked Out | `checked_out` | Guest has departed; linger period active |

### Transitions

```text
┌──────────────────┐
│  no_reservation   │
└────────┬─────────┘
         │ Event identified by coordinator
         ▼
┌──────────────────┐
│ awaiting_checkin  │◄────────────────────────────────┐
└────────┬─────────┘                                  │
         │ Time-based (event start)                   │
         │ OR keymaster unlock detected               │
         ▼                                            │
┌──────────────────┐                                  │
│   checked_in     │                                  │
└────────┬─────────┘                                  │
         │ Time-based (event end)                     │
         │ OR manual checkout action                  │
         ▼                                            │
┌──────────────────┐                                  │
│  checked_out     │──────────────────────────────────┘
└────────┬─────────┘  (same-day: half-gap → awaiting)
         │
         │ (no follow-on: cleaning window → no_reservation)
         │ (different-day: midnight → no_reservation)
         ▼
┌──────────────────┐
│  no_reservation   │
└──────────────────┘
```

### Transition Rules

| From | To | Trigger | Condition |
|------|----|---------|-----------|
| `no_reservation` | `awaiting_checkin` | Coordinator update | Relevant event found |
| `awaiting_checkin` | `checked_in` | Timer (event start) | Keymaster monitoring disabled |
| `awaiting_checkin` | `checked_in` | Keymaster unlock event | Monitoring enabled, matching slot, `code_slot_num != 0` |
| `checked_in` | `checked_out` | Timer (event end) | Automatic transition |
| `checked_in` | `checked_out` | Manual checkout action | Guards pass: `checked_in`, same day, before end |
| `checked_out` | `awaiting_checkin` | Timer (half-gap) | Same-day turnover (FR-006a) |
| `checked_out` | `no_reservation` | Timer (cleaning window) | No follow-on reservation (FR-006b) |
| `checked_out` | `no_reservation` | Timer (midnight boundary) | Next reservation on different day (FR-006c) |
| `no_reservation` | `awaiting_checkin` | Timer (00:00 event start day) | After FR-006c midnight transition |

---

## Event Identity

Events are identified by a composite key for FR-007 tracking:

```python
def _event_key(
    summary: str, start: datetime, end: datetime
) -> str:
    """Generate a unique identity key for an event.

    The original end time is included so that extensions
    (same summary + start but later end) are detected as
    the same event rather than a new one.
    """
    return (
        f"{summary}|{start.isoformat()}"
        f"|{end.isoformat()}"
    )
```

The `end` parameter captures the **original** end time at first
association. This key is stored when the sensor first associates
with an event and is used to:
- Detect the same event with a modified end time (key matches → no re-transition)
- Detect a genuinely new event (key differs → allow new cycle)

---

## Configuration

### New Config Options

| Key | Type | Default | Description | Flow |
|-----|------|---------|-------------|------|
| `CONF_CLEANING_WINDOW` | `float` | `6.0` | Hours to linger in checked_out when no follow-on (FR-008) | Options |

**Note**: The cleaning window is the only new configuration option. The keymaster
monitoring toggle (FR-013) and early checkout expiry toggle (FR-021) are runtime
switch entities, not config flow options.

### Existing Config Used

| Key | Current Use | New Use |
|-----|-------------|---------|
| `CONF_LOCK_ENTRY` | Keymaster lock entity | Determines if toggle entities are created |
| `CONF_START_SLOT` | First keymaster slot | Defines managed slot range for unlock detection |
| `CONF_MAX_EVENTS` | Number of event sensors | Defines managed slot range upper bound |
| `CONF_CHECKIN` | Default check-in time | Used for time-based auto check-in |
| `CONF_CHECKOUT` | Default check-out time | Used for automatic checkout timing |

---

## HA Event Bus Events

### rental_control_checkin

Fired when sensor transitions to `checked_in`.

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | `str` | Sensor entity ID |
| `summary` | `str` | Event summary |
| `start` | `str` | Event start (ISO 8601) |
| `end` | `str` | Event end (ISO 8601) |
| `guest_name` | `str` | Extracted guest name |
| `source` | `str` | `"keymaster"` or `"automatic"` |

### rental_control_checkout

Fired when sensor transitions to `checked_out`.

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | `str` | Sensor entity ID |
| `summary` | `str` | Event summary |
| `start` | `str` | Event start (ISO 8601) |
| `end` | `str` | Event end (ISO 8601) |
| `guest_name` | `str` | Extracted guest name |
| `source` | `str` | `"manual"` or `"automatic"` |
