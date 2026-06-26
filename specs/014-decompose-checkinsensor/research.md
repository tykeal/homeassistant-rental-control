<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Check-in Sensor

## R-001: Internal `sensors.checkin` Subpackage

**Decision**: Split the monolithic sensor into an HA-facing shell at
`custom_components/rental_control/sensors/checkinsensor.py` and internal helper
modules under `custom_components/rental_control/sensors/checkin/`:
`models.py`, `persistence.py`, `event_selection.py`,
`transition_decisions.py`, `restore_decisions.py`, `timers.py`, and
`applicator.py`.

**Rationale**:

- The live file is roughly 1,700 lines and mixes HA entity APIs, pure event
  selection, state-machine branches, restore reconciliation, stored-data
  serialization, timer scheduling, manual checkout, debug override, and
  Keymaster unlock handling.
- The entity class must remain the HA boundary for lifecycle, coordinator
  subscription, state writes, bus events, and services (FR-010). Keeping
  `checkinsensor.py` as the shell preserves the platform setup call in
  `custom_components/rental_control/sensor.py:68-72`.
- The source already contains separable groups: event identity/selection helpers
  (`_event_key`, `_get_relevant_event`, `_find_tracked_event`,
  `_find_followon_event`), transition helpers, four timer callbacks, and restore
  validation branches. Moving each group behind typed inputs makes them testable
  without constructing a full HA entity.
- An internal subpackage keeps the feature local to the check-in sensor and does
  not create public APIs for unrelated Rental Control modules.

**Alternatives considered**:

- **Keep all helpers in one smaller `checkinsensor.py`**: Rejected because the
  entity shell would still contain pure decision logic and is unlikely to stay
  below the 400-line file threshold.
- **Move helpers to top-level `rental_control/checkin.py`**: Rejected because the
  behavior is sensor-specific and should not look like an integration-wide public
  API.
- **Create many one-function modules**: Rejected because it increases import and
  review overhead without improving testability. The chosen modules map to
  cohesive concerns.

---

## R-002: Snapshot and Stored-Data Initializer

**Decision**: Introduce `CheckinStateSnapshot` as the single typed carrier for
logical sensor state and persisted fields. `CheckinExtraStoredData` moves to
`persistence.py`, adds a snapshot-based factory for new internal code, and keeps
the current keyword-field construction as a compatibility shim while continuing
to round-trip the exact existing dictionary shape.

**Rationale**:

- Ground truth on current `origin/main` shows `CheckinTrackingSensor.__init__`
  already has three parameters: `hass`, `coordinator`, and `config_entry`.
  Therefore no HA entity constructor change is required.
- The project-owned oversized initializer is `CheckinExtraStoredData.__init__`,
  which currently takes `state`, tracked event fields, sources, checkout time,
  transition target, checked-out key, follow-up day, and lock name. It is called
  from `extra_restore_state_data`, `from_dict`, and a direct persistence test.
- A snapshot dataclass lets transition decisions, restore decisions, persistence,
  and attributes share one explicit representation without copying twelve
  separate arguments through each extracted function. The compatibility shim
  lets existing tests instantiate `CheckinExtraStoredData(...)` with the current
  field keywords unchanged, satisfying FR-002.
- Keeping `from_dict()` and `as_dict()` stable preserves FR-007, SC-003, and
  older stored data. The warning behavior for invalid datetimes remains in the
  persistence module.

**Alternatives considered**:

- **Keep only the wide stored-data initializer**: Rejected because it violates
  the initializer parameter goal and encourages future helpers to pass long
  argument lists. A `snapshot` plus `**legacy_fields` signature keeps legacy
  callers working while keeping the project-owned signature small.
- **Persist a new nested snapshot schema**: Rejected because the spec requires
  field names, defaults, parsing, and optional fields to remain backward
  compatible.
- **Use an untyped dict everywhere**: Rejected because it weakens mypy coverage
  and makes decision tests less explicit.

---

## R-003: Ordered Decisions Plus Entity Applicator

**Decision**: Extract coordinator-update and restore logic as pure decision
functions returning ordered effect lists. Keep actual mutation, HA event firing,
timer scheduling, service calls, and `async_write_ha_state()` in the entity
shell/applicator.

