<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Fix Duplicate Keymaster Code Slot Assignment

**Input**: Design documents from `/specs/005-fix-duplicate-slot/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: Included — plan.md mandates unit tests for every new async method and integration tests for concurrency scenarios. Constitution check I confirms test coverage requirement.

**Organization**: Tasks are grouped by user story. The foundational phase builds the core lock/async infrastructure; user story phases wire it up, validate, and extend for each scenario.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- All file paths are relative to repository root

## Path Conventions

```text
custom_components/rental_control/
├── const.py                 # Constants
├── event_overrides.py       # PRIMARY: Lock, reserve, dedup, retry tracking
├── coordinator.py           # Adapt update_event_overrides() to async
├── util.py                  # Pre-verification, retry/escalation
└── sensors/
    └── calsensor.py         # Replace check-then-act with async reservation

tests/
├── unit/
│   ├── test_event_overrides.py  # Extended: lock, reserve, dedup tests
│   └── test_util.py             # Extended: pre-verification, escalation tests
└── integration/
    └── test_slot_concurrency.py # NEW: end-to-end concurrent slot tests
```

---

## Phase 1: Setup — Constants & Type Definitions

**Purpose**: Add new constants and the `ReserveResult` return type needed by all subsequent phases.

- [x] T001 Add `DEFAULT_MAX_RETRY_CYCLES = 3` constant to `custom_components/rental_control/const.py`
- [x] T002 [P] Define `ReserveResult` NamedTuple with fields `slot: int | None`, `is_new: bool`, `times_updated: bool` at module level in `custom_components/rental_control/event_overrides.py`

---

## Phase 2: Foundational — EventOverrides Lock & Core Async Methods

**Purpose**: Build the complete lock infrastructure and async mutation API on `EventOverrides`. All user story work depends on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete. All tasks are in `custom_components/rental_control/event_overrides.py` and must be executed sequentially.

- [x] T003 Add `_lock: asyncio.Lock`, `_retry_counts: dict[int, int]` (default `{}`), `_escalated: dict[int, bool]` (default `{}`), and `_slot_uids: dict[int, str | None]` (default `{}`) fields to `EventOverrides.__init__()` in `custom_components/rental_control/event_overrides.py`
- [x] T004 Implement `_find_overlapping_slot(self, slot_name: str, start_time: datetime, end_time: datetime, uid: str | None) -> int | None` private method that scans `_overrides` for any slot where `slot_name` matches AND time ranges overlap (`start_a < end_b and start_b < end_a`); for each candidate slot, look up its stored UID from `_slot_uids` and compare it to the incoming `uid` — if both are non-None and differ, skip that slot as a distinct reservation; return matching slot number or None in `custom_components/rental_control/event_overrides.py`
- [x] T005 Implement `async_reserve_or_get_slot(self, slot_name, slot_code, start_time, end_time, uid=None, prefix=None) -> ReserveResult`: acquire `_lock`, strip prefix from slot_name, call `_find_overlapping_slot`; if found and times differ update them and return `ReserveResult(slot, False, True)`, if found and times match return `ReserveResult(slot, False, False)`; if not found and `_next_slot` available write new `EventOverride` to `_next_slot` and call `__assign_next_slot()` then return `ReserveResult(new_slot, True, False)`; whenever a concrete slot (existing or new) is returned and `uid` is not None, update the runtime UID tiebreaker map `_slot_uids[slot] = uid`; if no slot available log overflow warning (FR-010), do not modify `_slot_uids`, and return `ReserveResult(None, False, False)`; release lock in `custom_components/rental_control/event_overrides.py`
- [x] T006 Implement `async_update(self, slot, slot_code, slot_name, start_time, end_time, prefix=None) -> None`: acquire `_lock`, strip prefix, if `slot_name` is non-empty scan other slots for duplicate name+overlap via `_find_overlapping_slot`; if duplicate found redirect write to existing slot and log warning (FR-004); otherwise write `EventOverride` to target slot (or `None` if clearing); whenever a slot is cleared, also clear any associated UID from the `_slot_uids` map (e.g., `_slot_uids.pop(slot, None)`); call `__assign_next_slot()`; release lock in `custom_components/rental_control/event_overrides.py`
- [x] T007 Implement `verify_slot_ownership(self, slot: int, expected_name: str) -> bool` read-only method (no lock) that returns `True` if `_overrides[slot]` is not None and `slot_name` matches `expected_name` in `custom_components/rental_control/event_overrides.py`
- [x] T008 Implement `record_retry_failure(self, slot: int) -> bool` that increments `_retry_counts[slot]` and returns `True` if count reaches `DEFAULT_MAX_RETRY_CYCLES` and `_escalated[slot]` is not yet True (then sets it True); implement `record_retry_success(self, slot: int) -> None` that resets `_retry_counts[slot]` to 0 and `_escalated[slot]` to False in `custom_components/rental_control/event_overrides.py`
- [x] T009 Update existing `async_check_overrides()` to acquire `self._lock` for the entire check-and-clear iteration; on `async_fire_clear_code()` failure keep slot occupied (do not write None to `_overrides`) per FR-012 in `custom_components/rental_control/event_overrides.py`
- [x] T010 Add docstring warning to existing sync `update()` method: "WARNING: Bootstrap-only. Must not be called after listeners are registered. Use async_update() for post-bootstrap mutations." in `custom_components/rental_control/event_overrides.py`

**Checkpoint**: EventOverrides API complete — `async_reserve_or_get_slot`, `async_update`, `verify_slot_ownership`, `record_retry_*`, and lock-protected `async_check_overrides` are all available for callers.

---

## Phase 3: User Story 1 — Concurrent Reservations Get Unique Slots (Priority: P1) 🎯 MVP

**Goal**: Each guest receives exactly one code slot during concurrent calendar processing. No slot sharing, no overwrites.

**Independent Test**: Trigger a calendar refresh containing 2+ new reservations; verify each guest name appears in exactly one slot across the managed range.

### Tests for User Story 1

- [ ] T011 [US1] Write unit tests for `async_reserve_or_get_slot()` new-reservation path: single reservation gets slot, two sequential reservations get different slots, verify `_next_slot` recalculated after each in `tests/unit/test_event_overrides.py`
- [ ] T012 [US1] Write unit tests for `_find_overlapping_slot()`: same name + overlapping times returns existing slot; different name returns None; same name + non-overlapping times returns None (back-to-back stays) in `tests/unit/test_event_overrides.py`
- [ ] T013 [US1] Write unit tests for slot overflow: all slots occupied → `async_reserve_or_get_slot()` returns `ReserveResult(None, False, False)` and logs warning in `tests/unit/test_event_overrides.py`

### Implementation for User Story 1

- [ ] T014 [P] [US1] Implement `async _async_handle_slot_assignment(self)` coroutine in `RentalControlCalSensor`: extract slot_name/code/times/uid from current event, call `await overrides.async_reserve_or_get_slot(...)`, if `result.is_new` call `await async_fire_set_code()`, if `result.times_updated` call `await async_fire_update_times()`, if `result.slot is None` return silently in `custom_components/rental_control/sensors/calsensor.py`
- [ ] T015 [US1] Refactor `_handle_coordinator_update()` to remove direct `next_slot` reads and `get_slot_with_name()` slot-assignment decisions; instead extract event data and schedule `self.hass.async_create_task(self._async_handle_slot_assignment())` for slot operations in `custom_components/rental_control/sensors/calsensor.py`

**Checkpoint**: Core race condition fix is functional. Concurrent sensors serialize through the lock; each guest gets a unique slot.

---

## Phase 4: User Story 2 — Idempotent Reservation Updates (Priority: P2)

**Goal**: Re-delivered reservations with changed times update the existing slot's time range. Identical re-deliveries are no-ops.

**Independent Test**: Assign guest to slot, re-deliver same guest with different times, verify original slot updated (not duplicated). Re-deliver with identical times, verify zero modifications.

### Tests for User Story 2

- [x] T016 [US2] Write unit tests for time-update path: guest in slot 10 with Mon–Fri, reserve again with Mon–Sat → `ReserveResult(10, False, True)` and stored times updated in `tests/unit/test_event_overrides.py`
- [x] T017 [US2] Write unit tests for identical-reservation no-op: guest in slot 10 with Mon–Fri, reserve again with Mon–Fri → `ReserveResult(10, False, False)` and no state changes in `tests/unit/test_event_overrides.py`

### Implementation for User Story 2

- [x] T018 [P] [US2] Adapt `update_event_overrides()` to be async and call `await self.event_overrides.async_update()` instead of sync `self.event_overrides.update()`; update all callers of `update_event_overrides()` (e.g., `handle_state_change` listener in `util.py`) to await it in `custom_components/rental_control/coordinator.py`

**Checkpoint**: Time updates and re-deliveries handled idempotently. Coordinator state-change path uses async_update.

---

## Phase 5: User Story 3 — Slot Cleanup After Checkout (Priority: P3)

**Goal**: Expired slots are cleared safely under concurrency. Failed lock commands retry on subsequent coordinator cycles with persistent_notification escalation after 3 failures.

**Independent Test**: Assign guest to slot, advance time past checkout, verify full clear (name, code, times, lock command sent). Simulate clear-code failure, verify retry on next cycle and escalation after threshold.

### Tests for User Story 3

- [ ] T019 [P] [US3] Write unit tests for `verify_slot_ownership()` — match returns True, mismatch returns False, empty slot returns False; write unit tests for `async_check_overrides()` under lock — expired slot cleared with `async_fire_clear_code()`, clear-failure keeps slot occupied in `tests/unit/test_event_overrides.py`
- [ ] T020 [P] [US3] Write unit tests for pre-execution verification abort (ownership mismatch → operation aborted, logged) and retry/escalation (3 failures → `record_retry_failure` returns True → `persistent_notification` created; success → counter reset, notification dismissed) in `tests/unit/test_util.py`

### Implementation for User Story 3

- [ ] T021 [P] [US3] Add pre-execution slot verification at the start of `async_fire_set_code()`, `async_fire_clear_code()`, and `async_fire_update_times()`: call `coordinator.event_overrides.verify_slot_ownership(slot, expected_name)`, if False log warning and return early without executing Keymaster commands in `custom_components/rental_control/util.py`
- [ ] T022 [US3] Add try/except around Keymaster service calls in `async_fire_set_code()` and `async_fire_clear_code()`: on success call `record_retry_success(slot)` and if `_escalated[slot]` was True call `persistent_notification.async_dismiss(hass, notification_id=...)` to clear the escalation notification; on failure call `record_retry_failure(slot)` and if it returns True create `persistent_notification.async_create()` with stable `notification_id` (`rental_control_slot_{slot}_failure` / `rental_control_slot_{slot}_clear_failure`); do NOT release slot on failure; re-raise the exception after recording retry failure so `async_check_overrides` can detect the failure and keep the slot occupied (cross-ref T009) in `custom_components/rental_control/util.py`

**Checkpoint**: Cleanup, pre-verification, and retry/escalation fully functional. Failed lock commands retry automatically with admin notification.

---

## Phase 6: User Story 4 — Duplicate Prevention as Last Line of Defense (Priority: P4)

**Goal**: Storage layer rejects any write that would create duplicate name+overlap entries, regardless of how the request originated.

**Independent Test**: Directly invoke `async_update()` targeting a different slot with a name+overlap that already exists in another slot; verify redirect to existing slot with warning logged.

### Tests for User Story 4

> Note: The dedup enforcement implementation is in T006 (foundational phase). This phase validates it exhaustively.

- [x] T023 [US4] Write unit tests for dedup redirect: guest "Alice" in slot 3 Mon–Fri, `async_update()` attempts "Alice" in slot 5 Wed–Sun → slot 3 times updated to Mon–Sun, slot 5 unchanged, warning logged in `tests/unit/test_event_overrides.py`
- [x] T024 [US4] Write unit tests for back-to-back stays: guest "Alice" in slot 3 Mon–Fri, `async_update()` writes "Alice" to slot 5 following Mon–Fri → both slots active, no warning (non-overlapping times = distinct reservations) in `tests/unit/test_event_overrides.py`
- [x] T025 [US4] Write unit tests for UID tiebreaker: "Alice" in slot 3 Mon–Fri uid="AAA", `async_reserve_or_get_slot()` with "Alice" Mon–Fri uid="BBB" → new slot reserved (different UIDs prove distinct reservations despite name+overlap) in `tests/unit/test_event_overrides.py`

**Checkpoint**: Storage-layer identity invariant exhaustively validated. Defense-in-depth confirmed.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end integration tests, quality gates, and compliance verification.

- [ ] T026 Create `tests/integration/test_slot_concurrency.py` with test fixtures (mock coordinator, mock EventOverrides with slots, mock Keymaster service calls) and shared helpers
- [ ] T027 Write concurrent-reservation integration test: simulate 10 sensors scheduling `_async_handle_slot_assignment()` near-simultaneously with 5 managed slots, verify the first 5 each get a unique slot and the remaining 5 hit overflow gracefully with zero duplicates or overwrites (validates SC-001 and partially SC-004) in `tests/integration/test_slot_concurrency.py`
- [ ] T028 Write idempotent re-delivery integration test: assign guest then re-deliver same event with changed times and identical times, verify update-then-noop behavior end-to-end (validates SC-002/SC-003) in `tests/integration/test_slot_concurrency.py`
- [ ] T029 Write cleanup-during-assignment integration test: schedule slot clear and new assignment concurrently, verify both complete correctly with no cross-contamination (validates SC-005) in `tests/integration/test_slot_concurrency.py`
- [ ] T030 Write overflow integration test: fill all managed slots then attempt additional reservation, verify graceful handling with zero overwrites (validates SC-004) in `tests/integration/test_slot_concurrency.py`
- [ ] T030b Write dedup-rejection integration test: pre-populate slot 3 with "Alice" Mon–Fri, then invoke `async_update()` targeting slot 5 with "Alice" Wed–Sun, verify redirect to slot 3 with updated times and warning logged end-to-end (validates SC-006) in `tests/integration/test_slot_concurrency.py`
- [ ] T030c Write single-reservation regression test: assign one guest to one slot, update times, then clear — verify full lifecycle works identically to pre-fix behavior end-to-end (validates SC-007) in `tests/integration/test_slot_concurrency.py`
- [ ] T031 Run full test suite with `uv run pytest tests/ -v` and fix any failures
- [ ] T032 [P] Run pre-commit hooks (`ruff`, `mypy`, `interrogate`, `reuse-tool`, `gitlint`) on all modified files and fix issues
- [ ] T033 [P] Verify SPDX-FileCopyrightText and SPDX-License-Identifier headers on all new and modified files per REUSE.toml and project constitution

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1: Setup ──────────────────────────┐
                                         ▼
Phase 2: Foundational ──── BLOCKS ALL ───┤
                                         │
         ┌───────────────────────────────┤
         │           │          │        │
         ▼           ▼          ▼        ▼
Phase 3: US1   Phase 4: US2  Phase 5: US3  Phase 6: US4
  (P1 MVP)       (P2)         (P3)          (P4)
         │           │          │            │
         └───────────┴──────────┴────────────┘
                                         │
                                         ▼
                              Phase 7: Polish
```

