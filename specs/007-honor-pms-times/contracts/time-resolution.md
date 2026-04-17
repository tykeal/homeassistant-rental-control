<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Time Resolution in `_ical_parser`

**Feature**: 007-honor-pms-times
**Date**: 2025-07-22

## Overview

This contract defines the time resolution interface within `coordinator._ical_parser()`. Since
Rental Control is a Home Assistant integration (not a REST/GraphQL API), the "contract" is the
internal interface between the coordinator's event-building logic and the downstream sensor/override
pipeline.

## Interface: Time Resolution Function

### Current Behavior (FR-005 — preserved when `honor_event_times=False`)

```
resolve_times(event, override, defaults) -> (checkin: time, checkout: time)

Priority:
  1. override exists        → override.start_time.time(), override.end_time.time()
  2. event has explicit time → event.DTSTART.dt.time(), event.DTEND.dt.time()
  3. all-day event          → defaults.checkin, defaults.checkout
```

### New Behavior (FR-003, FR-004 — when `honor_event_times=True`)

```
resolve_times(event, override, defaults) -> (checkin: time, checkout: time)

Priority:
  1. event has explicit time → event.DTSTART.dt.time(), event.DTEND.dt.time()
  2. override exists        → override.start_time.time(), override.end_time.time()
  3. all-day event          → defaults.checkin, defaults.checkout
```

### Explicit Time Detection

```python
# An event has explicit times when DTSTART.dt is a datetime (not date)
has_explicit_times: bool = isinstance(event["DTSTART"].dt, datetime)
```

## Interface: Configuration Propagation

### Config Flow → Coordinator

```
config_entry.data["honor_event_times"] : bool
  │
  ├── __init__() → self.honor_event_times = bool(config.get(CONF_HONOR_EVENT_TIMES))
  │
  └── update_config() → self.honor_event_times = bool(config.get(CONF_HONOR_EVENT_TIMES))
```

### Options Flow → Config Entry

```
User toggles "Honor event times" in UI
  │
  └── config_flow._start_config_flow() → user_input["honor_event_times"] = True/False
        │
        └── update_listener() → config_entry.data["honor_event_times"] = value
              │
              └── coordinator.update_config() → self.honor_event_times = value
                    │
                    └── coordinator.async_request_refresh() → _ical_parser uses new value
```

## Interface: Downstream Time Update (unchanged — no modifications)

### Preconditions

- CalendarEvent is built with resolved times from the time resolution function above
- Sensor has an assigned Keymaster slot via `async_reserve_or_get_slot()`

### Contract

```
async_reserve_or_get_slot(slot_name, slot_code, start_time, end_time, uid, prefix)
  → ReserveResult(slot, is_new, times_updated)

Postconditions:
  - If times_updated=True:
    - Override's stored start_time/end_time are updated to match incoming times
    - Caller MUST fire async_fire_update_times() or async_fire_clear_code()
  - If times_updated=False:
    - Override's stored times already matched incoming — no action needed
    - FR-007 satisfied: no unnecessary updates
```

## Migration Contract

### v7 → v8

```
Precondition:  config_entry.version == 7
               config_entry.data does NOT contain "honor_event_times"

Action:        config_entry.data["honor_event_times"] = False

Postcondition: config_entry.version == 8
               config_entry.data["honor_event_times"] == False
               Existing behavior preserved (FR-005)
```

## Test Verification Points

| Contract Point | Test Assertion |
|----------------|----------------|
| honor_event_times=False, override exists | CalendarEvent times == override times |
| honor_event_times=True, timed event, override exists | CalendarEvent times == calendar times (not override) |
| honor_event_times=True, all-day event, override exists | CalendarEvent times == override times |
| honor_event_times=True, all-day event, no override | CalendarEvent times == default times |
| honor_event_times=True, timed event, no override | CalendarEvent times == calendar times |
| Time difference detected by async_reserve_or_get_slot | times_updated=True, async_fire_update_times called |
| No time difference | times_updated=False, no update fired |
| Migration v7→v8 | honor_event_times key exists and equals False |
| Options flow toggle | Setting persists after reload |
