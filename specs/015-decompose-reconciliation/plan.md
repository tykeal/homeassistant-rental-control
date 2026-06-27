<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Reconciliation Engine

**Feature**: `015-decompose-reconciliation` | **Planning Branch**:
`015-decompose-reconciliation-plan` | **Date**: 2026-06-27 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/015-decompose-reconciliation/spec.md` and GitHub issue #627

## Summary

Decompose `custom_components/rental_control/reconciliation.py` without changing
any runtime behavior of the 3.6.0 stateless slot-reconciliation engine. The
current 2,584-line source is the load-bearing contract: it defines the legacy
`DesiredPlan` planner, the newer `StatelessPlan` planner, stable fingerprint and
alias helpers, persisted rematch hierarchy, slot-name matching, duplicate-name
pairing, action selection, and diagnostics.

The implementation will replace the single module with a
`custom_components/rental_control/reconciliation/` package split by phase. The
package `__init__.py` is the compatibility boundary and re-exports the full
production and test-consumed surface so coordinator, event override, event
sensor, and regression-test imports do not change. The refactor is acceptable
only if identical inputs produce byte-for-byte equivalent plans, diagnostics,
actions, action ordering, and public helper results.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `icalendar>=7.0.0`, and
`x-wr-timezone>=2.0.0`
**Storage**: Home Assistant `Store` data remains cache-only for reconciliation;
no new storage and no Store authority in planner correctness
**Testing**: `uv run pytest tests/`; targeted reconciliation coverage in
`tests/unit/test_slot_reconciliation.py`, `tests/unit/test_event_overrides.py`,
`tests/unit/test_coordinator.py`, `tests/unit/test_keymaster_event_diagnostics.py`,
`tests/integration/test_refresh_cycle.py`, and
`tests/integration/test_slot_concurrency.py`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for reuse, ruff,
mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Keep reconciliation pure, synchronous, and bounded by the
already-observed managed slots plus current reservations. Do not add blocking
I/O, coordinator refreshes, Store reads, Store writes, or Keymaster service calls
inside planning.
**Constraints**: Documentation-only PLAN PR; no production code. Runtime refactor
must preserve the no-duplicate lock-code assignment guarantee, slot-name identity
matching, in-place updates, confirmed-reset-before-reapply ordering, diagnostics,
and cache-only persisted Store semantics exactly.
**Scale/Scope**: One 2,584-line module becomes a focused internal package. The
implementation target is reconciliation files below 400 lines, functions below 80
lines, and project-owned parameter lists no more than six parameters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing reconciliation unit/integration tests to pass unchanged and adds focused parity tests around each extracted phase. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split models, identity, pairing, planners, diagnostics, wiring, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python modules must retain project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested AI co-author trailer. |
| VI: User Experience Consistency | PASS | Public imports, caller call patterns, plan contents, diagnostics, actions, and lock-code behavior are explicitly preserved. |
| VII: Performance Requirements | PASS | The split keeps the planner in-memory and side-effect-free, with no added I/O, refreshes, or Store authority. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/015-decompose-reconciliation/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 entities and module ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This refactor introduces no external
HTTP, WebSocket, Home Assistant service, entity-service, event, or public API
contract. The internal compatibility boundary is specified in this plan and in
[data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── coordinator.py                 # Existing imports and call patterns stay
├── event_overrides.py             # Existing action/plan imports stay
├── sensors/
│   └── calsensor.py               # Existing fingerprint import stays
└── reconciliation/
    ├── __init__.py                # Compatibility boundary; re-exports the
    │                              # full consumed reconciliation surface
    ├── enums.py                   # FINGERPRINT_VERSION, SlotStatus,
    │                              # ObservedSlotStatus, ActionKind
    ├── action_models.py           # SlotAction
    ├── plan_models.py             # Reservation, ManagedSlot, PlannedSlot,
    │                              # DesiredPlan
    ├── stateless_models.py        # ObservedSlot, DesiredReservation,
    │                              # StatelessPlan
    ├── store_models.py            # CacheOnlyStoreRecord, StoredIdentity,
    │                              # StoredActual, SlotMapping
    ├── rematch_models.py          # RematchKind, RematchResult
    ├── identity.py                # fingerprint, UTC canonicalization,
    │                              # booking aliases, name normalization/forms
    ├── pairing.py                 # slot-name matching, distance helpers,
    │                              # _pair_partial_* and duplicate ordering
    ├── rematch_names.py           # Store/rematch name-form helpers
    ├── rematch_dates.py           # UTC parsing and date comparison helpers
    ├── rematch_continuity.py      # Continuity and competition checks
    ├── rematch.py                 # find_reservation_rematch dispatcher and
    │                              # exact/alias/name-time rule helpers
    ├── desired.py                 # compute_desired_plan compatibility shim,
    │                              # request/context model, legacy planner phases
    ├── stateless.py               # compute_stateless_plan compatibility shim,
    │                              # request/context model, stateless phases
    ├── actions.py                 # _compute_drift_fields, _build_slot_action,
    │                              # action metadata/preflight flags
    └── diagnostics.py             # DesiredPlan and StatelessPlan diagnostic
                                   # snapshots, redaction, carry-over keys

tests/
├── unit/
│   ├── test_slot_reconciliation.py          # Existing behavior oracle;
│   │                                       # add phase-level parity tests
│   ├── test_event_overrides.py              # Existing apply/action behavior
│   ├── test_coordinator.py                  # Existing caller compatibility
│   └── test_keymaster_event_diagnostics.py  # Existing diagnostics behavior
└── integration/
    ├── test_refresh_cycle.py                # End-to-end no-duplicate oracle
    └── test_slot_concurrency.py             # Apply/callback ordering oracle
```

