# Data Model: Lock Code Buffer Times

**Feature Branch**: `009-lock-code-buffer`
**Date**: 2025-07-17

## Entities

### Configuration Entity: Code Buffer Before

| Property | Value |
|----------|-------|
| **Constant** | `CONF_CODE_BUFFER_BEFORE = "code_buffer_before"` |
| **Default** | `DEFAULT_CODE_BUFFER_BEFORE = 0` |
| **Type** | `int` (non-negative) |
| **Storage** | `config_entry.data["code_buffer_before"]` |
| **Validation** | `vol.All(vol.Coerce(int), vol.Range(min=0))` |
| **Unit** | Minutes |
| **Semantics** | Number of minutes before reservation start that lock code becomes valid |

### Configuration Entity: Code Buffer After

| Property | Value |
|----------|-------|
| **Constant** | `CONF_CODE_BUFFER_AFTER = "code_buffer_after"` |
| **Default** | `DEFAULT_CODE_BUFFER_AFTER = 0` |
| **Type** | `int` (non-negative) |
| **Storage** | `config_entry.data["code_buffer_after"]` |
| **Validation** | `vol.All(vol.Coerce(int), vol.Range(min=0))` |
| **Unit** | Minutes |
| **Semantics** | Number of minutes after reservation end that lock code remains valid |

### Derived Value: Lock Code Validity Window

| Property | Value |
|----------|-------|
| **Buffered Start** | `event_start - timedelta(minutes=code_buffer_before)` |
| **Buffered End** | `event_end + timedelta(minutes=code_buffer_after)` |
| **Not persisted** | Computed inline at Keymaster service call time |
| **Consumers** | `async_fire_set_code`, `async_fire_update_times` only |

## Relationships

```text
config_entry.data
├── code_buffer_before: int    ──► RentalControlCoordinator.code_buffer_before
├── code_buffer_after: int     ──► RentalControlCoordinator.code_buffer_after
│
RentalControlCoordinator
├── .code_buffer_before ──► async_fire_set_code() ──► Keymaster date_range_start
├── .code_buffer_after  ──► async_fire_set_code() ──► Keymaster date_range_end
├── .code_buffer_before ──► async_fire_update_times() ──► Keymaster date_range_start
└── .code_buffer_after  ──► async_fire_update_times() ──► Keymaster date_range_end
```

## State Transitions

### Config Version Migration

```text
Version 9 ──► Version 10
  Added: code_buffer_before = 0
  Added: code_buffer_after = 0
```

### Buffer Value Lifecycle

```text
[Config Entry Created / Migrated]
        │
        ▼
  code_buffer_before = 0 (default)
  code_buffer_after = 0 (default)
        │
        ▼ (user modifies via options flow)
  code_buffer_before = N
  code_buffer_after = M
        │
        ▼ (update_listener fires)
  coordinator.update_config(new_data)
        │
        ▼ (coordinator.async_request_refresh)
  All active slots reprogrammed with new buffer offsets
```

## Validation Rules

| Field | Rule | Error Key |
|-------|------|-----------|
| `code_buffer_before` | Must be non-negative integer (≥0) | Enforced by `vol.Range(min=0)` — no custom error needed |
| `code_buffer_after` | Must be non-negative integer (≥0) | Enforced by `vol.Range(min=0)` — no custom error needed |

## Boundary: What Buffers Do NOT Affect

Per FR-005, the following use **unbuffered** times and must not be modified:

| Component | File | Remains Unbuffered |
|-----------|------|--------------------|
| Calendar event display | `sensors/calsensor.py` | `_event_attributes["start"]`, `["end"]` |
| Check-in sensor tracking | `sensors/checkinsensor.py` | `_tracked_event_start`, `_tracked_event_end` |
| Event override matching | `event_overrides.py` | `override["start"]`, `override["end"]` |
| Auto check-in/checkout timers | `sensors/checkinsensor.py` | Timer scheduling uses event times directly |
| ETA calculations | `sensors/calsensor.py` | Uses raw `event.start` |
| Slot ownership verification | `event_overrides.py` | Compares raw event times |
