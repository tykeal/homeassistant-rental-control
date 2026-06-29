<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Calendar Sensor

## Decision: Keep `calsensor.py` as the compatibility shell

**Rationale**: The production import path is
`custom_components.rental_control.sensors.calsensor.RentalControlCalSensor`, and
`sensor.py` is the only production importer. Visible tests also import the class
from that module, call private helper methods, inspect private attributes, and
patch module-level `get_slot_name` and `make_reservation_fingerprint`. Keeping
`calsensor.py` as the Home Assistant entity shell preserves these import and
patch boundaries while allowing behavior to move to internal helpers.

**Alternatives considered**:

- Convert `calsensor.py` into a package with `__init__.py`: rejected because it
  adds package-conversion risk for no behavior benefit.
- Move callers directly to helper modules: rejected because the feature is a
  behavior-preserving decomposition, not a caller rewrite.

## Decision: Add `sensors/calsensor_helpers/` as an internal package

**Rationale**: The current file mixes entity metadata, coordinator rendering,
ETA/state mapping, description parsing, generated-code fallback, and read-only
slot lookup. A small helper package lets each concern stay below file and
function thresholds without making helper modules public API. The package sits
beside `calsensor.py`, matching the existing sensors layout and avoiding import
cycles with top-level coordinator and reconciliation modules.

**Alternatives considered**:

- Add one large `calsensor_helpers.py`: rejected because it risks recreating the
  same oversized mixed-concern file.
- Place helpers at `custom_components/rental_control/calsensor_helpers/`:
  rejected because these helpers are sensor-specific and do not need a top-level
  public-looking namespace.

## Decision: Preserve module patch seams through dependency callables

**Rationale**: Tests patch
`custom_components.rental_control.sensors.calsensor.get_slot_name` and
`custom_components.rental_control.sensors.calsensor.make_reservation_fingerprint`.
If helpers import those functions directly from `util` or `reconciliation`, those
patches would stop intercepting the update path. The shell should pass the
current calsensor module attributes into slot helpers at call time.

**Alternatives considered**:

- Import `get_slot_name` and `make_reservation_fingerprint` directly in
  `slots.py`: rejected because calsensor-level patches would miss the helper.
- Change tests to patch helper modules: rejected because current compatibility
  seams are part of the source-of-truth behavior.

## Decision: Extract description parsing as pure helpers with wrappers

**Rationale**: `_extract_email`, `_extract_phone_number`, `_extract_num_guests`,
`_extract_last_four`, `_extract_url`, `_extract_booking_id`, and
`_extract_dynamic_attributes` are independently testable string parsers. Moving
the regex and slugification logic into pure helpers reduces the entity file while
keeping each private class method as a wrapper over
`self._event_attributes["description"]`.

**Alternatives considered**:

- Leave parsing on the entity and extract only `_handle_coordinator_update`:
  rejected because `calsensor.py` would remain too close to the file-size limit.
- Change parsing to richer structured objects: rejected because any parser output
  drift could change Home Assistant attributes.

## Decision: Extract generated-code decisions without changing RNG behavior

**Rationale**: `_generate_door_code` has many compatibility-sensitive cases:
last-four only for four-digit codes, static-random UID preference, description
fallback, empty UID handling, forced date-based fallback, and date digit order.
A pure `DoorCodeRequest` keeps inputs explicit and allows focused parity tests.
The wrapper method remains on `RentalControlCalSensor` for direct tests.

**Alternatives considered**:

- Replace global `random.seed()` with a local RNG: rejected because it could alter
  the exact side effects pinned by existing tests.
- Move code generation into coordinator reconciliation: rejected because the
  current sensor fallback is the behavior source of truth when reconciliation has
  no code.

## Decision: Split coordinator update into render-result helpers

**Rationale**: `_handle_coordinator_update` currently performs many unrelated
steps in one 147-line callback. A pure render pipeline can build base event
attributes, ETA fields, state strings, parsed attributes, slot read results, and
no-reservation resets, then return a result that the entity shell assigns. This
keeps the callback short while preserving the same one final HA state write.

**Alternatives considered**:

- Move the entire callback body wholesale to a helper: rejected because the
  helper would still exceed the function-length threshold.
- Let helpers mutate `self._event_attributes` directly: rejected because it keeps
  tight coupling and makes parity harder to reason about.

## Decision: Keep slot lookup read-only and dependency-driven

**Rationale**: The sensor must remain a read-only view of coordinator
reconciliation. Slot helpers should prepare the fingerprint from entry ID, slot
name, start, and end; read `get_slot_assignment` and `get_slot_code`; and return
values for attributes. They must not reserve slots, call Keymaster services,
write event overrides, or schedule async tasks.

**Alternatives considered**:

- Reintroduce `async_reserve_or_get_slot` from the sensor: rejected as an explicit
  non-goal and a safety regression.
- Skip reconciliation lookup and always generate local codes: rejected because it
  changes the current slot-code priority order.

## Decision: Reduce `_async_handle_slot_assignment` with a context dataclass

**Rationale**: The deprecated shim is a no-op but its seven keyword-only inputs
plus `self` exceed the parameter-count threshold. The only visible caller is
`tests/unit/test_sensors.py::test_async_handle_slot_assignment_is_noop`. Using a
single `SlotAssignmentContext` dataclass removes the finding while preserving the
method name, async no-op behavior, and no-scheduling guarantee. The test caller
can be updated to construct the context during implementation; no production
caller is affected.

**Alternatives considered**:

- Keep the legacy keyword-only signature: rejected because it leaves the active
  parameter-count finding unresolved.
- Accept `*args` and `**kwargs` for compatibility: rejected for this plan because
  the source-of-truth caller is known and a typed context is clearer for future
  removal of the deprecated shim.
- Delete the method: rejected because current tests and compatibility
  documentation require the no-op seam to remain.

## Decision: Omit contracts and agent-context updates

**Rationale**: This is an internal refactor plan. It adds no external API,
service schema, entity contract, event payload, runtime dependency, language,
framework, or tool. Existing Home Assistant-visible behavior and Python import
surfaces are preserved rather than extended.

**Alternatives considered**:

- Add contract files for internal helper dataclasses: rejected because
  data-model.md and plan.md already define those internal structures.
- Run `update-agent-context.sh`: rejected because no new technology or dependency
  is introduced.
