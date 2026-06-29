<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Calendar Sensor

**Feature**: `019-decompose-calsensor` | **Planning Branch**:
`019-decompose-calsensor-plan` | **Date**: 2026-06-29 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/019-decompose-calsensor/spec.md` and GitHub issue #576

## Summary

Decompose `custom_components/rental_control/sensors/calsensor.py` without
changing Home Assistant-visible behavior. The current 537-line module is the
compatibility shell for `RentalControlCalSensor`, event state and attributes,
description parsing, generated code fallback, read-only reconciliation lookup,
and the deprecated slot-assignment no-op shim.

The implementation will keep `RentalControlCalSensor` importable from
`custom_components.rental_control.sensors.calsensor` and constructible by
`sensor.py` with the existing four-argument pattern. Focused helpers under a
new sibling package, `custom_components/rental_control/sensors/calsensor_helpers/`,
will own pure parsing, code generation, event attribute construction, ETA/state
mapping, read-only slot lookup preparation, and the grouped no-op slot assignment
context. `calsensor.py` remains the Home Assistant entity shell and keeps the
current private methods as thin compatibility wrappers so visible and hidden
helper seams remain patchable from the calsensor module.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `icalendar>=7.0.0`, and
`x-wr-timezone>=2.0.0`
**Storage**: N/A; this refactor adds no persistent storage and preserves
coordinator-owned reconciliation and `event_overrides` state authority
**Testing**: `uv run pytest tests/unit/test_sensors.py`; caller and
reconciliation parity via `tests/integration/test_refresh_cycle.py`,
`tests/integration/test_checkin_tracking.py`, and reconciliation import tests;
ruff via `uv run ruff check custom_components/ tests/`; pre-commit hooks for
reuse, ruff, mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Coordinator update handling performs the same in-memory
event selection, ETA calculation, description parsing, read-only reconciliation
lookup, generated-code fallback, and single `async_write_ha_state()` call with
no additional refreshes, service calls, Store reads, or async tasks
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
implementation must preserve `RentalControlCalSensor` imports, constructor,
entity properties, module-level `get_slot_name` and
`make_reservation_fingerprint` patch seams, private helper methods exercised by
tests, date-based/last-four/static-random code semantics, read-only
reconciliation integration, and no-reservation reset behavior.
**Scale/Scope**: One 537-line module becomes a small entity shell plus a helper
package. Current measured complexity debt is file size, the 147-line
`_handle_coordinator_update`, and the over-parameter deprecated
`_async_handle_slot_assignment` shim with seven keyword-only values plus
`self`. Implementation target: every calendar-sensor-related file below 400
lines, every project-owned function below 80 lines, and every project-owned
parameter list no more than six parameters without an Aislop suppression.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing sensor behavior tests and integration callers to pass, plus focused parity tests for extracted pure helpers. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split helper models, parser/code extraction, state mapping, slot lookup, shell wiring, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python helper modules must include project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested AI co-author trailer. |
| VI: User Experience Consistency | PASS | Entity names, unique IDs, attributes, state strings, immediate update timing, imports, and patch seams are explicitly preserved. |
| VII: Performance Requirements | PASS | Extracted helpers are pure in-memory logic; the shell keeps the same single state write and performs no new blocking I/O, refresh, service call, or async scheduling. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/019-decompose-calsensor/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 module/data ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This behavior-preserving refactor adds
no external HTTP, WebSocket, Home Assistant service, entity-service, event, or
public Python API contract. Internal dataclasses and helper boundaries are
specified in this plan and in [data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── sensor.py                                # Existing production import of
│                                           # RentalControlCalSensor remains
└── sensors/
    ├── calsensor.py                         # Public HA entity shell;
    │                                       # current module patch/private seams
    │                                       # stay here as wrappers
    ├── calsensor_helpers/
    │   ├── __init__.py                      # Internal package marker/exports
    │   ├── models.py                        # EventAttributeSnapshot,
    │   │                                   # ParsedReservationAttributes,
    │   │                                   # EtaSnapshot,
    │   │                                   # SlotReadContext,
    │   │                                   # SlotAssignmentContext
    │   ├── descriptions.py                  # Email, phone, guest count,
    │   │                                   # last-four, URL, booking ID,
    │   │                                   # and dynamic field parsing
    │   ├── codes.py                         # date-based, last-four, and
    │   │                                   # static-random door-code decisions
    │   ├── attributes.py                    # initial/no-reservation attrs,
    │   │                                   # event attr snapshot building,
    │   │                                   # ETA, parsed attr assembly
    │   ├── slots.py                         # slot name dependency call,
    │   │                                   # fingerprint preparation, read-only
    │   │                                   # assignment/code lookup decisions
    │   └── state.py                         # event selection, state string,
    │                                       # update result orchestration
    └── checkinsensor.py                     # Unchanged

tests/
├── unit/
│   ├── test_sensors.py                      # Existing behavior oracle;
│   │                                       # update only the no-op shim caller
│   │                                       # to pass SlotAssignmentContext
│   ├── test_calsensor_descriptions.py       # Focused parser parity tests
│   ├── test_calsensor_codes.py              # Focused generated-code parity
│   └── test_calsensor_attributes.py         # Focused update/attribute parity
└── integration/
    ├── test_refresh_cycle.py                # Existing caller parity
    └── test_checkin_tracking.py             # Existing sensor update callers
```

