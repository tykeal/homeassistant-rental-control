<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Entity Contracts: Switch Platform

**Feature Branch**: `004-checkin-tracking`
**Date**: 2025-07-15

## KeymasterMonitoringSwitch

**Entity ID pattern**: `switch.rental_control_{calendar_name}_keymaster_monitoring`
**Conditional**: Only created when `coordinator.lockname` is truthy (keymaster configured)

### State

| State | Description |
|-------|-------------|
| `on` | Keymaster unlock events trigger check-in detection |
| `off` | Time-based auto check-in only (default) |

### Methods

| Method | Description |
|--------|-------------|
| `async_turn_on()` | Enable keymaster monitoring |
| `async_turn_off()` | Disable keymaster monitoring |
| `turn_on()` | Sync wrapper (required by SwitchEntity) |
| `turn_off()` | Sync wrapper (required by SwitchEntity) |

### Restore Behavior

On HA restart, restores last known on/off state via `RestoreEntity`. If no
prior state exists, defaults to `off`.

---

## EarlyCheckoutExpirySwitch

**Entity ID pattern**: `switch.rental_control_{calendar_name}_early_checkout_expiry`
**Conditional**: Only created when `coordinator.lockname` is truthy (keymaster configured)

### State

| State | Description |
|-------|-------------|
| `on` | Manual checkout updates keymaster slot end to `min(now+15m, event_end)` |
| `off` | Manual checkout does not modify keymaster slot dates (default) |

### Methods

| Method | Description |
|--------|-------------|
| `async_turn_on()` | Enable early lock code expiry |
| `async_turn_off()` | Disable early lock code expiry |
| `turn_on()` | Sync wrapper (required by SwitchEntity) |
| `turn_off()` | Sync wrapper (required by SwitchEntity) |

### Restore Behavior

On HA restart, restores last known on/off state via `RestoreEntity`. If no
prior state exists, defaults to `off`.

---

## Platform Registration

The `switch` platform is added to `PLATFORMS` in `const.py`:

```python
PLATFORMS = [CALENDAR, SENSOR, SWITCH]
```

Platform setup in `switch.py`:

```python
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    coordinator = hass.data[DOMAIN][config_entry.entry_id][COORDINATOR]

    entities: list[SwitchEntity] = []

    if coordinator.lockname:
        entities.append(
            KeymasterMonitoringSwitch(coordinator, config_entry)
        )
        entities.append(
            EarlyCheckoutExpirySwitch(coordinator, config_entry)
        )

    async_add_entities(entities)
    return True
```
