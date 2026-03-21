<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# HA Service Schema: checkout

**Feature Branch**: `004-checkin-tracking`
**Date**: 2025-07-15

## Service Definition

**Domain**: `rental_control`
**Service name**: `checkout`
**Registered via**: `async_register_entity_service()` in `sensor.py`

## Schema

```yaml
# No input parameters — the service targets the entity directly via entity_id
service: rental_control.checkout
target:
  entity_id: sensor.rental_control_{calendar_name}_checkin
```

## Guard Conditions (FR-019)

The service handler validates the following before executing:

1. **State check**: Sensor must be in `checked_in` state
   - Error: `"Checkout is only available when the guest is checked in (current state: {state})"`

2. **Date check**: Current date must be within the tracked event's date range (inclusive)
   - Error: `"Checkout is only available during the reservation dates (current date: {current_date}, allowed: {start_date}–{end_date})"`

3. **Time check**: Current time must be before the tracked event's end time
   - Error: `"Checkout is not available after the event end time; automatic checkout should have occurred"`

## Success Response

- Sensor transitions to `checked_out`
- `rental_control_checkout` event fires with `source: manual`
- If early checkout expiry is enabled and keymaster is configured:
  - Keymaster slot end time updated to `min(now + 15min, original_event_end)`

## Error Response

- Raises `ServiceValidationError` with descriptive message
- No state change occurs
- No events fired

## Integration with Entity Service Registration

```python
# In sensor.py async_setup_entry():
platform = entity_platform.async_get_current_platform()
platform.async_register_entity_service(
    "checkout",
    {},  # Empty schema — no parameters
    "async_checkout",  # Method name on CheckinTrackingSensor
)
```
