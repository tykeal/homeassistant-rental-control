<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Shared Door Code Allocator with Persisted Registry

**Input**: Design documents from `specs/022-shared-code-allocator/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md),
[research.md](research.md), [data-model.md](data-model.md),
[contracts/code-registry-store.md](contracts/code-registry-store.md),
[quickstart.md](quickstart.md)
**Feature branch (implementation)**: `022-shared-code-allocator`
**Issue**: #743

**Tests**: Test tasks are included and are mandatory. The spec's success
criteria are behavioural, the plan names a required hazard-1 regression test,
and the repository enforces a 95% coverage floor, so new modules need tests in
the same commit as the code they cover.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: May run in parallel — different files, no dependency on an
  incomplete task. Tasks touching the same file are never marked `[P]`.
- **[Story]**: The user story the task serves (US1-US4). Setup, Foundational,
  and Polish tasks carry no story label.
- **⚠️ ATOMIC**: Tasks sharing an atomic-commit group MUST be committed
  together in one commit. Shipping them separately leaves a known-unsafe tree.

## Validation commands

```bash
uv run pytest tests/ -q -p no:randomly
uv run ruff check custom_components/ tests/
pre-commit run --all-files
```

Every new Python file needs the SPDX header pair. Known flake, re-run in
isolation before treating it as a regression:
`tests/integration/test_refresh_cycle.py::test_missing_store_adopts_coded_slots_when_unavailable_at_setup`.

## Phase ordering note

All of US1, US2, and US3 are priority P1. Within that tier the phases are
ordered by safety dependency rather than by spec listing order:

- **US3 (adoption) precedes US1 (issuance)** because FR-020 requires adoption
  to complete for all loaded entries before any new code is issued. Building
  issuance first would leave an intermediate tree in which the adoption gate
  opens on a no-op adopt phase and a code physically present on a lock could be
  handed to another reservation.
- **US2 (sensor parity) follows US1** because the sensor can only display an
  allocated code once one exists.
- **US4 (persistence, release, orphans) is P2 and last**, and its release and
  cleanup behaviour depends on `AllocationOwner.lockname`/`slot` and
  `CycleObservation` from the foundational phase and on adoption from US3.

---

## Phase 1: Setup

**Purpose**: Establish the behaviour oracle and the shared constants and
package the rest of the feature hangs off.

- [ ] T001 Run and record the pre-change baseline from `tests/` with
      `uv run pytest tests/ -q -p no:randomly` and
      `uv run ruff check custom_components/ tests/`, and confirm the known flake
      in `tests/integration/test_refresh_cycle.py` passes in isolation, per
      [quickstart.md](quickstart.md) §0
- [ ] T002 Add `ALLOCATOR`, `STORE_CODE_REGISTRY_KEY =
      "rental_control.code_registry"`, and `CODE_REGISTRY_SCHEMA_VERSION = 1`
      to `custom_components/rental_control/const.py` (contract §1, plan
      "Storage")
- [ ] T003 Create the package entry point
      `custom_components/rental_control/allocator/__init__.py` with SPDX
      headers, exporting `DoorCodeAllocator`,
      `async_get_or_create_allocator`, and `get_allocator` (plan "Source Code",
      contract §2)

**Checkpoint**: Constants resolve, the package imports, and the baseline suite
is green.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The pure registry core, its persistence, the singleton lifecycle,
and the reconciliation fail-closed guards. **No user story work may begin until
this phase is complete** — in particular the registry and its store round-trip
must land before anything consumes them, and `slot_code` may not become
optional without the guards that make an absent code safe.

### 2a. Pure core (no Home Assistant imports)

- [ ] T004 [P] Implement the pure dataclasses in
      `custom_components/rental_control/allocator/models.py`:
      `AllocationOrigin`, `AllocationOwner`, `AllocationRecord`,
      `AllocationRequest`, `AllocationResult`, `AdoptionRequest`,
      `CycleObservation`, `CycleRequest`, `CycleResult`, `ReleaseReport`,
      `OrphanCleanupReport`, `OrphanOutcome`, including
      `lockname`/`slot`/`lock_observed` on the owner and
      `previously_published`/`issuance_allowed` on the request
      (data-model "New entities", contract §2)
- [ ] T005 [P] Implement the deterministic affine candidate walk in
      `custom_components/rental_control/allocator/candidates.py`:
      `seed = sha256(identity_key)`, `start = seed % n`, an odd, non-multiple-of-5
      `step` coprime to `n = 10 ** code_length`, zero-padded output (FR-011,
      plan decision 3)
- [ ] T006 [P] Add `tests/unit/test_allocator_candidates.py` asserting
      full-cycle non-repetition for lengths 4 and 6, determinism for a fixed
      identity across runs, and stability when the registry changes around it
      (FR-011, quickstart §1)
- [ ] T007 Implement the pure registry in
      `custom_components/rental_control/allocator/registry.py`: plain-code
      `records` keyed by code, derived `by_identity` index, owner add/remove,
      conflict detection when a code gains a second owner, `code_length`
      filtering, and availability rules (FR-005, FR-007, FR-012, FR-022,
      data-model "AllocationRegistry")
- [ ] T008 [P] Add `tests/unit/test_allocator_registry.py` covering
      allocate/lookup/release, idempotent re-request, conflict recording making
      a code unavailable, `code_length` filtering, and rejection of malformed
      inputs (FR-005, FR-007, FR-012, FR-022)

### 2b. Persistence

**⚠️ ATOMIC group A** — T009 and T010 ship as one commit. A store without its
round-trip and no-plaintext assertions is not a store this feature can rely on.

- [ ] T009 Implement `custom_components/rental_control/allocator/store.py`:
      `Store` at `rental_control.code_registry` schema version 1,
      `encode_code`/`decode_code` (salted base64, salt =
      `encoding_salt_value`), `code_ref_salt` generation, full payload
      validation per the contract's validation list, empty-registry-plus-warning
      on absence/corruption, `ConfigEntryNotReady` on storage I/O failure, and
      `async_delay_save` (FR-016, FR-018, FR-025, contract §1) — **⚠️ ATOMIC A**
- [ ] T010 Add `tests/unit/test_allocator_store.py`: exact encode/decode
      round-trip including leading zeros, per-field assertion that no serialized
      field is or contains a plaintext code, missing/unreadable/wrong-version/
      corrupt payload each yielding an empty registry plus a warning, two
      records decoding to the same code rejected, a duplicate `identity_key`
      rejected, and delayed save used (FR-018, contract §1, quickstart §6) —
      **⚠️ ATOMIC A**

### 2c. Allocator object and singleton lifecycle

- [ ] T011 Implement the `DoorCodeAllocator` shell in
      `custom_components/rental_control/allocator/allocator.py`: `hass`,
      `_registry`, `_store`, non-reentrant `_lock`, `_pending_adoption`,
      `_gate_deadline`, `_registry_lost`, `async_load`,
      `async_register_entry`, `async_unregister_entry`, the `code_ref` helper,
      and the `diagnostics` property, with no method emitting a reconciliation
      action or calling a lock service (FR-001, FR-002, FR-006, FR-018, FR-025,
      contract §2)
- [ ] T012 Implement `custom_components/rental_control/allocator/singleton.py`:
      `async_get_or_create_allocator` and `get_allocator` using a module-level
      `_CREATE_LOCK`, a re-checked presence test inside the lock, and
      `hass.data[DOMAIN].pop(ALLOCATOR, None)` on creation failure (FR-001,
      FR-002, plan decision 1, quickstart §2)
- [ ] T013 [P] Add `tests/unit/test_allocator_singleton.py`: a second entry
      reuses the first allocator, creation failure pops the key and reports the
      entry not ready, unloading one entry of two leaves the allocator and the
      other entry's allocations intact, and an entry that fails setup is
      unregistered from the adoption gate (FR-001, FR-002, FR-003)
- [ ] T014 Wire the allocator into
      `custom_components/rental_control/__init__.py`: create it immediately
      after `hass.data[DOMAIN]` is ensured and before
      `coordinator.async_load_slot_store()`, call
      `async_register_entry(entry_id)` there, and call
      `async_unregister_entry(entry_id)` on the `ConfigEntryNotReady` raise, on
      any later exception in `async_setup_entry`, and in `async_unload_entry`
      while the entry is still pending; `async_unload_entry` otherwise leaves
      the allocator and registry untouched (FR-001, FR-002, FR-003, plan
      decision 1)

### 2d. Fail-closed reconciliation guards

**⚠️ ATOMIC group B** — T015 through T020 ship as ONE commit. Making
`slot_code` optional without the guards preserves today's unsafe behaviour:
`slot_code` is never persisted (`reconciliation/plan_models.py:31`, #736), so a
ghost reservation cannot restore the real code. If that missing code is modeled
as the current `""` sentinel, it compares unequal to every real PIN at
`reconciliation/actions.py:35` and yields `OVERWRITE_MANUAL_CHANGE`, which is
the guest-lockout path. These tasks fix that existing defect and must never be
merged independently of each other.

- [ ] T015 Change `Reservation.slot_code` to `str | None` in
      `custom_components/rental_control/reconciliation/plan_models.py`, extend
      `code_source` with `"allocated"`, `"collision_resolved"`, `"adopted"`,
      and `"unallocated"` (`AllocationOrigin.PREFERRED` → `"allocated"`,
      `COLLISION_RESOLVED` → `"collision_resolved"`, `ADOPTED` → `"adopted"`),
      add the `"code_unavailable"` overflow reason, replace
      the superseded #736 "never written to the HA Store" docstring note, and
      change ghost reservations from `""` to `None` in
      `custom_components/rental_control/coordinator_helpers/ghost_reservations.py`
      (data-model "Changed entities", FR-018) — **⚠️ ATOMIC B**
- [ ] T016 Guard `classify_matched_desired_slot` in
      `custom_components/rental_control/reconciliation/actions.py` to return
      `(ActionKind.NOOP, "code_unavailable")` when `desired_res.slot_code is
      None`, **before** the `code_drift` computation at line 35 (FR-018,
      hazard 1 guard 1) — **⚠️ ATOMIC B**
- [ ] T017 Guard `_classify_slot` and `assign_unmatched_reservations` in
      `custom_components/rental_control/reconciliation/desired.py`: a slot with
      a non-`None` `desired_identity_key` may never be classified `stale`,
      `phantom`, `mis_assigned`, or `duplicate_non_canonical`; a codeless
      reservation is never assigned to a `FREE` slot and is recorded as
      `plan.overflow[key] = "code_unavailable"`; capacity filtering retains an
      already-occupied slot for its codeless reservation (FR-018, hazard 1
      guards 2 and 3) — **⚠️ ATOMIC B**
- [ ] T018 Add the `DesiredPlan.validate` invariant in
      `custom_components/rental_control/reconciliation/plan_models.py` rejecting
      `SET`, `OVERWRITE_MANUAL_CHANGE`, and `UPDATE_TIMES` for a codeless
      reservation (threading `reservation_by_identity` into validation), and
      make `async_apply_plan` in
      `custom_components/rental_control/event_overrides.py` skip such an action
      defensively (FR-018, hazard 1 guard 4) — **⚠️ ATOMIC B**
- [ ] T019 Add the mandatory hazard-1 regression in
      `tests/integration/test_allocator_failclosed.py`: an occupied slot holding
      a working code matched to a reservation with `slot_code is None` produces
      no `CLEAR`, `RESET`, `OVERWRITE_MANUAL_CHANGE`, or `SET` for that slot and
      leaves the physical code unchanged, asserted for both readable and
      unreadable observations (FR-018, hazard 1, plan "Test strategy") —
      **⚠️ ATOMIC B**
- [ ] T020 Update the existing planner and reconciliation suites under
      `tests/unit/` and `tests/integration/` that assume `slot_code: str` so
      they construct and assert against the optional field without weakening
      any existing assertion (plan "Test strategy") — **⚠️ ATOMIC B**

**Checkpoint**: The registry persists and round-trips, exactly one allocator
exists per system, and an absent code holds a slot instead of clearing it.
User story work may now begin.

---

## Phase 3: User Story 3 - Existing Guests' Codes Do Not Change (Priority: P1)

**Goal**: Upgrading adopts every code already observed on a managed lock into
the registry, so no in-flight guest code is rotated, and adoption completes
before any new code can be issued.

**Independent Test**: Populate locks with codes for active reservations, start
from an empty registry, run one reconciliation cycle, and confirm no code
changed and each observed code now appears in the registry owned by the
reservation holding it.

- [ ] T021 [US3] Implement `async_adopt` and its private non-locking helper in
      `custom_components/rental_control/allocator/allocator.py`: record an
      observed code with its `lockname`/`slot`/`lock_observed`, treat an owner
      matched through `fingerprint_history` as the same owner before conflict
      detection, and record a second distinct identity as a conflict owner that
      is reported and retained without rotation (FR-020, FR-021, FR-022,
      contract §2)
- [ ] T022 [US3] Implement the adoption gate in
      `custom_components/rental_control/allocator/allocator.py`: seed
      `_pending_adoption` from `hass.config_entries.async_entries(DOMAIN)` at
      creation, drain an entry on a completed pass or on failure/disable/unload/
      removal, suppress **issuance only** while the set is non-empty returning
      `reason="adoption_pending"`, and warn plus notify at `_gate_deadline`
      without opening the gate (FR-020, FR-018, hazard 2)
- [ ] T023 [US3] Create
      `custom_components/rental_control/coordinator_helpers/code_allocation.py`
      with `async_resolve_codes`: build the entry's `CycleObservation` from what
      `keymaster_observation.py` already produced (`managed_slots`,
      `observed_codes`, `unreadable_slots` containing all slots whose observed
      `status is SlotStatus.UNKNOWN`, including missing/unavailable entity state
      and `blocked_reason="unreadable"`; known-empty `SlotStatus.FREE` slots are
      not unreadable), build `AdoptionRequest`s from
      `_resolve_observed_code` results in
      `coordinator_helpers/reservations.py`, and call the allocator through the
      adopt path only for the safe Phase 3 checkpoint (FR-020, contract §2, plan
      decision 2)
- [ ] T024 [US3] Splice the step into
      `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`
      `_run_reconciliation`, between `_apply_checkin_protection` and
      `compute_desired_plan`, hydrating `fingerprint_history`, `missing_count`,
      and ghost reservations from the persisted mapping store first, treating
      `published_once` as absent/`False` until T031 records it durably, and
      holding any reservation that was not adopted this pass at
      `slot_code=None`/`code_source="unallocated"` so the planner cannot emit a
      generated `SET` before T029 consumes allocator results, while preserving
      the existing cycle-skipping `try/except`
      (FR-020, plan decision 2)
- [ ] T025 [P] [US3] Add adoption coverage to
      `tests/unit/test_code_allocation_step.py`: adopt-before-allocate ordering
      within a cycle, idempotency on a repeated request, adoption taking
      precedence over the generator's preferred code, and
      `fingerprint_history` matching not self-conflicting (FR-020, FR-021)
- [ ] T026 [P] [US3] Add `tests/integration/test_allocator_adoption.py`: an
      empty registry with coded slots adopts every code and rotates none
      (SC-004), a pre-existing duplicate records both owners, reports the
      conflict to the operator (SC-008), and leaves the code unavailable to new
      allocations (FR-022),
      and a missing or unreadable registry warns and rebuilds by adoption
      (FR-018)

**Checkpoint**: An upgrade adopts in-flight codes and rotates nothing. Issuance
is still gated shut, which is the safe intermediate state.

---

## Phase 4: User Story 1 - No Two Guests Share a Door Code (Priority: P1) 🎯 MVP

**Goal**: Every code a reservation receives is issued by the shared allocator
against the registry, so no two concurrent reservations anywhere on the system
hold the same code, for all three generators.

**Independent Test**: Configure two entries whose reservations have identical
start and end dates under the default `date_based` generator, run a
reconciliation cycle for each, and confirm the two reservations hold different
codes and both codes are recorded in the shared registry.

- [ ] T027 [US1] Implement `async_allocate` and its private non-locking helper
      in `custom_components/rental_control/allocator/allocator.py`: return an
      existing allocation unchanged, else the preferred code when free or
      already self-owned, else the first free candidate from the affine walk,
      else `reason="exhausted"` after all `n` candidates are rejected (FR-005,
      FR-007, FR-008, FR-009, FR-010, FR-011)
- [ ] T028 [US1] Implement `async_resolve_cycle` in
      `custom_components/rental_control/allocator/allocator.py`: acquire `_lock`
      once for the whole Phase 4 cycle, convert T023's adoption-only call site
      to this batch entrypoint, drive adopt → allocate through private helpers
      only (never the public phase methods), derive `unaccounted_slots` from the
      `CycleObservation`, and set `issuance_allowed` from it so an entry with
      unaccounted unreadable slots returns `reason="unaccounted_slots"`; T040 and
      T041 later add the rekey and sweep phases (FR-006, FR-018, contract §2)
- [ ] T029 [US1] Consume the results in
      `custom_components/rental_control/coordinator_helpers/code_allocation.py`:
      build `AllocationRequest`s sorted by `identity_key` with the planned
      `lockname`/`slot`, set `Reservation.slot_code` and `code_source` from each
      result (`PREFERRED` → `"allocated"`, `COLLISION_RESOLVED` →
      `"collision_resolved"`, `ADOPTED` → `"adopted"`), notify only for
      `exhausted`, and log `unaccounted_slots` as warn-only once per cycle per
      entry using `code_ref` only (FR-008, FR-025, SC-008 operator surfacing,
      contract "AllocationResult.reason values")
- [ ] T030 [US1] Add the lockless branch for `event_overrides is None` in
      `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`
      `_async_update_data`: build reservations with `managed_slots=None`
      including ghost reservations, missing-count state, and the durable
      `published_once` flag from T031, run allocation only until T040 and T041
      add rekey and sweep, set `_latest_res_by_key`, compute no plan, emit no
      action, and call no service (FR-023)
- [ ] T031 [US1] Implement the `recovery_fail_closed` path across
      `custom_components/rental_control/allocator/allocator.py` and
      `custom_components/rental_control/coordinator_helpers/store_sync.py`:
      record a durable `published_once` flag in the per-entry cache when a code
      is exposed, carry it as `previously_published` on the request, and hold a
      previously published lockless reservation at
      `code_source="unallocated"` rather than issuing a replacement when
      `_registry_lost` is set (FR-018, plan "Backwards compatibility")
- [ ] T032 [P] [US1] Add allocation coverage to
      `tests/unit/test_code_allocation_step.py`: idempotent repeat allocation,
      collision resolution determinism, feed-order independence via the
      `identity_key` sort, exhaustion reported once per cycle,
      `unaccounted_slots` suppression, and `recovery_fail_closed` (FR-005 to
      FR-011, FR-018)
- [ ] T033 [P] [US1] Add `tests/integration/test_allocator_cross_entry.py`: two
      entries with identical dates under `date_based` receive distinct codes
      with both recorded (SC-001), the same holds for `static_random` and
      `last_four`, and the full code space stays available to every entry
      (SC-002)
- [ ] T034 [US1] Add the options-flow validation guard in
      `custom_components/rental_control/config_flow_helpers/validation.py`
      rejecting a code-length change while the entry holds active allocations,
      with a test, and introducing no new operator-supplied option (FR-007,
      FR-012, FR-024, SC-007, plan "Backwards compatibility")

**Checkpoint**: MVP. Cross-entry uniqueness holds for all three generators, and
existing codes are still adopted rather than rotated.

---

## Phase 5: User Story 2 - The Sensor and the Lock Agree (Priority: P1)

**Goal**: The calendar sensor displays exactly the allocator's code and
generates nothing of its own; when no allocation exists it reports no code.

**Independent Test**: Force a collision so a reservation receives a resolved
code differing from its generator's preferred code, then read the sensor's
`slot_code` attribute and confirm it matches the allocated code.

- [ ] T035 [US2] Delete `_generate_door_code` and the `slot_code is None`
      backfill in `_handle_event_update`, along with the now-unused
      `DoorCodeRequest` construction, in
      `custom_components/rental_control/sensors/calsensor.py` (FR-019, plan
      decision 5) — **⚠️ ATOMIC C**
- [ ] T036 [US2] Replace the `event_overrides_present` gate in
      `custom_components/rental_control/sensors/calsensor_helpers/slots.py`
      with an unconditional confirmed-code lookup through the coordinator, and
      remove the display path's import of
      `custom_components/rental_control/sensors/calsensor_helpers/codes.py`
      while leaving `last_four` parsing untouched (FR-019, FR-023) —
      **⚠️ ATOMIC C**
- [ ] T037 [US2] Give `get_slot_code` in
      `custom_components/rental_control/coordinator.py` its confirmation
      semantics: for lock-backed entries expose the allocated code only after
      the matching write is confirmed by physical observation, retaining the
      last observed code or `None` meanwhile; lockless entries publish the
      allocated value once it is in `_latest_res_by_key` (FR-019, plan
      decision 5)
- [ ] T038 [P] [US2] Rework `tests/unit/test_calsensor_codes.py` and any other
      sensor test asserting regeneration into parity assertions against
      `coordinator.get_slot_code`, including the no-allocation case reporting
      no code rather than generating one (FR-019, plan "Test strategy")
- [ ] T039 [P] [US2] Add a sensor-parity integration test asserting that a
      collision-resolved code — not the generator's preferred code — is what the
      sensor publishes, and that it matches the code programmed into the lock
      slot (SC-003)

**Checkpoint**: The sensor, the allocator, and the lock agree on one value.

---

## Phase 6: User Story 4 - Codes Survive Restarts and Reloads (Priority: P2)

**Goal**: A reservation's code is identical across restarts, reloads, and
reinstalls; released codes return to the pool; orphaned allocations have a safe
operator recovery path.

**Independent Test**: Allocate codes including at least one collision-resolved
code, restart Home Assistant, and confirm every reservation holds the same code
it held before.

- [ ] T040 [US4] Implement `async_rekey` in
      `custom_components/rental_control/allocator/allocator.py` and drive it
      from the hydrated `fingerprint_history` in
      `custom_components/rental_control/coordinator_helpers/code_allocation.py`,
      so a date or UID change keeps the code rather than issuing a new one
      (FR-017, SC-005, plan decision 2 phase 2)
- [ ] T041 [US4] Implement `async_sweep` plus the single shared FR-014 release
      guard helper in
      `custom_components/rental_control/allocator/allocator.py`: refresh
      `lock_observed` from `observed_codes` and from unreadable slots only when
      the owner `lockname` matches the observation and
      `slot in unreadable_slots`; release only allocations whose reservation is
      inactive and whose code the guard proves
      is not programmed, apply the same booking-end, cancellation, and
      disappearance-grace rules for lockless entries, and make a released code
      immediately available again (FR-013, FR-014, FR-015, SC-006)
- [ ] T042 [US4] Implement `async_mark_entry_removed` in
      `custom_components/rental_control/allocator/allocator.py`, create
      `async_remove_entry` in `custom_components/rental_control/__init__.py`,
      and call it from that entry-removal path: abort pending adoption for the
      removed entry and mark its allocations orphaned without releasing any code
      (FR-004)
- [ ] T043 [US4] Implement `async_clear_orphans` in
      `custom_components/rental_control/allocator/allocator.py` and the
      `rental_control.clear_orphaned_codes` handler in
      `custom_components/rental_control/allocator/services.py`, registered from
      `async_get_or_create_allocator` behind
      `hass.services.has_service(...)`, with `dry_run`,
      `SupportsResponse.OPTIONAL`, reuse of the T041 guard helper, retention
      reasons `code_still_programmed`/`unverifiable_lock`/`adoption_conflict`,
      and a report, log line, and notification that carry `code_ref` only
      (FR-004, FR-014, FR-025, contract §3)
- [ ] T044 [P] [US4] Declare the service in
      `custom_components/rental_control/services.yaml`,
      `custom_components/rental_control/strings.json`,
      `custom_components/rental_control/translations/en.json`, and
      `custom_components/rental_control/translations/fr.json`, matching the
      existing `checkout` and `set_state` structure (contract §3)
- [ ] T045 [P] [US4] Add `tests/unit/test_allocator_services.py`: the service
      registers once across two entries, `dry_run` changes nothing, a code still
      on a lock is retained as `code_still_programmed`, an orphan on an
      unobserved lock is retained as `unverifiable_lock`, a conflict record is
      retained as `adoption_conflict`, a lockless orphan releases immediately, a
      second call is a no-op, and no response field or log line contains a code
      (FR-004, FR-014, FR-025)
- [ ] T046 [P] [US4] Add release and persistence coverage in
      `tests/unit/test_allocator_registry.py` and a restart/reload integration
      test: codes including at least one collision-resolved code are identical
      across a restart and across a single-entry reload while other entries stay
      loaded, a released reservation regains its preferred code when it is free,
      and a code still programmed is never released (FR-014, FR-015, FR-017,
      SC-005, SC-006)

**Checkpoint**: Codes are stable across restarts, the code space no longer
leaks, and orphans have a guarded operator remedy.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T047 [P] Populate the `diagnostics` property in
      `custom_components/rental_control/allocator/allocator.py` and surface it
      through the integration's existing diagnostics in
      `custom_components/rental_control/coordinator_helpers/diagnostics.py`,
      exposing record counts, conflicts, orphans, pending-adoption entries, and
      `code_ref` values only — never a code in plain or encoded form (FR-025)
- [ ] T048 Audit logging across the `allocator/` package and
      `coordinator_helpers/code_allocation.py` so allocation, collision
      resolution, release, adoption, adoption conflicts, and exhaustion are each
      logged with enough detail to attribute a `code_ref` to a reservation, and
      grep the new modules to confirm no `%s` formatting of a raw `code`
      (FR-025, quickstart §6)
- [ ] T049 Run the full gate — `uv run pytest tests/ -q -p no:randomly`,
      `uv run ruff check custom_components/ tests/`, and
      `pre-commit run --all-files` — and confirm the coverage floor of 95%, no
      file over 400 lines, no function over 80 lines, no signature over six
      parameters, no aislop suppression, SPDX headers on every new file, and the
      hazard-1 regression from T019 passing (quickstart §6, plan "Constraints")

---

## Dependencies

### Phase-level

```text
Phase 1 Setup
    └─► Phase 2 Foundational  (2a pure core ─► 2b store ─► 2c singleton;
                               2d guards independent of 2a-2c)
            └─► Phase 3 US3 Adoption
                    └─► Phase 4 US1 Allocation (MVP)
                            ├─► Phase 5 US2 Sensor parity
                            └─► Phase 6 US4 Persistence / release / orphans
                                    └─► Phase 7 Polish
