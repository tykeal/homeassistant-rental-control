<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Decompose Reconciliation Validation

## Scope

This guide validates the implementation stage for spec
`015-decompose-reconciliation`. It proves that the package split is
behavior-preserving for the 3.6.0 stateless reconciliation engine. The validation
oracle is current `origin/main`: identical inputs must produce identical public
imports, fingerprints, rematch results, desired plans, stateless plans, actions,
action ordering, and diagnostics.

## Developer Setup

```bash
cd path/to/homeassistant-rental-control
uv sync
```

Run targeted reconciliation tests first. Escalate to broader tests only when the
changed behavior touches those areas or when targeted failures require it.

```bash
uv run pytest tests/unit/test_slot_reconciliation.py \
  tests/unit/test_event_overrides.py \
  tests/unit/test_coordinator.py \
  tests/unit/test_keymaster_event_diagnostics.py \
  tests/integration/test_refresh_cycle.py \
  tests/integration/test_slot_concurrency.py -q
uv run ruff check custom_components/ tests/
```

Before merging implementation, run the full suite and hooks:

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
uv run pre-commit run --all-files
```

## Import Compatibility Tests

Add or update a focused test that imports every compatibility symbol from
`custom_components.rental_control.reconciliation`:

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

The test should also assert that production modules still import unchanged:
coordinator, event_overrides, and sensors/calsensor must not require import
rewrites.

## Public Call Pattern Compatibility

Keep existing `compute_desired_plan` calls unchanged in tests and production.
Add explicit coverage for the legacy call shape:

```python
compute_desired_plan(
    reservations,
    managed_slots,
    max_events,
    plan_id,
    generated_at,
    entry_id=entry_id,
    lockname=lockname,
    start_slot=start_slot,
)
```

The implementation may internally build a `DesiredPlanRequest`, but this call
must continue to work. Add negative coverage for unknown context keywords so the
compatibility shim fails loudly rather than silently ignoring typos.

## Byte-for-Byte Parity Harness

Before moving code, create test helpers that serialize plans deterministically:

- dataclasses converted with field order preserved;
- enum values serialized as their `.value` strings;
- datetimes serialized with the existing `isoformat()` output;
- sets sorted where the current diagnostics sort them;
- raw PINs excluded wherever the current diagnostics exclude them.

For representative fixtures, compare current and decomposed outputs for:

1. `normalize_slot_name_for_fingerprint()`;
2. `make_reservation_fingerprint()`;
3. `extract_booking_aliases()`;
4. `find_reservation_rematch()`;
5. `compute_desired_plan()`;
6. `compute_stateless_plan()`;
7. diagnostics snapshots and action lists.

## DesiredPlan Regression Scenarios

Use existing `tests/unit/test_slot_reconciliation.py`,
`tests/unit/test_event_overrides.py`, `tests/unit/test_coordinator.py`, and
`tests/integration/test_refresh_cycle.py` as the oracle. Ensure each scenario
passes unchanged and, where helpful, add phase-level tests that compare extracted
helper output to the final plan.

1. **Already correct no-op**: selected physical slot matches name, code, dates,
   and switches; action remains `NOOP` and is omitted from `plan.actions`.
2. **Length increase/decrease**: same stable slot name remains in the same
   physical slot; dates update in place and no duplicate slot is selected.
3. **Full date shift with generated-code change**: stable slot-name identity
   matches the old physical slot; replacement stays bound to that slot and never
   assigns a second slot.
4. **Code/name drift**: action kind, reason, drift fields, preflight flag, and
   confirmed-empty requirement match current output.
5. **Duplicate reservation names**: desired groups sort by start, end, and
   identity key; physical groups sort by observed start, end, and slot number;
   each selected reservation appears once.
6. **Duplicate physical slot-name matches**: canonical minimum-distance slot
   remains matched; non-canonical duplicates clear/reset with the same reason.
7. **Protected active guest**: protected reservations select first, count against
   capacity, and block unsafe pending clears as today.
8. **Capacity and no-empty overflow**: overflow reasons and `overflow_details`
   ranks stay identical.
9. **Pending/blocked/unknown slots**: `RETRY_CLEAR` and `BLOCKED` decisions keep
   current blocked reasons, retry counts, and last errors.
10. **Stale and phantom slots**: clear actions and diagnostic reasons remain
    `stale`, `phantom`, or `mis_assigned` exactly as current source emits.

## StatelessPlan Regression Scenarios

Use the existing stateless tests in `tests/unit/test_slot_reconciliation.py`.
Add phase-level coverage only after the unchanged tests pass.

1. `ObservedSlot` classification preserves empty, occupied, phantom, and unknown
   behavior for `None`, blank, `unknown`, `none`, and `unavailable` text states.
2. Protected and non-protected `DesiredReservation` selection preserves selected
   ranks and capacity overflow.
3. Prefix-aware physical names match the same desired stable/display forms.
4. Duplicate observed slots select the same canonical slot and reset the same
   non-canonical slots.
5. Empty confirmed slots receive `ASSIGN`; non-empty mismatches reset before any
   later assignment.
6. Code or display-name replacement emits `UPDATE_IN_PLACE` for the matched slot
   with `requires_confirmed_empty=True` and `preflight_read=True`.
7. Date-only drift emits `UPDATE_TIMES` with the same `matched_by` and reason.
8. Diagnostics preserve the current `plan_id`, `generated_at`, `selected`,
   `overflow`, and action summary shape.

## Rematch Regression Scenarios

Keep existing `find_reservation_rematch()` tests unchanged and add direct helper
coverage only around extracted private phases.

1. Exact fingerprint wins when fresh physical name does not conflict.
2. Fresh observed physical-name conflict skips otherwise exact or alias
   candidates.
3. UID alias plus normalized name returns `UID_ALIAS` and `date_shifted=True`.
4. Booking alias plus normalized name returns `BOOKING_ALIAS`.
5. Normalized name plus exact UTC start/end returns `NAME_TIME`.
6. Conservative continuity returns one candidate only when no other current
   reservation competes.
7. Multiple continuity candidates with exactly one stored or observed date match
   return `CONTINUITY` for that date-matching candidate.
8. Multiple compatible candidates without a single date tie-break return
   `AMBIGUOUS` with the same key order.
9. No compatible candidate returns `NO_MATCH`.

## Diagnostics and Redaction Checks

For both plan types, assert diagnostics are byte-for-byte identical for the same
inputs:

- plan id and generated timestamp;
- `entry_id`, `lockname`, and `start_slot` context when provided;
- slot desired identity, classification, action, blocked reason, retry count,
  last error, and drift fields;
- reservation selected/protected/overflow/missing/assigned fields;
- sorted UID and booking aliases;
- stable-name match details;
- absence of raw slot codes.

## Cache-Only Store Safety Checks

The decomposition must not add Store authority. Re-run scenarios with cache data
present, missing, stale, contradictory, and deleted. Planned selections and
actions must remain determined by current reservations plus physical slots.

Required cases:

1. deleted Store cold start with existing coded physical slots;
2. stale `SlotMapping` claiming a different slot than physical truth;
3. cache entry with old pending-clear fields while physical state is empty;
4. cache entry with aliases used only for rematch diagnostics;
5. Store save/load failure not changing the computed plan.

## Complexity and `aislop` Validation

After extraction:

1. verify no reconciliation package file is 400 lines or more;
2. verify project-owned functions are below 80 lines;
3. verify project-owned parameter lists are no more than six parameters;
4. remove the old `aislop-ignore-file complexity/file-too-large
   complexity/function-too-long` directive;
5. run the active complexity checks through pre-commit without adding new
   suppressions.

## Manual Review Checklist

Before opening the implementation PR, rubber-duck the diff against these
questions:

- Are all public symbols still importable from the package root?
- Do existing coordinator, event override, calsensor, and test call sites remain
  unchanged?
- Does `compute_desired_plan` still accept the legacy five positional arguments
  plus `entry_id`, `lockname`, and `start_slot` keywords?
- Can every extracted helper be mapped directly to current source lines with no
  business-rule change?
- Do no-duplicate assignment, stable slot-name matching, in-place update,
  confirmed-reset-before-reapply, and cache-only Store guarantees have explicit
  tests?
- Are diagnostics and action ordering byte-for-byte identical?
