<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Child Lock Monitoring Implementation

**Feature**: 006-child-lock-monitoring
**Date**: 2025-07-18

## Overview

This feature extends rental-control to monitor unlock events from keymaster child locks, not just the parent lock. Three files are modified in production code, plus test additions.

## Implementation Order

```text
1. coordinator.py  — Add child lock discovery + monitored_locknames property
2. __init__.py      — Expand event listener to use monitored_locknames + pass lock_name
3. checkinsensor.py — Accept lock_name parameter + include in event data
4. tests/           — Unit and integration tests for all changes
```

## Change Summary

### 1. Coordinator: Child Lock Discovery

**File**: `custom_components/rental_control/coordinator.py`

**What to add**:
- `_child_locknames: set[str]` field initialized to `set()` in `__init__`
- `_parent_entry_id: str | None` field — the keymaster config entry ID matching `self.lockname`
- `monitored_locknames` property returning `frozenset` of parent + children
- `_discover_child_locks()` method called from `_async_update_data()`

**Key logic** (in `_discover_child_locks`):
```python
def _discover_child_locks(self) -> None:
    if not self.lockname:
        self._child_locknames = set()
        return

    new_children: set[str] = set()
    for entry in self.hass.config_entries.async_entries(LOCK_MANAGER):
        parent_id = entry.data.get("parent_entry_id")
        if parent_id and parent_id == self._parent_entry_id:
            child_name = slugify(entry.data.get("lockname", ""))
            if child_name:
                new_children.add(child_name)

    # Log changes
    added = new_children - self._child_locknames
    removed = self._child_locknames - new_children
    if added:
        _LOGGER.debug("Discovered new child locks: %s", added)
    if removed:
        _LOGGER.debug("Child locks removed: %s", removed)

    self._child_locknames = new_children
```

**Finding `_parent_entry_id`**: In `__init__`, look up which keymaster config entry has `data["lockname"]` matching `self.lockname`:
```python
self._parent_entry_id: str | None = None
if self.lockname:
    for entry in hass.config_entries.async_entries(LOCK_MANAGER):
        if slugify(entry.data.get("lockname", "")) == self.lockname:
            if not entry.data.get("parent_entry_id"):
                self._parent_entry_id = entry.entry_id
                break
```

### 2. Event Bus Listener: Multi-Lockname Matching

**File**: `custom_components/rental_control/__init__.py`
**Function**: `async_register_keymaster_listener`

**What changes**:
- Remove: `lockname = coordinator.lockname` (captured once at registration)
- The inner `_handle_keymaster_event` reads `coordinator.monitored_locknames` on each event (dynamic)
- Change: `event_data.get("lockname") != lockname` → `event_data.get("lockname") not in coordinator.monitored_locknames`
- Add: capture `event_lockname = event_data.get("lockname")` and pass to sensor
- Update: `checkin_sensor.async_handle_keymaster_unlock(code_slot_num=code_slot_num, lock_name=event_lockname)`

### 3. Check-in Sensor: Lock Identity

**File**: `custom_components/rental_control/sensors/checkinsensor.py`

**What changes**:
- `async_handle_keymaster_unlock`: add `lock_name: str` parameter
- Store `self._checkin_lock_name = lock_name` before transition
- `_transition_to_checked_in`: include `"lock_name": self._checkin_lock_name or ""` in event data
- State resets: clear `_checkin_lock_name = None` when entering `no_reservation` or `awaiting_checkin`
- `extra_state_attributes`: include `"lock_name"` when state is `checked_in` and source is `keymaster`
- `RentalControlCheckinExtraData`: add `lock_name` to stored/restored data

### 4. Tests

**Unit tests** (add to existing test files):

In `test_coordinator.py`:
- `test_discover_child_locks_finds_children`
- `test_discover_child_locks_no_children`
- `test_discover_child_locks_dynamic_add_remove`
- `test_monitored_locknames_property`
- `test_monitored_locknames_no_lock_configured`

In `test_init.py`:
- `test_keymaster_listener_accepts_child_lock_event`
- `test_keymaster_listener_rejects_unknown_lock_event`
- `test_keymaster_listener_passes_lock_name_to_sensor`

In `test_checkin_sensor.py`:
- `test_keymaster_unlock_includes_lock_name_in_event`
- `test_keymaster_unlock_lock_name_in_attributes`
- `test_automatic_checkin_lock_name_empty`
- `test_lock_name_cleared_on_state_reset`
- `test_lock_name_restored_from_stored_data`

**Integration tests** (add to `test_checkin_tracking.py`):
- `test_child_lock_unlock_triggers_checkin`
- `test_parent_and_child_simultaneous_unlock_single_checkin`
- `test_dynamic_child_discovery_during_lifecycle`

## Files NOT Changed

| File | Reason |
|------|--------|
| `config_flow.py` | Still configure single parent lock |
| `switch.py` | Monitoring switch already gates all events |
| `util.py` | Slot naming unchanged |
| `event_overrides.py` | Slot management unchanged |
| `sensors/calsensor.py` | Calendar sensor unchanged |
| `const.py` | No new constants needed (LOCK_MANAGER already exists) |

## Prerequisites

- Keymaster integration with parent/child lock support
- Understanding of `hass.config_entries.async_entries()` API
- Familiarity with `@callback` decorator (sync event handlers)

## Verification

```bash
# Run all tests
uv run pytest tests/ -v

# Run only child-lock-related tests
uv run pytest tests/ -v -k "child_lock or child_lockname or monitored_locknames"

# Run pre-commit checks
pre-commit run --all-files
```