**Structure Decision**: Convert `reconciliation.py` into a package named
`reconciliation`. Python import sites already use
`custom_components.rental_control.reconciliation`; after the file-to-package move
that import path resolves to `reconciliation/__init__.py`. No production caller
or existing test should import from the new internal modules. Internal modules
may be tested directly only for new phase-level coverage; existing compatibility
tests must continue importing from the package root.

## Concrete Decomposition Design

### Compatibility boundary

`custom_components/rental_control/reconciliation/__init__.py` must re-export the
full consumed surface verified from production imports, current tests, and
FR-014/FR-015:

```text
ActionKind
CacheOnlyStoreRecord
DesiredPlan
DesiredReservation
FINGERPRINT_VERSION
ManagedSlot
ObservedSlot
ObservedSlotStatus
PlannedSlot
RematchKind
RematchResult
Reservation
SlotAction
SlotMapping
SlotStatus
StatelessPlan
StoredActual
StoredIdentity
compute_desired_plan
compute_stateless_plan
extract_booking_aliases
find_reservation_rematch
make_reservation_fingerprint
normalize_slot_name_for_fingerprint
```

Production callers currently consume `DesiredPlan`, `ManagedSlot`,
`Reservation`, `SlotStatus`, `compute_desired_plan`, `extract_booking_aliases`,
`make_reservation_fingerprint`, `normalize_slot_name_for_fingerprint`,
`ActionKind`, `SlotAction`, and `make_reservation_fingerprint` from this module.
Tests additionally consume the stateless models, rematch models, Store/cache
models, and planner helpers above. The implementation must add an import
compatibility test that imports every name from the package root and asserts the
objects are the same objects exported by their internal owner modules.

### Module responsibilities

The model layer is split so each file can satisfy the sub-400-line gate.
`enums.py` owns `FINGERPRINT_VERSION`, `SlotStatus`, `ObservedSlotStatus`, and
`ActionKind`; `action_models.py` owns `SlotAction`; `plan_models.py` owns
`Reservation`, `ManagedSlot`, `PlannedSlot`, and `DesiredPlan`;
`stateless_models.py` owns `ObservedSlot`, `DesiredReservation`, and
`StatelessPlan`; `store_models.py` owns `CacheOnlyStoreRecord`,
`StoredIdentity`, `StoredActual`, and `SlotMapping`; `rematch_models.py` owns
`RematchKind` and `RematchResult`. These modules preserve field names, defaults,
`slots=True`, post-init validation, enum values, and docstring semantics. They
must not import planner modules, perform I/O, or mutate process-global state.

`identity.py` owns `normalize_slot_name_for_fingerprint`,
`make_reservation_fingerprint`, `extract_booking_aliases`, `_dt_to_utc_iso`,
`_desired_name_forms`, `_slot_name_variants`, `_names_match`,
`_reservation_name_key`, and `_desired_name_key`. The exact v1 fingerprint
canonical string, UTC formatting, Airbnb alias extraction, casefold/strip
normalization, prefix stripping, and non-generic-prefix matching remain
unchanged.

`pairing.py` owns `_slot_times_match`, `_datetime_distance`,
`_managed_slot_distance`, `_observed_slot_distance`, `_select_managed_subset`,
`_select_observed_subset`, `_pair_partial_managed`, and
`_pair_partial_observed`. These helpers preserve minimum-distance matching,
start-time ordered duplicate disambiguation, deterministic slot fallback, and
canonical duplicate selection.

