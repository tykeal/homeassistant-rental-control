<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Force Re-Issue of a Door Code

**Input**: Design documents from `specs/023-force-reissue/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md),
[research.md](research.md), [data-model.md](data-model.md),
[contracts/force-reissue-service.md](contracts/force-reissue-service.md),
[quickstart.md](quickstart.md)
**Feature branch (implementation)**: `023-force-reissue`
**Issue**: #735

**Tests**: Test tasks are included and are mandatory. The spec's success
criteria are behavioural, the plan names several required regressions, and the
repository enforces a 95% coverage floor, so new modules need tests in the same
commit as the code they cover.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: May run in parallel — different files, no dependency on an
  incomplete task. Tasks touching the same file are never marked `[P]`.
- **[Story]**: The user story the task serves (US1-US4). Setup, Foundational,
  and Polish tasks carry no story label.
- **⚠️ ATOMIC**: Tasks sharing an atomic-commit group MUST be committed
  together in one commit. Shipping them separately leaves a known-unsafe or
  known-broken tree.

## Validation commands

```bash
uv run pytest tests/ -q -p no:randomly
uv run ruff check custom_components/ tests/
uv run pre-commit run --all-files
```

Every new Python file needs the SPDX header pair. Coverage floor is 95%. Never
pass `--no-verify`.

Known flakes — do **not** write tasks to fix these, and re-run them in
isolation before treating either as a regression:
`tests/integration/test_refresh_cycle.py::test_missing_store_adopts_coded_slots_when_unavailable_at_setup`
and `tests/integration/test_refresh_cycle.py::test_deleted_store_reenable_recovers_coded_slots`.
They share a fixture and fail together spuriously.

## Phase ordering note

US1 (heal a duplicate) and US2 (rotate a disclosed code) are both P1 and are
delivered by **exactly the same mechanism**; US3 (migrate onto a new generator)
adds only the dry-run preview, and US4 (ghost slot with no reservation) adds
only the bare lock/slot target. The phases below are therefore ordered by
**safety dependency**, not by story listing order, and story labels are attached
to the tasks that are story-specific:

- **The guard exemption lands before anything can create a forced-release
  hold** (Phase 2). A hold released by a guard that has not yet learned to
  validate an exemption, or a guard that accepts an exemption before the
  six-condition validation exists, is fail-open: a code could be returned to the
  pool while it is still physically programmed on a lock.
- **Re-homing an identity and creating its hold land together with the hold
  release** (Phase 3). `registry.allocate()` returns the existing record for a
  known identity and `by_identity` is one-code-per-identity, so without the hold
  the re-issue is a silent no-op; with the hold but no release path, the
  replaced code is pinned forever.
- **All three retention-suppression sites land together with the
  `_adoption_complete` exclusion** (Phase 4). Any subset is either silently
  ineffective on one path or disables issuance for the entire config entry for
  that cycle.
- **The service (Phase 5) comes last of the behaviour phases**, because it is
  the only thing that can start a re-issue, and it must not exist before the
  machinery that completes one safely.

---

## Phase 1: Setup

**Purpose**: Establish the behaviour oracle and confirm the live signatures this
feature modifies still look the way the design documents claim.

- [ ] T001 Run and record the pre-change baseline with
      `uv run pytest tests/ -q -p no:randomly` and
      `uv run ruff check custom_components/ tests/`, confirming the two known
      `tests/integration/test_refresh_cycle.py` flakes pass in isolation
      ([quickstart.md](quickstart.md) "Validation gate")
- [ ] T002 Re-read and record the live signatures this feature changes, per
      [quickstart.md](quickstart.md) "Before you start":
      `_release_guard_reason`, `_owner_still_programmed`, `_sweep_unlocked`,
      `async_mark_entry_removed`, `async_clear_orphans` in
      `custom_components/rental_control/allocator/allocator.py`;
      `release`/`is_available`/`add_owner`/`allocate` in
      `custom_components/rental_control/allocator/registry.py`;
      `resolve_cycle`/`allocate_request`/`observed_alias_key`/
      `unaccounted_slots` in
      `custom_components/rental_control/allocator/issuance.py`;
      `_resolve_observed_code` in
      `custom_components/rental_control/coordinator_helpers/reservations.py`;
      `build_protected_reservation` in
      `custom_components/rental_control/coordinator_helpers/checkin_protection.py`;
      `build_adoption_requests`/`_adoption_complete`/`build_cycle_observation`/
      `async_resolve_codes` in
      `custom_components/rental_control/coordinator_helpers/code_allocation.py`;
      and the two `SlotStatus.UNKNOWN` construction sites in
      `custom_components/rental_control/coordinator_helpers/keymaster_observation.py`,
      adjusting the tasks below where source and plan disagree

**Checkpoint**: Baseline is green and the signatures to be modified are known
from source, not from prose.

---

## Phase 2: Foundational - The Guard Exemption (Blocking Prerequisite)

**Purpose**: Teach the release guard to recognise exactly one narrowly-typed
exemption, and prove the three ordinary release paths are unchanged. **No task
in any later phase may create a forced-release hold until this phase is
complete.**

**⚠️ ATOMIC group A** — T003 through T008 ship as ONE commit. The
`ForcedReleaseExemption` type and its six-condition validation MUST land with
the `_release_guard_reason` change that honours it: a guard that accepts an
exemption before the validation exists is fail-open and could release a code
that is still physically programmed on a lock. The regression proving the three
ordinary release paths are unaffected is part of the same commit, not a
follow-up, because an unproven exemption is indistinguishable from a widened
guard.

- [ ] T003 Add the frozen, slotted value objects to
      `custom_components/rental_control/allocator/models.py`:
      `ForcedReleaseExemption(code, entry_id, identity_key)`,
      `ForcedReissueDirective(entry_id, identity_key, lockname, slot)`,
      `ReissueOutcome(entry_id, identity_key, lockname, slot,
      replaced_code_ref, replacement_code_ref, origin, disposition,
      retention_reason)`, and `ReissuePreview(replacement_code,
      replacement_code_ref, origin, reason, replaced_code_ref)`, with
      `replacement_code` declared `repr=False` in the same style as
      `AllocationResult.code` ([data-model.md](data-model.md) "New entities") —
      **⚠️ ATOMIC A**
- [ ] T004 Create `custom_components/rental_control/allocator/reissue.py` with
      SPDX headers and no Home Assistant imports, holding the pure
      `forced_release_hold_key(identity_key, entry_id, lockname, slot)`
      rendering `f"{identity_key}:reissued:{entry_id}:{lockname}:{slot}"` with
      `none` for a lockless owner, `is_forced_release_hold(key)` recognising the
      `:reissued:` marker, and `_conflict_exempt(record, owners,
      forced_release)` returning `True` only when all six conditions in
      [data-model.md](data-model.md#forcedreleaseexemption) hold
      ([plan.md](plan.md) "The mechanism") — **⚠️ ATOMIC A**
- [ ] T005 Add the keyword-only `forced_release: ForcedReleaseExemption | None =
      None` parameter to `DoorCodeAllocator._release_guard_reason` in
      `custom_components/rental_control/allocator/allocator.py` — whose live
      signature is `(self, record, owners, observations, *,
      refresh_observed=True)` — gating only the `len(record.owners) > 1`
      branch through `reissue._conflict_exempt`, and leaving the
      `unverifiable_lock` and `code_still_programmed` branches byte-for-byte
      unchanged; the three existing callers `_sweep_unlocked`,
      `async_mark_entry_removed`, and `async_clear_orphans` pass nothing and are
      otherwise untouched (FR-019,
      [contracts/force-reissue-service.md](contracts/force-reissue-service.md)
      §4) — **⚠️ ATOMIC A**
- [ ] T006 Add the hold-namespace early `continue` to
      `DoorCodeAllocator._sweep_unlocked` in
      `custom_components/rental_control/allocator/allocator.py`, so the ordinary
      sweep never evaluates or reports a hold owner under the full guard and can
      never report a duplicate's hold as `adoption_conflict`-retained (FR-021,
      plan decision 5) — **⚠️ ATOMIC A**
- [ ] T007 Add the mandatory regression suite
      `tests/unit/test_allocator_release_guard.py` proving the ordinary release
      paths are unaffected by the exemption: parity of released and retained
      sets with reasons across `_sweep_unlocked`, `async_mark_entry_removed`,
      and `async_clear_orphans` over the single-owner / two-owner ×
      covered / uncovered lock × code observed / not observed /
      `SlotStatus.UNKNOWN` / `SlotStatus.FREE` × lockless matrix; the default
      no-exemption call on a two-owner record still returning
      `adoption_conflict`; a mismatched exemption (wrong code, wrong entry_id,
      wrong identity_key, non-hold identity key, or a two-element `owners` list)
      being inert; `unverifiable_lock` and `code_still_programmed` still firing
      under a correctly matching exemption while a `FREE` slot does not; only
      the named hold owner being releasable from a two-owner record; and a
      static assertion over `custom_components/` that
      `ForcedReleaseExemption(` is constructed in exactly one non-test module
      and `forced_release=` appears at exactly one call site (SC-003, FR-019,
      plan "Mandatory: the ordinary release paths are unaffected") —
      **⚠️ ATOMIC A**
- [ ] T008 Add `tests/unit/test_allocator_reissue.py` covering the hold identity
      namespace: `forced_release_hold_key` round-trips through
      `is_forced_release_hold`, renders `none` for a lockless owner, is
      deterministic for one target, and can never collide with a
      `make_reservation_fingerprint` value or with an
      `issuance.observed_alias_key` value ([data-model.md](data-model.md)
      "Forced-release hold identity") — **⚠️ ATOMIC A**

**Checkpoint**: The guard understands one exemption, refuses every non-matching
one, and the three ordinary callers are provably unchanged. Nothing can yet
create a hold, which is the safe intermediate state.

---

## Phase 3: Cycle Mechanics - Re-Home, Hold, Release

**Purpose**: Make one cycle able to move a target identity off its old code onto
a purpose-built hold, allocate a replacement, and release the hold once the
guard proves the old code is gone.

- [ ] T009 Extract the candidate-selection body of `issuance.allocate_request`
      in `custom_components/rental_control/allocator/issuance.py` into a pure
      `select_code(registry, preferred_code, code_length, identity_key, *,
      exclude)` helper with no behaviour change, so the real path and the
      Phase 6 preview share one selection implementation (plan decision 7,
      research §11)

**⚠️ ATOMIC group B** — T010 through T015 ship as ONE commit. Re-homing an
identity to the hold and creating the `:reissued:` hold are the same act: the
plan established that `registry.allocate()` returns the existing record for a
known identity and that `by_identity` is one-code-per-identity, so without the
hold the re-issue is a silent no-op, and a half-landed version could strand a
code with no owner or let a second entry be issued a value still on the lock.
The hold release and the exhaustion rollback are in the same commit for the same
reason: a hold with no release path pins a code forever, and a staged hold with
no rollback loses the target's existing allocation when issuance is blocked.

- [ ] T010 Add `forced_reissues: tuple[ForcedReissueDirective, ...] = ()` to
      `CycleRequest` and `reissues: tuple[ReissueOutcome, ...] = ()` to
      `CycleResult` in `custom_components/rental_control/allocator/models.py`,
      with defaults that keep every existing construction site and test valid
      ([data-model.md](data-model.md) "Changed entities") — **⚠️ ATOMIC B**
- [ ] T011 Implement `apply_forced_reissues(allocator, request)` in
      `custom_components/rental_control/allocator/reissue.py` as a non-locking
      helper: locate the record the target identity owns, or for a bare
      lock/slot target the owner whose `entry_id`, `lockname`, and `slot` match;
      determine the replaced physical code, collapsing an `:observed:` alias
      owner for the same entry, lock, and slot into the hold even when it sits
      on a different record and removing the target identity from its stale
      non-physical record; rename that single retained owner's `identity_key` to
      the hold key while preserving `entry_id`, `origin`, `lockname`, `slot`,
      `lock_observed`, `first_seen`, and `last_seen`; update
      `AllocationRegistry.by_identity` accordingly; and emit a `ReissueOutcome`
      per directive, with `no_existing_allocation` for an identity target that
      owns nothing and a terminal `no_existing_allocation` for a bare slot
      target with no matching owner (FR-014, FR-020, research §1, §2) —
      **⚠️ ATOMIC B**
- [ ] T012 Implement `release_forced_holds(allocator, request)` in
      `custom_components/rental_control/allocator/reissue.py`: walk this entry's
      hold-namespace owners, build one `ForcedReleaseExemption` **from each
      owner it is about to evaluate**, call
      `allocator._release_guard_reason(..., forced_release=exemption)`, call
      `AllocationRegistry.release(hold_key)` only when the guard returns `None`,
      and emit a `ReissueOutcome` with disposition `released` or
      `held_pending_release` plus the retention reason for every deferral, which
      can never be `adoption_conflict` (FR-018, FR-021, contract §4) —
      **⚠️ ATOMIC B**
- [ ] T013 Splice both steps into `issuance.resolve_cycle` in
      `custom_components/rental_control/allocator/issuance.py` inside the
      **existing single `allocator._lock` hold** — `apply_forced_reissues`
      first, before adoption, and `release_forced_holds` last, after the sweep —
      calling only non-locking helpers so `_lock` is never acquired
      re-entrantly, reusing the existing `_store.async_save` call rather than
      adding another, and populating `CycleResult.reissues` (contract §4, plan
      decision 5) — **⚠️ ATOMIC B**
- [ ] T014 Implement the exhaustion and blocked-issuance rollback in
      `custom_components/rental_control/allocator/reissue.py`, invoked from
      `issuance.resolve_cycle` within the same lock hold: when a staged hold's
      target obtained no replacement — `exhausted`, `adoption_pending`,
      `unaccounted_slots`, `recovery_fail_closed`, or no allocation request
      built — restore the original owner's `identity_key`, remove the hold
      identity, and report the terminal reason, so the target's existing code is
      left in place in both the registry and the lock (FR-008, SC-010, plan
      decision 6) — **⚠️ ATOMIC B**
- [ ] T015 Add `tests/unit/test_allocator_reissue_cycle.py`: a directive frees
      the identity and re-homes the owner with `lockname`, `slot`, and
      `lock_observed` preserved; an observed alias for the same slot on a
      different record is collapsed into the hold; an identity target with no
      existing allocation records `no_existing_allocation` and still allocates;
      a bare slot target with no owner is terminal and allocates nothing;
      `release_forced_holds` releases only on a `None` guard verdict and reports
      `code_still_programmed` or `unverifiable_lock` otherwise, never
      `adoption_conflict`; the ordering inside `resolve_cycle` is
      re-home → adopt → allocate → sweep → release; and exhaustion restores the
      original owner and removes the hold (FR-008, FR-014, FR-018, FR-021) —
      **⚠️ ATOMIC B**

**Checkpoint**: A directive can move an identity onto a fresh code while the old
code stays registry-held and unavailable, and the hold is released only when the
guard proves the old code is gone. Nothing yet produces a directive.

---

## Phase 4: Retention Suppression for Exactly One Cycle

**Purpose**: Make the next reconcile cycle generate a fresh code for exactly one
target instead of retaining the observed one, without weakening retention for
any other reservation or any other cycle.

- [ ] T016 Create
      `custom_components/rental_control/coordinator_helpers/reissue.py` with
      SPDX headers: the frozen `ReissueSuppression(identity_keys: frozenset[str],
      slots: frozenset[int])` with an empty default value, the `ReissuePhase`
      enum, the `PendingReissue` record per
      [data-model.md](data-model.md#pendingreissue), and the in-memory
      `dict[str, PendingReissue]` accessors keyed by `identity_key` or
      `f"slot:{lockname}:{slot}"` — a plain coordinator attribute that is never
      written to any store, plus the second in-memory completed-fingerprint map
      for same-runtime repeat calls; both lapse on restart (FR-013, FR-009,
      plan decision 3)
- [ ] T017 Add `reissue_suppression: ReissueSuppression = ReissueSuppression()`
      to `ReservationBuildContext` in
      `custom_components/rental_control/coordinator_helpers/models.py`
      (live definition at line 90, eleven fields, plain `@dataclass`) and fill
      it from the coordinator's pending re-issues in
      `_reservation_build_context` in
      `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`,
      keeping the empty default so every existing construction site and test is
      unaffected (plan decision 4)

**⚠️ ATOMIC group C** — T018 through T024 ship as ONE commit. The plan
identified **three** `manual_observed` retention sites; suppressing fewer than
all three leaves the feature silently ineffective on the unsuppressed path.
Skipping the target's adoption request without excluding its slot from
`_adoption_complete` flips `readable_coded_slots <= adopted_slots` to `False`,
empties `allocations`, and disables issuance for the **entire config entry** for
that cycle, so the two changes and their regression may never be separated.

- [ ] T018 Suppress retention in `_resolve_observed_code` in
      `custom_components/rental_control/coordinator_helpers/reservations.py`
      (live definition at line 224, six parameters — carry the suppression on
      `ctx`, do not add a seventh parameter): when the reservation's
      `identity_key` is suppressed, or for an identity-less bare slot target
      only when its matched physical slot number is suppressed, return the
      caller's freshly generated `(slot_code, code_source)` instead of
      `(observed_code, "manual_observed")`, leaving everything else about the
      function unchanged so every non-targeted reservation keeps full retention
      (FR-013, plan decision 4 site 1) — **⚠️ ATOMIC C**
- [ ] T019 Suppress the second retention site: in
      `custom_components/rental_control/coordinator_helpers/coordinator_checkin_shell.py`
      `_synthesize_checkin_reservation`, pass the freshly generated code instead
      of `matched_physical.actual_code` when the matched physical slot is
      suppressed, and make `build_protected_reservation` in
      `custom_components/rental_control/coordinator_helpers/checkin_protection.py`
      report `code_source="generated"` rather than `"manual_observed"` for that
      case — its live signature already takes six parameters, so carry the flag
      inside the existing `identity` tuple or snapshot rather than adding a
      seventh (FR-013, plan decision 4 site 2, quickstart "Traps") —
      **⚠️ ATOMIC C**
- [ ] T020 Suppress the third retention site and keep the adoption gate honest
      in `custom_components/rental_control/coordinator_helpers/code_allocation.py`:
      `build_adoption_requests` contributes no `AdoptionRequest` for a
      suppressed target, and `_adoption_complete` (live definition at line 154)
      excludes suppressed slots from `readable_coded_slots` so
      `readable_coded_slots <= adopted_slots` still holds and issuance is not
      disabled for the whole entry — these two changes are inseparable
      (FR-013, research §5, plan "Trap") — **⚠️ ATOMIC C**
- [ ] T021 Thread the cycle's suppression and forced-reissue directives into
      `code_allocation.async_resolve_codes` in
      `custom_components/rental_control/coordinator_helpers/code_allocation.py`
      and surface `CycleResult.reissues` back to the coordinator; the live
      function already takes six parameters and the module is already 365 lines,
      so bundle the inputs into one request value object rather than adding
      parameters, and put new logic in a new module rather than growing this one
      (FR-011, FR-012, contract §4) — **⚠️ ATOMIC C**
- [ ] T022 Consume pending re-issues in
      `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`:
      build `ForcedReissueDirective` values for this cycle in both
      `_run_reconciliation` and `_run_lockless_allocation`, clear
      `suppress_pending` after the cycle reaches a terminal outcome so the
      suppression is next-cycle-only, and exclude pending target identities and
      bare target slots from persisted ghost hydration inside
      `_prepare_reservations_for_adoption` before `_build_ghost_reservations`
      runs, so a reservation that vanished from the feed is dropped rather than
      resurrected as a codeless ghost (FR-013, FR-017, plan decision 6) —
      **⚠️ ATOMIC C**
- [ ] T023 Add `tests/unit/test_reissue_suppression.py`: all three suppression
      sites return the generated code for the suppressed target while every
      other reservation in the same cycle still returns `manual_observed`; the
      suppression is gone on the following cycle (next-cycle-only); a second
      target in the same entry is unaffected (this-target-only); slot matching
      applies only to identity-less bare slot targets so a reservation that
      later occupies the same physical slot never inherits it; and a simulated
      restart drops the in-memory suppression entirely (FR-013, spec edge case
      "Home Assistant restarts") — **⚠️ ATOMIC C**
- [ ] T024 Add the entry-wide issuance regression to
      `tests/unit/test_code_allocation_step.py`: with the only unadopted
      readable coded slot being the suppressed target, `_adoption_complete`
      stays `True`, `allocations` is not emptied, and every **other** reservation
      in the same entry is still issued its code that cycle — the defect that
      would otherwise silently disable issuance for the whole entry (research
      §5, plan "Risks and mitigations") — **⚠️ ATOMIC C**

**Checkpoint**: One named target generates a fresh code for one cycle, every
other reservation retains as before, and entry-wide issuance is unaffected.

---

## Phase 5: The Service Surface (US1, US2, US4)

**Goal**: Give the operator one surgical, single-target invocation that
resolves and validates a target, records the pending re-issue, and lets the
existing refresh pipeline do the work.

**Independent Test (US1)**: Two reservations in different config entries hold
the same code on one shared parent lock and are recorded as an adoption
conflict. One invocation against one side's reservation sensor gives that side a
different code and leaves the other side untouched.

**Independent Test (US4)**: A managed slot holds a code no active reservation
claims; an invocation naming that lock and slot acts on it with no sensor
entity involved.

- [ ] T025 [US1] Create
      `custom_components/rental_control/allocator/reissue_service.py` with SPDX
      headers, `SERVICE_FORCE_REISSUE = "force_reissue"`, the `ReissueRequest`
      value object, and the voluptuous schema from contract §1 using
      `cv.entity_id` (never `cv.entity_ids`) plus a `_positive_slot` validator
      that rejects booleans and fractional values before the `vol.Range(min=1)`
      check, so a list, `area_id`, `device_id`, or `all` fails validation before
      any handler code runs and no bulk or wildcard form is expressible
      (FR-001, FR-004, contract §1)
- [ ] T026 [US4] Implement target resolution in
      `custom_components/rental_control/coordinator_helpers/reissue.py`
      producing `ReissueTarget(entry_id, identity_key, lockname, slot,
      target_key, checked_in, observed_code)`: the entity form requires a loaded
      `RentalControlCalSensor` currently carrying an event and derives its
      identity with `make_reservation_fingerprint(entry_id, slot_name, start,
      end)`, the same derivation `sensors/calsensor_helpers/slots.py:read_slot`
      uses; the slot form selects the single loaded coordinator whose `lockname`
      matches and whose `range(coordinator.start_slot, coordinator.start_slot +
      coordinator.max_events)` contains the slot, refusing on no match and on
      more than one match rather than guessing, and attaching the live
      reservation identity when one occupies the slot (FR-002, FR-005,
      [data-model.md](data-model.md#reissuetarget))
- [ ] T027 [US1] Implement the nine ordered validation checks from contract §2
      in `custom_components/rental_control/allocator/reissue_service.py`:
      half-supplied slot form, both-or-neither targeting form, non-reservation
      entity, unknown or ambiguous lock, slot outside the managed range,
      lock-backed target whose slot is uncovered by a current observation or
      whose status is `SlotStatus.UNKNOWN` (lockless targets skip this;
      `SlotStatus.FREE` is known-empty and is not unreadable), checked-in target
      without `force` read from the entry's `CHECKIN_SENSOR` state, an existing
      pending re-issue or outstanding hold for this target returning the prior
      outcome instead of rotating again, and an identity-backed target for which
      no unique replacement is obtainable — each refusal changing nothing and
      returning a structured `{"status": "refused", "reason": ...}` when a
      response is requested while schema failures still raise
      `ServiceValidationError` (FR-003, FR-005, FR-006, FR-008, FR-009, FR-010)
- [ ] T028 [US1] Implement the accepted-invocation handler in
      `custom_components/rental_control/allocator/reissue_service.py`: record
      the `PendingReissue` on the target's coordinator, emit the acceptance
      audit log line, request a coordinator refresh, and build the non-dry-run
      response from contract §3 — every field `code_ref` only, with
      `replacement_code_ref` `null` until the cycle issues, `retention_reason`
      never `adoption_conflict`, and a response builder that is **separate** from
      the dry-run builder rather than a conditional field removal (FR-001,
      FR-022, FR-025)
- [ ] T029 [US1] Register the service from
      `custom_components/rental_control/allocator/services.py` with
      `supports_response=SupportsResponse.OPTIONAL` behind its **own**
      `hass.services.has_service(DOMAIN, SERVICE_FORCE_REISSUE)` guard placed
      before the existing `clear_orphaned_codes` early return, which currently
      returns as soon as that first service is registered and would otherwise
      skip `force_reissue` for every config entry after the first (plan
      decision 1)
- [ ] T030 [P] [US1] Declare the service in
      `custom_components/rental_control/services.yaml` exactly as contract §1
      specifies, and add matching name, description, and field entries to
      `custom_components/rental_control/strings.json`,
      `custom_components/rental_control/translations/en.json`, and
      `custom_components/rental_control/translations/fr.json`, following the
      `clear_orphaned_codes` block already present in all three files (FR-001,
      contract §1)
- [ ] T031 [P] [US4] Add `tests/unit/test_reissue_targets.py`: entity form
      resolves a reservation sensor to its fingerprint identity; the check-in
      tracking sensor and any non-Rental-Control entity are refused; a slot
      inside exactly one entry's managed range resolves; a slot outside every
      range is refused; a slot claimed by two matching entries is refused as
      ambiguous; a slot occupied by a live reservation attaches that identity
      while an unoccupied one yields a bare lock/slot target (FR-002, FR-005)
- [ ] T032 [P] [US1] Add `tests/unit/test_reissue_service.py`: both-targets and
      neither-target refusals; a half-supplied slot pair refused; a list of
      entity ids rejected by the schema; an unreadable target slot refused; a
      checked-in target refused without `force` and accepted with it; a repeat
      invocation producing exactly one rotation; and a field-by-field assertion —
      not a substring search — that no non-dry-run response field equals any
      known code (FR-003, FR-004, FR-006, FR-009, FR-010, FR-022, SC-004,
      SC-006, SC-007)
- [ ] T033 [P] [US1] Add a multi-entry registration test to
      `tests/unit/test_allocator_services.py` asserting that `force_reissue` is
      registered exactly once and is present when a **second** config entry sets
      up after `clear_orphaned_codes` already exists, locking in the independent
      idempotent guard from T029 (plan decision 1)

**Checkpoint**: An operator can heal a live duplicate and rotate a disclosed
code with one invocation, and every refusal path changes nothing.

---

## Phase 6: Dry Run (US3)

**Goal**: Let an operator see exactly what would be issued before committing,
without touching any lock, any registry record, or any sensor state.

**Independent Test (US3)**: Change a config entry's generator, preview a
re-issue against one existing reservation, and confirm the response names the
code the newly configured generator prefers — or a collision-resolved code
derived from it — while nothing changes anywhere.

- [ ] T034 [US3] Implement `async_preview_reissue(request) -> ReissuePreview` on
      `DoorCodeAllocator` in
      `custom_components/rental_control/allocator/allocator.py`, delegating to a
      read-only helper in `allocator/reissue.py`: take `_lock`, perform **no**
      registry mutation and **no** `_store.async_save`, model the post-hold
      registry by excluding the target's current code and any collapsed observed
      alias, call the shared `select_code` from T009, and return the clear-only
      outcome with no `AllocationRequest` for a bare ghost slot target (FR-007,
      contract §4)
- [ ] T035 [US3] Build the preferred code for the preview from the same
      reservation builder the real cycle uses, run against the coordinator's
      cached calendar with suppression applied to isolated copies of the slot
      mappings and diagnostics, carrying the same observation,
      adoption-complete, pending-recovery, and unaccounted-slot guards so a
      guard that would block the real cycle is reported instead of a speculative
      code, and never calling a generator from the service itself (FR-007,
      FR-011, FR-012, plan decision 7)
- [ ] T036 [US3] Add the separate dry-run response builder in
      `custom_components/rental_control/allocator/reissue_service.py` returning
      `"status": "preview"`, `"dry_run": true`, and the single raw
      `replacement_code` field — the only place in the entire feature where a
      raw code is emitted (FR-023, contract §3)
- [ ] T037 [P] [US3] Add dry-run coverage to
      `tests/unit/test_reissue_service.py`: a preview leaves the registry, every
      lock, and every sensor state byte-for-byte unchanged and performs no store
      save; it still returns the raw `replacement_code`; a collision-resolved
      preview reports the resolved code and the collision-resolved origin; a
      preview against an exhausted code space refuses up front; and a bare ghost
      slot preview returns the clear-only outcome with no replacement code
      (FR-007, FR-008, FR-023, SC-005)

**Checkpoint**: Operators can preview a migration or a healing before
committing to it.

---

## Phase 7: Observability, Audit, and Reporting

**Goal**: Make every forced re-issue reconstructable from the logs alone and
every stuck hold visible, with no raw code anywhere outside the dry-run
response.

- [ ] T038 Emit the three-line audit trail using `code_ref` only: the service's
      acceptance line in
      `custom_components/rental_control/allocator/reissue_service.py` recording
      entry id, target form, identity key or lock and slot,
      `call.context.user_id` and `call.context.origin`, whether the checked-in
      override was used, whether it was a dry run, and the replaced `code_ref`;
      and the cycle's replacement line and replaced-code disposition line from
      `custom_components/rental_control/allocator/reissue.py` (FR-024, FR-025,
      SC-006, SC-008)
- [ ] T039 Report holds whose release has been deferred for more than one cycle
      through the existing persistent-notification mechanism in
      `custom_components/rental_control/allocator/services.py` style, carrying
      the retention reason so an indefinitely stuck release is visible, and
      assert by construction that an exempted multiple-owner deferral can never
      appear there (FR-021)
- [ ] T040 [P] Suppress the intermediate-state alarm in
      `custom_components/rental_control/allocator/adoption.py` while a hold
      explains that exact entry, lock, and slot: silence
      `_report_identity_mismatch` and suppress or collapse the matching
      `_record_mismatched_observed_owner` alias write into the existing hold,
      leaving mismatch **detection** itself unchanged (plan decision 8)
- [ ] T041 [P] Add outstanding forced-release hold counts and their retention
      reasons to `allocator_diagnostics` in
      `custom_components/rental_control/allocator/diagnostics.py`, as `code_ref`
      values only and never a code in plain or encoded form (FR-024)

**Checkpoint**: Every re-issue is auditable, every stuck hold is visible, and
the intermediate state no longer fires a warning every cycle.

---

## Phase 8: End-to-End Scenarios

**Goal**: Prove the motivating production condition heals, and that every
deferral, rollback, and lifecycle edge behaves as specified.

- [ ] T042 [US1] Add the mandatory end-to-end healing test
      `tests/integration/test_reissue_duplicate_healing.py`: two config entries
      with disjoint carved-out slot ranges on **one shared parent lock**, one
      reservation each, both slots physically programmed with the **same** code;
      one refresh adopts both, producing one record with two owners and a
      reported adoption conflict; one `force_reissue` invocation against one
      side's reservation sensor; assert immediately that the allocator and
      desired plan hold a different replacement code for the targeted
      reservation while its calendar sensor still publishes the old confirmed
      code per FR-026, the untargeted reservation is unchanged, the old record
      still has two owners so its code is still unavailable, and
      `OVERWRITE_MANUAL_CHANGE` was emitted for the targeted slot only; then
      advance the simulated lock so the targeted slot reads the new code, run one
      more refresh, and assert the end state — two **distinct** codes, the old
      record reduced from two owners to exactly one (the untargeted entry), no
      hold remaining, and no duplicate reported for that code any longer
      (SC-001, FR-019, FR-020, FR-026)
- [ ] T043 [P] [US1] Add `tests/integration/test_reissue_deferred_release.py`:
      the lock write does not confirm for two cycles, the hold is retried and
      reported each cycle with `code_still_programmed`, a third reservation
      cannot be allocated the old code meanwhile, and the release happens on the
      cycle after confirmation (FR-020, FR-021, SC-003)
- [ ] T044 [P] [US2] Add `tests/integration/test_reissue_lockless.py`: a
      lockless config entry publishes the replacement code immediately and its
      hold — whose owner has `lockname is None`, so `unverifiable_lock` is
      skipped and `_owner_still_programmed` returns `False` at its first branch —
      is released in the same cycle (FR-016, FR-018)
- [ ] T045 [P] [US4] Add `tests/integration/test_reissue_ghost_slot.py`: a
      slot-targeted invocation against a managed slot no reservation claims
      clears the bad code through the ordinary plan and handles its registry
      owner under the same guarded hold lifecycle, with the targeted lock and
      slot excluded from ghost hydration so the stale mapping cannot recreate a
      codeless ghost and turn the clear-only operation into a
      `NOOP code_unavailable` plan (FR-017, SC-011, plan decision 6)
- [ ] T046 [P] Add `tests/integration/test_reissue_lifecycle_edges.py`: a
      reservation that disappears from the feed between invocation and the cycle
      creates or resurrects no slot and drops its pending record; a full code
      space makes the invocation fail with the existing code still on the lock
      and still owned by the original identity; and a simulated Home Assistant
      restart drops the in-memory suppression while the registry hold persists
      and keeps being retried, so no code is lost or double-issued (FR-008,
      FR-013, FR-017, SC-010, plan "Complexity Tracking")

**Checkpoint**: The live production duplicate heals in one invocation, and every
deferral direction is conservative.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T047 [P] Document the new service action in `README.md` beside the
      existing manual checkout action section: what `force_reissue` does, the
      two targeting forms, the `force` and `dry_run` fields, the expected
      sensor lag until the lock write is confirmed (FR-026), and the documented
      restart behaviour that an unconsumed re-issue lapses and is simply invoked
      again (FR-013)
- [ ] T048 [P] Add the CHANGELOG entry for the force re-issue service
      referencing #735, creating `CHANGELOG.md` with its SPDX header pair in the
      Keep a Changelog format if the repository still has no such file at
      implementation time
- [ ] T049 Run the full gate — `uv run pytest tests/ -q -p no:randomly`,
      `uv run ruff check custom_components/ tests/`, and
      `uv run pre-commit run --all-files` — and confirm the 95% coverage floor,
      SPDX headers on every new file, no raw code in any log line or response
      outside the dry-run `replacement_code`, and the T007, T024, and T042
      regressions passing, re-running the two known
      `tests/integration/test_refresh_cycle.py` flakes in isolation before
      treating either as a regression (quickstart "Validation gate")

---

## Dependencies

### Phase-level

```text
Phase 1 Setup
    └─► Phase 2 Guard exemption (ATOMIC A)      ← nothing may create a hold first
            └─► Phase 3 Cycle mechanics (ATOMIC B)
                    └─► Phase 4 Suppression (ATOMIC C)
                            └─► Phase 5 Service surface (US1, US2, US4)
                                    ├─► Phase 6 Dry run (US3)
                                    └─► Phase 7 Observability
                                            └─► Phase 8 End-to-end
                                                    └─► Phase 9 Polish
```

### Hard task-level edges

- T003 → T004, T005 (the exemption type exists before the predicate and the
  guard parameter that consume it) — and T003 through T008 ship in **one
  commit** (ATOMIC A)
- T005 → T012 (a hold may only be released through a guard that already
  validates an exemption)
- T009 → T034 (the preview and the real path share one `select_code`)
- T010 → T011, T012, T013 — and T010 through T015 ship in **one commit**
  (ATOMIC B)
- T011 → T012 → T014 (re-home, release, then rollback of a staged hold)
- T016 → T017 → T018, T019, T020, T022 (the suppression value object and its
  context field exist before any site consults them)
- T018, T019, T020, T021, T022, T023, T024 ship in **one commit** (ATOMIC C)
- T020 → T024 (the regression asserts the exclusion it ships with)
- T013, T022 → T028 (the service may only record a pending re-issue that a
  cycle can actually consume)
- T025 → T027 → T028 → T029 → T030
- T026 → T027, T031
- T029 → T033
- T034 → T035 → T036 → T037
- T028, T036 → T038 (all three audit lines exist once both response paths do)
- T012 → T039 (deferrals can only be reported once holds are evaluated)
- Phases 2-7 → T042, T043, T044, T045, T046
- Everything → T049

### Atomic-commit groups

| Group | Tasks | Why it must be one commit |
|-------|-------|---------------------------|
| A | T003, T004, T005, T006, T007, T008 | `ForcedReleaseExemption` and its six-condition validation MUST land with the `_release_guard_reason` change that honours it. A guard that accepts an exemption before the validation exists is fail-open and could release a code still physically programmed on a lock. The proof that `_sweep_unlocked`, `async_mark_entry_removed`, and `async_clear_orphans` are unaffected ships with it, because an unproven exemption is indistinguishable from a widened guard. |
| B | T010, T011, T012, T013, T014, T015 | Re-homing an identity to a new code and creating the `:reissued:` hold are one act: `registry.allocate()` returns the existing record for a known identity and `by_identity` is one-code-per-identity, so without the hold the re-issue is a silent no-op, and a half-landed version could strand or double-issue a code. A hold with no release path pins a code forever; a staged hold with no rollback loses the target's existing allocation when issuance is blocked. |
| C | T018, T019, T020, T021, T022, T023, T024 | Fewer than all three `manual_observed` suppression sites leaves the feature silently ineffective on the unsuppressed path. Skipping the target's adoption without excluding its slot from `_adoption_complete` flips `readable_coded_slots <= adopted_slots` to `False` and disables issuance for the **entire** config entry for that cycle. |

Every other task is sized to stand alone as one coherent commit.

## Parallel execution examples

**Phase 5** — T030 (`services.yaml`, `strings.json`, both translations), T031
(`tests/unit/test_reissue_targets.py`), T032
(`tests/unit/test_reissue_service.py`), and T033
(`tests/unit/test_allocator_services.py`) are four different file sets and run
in parallel once T029 is done.

**Phase 7** — T040 (`allocator/adoption.py`) and T041
(`allocator/diagnostics.py`) are different files and run in parallel.

**Phase 8** — T043, T044, T045, and T046 are four different integration test
files and run in parallel once T042's fixtures exist.

**Phase 9** — T047 (`README.md`) and T048 (`CHANGELOG.md`) are different files.

**Not parallel, despite looking it**: T003 and T010 both edit
`allocator/models.py`; T004, T011, T012, and T014 all edit
`allocator/reissue.py`; T005, T006, and T034 all edit `allocator/allocator.py`;
T009 and T013 both edit `allocator/issuance.py`; T020 and T021 both edit
`coordinator_helpers/code_allocation.py`; T016 and T026 both edit
`coordinator_helpers/reissue.py`; T025, T027, T028, and T036 all edit
`allocator/reissue_service.py`; T032 and T037 both edit
`tests/unit/test_reissue_service.py`.

## Implementation strategy

1. **Safety before capability.** Phase 2 lands a guard that understands an
   exemption before anything can construct one, and Phase 3 lands the hold and
   its release together. At no point is the tree in a state where a door code
   could be released while it may still be physically programmed on a lock.
2. **MVP = Phase 1 through Phase 5.** That heals the live production duplicate
   (SC-001) and rotates a disclosed code (US2), which are the two P1 stories,
   and both are delivered by the same slice.
3. **Then the preview (Phase 6)**, which makes the P2 migration story usable
   without committing to an outcome (SC-005).
4. **Then audit and reporting (Phase 7)**, which makes every re-issue
   reconstructable from logs (SC-008) and every stuck hold visible (FR-021).
5. **Scope discipline throughout.** No bulk or "fix all collisions" mode, no
   code retirement or blacklisting, no automatic healing, no new sensor
   attribute, no new operator configuration option, and no persisted
   suppression. Pre-existing oversized modules unrelated to this feature —
   `coordinator_refresh_shell.py`, `reconciliation/desired.py`,
   `async_setup_entry`, `_ghost_from_mapping` — are known tech debt and stay out
   of this PR. An adjacent defect gets an issue, not a wider PR.

## Task summary

- **Total tasks**: 49
- **Setup**: 2 · **Guard exemption**: 6 · **Cycle mechanics**: 7 ·
  **Suppression**: 9 · **Service surface**: 9 · **Dry run**: 4 ·
  **Observability**: 4 · **End-to-end**: 5 · **Polish**: 3
- **Parallelizable**: 13 tasks marked `[P]`
- **Atomic-commit groups**: 3 (group A: 6 tasks, group B: 6 tasks,
  group C: 7 tasks)
- **Requirements coverage**: FR-001 to FR-026 each map to at least one task;
  FR-027 (no new configuration option) and FR-028 (no automatic invocation) are
  scope constraints with no code of their own and are verified by the Phase 9
  gate. SC-001 to SC-011 each have at least one asserting test task.
