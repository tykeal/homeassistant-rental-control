<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Honor PMS Calendar Event Times

**Feature**: 007-honor-pms-times
**Date**: 2025-07-22

## Entities

### Configuration Option: `honor_event_times`

| Property | Value |
|----------|-------|
| **Config key** | `honor_event_times` |
| **Type** | `bool` |
| **Default** | `False` |
| **Storage** | `config_entry.data` (via HA `.storage/` JSON) |
| **Persisted** | Yes — survives reloads and restarts |
| **Migration** | v7 → v8: set `False` for existing entries |

**Relationships**:
- Read by `RentalControlCoordinator.__init__()` → stored as `self.honor_event_times`
- Read by `RentalControlCoordinator.update_config()` → updated on options flow save
- Consumed by `RentalControlCoordinator._ical_parser()` → controls time resolution
- Displayed in `_get_schema()` → options flow toggle (both config and options steps)

### Existing Entity: `EventOverride` (unchanged)

```python
class EventOverride(TypedDict):
    slot_name: str       # Guest/reservation name
    slot_code: str       # Lock code for this slot
    start_time: datetime  # Check-in time (UTC)
    end_time: datetime    # Check-out time (UTC)
```

| Property | Impact of This Feature |
|----------|-----------------------|
| `start_time` | When `honor_event_times=True` and event has explicit times, override times are **superseded** during CalendarEvent construction. The override's stored times are then **updated** by `async_reserve_or_get_slot()` when it detects the difference. |
| `end_time` | Same as `start_time`. |
| `slot_name` | Unchanged — still used for slot lookup. |
| `slot_code` | Unchanged — lock code management is orthogonal. |

### Existing Entity: `RentalControlCoordinator` (modified)

New attribute added:

| Attribute | Type | Source | Default |
|-----------|------|--------|---------|
| `honor_event_times` | `bool` | `config_entry.data[CONF_HONOR_EVENT_TIMES]` | `False` |

### Existing Entity: `RentalControlFlowHandler` (modified)

| Property | Change |
|----------|--------|
| `VERSION` | `7` → `8` |
| `DEFAULTS` | Add `CONF_HONOR_EVENT_TIMES: DEFAULT_HONOR_EVENT_TIMES` |

## State Transitions

### Time Resolution Priority (per event, during `_ical_parser`)

```
┌─────────────────────────────┐
│ For each VEVENT in calendar │
└─────────────┬───────────────┘
              │
              ▼
     ┌────────────────┐
     │ Has override?   │──No──┐
     │ (slot assigned) │      │
     └───────┬────────┘      │
             │Yes             │
             ▼                │
  ┌──────────────────────┐    │
  │ honor_event_times    │    │
  │ enabled?             │    │
  └───┬────────────┬─────┘    │
      │Yes         │No        │
      ▼            ▼          │
┌──────────┐  ┌──────────┐   │
│ Has      │  │ Use      │   │
│ explicit │  │ override │   │
│ times?   │  │ times    │   │
└──┬────┬──┘  └──────────┘   │
   │Yes │No                   │
   ▼    ▼                     ▼
┌──────┐ ┌──────────┐  ┌──────────────┐
│ Use  │ │ Use      │  │ Has explicit │
│ cal  │ │ override │  │ times?       │
│ times│ │ times    │  └──┬───────┬───┘
└──────┘ └──────────┘     │Yes    │No
                          ▼       ▼
                    ┌──────┐ ┌─────────┐
                    │ Use  │ │ Use     │
                    │ cal  │ │ default │
                    │ times│ │ times   │
                    └──────┘ └─────────┘
```

### Downstream Time Update Flow (unchanged)

```
CalendarEvent built with resolved times
        │
        ▼
Sensor._reserve_or_update_slot()
        │
        ▼
EventOverrides.async_reserve_or_get_slot()
        │
        ├─ times match stored override → ReserveResult(times_updated=False) → no action
        │
        └─ times differ from stored override → update override in-place
                                              → ReserveResult(times_updated=True)
                                              → async_fire_update_times()
                                              → Keymaster datetime entities updated
```

## Validation Rules

| Field | Rule | Enforcement |
|-------|------|-------------|
| `honor_event_times` | Must be `bool` | `cv.boolean` in voluptuous schema |
| `honor_event_times` | Default `False` on new installs | Schema default in `_get_schema()` |
| `honor_event_times` | Default `False` on migration | Migration v7→v8 in `__init__.py` |

## Constants (new)

```python
# const.py additions
CONF_HONOR_EVENT_TIMES = "honor_event_times"
DEFAULT_HONOR_EVENT_TIMES = False
```
