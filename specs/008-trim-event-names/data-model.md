<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Trim Event Names

## Configuration Entities

### Existing Entity: Config Entry Data (`config_entry.data`)

The Rental Control config entry is a flat dictionary stored by Home Assistant's config entry system. This feature adds two new keys.

### New Fields

| Field | Key Constant | Type | Default | Validation | Description |
|-------|-------------|------|---------|------------|-------------|
| Trim Names | `CONF_TRIM_NAMES` = `"trim_names"` | `bool` | `False` | `cv.boolean` | Enables word-boundary trimming of combined slot names before sending to Keymaster |
| Max Name Length | `CONF_MAX_NAME_LENGTH` = `"max_name_length"` | `int` | `16` | `vol.All(vol.Coerce(int), vol.Range(min=4))` | Maximum character length for the combined slot name when trimming is enabled |

### Relationships

```
Config Entry Data (flat dict)
├── ... (existing fields: name, url, event_prefix, etc.)
├── trim_names: bool          ← NEW (FR-001)
└── max_name_length: int      ← NEW (FR-002)

RentalControlCoordinator (runtime)
├── ... (existing attrs: event_prefix, lockname, etc.)
├── trim_names: bool          ← mirrors config (FR-001)
└── max_name_length: int      ← mirrors config (FR-002)
```

### Validation Rules

1. `trim_names` must be a valid boolean (enforced by `cv.boolean` in voluptuous schema)
2. `max_name_length` must be an integer ≥ 4 (enforced by `vol.Range(min=4)`)
3. **Cross-field warning** (FR-007): When `trim_names` is `True` and `len(event_prefix) >= (max_name_length - 4)`, display warning `prefix_too_long_for_trim`
4. `max_name_length` is stored regardless of `trim_names` value (simplifies migration and UI)

### State Transitions

No state machine — these are static configuration values. Changes take effect on the next `async_fire_set_code()` call (i.e., next calendar refresh cycle that sets lock codes).

### Config Version Migration

| Version | Change |
|---------|--------|
| 8 → 9 | Add `trim_names: False` and `max_name_length: 16` to all existing entries |

## Runtime Data Flow

```
Calendar Event
    │
    ▼
async_fire_set_code(coordinator, event, slot)
    │
    ├── prefix = coordinator.event_prefix + " " (or "")
    ├── slot_name = prefix + event.slot_name
    │
    ├── IF coordinator.trim_names:
    │   └── slot_name = trim_name(slot_name, coordinator.max_name_length)
    │
    └── Send slot_name to Keymaster via entity calls
```

## Pure Function: `trim_name(name: str, max_length: int) -> str`

**Input**: Combined name string, maximum length
**Output**: Trimmed string ≤ max_length characters, no trailing whitespace

**Algorithm**:
1. If `len(name) <= max_length`: return `name` unchanged
2. Split `name` on whitespace into words
3. If first word length > `max_length`: return `first_word[:max_length]`
4. Accumulate words left-to-right: add word if `current_length + separator + word_length <= max_length`
5. Return joined accumulated words (no trailing whitespace by construction)
