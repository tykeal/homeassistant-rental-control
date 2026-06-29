<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Calendar Sensor Decomposition Parity

This quickstart is for the later IMPLEMENT stage. The PLAN stage is docs-only
and does not modify production code.

## Scope

This feature is a behavior-preserving refactor only. Do not add lock-code
business rules, sensors, services, configuration options, reconciliation
launches, Store authority, async tasks, Keymaster service calls, or
Home Assistant-visible behavior changes.

## 1. Establish the existing behavior oracle

Run the current calsensor-focused tests before extraction:

```bash
uv run pytest tests/unit/test_sensors.py -q
```

Run current integration callers that exercise calsensor update behavior:

```bash
uv run pytest \
  tests/integration/test_refresh_cycle.py \
  tests/integration/test_checkin_tracking.py \
  -q
```

Confirm production import remains limited to `sensor.py`, and the direct test
import still exists:

```bash
rg "from \\.sensors\\.calsensor import RentalControlCalSensor" custom_components
rg "from .*calsensor import RentalControlCalSensor" tests/unit/test_sensors.py
```

## 2. Add helper modules and keep wrappers first

Add project SPDX headers and public docstrings to any new Python files:

- `custom_components/rental_control/sensors/calsensor_helpers/__init__.py`
- `custom_components/rental_control/sensors/calsensor_helpers/models.py`
- `custom_components/rental_control/sensors/calsensor_helpers/descriptions.py`
- `custom_components/rental_control/sensors/calsensor_helpers/codes.py`
- `custom_components/rental_control/sensors/calsensor_helpers/attributes.py`
- `custom_components/rental_control/sensors/calsensor_helpers/slots.py`
- `custom_components/rental_control/sensors/calsensor_helpers/state.py`

Keep `RentalControlCalSensor` in `calsensor.py`. Move logic one concern at a
time, leaving existing private methods as thin wrappers until parity tests pass.

## 3. Pin parser parity

Before moving parser bodies, add focused tests for the same fixtures already in
`tests/unit/test_sensors.py`:

- email extraction, first email wins, no-description returns `None`;
- phone number variants and no-match behavior;
- guest count from `Guests`, `Adults`, and `Children`;
- explicit last-four, phone-last-four, and phone fallback cases;
- URL and booking ID parsing;
- dynamic unknown fields, known-field skipping, URL skipping, slugification, and
  no overwrite of dedicated attributes.

Suggested targeted command:

```bash
uv run pytest \
  tests/unit/test_sensors.py \
  tests/unit/test_calsensor_descriptions.py \
  -q
```

## 4. Pin generated-code parity

Add focused tests around `codes.py` while keeping
`RentalControlCalSensor._generate_door_code()` passing existing tests. Cover:

- date-based truncation and zero-fill fallback;
- date-based fallback when description is absent and static-random has no UID;
- last-four only when code length is four;
- static-random UID determinism;
- static-random description fallback when UID is absent;
- empty UID normalization and no-description behavior;
- current global RNG side effects expected by existing tests.

Suggested targeted command:

```bash
uv run pytest \
  tests/unit/test_sensors.py \
  tests/unit/test_calsensor_codes.py \
  -q
```

## 5. Split coordinator update rendering

Extract `_handle_coordinator_update` in small steps:

1. Create helpers for initial/no-reservation attributes and ETA snapshots.
2. Extract event attribute snapshot building and state string construction.
3. Extract parsed attribute assembly from description helpers.
4. Extract read-only slot lookup into `slots.py`, passing calsensor module
   `get_slot_name` and `make_reservation_fingerprint` as call-time dependencies.
5. Return a `CalendarSensorRenderResult` and keep the entity shell responsible for
   assigning `_event_attributes`, `_parsed_attributes`, `_state`, and calling
   `async_write_ha_state()` exactly once.

Patch smoke tests must continue to pass for:

- `custom_components.rental_control.sensors.calsensor.get_slot_name`
- `custom_components.rental_control.sensors.calsensor.make_reservation_fingerprint`

## 6. Reduce the no-op slot-assignment parameters

Introduce `SlotAssignmentContext` and update the only visible caller:

```python
await sensor._async_handle_slot_assignment(
    SlotAssignmentContext(
        slot_name="test",
        slot_code="1234",
        start_time=start,
        end_time=end,
        uid=None,
        prefix="",
        eta_days=5,
    )
)
```

The method must remain async, present on `RentalControlCalSensor`, return `None`,
not schedule work, and not call `async_reserve_or_get_slot` or any Keymaster
service helper.

## 7. Validate public compatibility surface

Run an import and attribute smoke check after shell wiring:

```bash
uv run python - <<'PY'
from custom_components.rental_control.sensors import calsensor

assert hasattr(calsensor, "RentalControlCalSensor")
assert hasattr(calsensor, "get_slot_name")
assert hasattr(calsensor, "make_reservation_fingerprint")
required = [
    "_handle_coordinator_update",
    "_generate_door_code",
    "_extract_email",
    "_extract_phone_number",
    "_extract_num_guests",
    "_extract_last_four",
    "_extract_url",
    "_extract_booking_id",
    "_extract_dynamic_attributes",
    "_async_handle_slot_assignment",
]
for name in required:
    assert hasattr(calsensor.RentalControlCalSensor, name), name
PY
```

## 8. Run targeted and full validation

Run the smallest parity set first:

```bash
uv run pytest \
  tests/unit/test_sensors.py \
  tests/unit/test_calsensor_descriptions.py \
  tests/unit/test_calsensor_codes.py \
  tests/unit/test_calsensor_attributes.py \
  -q
```

Then run caller integration tests:

```bash
uv run pytest \
  tests/integration/test_refresh_cycle.py \
  tests/integration/test_checkin_tracking.py \
  -q
```

Before committing implementation changes, run the full existing suite and ruff:

```bash
uv run pytest tests/
uv run ruff check custom_components/ tests/
```

## 9. Measure complexity before claiming completion

Confirm calendar-sensor files stay below the active thresholds:

```bash
wc -l \
  custom_components/rental_control/sensors/calsensor.py \
  custom_components/rental_control/sensors/calsensor_helpers/*.py
```

Measure function lengths and parameter counts with the repository's existing
complexity tooling or an AST check, and confirm:

- no calendar-sensor-related file is 400 lines or longer;
- no project-owned function is 80 lines or longer;
- no project-owned parameter list has more than six parameters;
- no `aislop-ignore`, `aislop-ignore-file`, or equivalent suppression was added.

## Behavior parity reminders

- State strings, entity metadata, unique IDs, availability, icon, and diagnostic
  category remain unchanged.
- Event attributes keep the same keys and values for summary, description,
  location, start, end, UID, ETA, slot name, slot number, and slot code.
- No-reservation resets clear slot attributes and parsed attributes exactly.
- Code-generator settings are reread from the coordinator on each successful
  update before code fallback is calculated.
- Reconciliation lookup remains read-only and falls back to generated code only
  when coordinator state has no slot code.
- `_handle_coordinator_update` must not schedule async tasks or call slot mutation
  helpers.
