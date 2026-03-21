<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Guest Check-in/Check-out Tracking

**Branch**: `004-checkin-tracking` | **Date**: 2025-07-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-checkin-tracking/spec.md`

## Summary

Add a guest check-in/check-out tracking sensor to the Rental Control integration
that monitors occupancy state via a four-state state machine (`no_reservation` →
`awaiting_checkin` → `checked_in` → `checked_out`). The sensor transitions
automatically based on calendar event timing, optionally using keymaster unlock
detection for check-in. It supports manual early check-out with optional lock
code expiry, persists state across HA restarts, fires event bus events on
transitions, and handles same-day turnovers with configurable post-checkout
linger timing.

**Technical approach**: New `CheckinTrackingSensor` as a `CoordinatorEntity` +
`RestoreEntity`, timer-scheduled transitions via `async_track_point_in_time()`,
keymaster event bus listener for unlock detection, new `switch` platform for
toggle entities, and a registered entity service for manual checkout.

## Technical Context

**Language/Version**: Python 3.13.2+ (per pyproject.toml `requires-python = ">=3.13.2"`)
**Primary Dependencies**: homeassistant ≥ 2025.8.0, icalendar ≥ 6.1.0, x-wr-timezone ≥ 2.0.0
**Storage**: Home Assistant RestoreEntity state persistence (built-in HA mechanism)
**Testing**: pytest + pytest-homeassistant-custom-component + aioresponses; markers: `unit`, `integration`
**Target Platform**: Home Assistant custom integration (runs on HA Core instances)
**Project Type**: Single HA custom integration (`custom_components/rental_control/`)
**Performance Goals**: State transitions within one coordinator update cycle of the relevant time boundary (SC-001); event bus events fired synchronously with transitions (SC-002)
**Constraints**: Must not block HA event loop (async patterns required); must work on resource-constrained hardware (Raspberry Pi); lock code generation must complete within 30s sensor update cycle
**Scale/Scope**: One check-in sensor per integration instance; handles 1–5 concurrent event slots per calendar; one optional keymaster event listener per instance

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate (Phase 0 Entry)

| Principle | Status | Evidence |
|-----------|--------|----------|
| **I: Code Quality & Testing** | ✅ PASS | Plan includes unit + integration tests for all new code; type hints required; docstrings enforced by interrogate |
| **II: Atomic Commit Discipline** | ✅ PASS | Implementation will be broken into logical atomic commits (constants, sensor, switch, service, config flow, tests) |
| **III: Licensing & Attribution** | ✅ PASS | All new files will include SPDX headers; existing files modified will retain headers |
| **IV: Pre-Commit Integrity** | ✅ PASS | All commits must pass ruff, mypy, interrogate, reuse-tool, gitlint |
| **V: Agent Co-Authorship & DCO** | ✅ PASS | Agent commits will include Co-authored-by trailer and DCO sign-off via `git commit -s` |
| **VI: User Experience Consistency** | ✅ PASS | Entity naming follows `sensor.rental_control_{name}_checkin` convention; config follows HA config flow patterns; entities linked to existing device |
| **VII: Performance Requirements** | ✅ PASS | Timer-based transitions avoid polling overhead; async patterns throughout; no blocking I/O |

### Post-Design Gate (Phase 1 Exit)

| Principle | Status | Evidence |
|-----------|--------|----------|
| **I: Code Quality & Testing** | ✅ PASS | Data model defines all entities with types; contracts define service schemas and event payloads; test files identified in quickstart |
| **II: Atomic Commit Discipline** | ✅ PASS | Source structure supports incremental implementation: constants → state machine → persistence → keymaster → toggles → service → config |
| **VI: User Experience Consistency** | ✅ PASS | Entity IDs follow existing `rental_control_{name}_*` pattern; state values are lowercase snake_case matching HA conventions; switch entities only created when keymaster configured (no orphan entities) |
| **VII: Performance Requirements** | ✅ PASS | `async_track_point_in_time()` for precise transitions avoids frequent polling; coordinator updates serve as fault-tolerance backup; no additional HTTP requests |

## Project Structure

### Documentation (this feature)

```text
specs/004-checkin-tracking/
├── plan.md                          # This file
├── research.md                      # Phase 0: Research findings and decisions
├── data-model.md                    # Phase 1: Entity definitions, state machine, events
├── quickstart.md                    # Phase 1: Dev setup and verification commands
├── checklists/                      # Pre-existing checklists from spec
├── contracts/
│   ├── checkout-service.md          # Phase 1: checkout action schema and guards
│   ├── events.md                    # Phase 1: HA event bus event schemas
│   └── switch-entities.md           # Phase 1: Switch entity contracts
└── tasks.md                         # Phase 2: Generated by /speckit.tasks (not this command)
```

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py                      # MODIFY: Add keymaster event bus listener
├── config_flow.py                   # MODIFY: Add cleaning_window to options flow
├── const.py                         # MODIFY: Add new constants
├── coordinator.py                   # (no changes — data source stays the same)
├── sensor.py                        # MODIFY: Add CheckinTrackingSensor, register checkout service
├── switch.py                        # NEW: Switch platform setup + toggle entities
├── strings.json                     # MODIFY: Add new entity/config translations
├── translations/
│   └── en.json                      # MODIFY: English translations for new items
├── sensors/
│   ├── calsensor.py                 # (no changes — existing sensor unaffected)
│   └── checkinsensor.py             # NEW: CheckinTrackingSensor implementation
├── calendar.py                      # (no changes)
├── event_overrides.py               # (no changes)
├── util.py                          # MODIFY: Add early expiry helper function
└── manifest.json                    # (no changes — no new requirements)

tests/
├── conftest.py                      # MODIFY: Add checkin-specific fixtures
├── fixtures/
│   ├── calendar_data.py             # (may add new test calendar data)
│   └── checkin_data.py              # NEW: Test data for checkin scenarios
├── unit/
│   ├── test_checkin_sensor.py       # NEW: State machine unit tests
│   └── test_switch.py               # NEW: Toggle entity unit tests
└── integration/
    └── test_checkin_tracking.py     # NEW: Full lifecycle integration tests
```