The rematch layer is also split to satisfy the file-size gate. `rematch.py` owns
`find_reservation_rematch` as a short dispatcher plus exact, UID-alias,
booking-alias, name-time, and continuity rule helpers. `rematch_names.py` owns
`_get_nested`, `_normalized_name_forms`, `_mapping_name_forms`,
`_mapping_name_matches_reservation`, `_is_adopted_mapping`,
`_should_include_observed_mapping`, and `_fresh_observed_name_conflicts`.
`rematch_dates.py` owns `_as_utc_datetime` and
`_mapping_dates_match_reservation`. `rematch_continuity.py` owns
`_is_continuity_compatible` and `_has_competing_reservation`. Ambiguous results,
fresh-observed-name conflict filtering, the multiple-continuity-candidate
date-match tie-break, and `date_shifted=True` semantics must remain identical.

`desired.py` owns the legacy `DesiredPlan` flow. It introduces an internal
`DesiredPlanRequest` dataclass bundling reservations, managed slots, max events,
plan id, generated timestamp, and optional diagnostics context. The public
`compute_desired_plan` remains the root import and keeps existing caller call
patterns while delegating immediately to a request object and phase helpers.

`stateless.py` owns the `StatelessPlan` flow. It introduces a
`StatelessPlanRequest` dataclass bundling observed slots, desired reservations,
max events, plan id, generated timestamp, and prefix. The public
`compute_stateless_plan` remains importable from the package root and delegates
to phase helpers without changing the six-parameter caller contract.

`actions.py` owns `_compute_drift_fields` and `_build_slot_action`. It also
centralizes action metadata that is currently assembled inline: `matched_by`,
`requires_confirmed_empty`, `preflight_read`, `reason`, `blocked_reason`, and
legacy `ActionKind` choices.

`diagnostics.py` owns `_build_plan_diagnostics_snapshot` and the stateless
snapshot builder. It splits plan metadata, slot diagnostics, reservation
diagnostics, action diagnostics, and carry-over merging into separately testable
functions. Raw slot codes remain excluded.

### `compute_desired_plan` split and parameter-count strategy

Current ground truth is:

```python
compute_desired_plan(
    reservations,
    managed_slots,
    max_events,
    plan_id,
    generated_at,
    *,
    entry_id=None,
    lockname=None,
    start_slot=None,
)
```

That is the public caller contract used by coordinator and tests. The
implementation must preserve those call patterns exactly, including positional
use of the first five arguments and keyword use of `entry_id`, `lockname`, and
`start_slot`, but internal helpers must not keep eight-parameter signatures.

The plan resolves the 8-parameter to <=6-parameter tension by making the package
root export a compatibility shim with no more than six declared project-owned
parameters, for example:

```python
def compute_desired_plan(
    reservations: list[Reservation] | DesiredPlanRequest,
    managed_slots: list[ManagedSlot] | None = None,
    max_events: int | None = None,
    plan_id: str | None = None,
    generated_at: datetime | None = None,
    **context: object,
) -> DesiredPlan: ...
```

The shim accepts the existing calls, validates that `context` contains only
`entry_id`, `lockname`, and `start_slot`, constructs `DesiredPlanRequest`, and
then delegates. New internal callers may pass `DesiredPlanRequest` directly.
This preserves source compatibility for existing callers while satisfying the
active parameter-count gate on the compatibility entry point and every internal
helper. Tests should pin both legacy call style and direct request style.

The decomposed desired-plan phases are:

1. `build_desired_plan_request()` validates legacy arguments and context.
2. `select_eligible_reservations()` preserves `eligible`, `checked_out`,
   `missing_count`, and protected-active filtering.
3. `select_desired_candidates()` preserves protected-first selection and
   non-protected soonest ordering by `(start, identity_key)`.
4. `record_capacity_overflow()` preserves `overflow_details` ranks and reasons.
5. `group_selected_by_stable_name()` preserves normalized slot-name grouping and
   start/end/identity ordering.
6. `match_existing_managed_slots()` preserves stable-name, persisted-identity,
   exact-time, partial-pair, minimum-distance, and duplicate physical-slot
   matching.
7. `assign_unmatched_reservations()` preserves lowest confirmed-free slot
   allocation and `no_empty_slot` overflow.
8. `classify_desired_plan_slots()` preserves pending-clear, blocked, unreadable,
   duplicate, stale, phantom, mis-assigned, drift, `UPDATE_TIMES`, and `NOOP`
   decisions.
