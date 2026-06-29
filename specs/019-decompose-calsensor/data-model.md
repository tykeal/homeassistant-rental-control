<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Calendar Sensor

This feature is a behavior-preserving refactor. The models below are internal
implementation aids, not new public API. `RentalControlCalSensor` remains
importable from `custom_components.rental_control.sensors.calsensor`.

## Existing public and compatibility entities retained on `calsensor.py`

### RentalControlCalSensor

**Owner module**: `custom_components.rental_control.sensors.calsensor`

**Fields/state retained**:

- `_code_generator` and `_code_length`
- `_entity_category`
- `_event_attributes`
- `_parsed_attributes`
- `_event_number`
- `_hass`
- `_state`
- `_unique_id`
- `_attr_has_entity_name`
- `_attr_name`

**Relationships**:

- extends `CoordinatorEntity[RentalControlCoordinator]`
- consumed by `custom_components/rental_control/sensor.py`
- calls helper modules for pure rendering decisions
- exposes private wrapper methods used by `tests/unit/test_sensors.py`

**Validation rules**:

- Constructor remains `hass, coordinator, sensor_name, event_number`.
- `async_added_to_hass()` still performs the immediate update when coordinator
  data is already successful and available.
- `extra_state_attributes` remains the merge of event and parsed attributes.
- `_handle_coordinator_update()` writes HA state once per invocation, including
  unsuccessful and no-reservation paths.
- Module-level calsensor patches for `get_slot_name` and
  `make_reservation_fingerprint` remain effective.

### EventAttributeSnapshot

**Owner module**: `calsensor_helpers.models` or `attributes.py`

**Purpose**: Complete event attributes exposed by `extra_state_attributes` before
parsed reservation attributes are merged.

**Fields**:

- `summary: str`
- `description: str | None`
- `location: str | None`
- `start: datetime | None`
- `end: datetime | None`
- `uid: str | None`
- `eta_days: int | None`
- `eta_hours: int | None`
- `eta_minutes: int | None`
- `slot_name: str | None`
- `slot_code: str | None`
- `slot_number: int | None`

**Validation rules**:

- No-reservation snapshots use the configured event prefix exactly as today.
- UID strings are stripped and converted to `None` when empty.
- Past events produce `None` for all ETA fields.
- Snapshot conversion preserves current dictionary keys and values.

### EtaSnapshot

**Owner module**: `calsensor_helpers.attributes`

**Purpose**: ETA values derived from event start time and current time.

**Fields**:

- `eta_days: int | None`
- `eta_hours: int | None`
- `eta_minutes: int | None`

**Validation rules**:

- Uses `datetime.now(start.tzinfo)` exactly as the current implementation does.
- Future starts calculate days, floor hours, and floor minutes from the same time
  delta as today.
- Past starts set every field to `None`.

### ParsedReservationAttributes

**Owner module**: `calsensor_helpers.descriptions` or `attributes.py`

**Purpose**: Guest attributes parsed from event descriptions and merged into
`extra_state_attributes`.

**Fields**:

- optional `last_four`
- optional `number_of_guests`
- optional `guest_email`
- optional `phone_number`
- optional `reservation_url`
- optional `booking_id`
- dynamic slugified fields from unknown `Field: Value` lines

**Validation rules**:

- Dedicated fields are omitted when their extractor returns `None`.
- Dynamic fields skip dedicated known labels and URL-like labels.
- Dynamic fields never overwrite dedicated parsed attributes.
- Slugification remains lowercase non-alphanumeric-to-underscore with edge
  underscores stripped.

### DoorCodeRequest

**Owner module**: `calsensor_helpers.models` or `codes.py`

**Purpose**: Inputs needed to produce the generated fallback slot code.

**Fields**:

- `generator: str`
- `code_length: int`
- `start: datetime`
- `end: datetime`
- `uid: str | None`
- `description: str | None`
- `last_four: str | None`

**Relationships**:

- Consumed by generated-code helper.
- Produced by `RentalControlCalSensor._generate_door_code()` wrapper from current
  entity attributes.

**Validation rules**:

- `last_four` only wins when generator is `last_four` and code length is four.
- Static-random uses UID when present, otherwise description when present.
- Static-random with no UID and no description falls through to date-based.
- Date-based uses start day, end day, start month, end month, start year, and end
  year in current order, then truncates or zero-fills.

### SlotReadContext

**Owner module**: `calsensor_helpers.models` or `slots.py`

**Purpose**: Input for read-only reconciliation lookup.

**Fields**:

- `entry_id: str`
- `summary: str`
- `description: str | None`
- `event_prefix: str`
- `start: datetime`
- `end: datetime`
- `event_overrides_present: bool`
- `get_slot_name`: callable supplied from calsensor module
- `make_reservation_fingerprint`: callable supplied from calsensor module

**Relationships**:

- Produces `SlotReadResult`.
- Calls coordinator `get_slot_assignment` and `get_slot_code` only after a
  fingerprint is available.

**Validation rules**:

- Fingerprint calculation is skipped when no `event_overrides` object is present
  or when slot name is `None`.
- Slot assignment and code reads use the same identity key.
- No method on `event_overrides` is called from the sensor render path.

### SlotReadResult

**Owner module**: `calsensor_helpers.models` or `slots.py`

**Purpose**: Read-only slot attributes for the rendered event.

**Fields**:

- `slot_name: str | None`
- `slot_number: int | None`
- `slot_code: str | None`

**Validation rules**:

- `slot_name` is always the result of calsensor-patchable `get_slot_name`.
- `slot_number` and `slot_code` are `None` when reconciliation is absent or has no
  value for the identity key.
- A `None` `slot_code` allows generated-code fallback exactly as today.

### CalendarSensorRenderResult

**Owner module**: `calsensor_helpers.models` or `state.py`

**Purpose**: Pure result assigned by the entity shell after a successful or
no-reservation render decision.

**Fields**:

- `state: str`
- `event_attributes: dict[str, Any]`
- `parsed_attributes: dict[str, str]`

**Validation rules**:

- Event state string remains `"{summary} - {day} {Month Year} {HH:MM}"`.
- No-reservation state is exactly the prefixed or unprefixed summary.
- Returned dictionaries contain the same keys as current `_event_attributes` and
  `_parsed_attributes` for identical inputs.

### SlotAssignmentContext

**Owner module**: `calsensor_helpers.models`

**Purpose**: Grouped value for the deprecated no-op slot-assignment shim.

**Fields**:

- `slot_name: str`
- `slot_code: str`
- `start_time: datetime`
- `end_time: datetime`
- `uid: str | None`
- `prefix: str`
- `eta_days: int | None`

**Relationships**:

- Passed by the updated visible no-op test to
  `RentalControlCalSensor._async_handle_slot_assignment(context)`.

**Validation rules**:

- The method remains async and returns `None`.
- The context is not used to reserve slots, call services, mutate overrides, or
  schedule tasks.
- Replacing seven keyword-only parameters with one context keeps the method under
  the active parameter threshold.