**Structure Decision**: Follows the existing single-integration structure at
`custom_components/rental_control/`. New sensor goes in `sensors/` subdirectory
(matching `calsensor.py` pattern). New `switch.py` at the platform level follows
HA convention for platform modules. No new directories beyond `contracts/` in
the spec folder.

## Complexity Tracking

> No constitution violations identified. All design decisions align with
> existing project patterns and constitutional principles.

## Design Decisions

### D-001: Sensor Architecture

The `CheckinTrackingSensor` inherits from both `CoordinatorEntity` and
`RestoreEntity`. It receives coordinator data updates via
`_handle_coordinator_update()` (same as existing `RentalControlCalSensor`)
but maintains internal state machine logic rather than deriving state purely
from event data.

**Key behaviors**:
- On coordinator update: identifies the "most relevant" event (event 0 from
  sorted coordinator data) and evaluates whether a state transition is needed
- On timer fire: executes scheduled transitions (auto check-in, auto
  check-out, post-checkout linger expiry)
- On keymaster event: processes unlock detection when monitoring is enabled
- On service call: handles manual checkout with guard validation

### D-002: Event Relevance Determination

The sensor uses `coordinator.data[0]` (first/nearest event from sorted list)
as the "most relevant" event. This aligns with the spec's FR-002 requirement
and leverages the coordinator's existing sorting and filtering logic without
additional calendar queries.

When the sensor is in `checked_out`, it also examines `coordinator.data[1]`
(if available) to determine which FR-006 post-checkout scenario applies:
- If `data[1]` starts on the same day → FR-006a (same-day turnover)
- If `data[1]` starts on a different day → FR-006c (midnight boundary)
- If no `data[1]` → FR-006b (cleaning window)

### D-003: Keymaster Event Bus Integration

The keymaster integration fires `keymaster_lock_state_changed` events on the
HA event bus with data including `lockname`, `state`, and `code_slot_num`.

The listener is registered in `__init__.py` when `coordinator.lockname` is
configured. It filters events by:
1. `lockname` matches `coordinator.lockname`
2. `state` is `"unlocked"`
3. `code_slot_num != 0` (exclude manual/RF unlocks)
4. `code_slot_num` is in range `[start_slot, start_slot + max_events)`

When a matching event is detected, the listener checks whether the keymaster
monitoring switch is `on` and the sensor is in `awaiting_checkin`, then
triggers the check-in transition.

### D-004: Early Lock Code Expiry

When manual checkout occurs and the `EarlyCheckoutExpirySwitch` is `on`:
1. Look up the keymaster slot assigned to the current guest via
   `event_overrides.get_slot_key_by_name(slot_name)`
2. Compute `expiry_time = min(now + timedelta(minutes=15), original_event_end)`
3. Update the slot's date range end using the existing `add_call()` pattern
   from `util.py` (same approach as `async_fire_update_times()`)

This reuses the established Keymaster interaction patterns.

### D-005: Config Flow Changes

Only one new config option is added: `cleaning_window` (float, default 6.0
hours) in the options flow. This is minimal because the keymaster monitoring
and early checkout expiry controls are runtime toggle switches, not config
options — they can be changed instantly without integration reload.

The options flow schema in `config_flow.py` adds:
```python
vol.Optional(CONF_CLEANING_WINDOW, default=6.0): vol.All(
    vol.Coerce(float), vol.Range(min=0.5, max=48.0)
)
```

## References

- **Spec**: [specs/004-checkin-tracking/spec.md](spec.md)
- **Research**: [specs/004-checkin-tracking/research.md](research.md)
- **Data Model**: [specs/004-checkin-tracking/data-model.md](data-model.md)
- **Quickstart**: [specs/004-checkin-tracking/quickstart.md](quickstart.md)
- **Contracts**:
  - [contracts/checkout-service.md](contracts/checkout-service.md)
  - [contracts/events.md](contracts/events.md)
  - [contracts/switch-entities.md](contracts/switch-entities.md)
