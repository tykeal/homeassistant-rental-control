# Internal API Contracts: Lock Code Buffer Times

**Feature Branch**: `009-lock-code-buffer`
**Date**: 2025-07-17

> This feature has no external/REST/GraphQL APIs. All contracts are internal
> Python function signatures and config entry schema changes.

## Contract 1: Configuration Schema Additions

### `const.py` — New Constants

```python
CONF_CODE_BUFFER_BEFORE: str = "code_buffer_before"
CONF_CODE_BUFFER_AFTER: str = "code_buffer_after"
DEFAULT_CODE_BUFFER_BEFORE: int = 0
DEFAULT_CODE_BUFFER_AFTER: int = 0
```

### `config_flow.py` — Schema Fields (conditional on lock entry)

```python
# Added inside _get_schema when lock entry is configured:
vol.Optional(
    CONF_CODE_BUFFER_BEFORE,
    default=_get_default(CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE),
): vol.All(vol.Coerce(int), vol.Range(min=0)),
vol.Optional(
    CONF_CODE_BUFFER_AFTER,
    default=_get_default(CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER),
): vol.All(vol.Coerce(int), vol.Range(min=0)),
```

### `strings.json` — UI Labels

```json
{
  "code_buffer_before": "Lock code buffer before check-in (minutes)",
  "code_buffer_after": "Lock code buffer after checkout (minutes)"
}
```

With descriptions:

```json
{
  "code_buffer_before": "Minutes before check-in time that the lock code becomes active (0 = no buffer)",
  "code_buffer_after": "Minutes after checkout time that the lock code remains active (0 = no buffer)"
}
```

---

## Contract 2: Coordinator Properties

### `RentalControlCoordinator.__init__`

```python
self.code_buffer_before: int = int(
    str(config.get(CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE))
)
self.code_buffer_after: int = int(
    str(config.get(CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER))
)
```

### `RentalControlCoordinator.update_config`

```python
self.code_buffer_before = int(
    str(config.get(CONF_CODE_BUFFER_BEFORE, DEFAULT_CODE_BUFFER_BEFORE))
)
self.code_buffer_after = int(
    str(config.get(CONF_CODE_BUFFER_AFTER, DEFAULT_CODE_BUFFER_AFTER))
)
```

---

## Contract 3: Buffer Application in Keymaster Service Calls

### `async_fire_set_code(coordinator, event, slot: int) -> None`

**Change**: Before sending `date_range_start` and `date_range_end` to Keymaster,
apply buffer offsets:

```python
from datetime import timedelta

# Compute buffered validity window
buffered_start = event.extra_state_attributes["start"] - timedelta(
    minutes=coordinator.code_buffer_before
)
buffered_end = event.extra_state_attributes["end"] + timedelta(
    minutes=coordinator.code_buffer_after
)

# Use buffered values in service calls:
# date_range_start → {"datetime": buffered_start}
# date_range_end   → {"datetime": buffered_end}
```

### `async_fire_update_times(coordinator, event, slot: int) -> None`

**Same change**: Apply identical buffer calculation before sending to Keymaster.

---

## Contract 4: Config Migration

### `async_migrate_entry` — v9→v10

```python
# 9 -> 10: Add code buffer before/after to configuration
if version == 9:
    _LOGGER.debug("Migrating from version %s", version)

    data = config_entry.data.copy()
    data[CONF_CODE_BUFFER_BEFORE] = 0
    data[CONF_CODE_BUFFER_AFTER] = 0
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=10,
    )

    version = 10
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
```

### `RentalControlFlowHandler.VERSION`

```python
VERSION = 10  # bumped from 9
```

---

## Contract 5: Unchanged Interfaces (Explicit Non-Changes)

The following interfaces are explicitly NOT modified:

| Interface | Reason |
|-----------|--------|
| `event_overrides.async_update(slot, code, name, start, end, prefix)` | FR-005: unbuffered times for matching |
| `event_overrides.verify_slot_ownership(slot, name)` | Uses stored unbuffered times |
| `calsensor._event_attributes["start"]` / `["end"]` | Display and ETA use raw times |
| `checkinsensor._tracked_event_start` / `_tracked_event_end` | Check-in timing uses raw times |
| `EVENT_RENTAL_CONTROL_SET_CODE` event payload | Carries raw event times for automation consumers |