### User Story Dependencies

- **US1 (P1)**: Depends only on Foundational (Phase 2). No dependencies on other stories. **This is the MVP.**
- **US2 (P2)**: Depends only on Foundational (Phase 2). Independent of US1. Adapts coordinator caller path.
- **US3 (P3)**: Depends only on Foundational (Phase 2). Independent of US1/US2. Adds pre-verification and retry to util.py.
- **US4 (P4)**: Depends only on Foundational (Phase 2). Independent of US1/US2/US3. Primarily test-driven validation of T006.

### Cross-Story Integration Note

While user stories are independently implementable, the **recommended order is P1 → P2 → P3 → P4** because:
- US1 (calsensor adaptation) exercises the reserve path end-to-end first
- US2 (coordinator adaptation) completes the async migration of the remaining sync caller
- US3 (util.py changes) adds defense layers on top of the working reservation flow
- US4 (tests only) validates the foundational dedup after all callers are adapted

### Within Each User Story

1. Tests written first to validate the foundational methods for that story's scenarios
2. Implementation wires up callers to consume the foundational API
3. Checkpoint: story independently testable before proceeding

---

## Parallel Opportunities

### Phase 1: Setup

```
Parallel group:
  T001: Add DEFAULT_MAX_RETRY_CYCLES to const.py
  T002: Define ReserveResult in event_overrides.py
```