**Structure Decision**: Keep `calsensor.py` as a module, not a package. The
verified production import is only `custom_components/rental_control/sensor.py`
importing `RentalControlCalSensor`; visible tests also import the class directly
from `custom_components.rental_control.sensors.calsensor`, call private methods,
and patch `calsensor.get_slot_name` and
`calsensor.make_reservation_fingerprint`. A sibling `calsensor_helpers/`
package gives the implementation focused extraction points while leaving the
stable import and monkeypatch module unchanged. No production caller imports from
`calsensor_helpers/`.

## Concrete Decomposition Design

### Public compatibility boundary

`custom_components/rental_control/sensors/calsensor.py` remains importable and
keeps these names effective at their current module path:

- `RentalControlCalSensor`
- `get_slot_name`
- `make_reservation_fingerprint`

`RentalControlCalSensor` keeps the same production constructor pattern:

```python
RentalControlCalSensor(hass, coordinator, sensor_name, event_number)
```

The entity shell keeps Home Assistant-facing state and properties:
`async_added_to_hass`, `device_info`, `entity_category`,
`extra_state_attributes`, `icon`, `state`, `unique_id`, `_event_attributes`,
`_parsed_attributes`, `_code_generator`, `_code_length`, `_event_number`, and
`_state`.

The private seams currently called by tests remain methods on the class:
`_handle_coordinator_update`, `_generate_door_code`, `_extract_email`,
`_extract_phone_number`, `_extract_num_guests`, `_extract_last_four`,
`_extract_url`, `_extract_booking_id`, `_extract_dynamic_attributes`, and
`_async_handle_slot_assignment`. Most of these become short wrappers over helper
functions and continue to read from the same `_event_attributes` source of truth.

### Ground-truth call and patch analysis

The implementation must start from the live `calsensor.py` source, not only from
the issue description. Current source facts captured during planning:

- `calsensor.py` is 537 lines.
- `_handle_coordinator_update` is the only long function and owns event
  selection, code setting refresh, ETA calculation, state string construction,
  slot-name construction, read-only reconciliation lookup, generated-code
  fallback, parsed description attributes, no-reservation reset, and the final
  `async_write_ha_state()` call.
- `_async_handle_slot_assignment` is a deprecated no-op with seven keyword-only
  values plus `self`.
- Production imports `RentalControlCalSensor` only from `sensor.py`.
- `tests/unit/test_sensors.py` is the visible direct-call oracle for private
  helper methods and the sole visible caller of `_async_handle_slot_assignment`.
- Visible tests patch `custom_components.rental_control.sensors.calsensor.get_slot_name`
  and `custom_components.rental_control.sensors.calsensor.make_reservation_fingerprint`.

### Entity shell responsibilities

`RentalControlCalSensor` keeps responsibilities that require the live entity,
coordinator, or Home Assistant boundary:

1. construction, entity metadata, initial no-reservation attributes, and unique ID;
2. `async_added_to_hass()` immediate processing of successful existing data;
3. the listener callback method name `_handle_coordinator_update()`;
4. refreshing `_code_generator` and `_code_length` from the coordinator before
   each successful event render;
5. current mutable `_event_attributes` and `_parsed_attributes` dictionaries;
6. the single final `async_write_ha_state()` per coordinator update;
7. runtime lookup of module-level `get_slot_name` and
   `make_reservation_fingerprint` so existing calsensor patches remain effective;
8. no-op compatibility method `_async_handle_slot_assignment()`.

Helpers receive explicit values, snapshots, or dependency callables and return
new dictionaries or decision objects. They must not call Home Assistant services,
mutate event overrides, call `async_request_refresh()`, schedule tasks, or write
HA state.

### `descriptions.py`

`descriptions.py` owns pure parsing currently implemented by the private
extractor methods:

- `extract_email(description)`
- `extract_phone_number(description)`
- `extract_num_guests(description)`
- `extract_last_four(description)`
- `extract_url(description)`
- `extract_booking_id(description)`
- `extract_dynamic_attributes(description, known_fields)`
- `build_parsed_attributes(description)`

The class methods remain wrappers that pass
`self._event_attributes["description"]`. `extract_last_four()` may accept a phone
extractor dependency or call the sibling phone parser so the current phone-last
four fallback stays exact. Known-field skipping remains centralized so dynamic
attributes do not overwrite dedicated attributes.

### `codes.py`