9. `assemble_desired_actions()` preserves action order and suppression of `NOOP`
   actions.
10. `build_desired_diagnostics()` preserves diagnostics keys, sorting, redaction,
    and carry-over behavior.

Each helper must be below 80 lines and accept either one request/context object
or no more than six scalar parameters.

### `compute_stateless_plan` split

`compute_stateless_plan` already has six declared parameters and remains
source-compatible. Its 259-line body splits into:

1. `build_stateless_plan_request()` and `initialize_stateless_plan()`.
2. `select_stateless_reservations()` for eligible/protected/non-protected
   ordering, selected ranks, and capacity overflow.
3. `group_stateless_reservations_by_name()` for normalized stable-name groups.
4. `match_observed_slots_by_name()` for prefix-aware stable-name matching,
   exact-time matches, partial observed pairing, duplicate canonical selection,
   and `matched_slot`/`assigned_slot` mutation.
5. `assign_unmatched_stateless_reservations()` for lowest confirmed-empty slot
   assignment and `no_empty_slot` overflow.
6. `build_stateless_actions()` for unreadable blocks, duplicate resets, stale
   resets, assignments, update-in-place replacement, and date updates.
7. `build_stateless_diagnostics()` for the current selected/overflow/action
   snapshot shape.

The implementation must not simplify the duplicated desired/stateless matching
logic unless parity tests prove the shared helper emits byte-for-byte identical
results for both model families.

### Behavior-equivalence strategy

The source of truth is current `origin/main` plus existing tests. The
implementation should first introduce serialization helpers in tests, then
capture before/after outputs for representative `DesiredPlan`, `StatelessPlan`,
`find_reservation_rematch`, fingerprint, alias, and diagnostics scenarios. For
identical inputs, serialized dataclasses, dictionaries, action lists, action
ordering, diagnostics, enum values, error behavior, and helper return values must
match byte-for-byte. No algorithmic improvement, new business rule, or caller
rewrite belongs in this feature.

Critical parity assertions:

- no selected reservation appears in more than one managed slot;
- stable slot-name matching remains trim-aware, prefix-aware, exact-display-name
  aware, and deliberately avoids unsafe generic prefix matching;
- duplicate or ambiguous reservation names remain start-time and
  minimum-distance disambiguated;
- changed reservations with stable slot-name identity update in place and do not
  allocate second slots;
- resets, replacements, retries, duplicates, stale occupants, phantoms, and drift
  corrections preserve confirmed-reset-before-reapply ordering;
- stale Store snapshots remain cache-only. Current `persisted_identity_key`
  continuity hints are preserved only where the current source already uses them
  and fresh physical state does not contradict them;
- diagnostics stay redacted and structurally identical.

### `aislop` directive removal

The implementation must remove the file-level
`# aislop-ignore-file complexity/file-too-large complexity/function-too-long`
directive only after the package files, project-owned functions, and
project-owned parameter lists satisfy active thresholds. No replacement
suppression should be added for this feature.

## Phase 0 Research

Research is complete in [research.md](research.md). It records the package split,
compatibility boundary, request-object parameter strategy, desired/stateless
phase decomposition, rematch decomposition, diagnostics/action extraction, and
behavior-parity approach, with alternatives grounded in the current source.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external API, service, event, or entity
  contract is introduced or changed.
- `update-agent-context.sh`: intentionally not run. The plan adds no new
  language, framework, database, runtime, package manager, or agent-relevant
  technology beyond the Python/Home Assistant stack already documented in the
  repository.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart requires existing reconciliation tests unchanged plus phase-level parity tests for imports, identity, pairing, rematch, desired/stateless planning, actions, diagnostics, and cache-only Store semantics. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; future implementation can be split into small extraction and test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the requested co-author trailer. |
| VI: User Experience Consistency | PASS | All production/test imports and legacy `compute_desired_plan` call patterns are preserved from the package root. |
| VII: Performance Requirements | PASS | Extracted planner phases are pure in-memory work over existing inputs and add no I/O, refreshes, Store authority, or Keymaster operations. |

**Gate result: PASS** — no complexity violations.

## Complexity Tracking

> No violations to justify — all constitution gates pass.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify runtime source in
  this PR.
- Implementation must treat current `origin/main` as truth. Planning shorthand
  and issue text are secondary when they disagree with `reconciliation.py`.
- Before deleting the original module, implementation must run the unchanged
  reconciliation tests as the oracle and add import-compatibility coverage for
  the full package-root re-export list.