```

### Hard task-level edges

- T004 → T007, T011 (the registry and allocator consume the models; T005 and
  T006 are independent candidate-walk work)
- T007 → T009 (the store serializes what the registry holds)
- T009, T010 → T011 → T012 → T014 (registry and its round-trip land before any
  consumer, per the safety constraint)
- T015 → T016, T017, T018, T019, T020 — and all six ship in **one commit**
  (ATOMIC B). `slot_code` nullability is never merged without the guards.
- T011, T014 → T021, T022 (adoption needs the allocator and the registered
  entry set)
- T021 → T023 (the helper calls the allocator's adopt path)
- T021, T022, T023 → T024 (the adoption-only step exists before it is spliced
  into the refresh)
- T016-T018 → T029, T030 (results may set `slot_code = None`, which is only
  safe once the guards exist)
- T023, T027 → T028 (Phase 4 converts the adoption-only step to the batch
  adopt+allocate entrypoint)
- T027, T028 → T029
- T028, T029, T031 → T030 (the lockless path consumes the batch entrypoint,
  allocation-result consumer, and durable `published_once`)
- T028, T030 → T040, T041 (Phase 6 extends the existing batch and lockless
  paths with rekey and sweep; Phase 4 does not call missing behaviours)
- T029 → T037 (the sensor can only display a code the coordinator holds)
- T037 → T035, T036 (ATOMIC C removes generated display only after
  `get_slot_code` has confirmation semantics)
- T004 (`lockname`/`slot` on the owner), T023 (`CycleObservation`), T021
  (adoption) → T041 → T042, T043 — release and cleanup behaviour comes after
  adoption, never before
- T041 → T043 (one shared guard helper, not a second copy)
- T043 → T044, T045
- Everything → T049

### Atomic-commit groups

| Group | Tasks | Why it must be one commit |
|-------|-------|---------------------------|
| A | T009, T010 | A persisted code format without its round-trip and no-plaintext assertions is unverifiable. |
| B | T015, T016, T017, T018, T019, T020 | `slot_code: str \| None` without the guards leaves the existing missing-code drift path able to emit `OVERWRITE_MANUAL_CHANGE` — the guest-lockout path. |
| C | T035, T036 | T035 alone leaves lockless entries publishing no code until T036 removes the `event_overrides_present` gate in `calsensor_helpers/slots.py:23`. |

Every other task is sized to stand alone as one coherent commit.

## Parallel execution examples

**Phase 2a** — three independent pure modules and their tests:

```text
T004 models.py   ┐
T005 candidates.py ├─ parallel, then T006 with T005 complete
T006 test_allocator_candidates.py ┘
T008 test_allocator_registry.py runs parallel to T009 once T007 is done
```

**Phase 3** — T025 (`tests/unit/test_code_allocation_step.py`) and T026
(`tests/integration/test_allocator_adoption.py`) are different files and run in
parallel once T024 is done.

**Phase 4** — T032 and T033 are different test files and run in parallel once
T029 is done.

**Phase 5** — T038 and T039 are different test files and run in parallel once
T037 is done.

**Phase 6** — T044, T045, and T046 are different files and run in parallel once
T043 is done.

**Not parallel, despite looking it**: T027, T028, T040, T041, T042, and T043 all
edit `allocator/allocator.py`; T023, T029, and T040 all edit
`coordinator_helpers/code_allocation.py`; T015 and T018 both edit
`reconciliation/plan_models.py`; T024 and T030 both edit
`coordinator_refresh_shell.py`; T025 and T032 both edit
`tests/unit/test_code_allocation_step.py`.

## Implementation strategy

1. **Foundation first, guards included.** Phase 2 is not optional groundwork; it
   contains both halves of the feature's safety story — a registry that
   round-trips, and a planner that holds a slot when no code exists. T019 is
   written to fail before the guards and pass after.
2. **MVP = Phase 1 + Phase 2 + Phase 3 + Phase 4.** That closes #743's security
   defect (SC-001) without rotating any in-flight code (SC-004), and is
   deployable on its own: the sensor still shows a code, it simply may lag by
   one refresh in the bounded cases hazard 2 enumerates.
3. **Then parity (Phase 5)**, which makes the allocator observable and closes
   the sensor divergence defect (SC-003).
4. **Then durability and hygiene (Phase 6)**, which turns the registry from
   correct into sustainable: stable across restarts (SC-005), non-leaking
   (SC-006), and recoverable from orphans (FR-004).
5. **Scope discipline throughout.** Code length (#741), force re-issue (#735),
   per-instance partitioning (rejected in PR #742), and any new config flow or
   options field stay out. An adjacent defect gets an issue, not a wider PR.

## Task summary

- **Total tasks**: 49
- **Setup**: 3 · **Foundational**: 17 · **US3**: 6 · **US1**: 8 · **US2**: 5 ·
  **US4**: 7 · **Polish**: 3
- **Parallelizable**: 15 tasks marked `[P]`
- **Atomic-commit groups**: 3 (group A: 2 tasks, group B: 6 tasks,
  group C: 2 tasks)
- **Requirements coverage**: FR-001 to FR-025 all mapped; SC-001 to SC-008 each
  have at least one asserting test task
