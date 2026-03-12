<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Coordinator Base Class Migration

## Entity Relationships

```text
┌─────────────────────────────────────────────────┐
│        DataUpdateCoordinator[list[CE]]          │
│  ┌─────────────────────────────────────────┐    │
│  │    RentalControlCoordinator             │    │
│  │                                         │    │
│  │  data: list[CalendarEvent]              │    │
│  │  event: CalendarEvent | None            │    │
│  │  event_overrides: EventOverrides | None │    │
│  │  _name, _unique_id, _entry_id           │    │
│  │  timezone, checkin, checkout             │    │
│  │  max_events, max_misses, num_misses     │    │
│  │  code_generator, code_length            │    │
│  │  start_slot, lockname, days             │    │
│  │                                         │    │
│  │  _async_update_data() → list[CE]        │    │
│  │  _async_setup() → None                  │    │
│  │  update_config(config)                  │    │
│  │  update_event_overrides(...)            │    │
│  │  async_get_events(hass, start, end)     │    │
│  └──────────────┬──────────────────────────┘    │
│                 │ notifies listeners             │
│    ┌────────────┼────────────┐                   │
│    ▼            ▼            ▼                   │
│ Calendar    Sensor[0]   Sensor[N-1]              │
│ Entity      Entity      Entity                   │
└─────────────────────────────────────────────────┘

CE = CalendarEvent
```

## Coordinator State

### Inherited from DataUpdateCoordinator

| Attribute | Type | Description |
|-----------|------|-------------|
| `data` | `list[CalendarEvent]` | Latest parsed calendar events (replaces `self.calendar`) |
| `last_update_success` | `bool` | Whether last refresh succeeded |
| `last_exception` | `Exception \| None` | Last error encountered |
| `update_interval` | `timedelta \| None` | Time between refreshes |
| `name` | `str` | Coordinator display name |
| `config_entry` | `ConfigEntry` | Associated config entry |

### Custom Attributes (preserved from current implementation)

| Attribute | Type | Description |
|-----------|------|-------------|
| `event` | `CalendarEvent \| None` | Next upcoming event (computed from `data`) |
| `event_overrides` | `EventOverrides \| None` | Keymaster slot overrides |
| `_unique_id` | `str` | Unique identifier |
| `_entry_id` | `str` | Config entry ID |
| `event_prefix` | `str \| None` | Event name prefix |
| `url` | `str` | iCal calendar URL |
| `timezone` | `tzinfo` | Calendar timezone |
| `refresh_frequency` | `int` | Minutes (for config display) |
| `checkin` | `time` | Default check-in time |
| `checkout` | `time` | Default check-out time |
| `start_slot` | `int` | Keymaster starting slot |
| `lockname` | `str \| None` | Keymaster lock entity |
| `max_events` | `int` | Maximum tracked events |
| `max_misses` | `int` | Consecutive empty tolerance |
| `num_misses` | `int` | Current miss counter |
| `days` | `int` | Look-ahead days |
| `ignore_non_reserved` | `bool` | Filter non-reserved events |
| `verify_ssl` | `bool` | SSL verification |
| `code_generator` | `str` | Code generation method |
| `should_update_code` | `bool` | Update codes on date change |
| `code_length` | `int` | Generated code length |
| `created` | `str` | Creation timestamp |

### Removed Attributes

| Attribute | Replacement |
|-----------|-------------|
| `calendar` | `self.data` (inherited) |
| `calendar_ready` | `self.last_update_success` (inherited) |
| `calendar_loaded` | `self.last_update_success` (inherited) |
| `next_refresh` | `self.update_interval` (inherited scheduler) |
| `event_sensors` | DUC listener mechanism (automatic) |
| `_events_ready` | Derived from entity state, not coordinator |

## Entity State Model

### Calendar Entity (RentalControlCalendar)

**Base classes**: `CoordinatorEntity[RentalControlCoordinator]`,
`CalendarEntity`

| Property | Source | Notes |
|----------|--------|-------|
| `event` | `self.coordinator.event` | Next upcoming event |
| `available` | `self.coordinator.last_update_success` | From CoordinatorEntity |
| `name` | `self.coordinator.name` | Calendar name |
| `unique_id` | `self.coordinator.unique_id` | Unchanged |
| `device_info` | `self.coordinator.device_info` | Unchanged |
| `should_poll` | `False` | From CoordinatorEntity |

### Sensor Entity (RentalControlCalSensor)

**Base classes**: `CoordinatorEntity[RentalControlCoordinator]`,
`Entity` (via CoordinatorEntity)

| Property | Source | Notes |
|----------|--------|-------|
| `state` | `self.coordinator.data[event_number]` | Nth event's check-in |
| `available` | `self.coordinator.last_update_success` | From CoordinatorEntity |
| `extra_state_attributes` | Computed from event data | Unchanged structure |
| `name` | Sensor name pattern | Unchanged |
| `unique_id` | Config-derived | Unchanged |
| `should_poll` | `False` | From CoordinatorEntity |

### State Transitions

```text
Integration Setup:
  LOADING → async_config_entry_first_refresh()
    ├─ SUCCESS → data populated, entities created, available=True
    └─ FAILURE → ConfigEntryNotReady (HA retries automatically)

Normal Operation:
  IDLE → update_interval elapses
    → _async_update_data() called
      ├─ SUCCESS → data updated, listeners notified, available=True
      ├─ EMPTY (misses < max) → return previous data (stale-valid)
      ├─ EMPTY (misses >= max) → return empty list, available=True
      └─ ERROR → raise UpdateFailed, available=False, retry next interval

Config Change:
  update_config() called
    → update_interval updated
    → async_request_refresh() triggers immediate refresh

Override Update:
  update_event_overrides() called
    → async_request_refresh() triggers debounced refresh
```

## Validation Rules

- `refresh_frequency` ≥ 0 (0 means minimum interval)
- `max_events` ≥ 1
- `max_misses` ≥ 0
- `code_length` ≥ 4
- `start_slot` ≥ 1
- `days` ≥ 1
- `url` must be a valid HTTP(S) URL
- `timezone` must be a valid IANA timezone