**Rationale**:

- `_handle_coordinator_update()` currently branches by four states and performs
  multiple ordered side effects. Examples include far-future checked-in
  self-healing, which checks out first and then may immediately begin awaiting a
  different relevant event, and checked-in end-time drift, which cancels the old
  timer before scheduling the new one.
- `_validate_restored_state()` performs similar branches but must stay silent for
  catch-up transitions; it must not fire checkout/check-in HA bus events while
  reconciling restored state.
- Pure decisions allow focused tests to assert the selected transition, timer
  intent, log intent, and write intent without building HA fixtures for every
  scenario.
- Applying ordered effects from the shell preserves observable behavior: event
  payloads, writes, warnings, timer target updates, and cancellation order remain
  anchored in the entity.

**Alternatives considered**:

- **Move transition methods wholesale to a helper class that mutates the entity**:
  Rejected because it hides side effects and makes behavior parity harder to
  prove.
- **Return only a final state**: Rejected because final state is insufficient for
  bus events, warnings, timer cancellation, same-day turnover baselines, and
  restore-silent behavior.
- **Make decisions async**: Rejected because coordinator update is a hot path and
  the source logic is synchronous over in-memory coordinator data.

---

## R-004: Timer Manager with Existing Single-Handle Semantics

**Decision**: Extract timer scheduling into `CheckinTimerManager`, but preserve
the current single `_unsub_timer` model and `async_track_point_in_time()` helper.
The manager records one scheduled transition intent at a time and exposes
cancel-before-replace operations for auto-check-in, auto-checkout, linger, and
FR-006c follow-up timers.

**Rationale**:

- Current source has one `CALLBACK_TYPE | None` handle and clears it in each
  timer callback before state guards. That prevents stale callbacks from owning a
  live handle.
- All replacements must cancel the existing handle before storing the new handle:
  awaiting transitions, checked-in end-time reschedule, checked-out follow-on
  recompute, restore timer recreation, debug override, no-reservation reset, and
  entity removal.
- Same-day turnover, different-day midnight, cleaning-window, auto-check-in,
  auto-checkout, and restored follow-up target calculations are behavior, not
  infrastructure. The manager schedules the intent chosen by decision helpers;
  it does not choose new business rules.

**Alternatives considered**:

- **One handle per timer path**: Rejected because the source has a single active
  scheduled transition and the spec requires behavior-preserving cancellation
  semantics.
- **Use `async_call_later()` for relative delays**: Rejected because the source
  uses absolute `async_track_point_in_time()` targets; changing helpers could
  alter HA time-change test behavior.
- **Leave timer scheduling in transition branches**: Rejected because timer
  cancellation/replacement is one of the required independently testable areas.

---

## R-005: Compatibility Boundaries and Test Strategy

**Decision**: Preserve import and runtime compatibility at the entity boundary
while adding focused tests for extracted modules. Existing behavior tests remain
the primary parity oracle.

**Rationale**:

- Existing tests are extensive: `tests/unit/test_checkin_sensor.py` covers state
  transitions, persistence, restore, timer rescheduling, Keymaster unlocks,
  debug override clearing, event lookup, self-healing, and manual checkout;
  `tests/integration/test_checkin_tracking.py` covers same-day turnover,
  different-day follow-on, no-follow-on cleaning-window behavior, HA events,
  and full lifecycles.
- `tests/unit/test_keymaster_event_diagnostics.py` also constructs the sensor,
  and platform setup constructs it from `sensor.py`. Those call sites should not
  receive new required parameters.
- Re-exporting `CheckinExtraStoredData` from `checkinsensor.py` avoids changing
  imports while the class implementation moves.
- New tests should verify parity and extraction correctness; they must not define
  new runtime behavior, states, events, services, timing rules, or configuration.

**Alternatives considered**:

- **Rewrite all tests to target only new helpers**: Rejected because the spec
  requires existing check-in tests to pass unchanged after decomposition.
- **Rely only on existing entity tests**: Rejected because FR-004 through FR-006
  require independently testable state, restore, and timer decisions.
- **Change public service or event contracts to simplify extraction**: Rejected
  by non-goals and FR-001/FR-014.