### Phase 3: US1

```
Parallel group (tests ∥ implementation in different files):
  T011–T013: Unit tests in tests/unit/test_event_overrides.py
  T014:      _async_handle_slot_assignment() in sensors/calsensor.py
```

### Phase 4: US2

```
Parallel group:
  T016–T017: Unit tests in tests/unit/test_event_overrides.py
  T018:      update_event_overrides() async adaptation in coordinator.py
```

### Phase 5: US3

```
Parallel group (three different files):
  T019: Unit tests in tests/unit/test_event_overrides.py
  T020: Unit tests in tests/unit/test_util.py
  T021: Pre-verification in custom_components/rental_control/util.py
```

### Phase 7: Polish

```
Parallel group (after T031 completes):
  T032: Pre-commit hooks
  T033: SPDX header verification
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (2 tasks, ~5 min)
2. Complete Phase 2: Foundational (8 tasks — this is the bulk of the work)
3. Complete Phase 3: User Story 1 (5 tasks — wires up calsensor + validates core fix)
4. **STOP and VALIDATE**: Run `uv run pytest tests/unit/test_event_overrides.py -v`
5. The core race condition is now fixed — concurrent sensors serialize through the lock

### Incremental Delivery

1. Setup + Foundational → Lock infrastructure ready
2. US1 → Concurrent reservations fixed → **MVP deployed** ✅
3. US2 → Idempotent time updates → coordinator path async
4. US3 → Pre-verification + retry/escalation → defense-in-depth complete
5. US4 → Storage-layer invariant validated → all 4 defense layers confirmed
6. Polish → Integration tests + quality gates → production ready

### Atomic Commit Strategy (per quickstart.md)

Each phase maps to one or more atomic commits per the project constitution:

| Commit | Content | Phase |
|--------|---------|-------|
| 1 | `const.py` — constants | Phase 1 |
| 2 | `event_overrides.py` — lock, reserve, dedup, retry, verify | Phase 2 |
| 3 | `tests/unit/test_event_overrides.py` — core method tests | Phase 3 (tests) + Phase 4 (tests) + Phase 6 |
| 4 | `coordinator.py` — async_update adaptation | Phase 4 (impl) |
| 5 | `util.py` — pre-verification + retry/escalation | Phase 5 (impl) |
| 6 | `tests/unit/test_util.py` — util tests | Phase 5 (tests) |
| 7 | `sensors/calsensor.py` — async reservation dispatch | Phase 3 (impl) |
| 8 | `tests/integration/test_slot_concurrency.py` — e2e tests | Phase 7 |

---

## Notes

- All tasks target existing files except `tests/integration/test_slot_concurrency.py` (new)
- The sync `update()` method is retained for bootstrap — only async paths use the lock
- `_find_overlapping_slot()` uses strict interval overlap: `start_a < end_b AND start_b < end_a`
- `verify_slot_ownership()` is intentionally lock-free (read-only, stale reads safe per research.md R-006)
- `record_retry_failure()`/`record_retry_success()` are lock-free (single-threaded asyncio, no await between read/write)
- Persistent notifications use stable `notification_id` per slot to prevent duplicates
- All new code requires type hints, 100% docstring coverage (interrogate), and SPDX headers
