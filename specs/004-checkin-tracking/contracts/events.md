<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# HA Event Bus Contracts

**Feature Branch**: `004-checkin-tracking`
**Date**: 2025-07-15

## Event: rental_control_checkin

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
}
```

### Source Values

| Value | Trigger |
|-------|---------|
| `"keymaster"` | Keymaster unlock event detected with matching code slot |
| `"automatic"` | Event start time reached (time-based mode or fallback) |

### Example

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
    },
)
```

---

## Event: rental_control_checkout

**Fired when**: Sensor transitions from `checked_in` to `checked_out`

### Event Data Schema

```python
{
    "entity_id": str,      # e.g., "sensor.rental_control_my_calendar_checkin"
    "summary": str,        # Event summary from calendar
    "start": str,          # ISO 8601 datetime string
    "end": str,            # ISO 8601 datetime string
    "guest_name": str,     # Extracted slot name / guest identifier
    "source": str,         # "manual" | "automatic"
}
```

### Source Values

| Value | Trigger |
|-------|---------|
| `"manual"` | User invoked the `rental_control.checkout` action |
| `"automatic"` | Event end time reached |

### Example

```python
hass.bus.async_fire(
    "rental_control_checkout",
    {
        "entity_id": "sensor.rental_control_beach_house_checkin",
        "summary": "Reserved - John Smith",
        "start": "2025-07-20T16:00:00-04:00",
        "end": "2025-07-25T11:00:00-04:00",
        "guest_name": "John Smith",
        "source": "manual",
    },
)
```
