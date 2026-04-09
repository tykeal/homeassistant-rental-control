<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# HA Event Bus Contracts — Child Lock Monitoring Update

**Feature Branch**: `006-child-lock-monitoring`
**Date**: 2025-07-18
**Extends**: `004-checkin-tracking/contracts/events.md`

## Event: rental_control_checkin (Updated)

**Fired when**: Sensor transitions from `awaiting_checkin` to `checked_in`

### Event Data Schema

```python
{
    "entity_id": str,      # e.g., "sensor.rental_control_my_calendar_checkin"
    "summary": str,        # Event summary from calendar
    "start": str,          # ISO 8601 datetime string
    "end": str,            # ISO 8601 datetime string
    "guest_name": str,     # Extracted slot name / guest identifier
    "source": str,         # "keymaster" | "automatic"
    "lock_name": str,      # NEW (FR-009): lockname of the triggering lock, or ""
}
```

### New Field: lock_name

| Value | When |
|-------|------|
| `"front_door"` | Keymaster unlock from parent lock named "Front Door" |
| `"side_door"` | Keymaster unlock from child lock named "Side Door" |
| `""` (empty string) | Source is `"automatic"` (no lock involved) |

**Derivation**: The `lock_name` value is the `lockname` field from the
`keymaster_lock_state_changed` event data. This is the slugified name of
the keymaster lock entry (e.g., `slugify("Front Door")` → `"front_door"`).

### Backward Compatibility

- The `lock_name` field is **added** to the existing schema. It is not a
  breaking change — existing automations that consume `rental_control_checkin`
  events will simply see an additional field they can ignore.
- When `source` is `"automatic"`, `lock_name` is `""` (empty string), not
  omitted, to maintain a consistent schema.

### Example: Parent Lock Check-in

```python
hass.bus.async_fire(
    "rental_control_checkin",
    {
        "entity_id": "sensor.rental_control_beach_house_checkin",
        "summary": "Reserved - John Smith",
        "start": "2025-07-20T16:00:00-04:00",
        "end": "2025-07-25T11:00:00-04:00",
        "guest_name": "John Smith",
        "source": "keymaster",
        "lock_name": "front_door",
    },
)
```

### Example: Child Lock Check-in

```python
hass.bus.async_fire(
    "rental_control_checkin",
    {
        "entity_id": "sensor.rental_control_beach_house_checkin",
        "summary": "Reserved - John Smith",
        "start": "2025-07-20T16:00:00-04:00",
        "end": "2025-07-25T11:00:00-04:00",
        "guest_name": "John Smith",
        "source": "keymaster",
        "lock_name": "side_door",
    },
)
```

### Example: Automatic Check-in (No Lock)

```python
hass.bus.async_fire(
    "rental_control_checkin",
    {
        "entity_id": "sensor.rental_control_beach_house_checkin",
        "summary": "Reserved - John Smith",
        "start": "2025-07-20T16:00:00-04:00",
        "end": "2025-07-25T11:00:00-04:00",
        "guest_name": "John Smith",
        "source": "automatic",
        "lock_name": "",
    },
)
```

---

## Event: rental_control_checkout (Unchanged)

No changes to the checkout event schema. The `lock_name` field is not
relevant for checkout events since checkout is triggered by time expiry
or manual service call, not by lock events.

---

## Internal Interface: async_handle_keymaster_unlock (Updated)

**Location**: `RentalControlCheckinSensor.async_handle_keymaster_unlock()`

### Previous Signature

```python
@callback
def async_handle_keymaster_unlock(
    self,
    code_slot_num: int,
) -> None:
```

### New Signature

```python
@callback
def async_handle_keymaster_unlock(
    self,
    code_slot_num: int,
    lock_name: str,
) -> None:
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `code_slot_num` | `int` | The keymaster code slot number used to unlock |
| `lock_name` | `str` | The lockname of the lock that was unlocked (from event data) |

### Behavior

The `lock_name` is stored in `self._checkin_lock_name` and included in:
1. The `rental_control_checkin` event payload (`lock_name` field)
2. The sensor's `extra_state_attributes` (as `lock_name`)
3. The `RentalControlCheckinExtraData` for state restoration

---

## Internal Interface: monitored_locknames (New)

**Location**: `RentalControlCoordinator.monitored_locknames`

### Property Signature

```python
@property
def monitored_locknames(self) -> frozenset[str]:
```

### Return Value

A `frozenset[str]` containing:
- The parent `lockname` (always included when not None)
- All discovered child locknames (zero or more)
- Empty `frozenset()` when `self.lockname` is None

### Example

```python
# Parent "Front Door" with children "Side Door" and "Garage"
coordinator.monitored_locknames
# → frozenset({"front_door", "side_door", "garage"})

# Parent only, no children
coordinator.monitored_locknames
# → frozenset({"front_door"})

# No lock configured
coordinator.monitored_locknames
# → frozenset()
```