`codes.py` owns generated door-code fallback behavior. It receives a
`DoorCodeRequest` or equivalent fields containing generator, code length, start,
end, UID, description, and last-four dependency. It must preserve:

- forced date-based generation when description is absent and static-random has
  no UID;
- last-four use only for generator `last_four` and code length four;
- static-random UID preference over description fallback;
- current deterministic `random.seed(seed)` and `randrange` behavior;
- current date-based digit ordering and zero-fill fallback.

`RentalControlCalSensor._generate_door_code()` remains a wrapper using current
entity attributes, so visible tests that call it directly keep working.

### `attributes.py` and `state.py`

`attributes.py` owns reusable attribute snapshots:

- initial and no-reservation event attributes with optional event prefix;
- UID normalization from event objects;
- ETA calculation using the event start timezone and current semantics;
- base event attribute construction;
- parsed attribute assembly from description helper results.

`state.py` owns the coordinator-update orchestration that can be expressed as a
pure result:

1. decide whether a coordinator update has a renderable event for `_event_number`;
2. build the state string exactly as today;
3. assemble base attributes and parsed attributes;
4. request slot-read information from `slots.py`;
5. request generated-code fallback from `codes.py` only when reconciliation has no
   slot code;
6. return a `CalendarSensorRenderResult` for the entity shell to assign and write.

The public `_handle_coordinator_update()` method becomes a short callback: log,
handle unsuccessful updates, refresh code settings, delegate successful render or
no-reservation reset, assign returned dictionaries/state, and call
`async_write_ha_state()` once.

### `slots.py`

`slots.py` owns read-only slot preparation and lookup decisions. It must accept
module-level callables from `calsensor.py`, not import direct frozen aliases that
bypass patches:

- `get_slot_name_func(summary, description, prefix)` computes the slot name.
- `make_fingerprint_func(entry_id, slot_name, start, end)` computes the
  reservation fingerprint only when `event_overrides` and `slot_name` exist.
- coordinator `get_slot_assignment(identity_key)` and
  `get_slot_code(identity_key)` are read, never mutated.

The result is a `SlotReadResult` containing `slot_name`, `slot_number`, and
`slot_code`. If no reconciliation code exists, the entity shell or state helper
uses `codes.py` for fallback. No helper in this feature may call
`async_reserve_or_get_slot`, `async_fire_set_code`, `async_fire_clear_code`,
`async_fire_update_times`, `hass.async_create_task`, or reconciliation launch
helpers.

### Slot-assignment parameter reduction

The deprecated `_async_handle_slot_assignment` shim is behaviorally a no-op and
is not called by production code. To satisfy the parameter-count threshold, the
implementation will introduce `SlotAssignmentContext` in
`calsensor_helpers.models` with the current legacy fields:

- `slot_name`
- `slot_code`
- `start_time`
- `end_time`
- `uid`
- `prefix`
- `eta_days`

The method signature becomes:

```python
async def _async_handle_slot_assignment(
    self,
    context: SlotAssignmentContext,
) -> None:
    ...
```

`tests/unit/test_sensors.py::test_async_handle_slot_assignment_is_noop` is the
only visible caller and will be updated to construct and pass the dataclass. The
method remains present, async, harmless, and unscheduled; the production entity
API does not change because production never calls this private shim. This is a
source change for one private test seam, but it preserves observable behavior and
removes the active eight-parameter finding without adding `*args`, `**kwargs`, or
an Aislop directive.

## Phase 0 Research Output

See [research.md](research.md). All planning questions are resolved; no
`NEEDS CLARIFICATION` markers remain.

## Phase 1 Design Output

See [data-model.md](data-model.md) for the internal helper entities and
[quickstart.md](quickstart.md) for the implementation validation guide. No
contracts are generated because this refactor introduces no external API or new
public caller behavior. Agent-context updates are intentionally omitted because
no new language, framework, runtime dependency, or tool is introduced.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Design keeps private wrappers testable and adds focused helper parity coverage around parsing, codes, attributes, and slot reads. |
| II: Atomic Commit Discipline | PASS | The future implementation can be staged by helper concern while each commit keeps tests passing. |
| III: Licensing & Attribution | PASS | New helper files must use Python SPDX headers; markdown artifacts already do. |
| IV: Pre-Commit Integrity | PASS | No bypass or suppression is part of the plan. |
| V: Agent Co-Authorship & DCO | PASS | PLAN commit remains signed off with the requested co-author. |
| VI: User Experience Consistency | PASS | State, attributes, entity metadata, import paths, and module patch seams are preserved. |
| VII: Performance Requirements | PASS | Helpers are pure and the shell keeps one state write with no new I/O or tasks. |

**Gate result: PASS** — no violations after Phase 1 design.

## Complexity Tracking

No constitutional violations require justification. The implementation must not
add `aislop-ignore`, `aislop-ignore-file`, or an equivalent suppression. If any
helper approaches 400 lines or 80-line functions during implementation, split by
coherent concern instead of suppressing the finding.
