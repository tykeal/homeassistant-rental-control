<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Child Lock Monitoring

**Feature**: 006-child-lock-monitoring
**Date**: 2025-07-18

## Entities

### Existing Entity: RentalControlCoordinator (Modified)

**File**: `custom_components/rental_control/coordinator.py`

#### New Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `_child_locknames` | `set[str]` | `set()` | Mutable set of discovered child locknames, updated on each refresh |

#### New Properties

| Property | Return Type | Description |
|----------|-------------|-------------|
| `monitored_locknames` | `frozenset[str]` | Immutable set of all locknames to monitor (parent + children). Returns `frozenset()` if `self.lockname` is None. |

#### New Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `_discover_child_locks` | `(self) -> None` | Iterates `hass.config_entries.async_entries("keymaster")`, finds entries whose `data.get("parent_entry_id")` matches the parent lock's entry_id, extracts their `lockname` via `slugify(entry.data["lockname"])`, updates `_child_locknames`. Logs additions/removals at DEBUG level. |

#### Modified Methods

| Method | Change |
|--------|--------|
| `_async_update_data` | Calls `self._discover_child_locks()` at the start of each refresh cycle, before calendar fetch. |

#### Relationships

```text
RentalControlCoordinator
  ├── lockname (str|None) ──── parent keymaster lock
  ├── _child_locknames (set[str]) ──── discovered child locks
  └── monitored_locknames (frozenset[str]) ──── parent + children (computed)
```

#### State Transitions

The `_child_locknames` set is updated on each coordinator refresh:

```text
[Coordinator Refresh Starts]
    │
    ▼
_discover_child_locks()
    │
    ├── Iterate hass.config_entries.async_entries("keymaster")
    │   │
    │   ├── entry.data.get("parent_entry_id") == parent_entry_id?
    │   │   ├── YES → add slugify(entry.data["lockname"]) to new_children set
    │   │   └── NO  → skip
    │   │
    │   └── (repeat for all keymaster entries)
    │
    ├── Log any additions: new_children - _child_locknames
    ├── Log any removals: _child_locknames - new_children
    └── self._child_locknames = new_children
```

---

### Existing Entity: Event Bus Listener (Modified)

**File**: `custom_components/rental_control/__init__.py`
**Function**: `async_register_keymaster_listener` → inner `_handle_keymaster_event`

#### Modified Behavior

| Aspect | Before | After |
|--------|--------|-------|
| Lockname capture | `lockname = coordinator.lockname` (single string) | `monitored = coordinator.monitored_locknames` (frozenset) |
| Lockname match | `event_data.get("lockname") != lockname` | `event_data.get("lockname") not in monitored` |
| Lock identity | Not captured | `event_lockname = event_data.get("lockname")` passed to sensor |
| Sensor call | `checkin_sensor.async_handle_keymaster_unlock(code_slot_num=...)` | `checkin_sensor.async_handle_keymaster_unlock(code_slot_num=..., lock_name=event_lockname)` |

---

### Existing Entity: RentalControlCheckinSensor (Modified)

**File**: `custom_components/rental_control/sensors/checkinsensor.py`

#### New Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `_checkin_lock_name` | `str \| None` | `None` | The lockname that triggered the most recent check-in |

#### Modified Methods

| Method | Change |
|--------|--------|
| `async_handle_keymaster_unlock` | Add `lock_name: str` parameter. Store in `self._checkin_lock_name` before calling `_transition_to_checked_in`. |
| `_transition_to_checked_in` | Add `lock_name` to the `rental_control_checkin` event payload. |
| `extra_state_attributes` (or equivalent) | Include `lock_name` in returned attributes when source is `keymaster`. |
| State reset methods | Clear `_checkin_lock_name` when transitioning to `no_reservation` or `awaiting_checkin`. |

#### Modified Stored/Restored Data

| Field | Added To | Description |
|-------|----------|-------------|
| `_checkin_lock_name` | `RentalControlCheckinExtraData` | Persisted across HA restarts via `RestoreEntity` |

---

## Validation Rules

1. **Lockname validation**: Child locknames are derived via `slugify(entry.data["lockname"])`, matching how the parent lockname is derived in the coordinator constructor.
2. **Parent entry_id matching**: The parent lock's config entry `entry_id` is obtained from the rental-control config entry's `CONF_LOCK_ENTRY` data. Only keymaster entries whose `data.get("parent_entry_id")` exactly matches this ID are considered children.
3. **No child lock entity tracking**: Per FR-010, entity state tracking (switches, text inputs, datetime, buttons) remains scoped to parent lock entities only. The `async_start_listener` function is NOT modified.

## No New Entities

This feature does not create any new entities, config entries, or platform entries. All changes are modifications to existing internal data structures and method signatures.
