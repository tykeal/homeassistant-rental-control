<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Reconciliation Engine

## R-001: Package split with root compatibility boundary

**Decision**: Convert `custom_components/rental_control/reconciliation.py` into a
`custom_components/rental_control/reconciliation/` package. Keep all external
imports pointed at `custom_components.rental_control.reconciliation` by making
`__init__.py` re-export every production and test-consumed symbol.

**Rationale**:

- Production callers import only from the current module root: coordinator uses
  `DesiredPlan`, `ManagedSlot`, `Reservation`, `SlotStatus`,
  `compute_desired_plan`, `extract_booking_aliases`,
  `make_reservation_fingerprint`, and
  `normalize_slot_name_for_fingerprint`; event overrides use `ActionKind`,
  `DesiredPlan`, `Reservation`, and `SlotAction`; calsensor uses
  `make_reservation_fingerprint`.
- Tests import a wider root surface, including `FINGERPRINT_VERSION`,
  `ObservedSlotStatus`, `ObservedSlot`, `DesiredReservation`, `StatelessPlan`,
  `PlannedSlot`, `StoredIdentity`, `StoredActual`, `SlotMapping`, `RematchKind`,
  `RematchResult`, `find_reservation_rematch`, and `compute_stateless_plan`.
  FR-015 also pins `CacheOnlyStoreRecord`, which exists in the source and should
  be re-exported even though current grep did not find a test import.
- A package split allows files to stay below 400 lines while preserving the
  import path that callers already use. The public root is the contract; internal
  module paths are implementation details.
- The current model definitions and rematch helpers are each too large for a
  single sub-400-line file once imports and docstrings are included, so the plan
  splits models and rematch helpers into narrower owner modules instead of using
  one catch-all `models.py` or `rematch_helpers.py`.

**Alternatives considered**:

- **Sibling modules such as `reconciliation_models.py` and
  `reconciliation_plan.py`**: Rejected because it leaves the oversized
  `reconciliation.py` compatibility module in place or forces callers to change
  imports. It also makes the feature area harder to discover.
- **A new package named `slot_reconciliation`**: Rejected because existing
  imports would require a larger caller migration and would weaken the
  behavior-preserving review story.
- **Keep one module and extract only helper classes**: Rejected because the
  current file is already 2,584 lines and must remove the file-size/function-size
  `aislop` suppression.

## R-002: Model submodules stay behavior-identical and side-effect-free

**Decision**: Move constants, enums, and dataclasses to focused model modules:
`enums.py`, `action_models.py`, `plan_models.py`, `stateless_models.py`,
`store_models.py`, and `rematch_models.py`. Do not change field names, defaults,
enum values, `slots=True`, validation, or public object identity through the
package root.

**Rationale**:

- The top half of the current module defines all core data structures:
  `SlotStatus`, `ObservedSlotStatus`, `ActionKind`, `SlotAction`, `Reservation`,
  `ManagedSlot`, `ObservedSlot`, `DesiredReservation`, `StatelessPlan`,
  `CacheOnlyStoreRecord`, `PlannedSlot`, `DesiredPlan`, `StoredIdentity`,
  `StoredActual`, and `SlotMapping`.
- Tests instantiate many of these classes directly and assert enum identities.
  Changing defaults, validation timing, or enum values would be a behavior
  change, not a decomposition.
- Keeping model submodules free of planner imports avoids circular dependencies
  and makes root re-export compatibility straightforward.
- Splitting by model family keeps each target file under 400 lines: legacy plan
  models, stateless models, Store/cache models, action models, enums, and rematch
  result models can evolve independently without another file-size suppression.

**Alternatives considered**:

- **Single `models.py` file**: Rejected because the current model classes alone
  are too large to leave enough room for imports, SPDX headers, and docstrings
  while satisfying the sub-400-line file gate.
- **Separate legacy and stateless models but keep actions/enums mixed in**:
  Rejected because shared `SlotAction` and `ActionKind` semantics are clearer and
  smaller as their own files.
- **Replace dataclasses with richer request/domain classes**: Rejected because
  the feature is explicitly behavior-preserving and existing tests use dataclass
  construction and fields as an oracle.

## R-003: Identity and alias helpers remain exact

**Decision**: Move fingerprinting, booking alias extraction, and slot-name
normalization/matching helpers into `identity.py` with no algorithm changes.

**Rationale**:

- `make_reservation_fingerprint()` currently hashes
  `FINGERPRINT_VERSION`, config entry id, normalized slot name, and UTC start/end
  into a 64-character SHA-256 value. This is consumed by coordinator, calsensor,
  and tests.
- `normalize_slot_name_for_fingerprint()` uses `strip().casefold()` and is part
  of the production import surface.
- `_names_match()` accepts exact stable/display forms and configured
  prefix-stripped physical forms, but intentionally avoids unsafe generic prefix
  matching between distinct names such as `Ann` and `Anna`.
- `extract_booking_aliases()` currently extracts Airbnb-style confirmation codes
  from summary and description. Rematch tests rely on those aliases.

**Alternatives considered**:

- **Broaden matching to fuzzy or generic-prefix comparisons**: Rejected because
  FR-005 requires the current exact display-name and prefix-aware model, and the
  source explicitly documents that generic prefix matching is unsafe.
- **Version the fingerprint during the split**: Rejected because FR-011 requires
  existing normalized values and versioning to stay unchanged.

## R-004: Pairing helpers own duplicate disambiguation

**Decision**: Move slot/date distance helpers and `_pair_partial_*` functions to
`pairing.py`. Desired-plan and stateless-plan modules call those helpers rather
than duplicating dynamic-programming subset selection.

**Rationale**:

- The duplicate-assignment guarantee depends on canonical matching when there
  are duplicate reservation names or duplicate physical slot-name matches.
- Current pairing uses date distance, start/end ordering, slot number fallback,
  and minimum-distance subset selection to pick canonical physical slots before
  marking non-canonical duplicates for reset.
- Isolating this phase makes it independently testable without changing the
  planner's public output.

**Alternatives considered**:

- **Inline pairing inside each planner**: Rejected because the current bodies are
  already oversized and the duplicate-name logic is one of the highest-risk
  invariants.
- **Replace dynamic programming with greedy matching**: Rejected as an algorithm
  change. Greedy matching could reintroduce duplicate or wrong-slot assignment in
  same-name date-shift scenarios.

## R-005: Rematch hierarchy becomes one helper per rule

**Decision**: Move `find_reservation_rematch()` to `rematch.py` and split its
241-line body into rule-specific helpers while preserving the current rule order:
exact fingerprint, UID alias plus name, booking alias plus name, name plus exact
UTC start/end, conservative continuity with competition checking, ambiguity, and
no match. Move supporting name, date, and continuity helpers to
`rematch_names.py`, `rematch_dates.py`, and `rematch_continuity.py` so no rematch
file exceeds the size gate. Preserve the current date-match tie-break inside the
continuity rule: when multiple continuity candidates exist but exactly one
matches stored or observed dates, the result remains `CONTINUITY` rather than
`AMBIGUOUS`.

**Rationale**:

- FR-010 pins the hierarchy and ambiguity behavior. The current implementation
  returns the first successful rule and uses `date_shifted=True` for UID alias
  rematches after the primary fingerprint fails.
- Current private helpers already isolate much of the rule logic; the public
  function can become a short dispatcher without changing outcomes.
- Fresh observed physical slot-name conflicts must continue to exclude stale
  Store mappings from exact and alias candidates.
- The multiple-candidate continuity date tie-break is an existing behavior, not
  a new rule; omitting it would change rematch outcomes for adopted/observed
  mappings that share weak continuity signals.
- The current rematch region is large enough that one `rematch.py` containing
  all helpers would likely fail the file-size goal. Splitting helpers by concern
  keeps the dispatcher readable and separately testable.

**Alternatives considered**:

- **Retire rematch because Store is cache-only**: Rejected because production and
  tests still import it, and cache-only aliases/diagnostics remain useful.
- **Collapse all alias rules into one scoring function**: Rejected because the
  explicit rule priority is part of the behavior contract and tests exercise it.

## R-006: Request objects resolve parameter pressure

**Decision**: Introduce `DesiredPlanRequest` and `StatelessPlanRequest` internal
context dataclasses. Keep public planner call patterns source-compatible, but
make public shims and all helper functions accept no more than six declared
project-owned parameters.

**Rationale**:

- The current `compute_desired_plan` caller contract accepts five positional
  arguments plus keyword-only `entry_id`, `lockname`, and `start_slot`.
  Coordinator and tests use that contract heavily.
- FR-017 requires the completed decomposition to satisfy the active
  parameter-count gate even for compatibility entry points that preserve current
  call patterns.
- A shim with five named legacy arguments plus `**context` can accept existing
  calls, reject unknown context keys, build `DesiredPlanRequest`, and delegate to
  small helpers. New internal callers pass the request object directly.
- `compute_stateless_plan` already has six parameters, but a request object keeps
  its extracted phases small and consistent.

**Alternatives considered**:

- **Keep the exact eight-parameter public signature**: Rejected because the spec
  explicitly requires the compatibility entry point to pass parameter-count
  checks.
- **Change all callers to pass a request object**: Rejected because FR-014
  requires existing callers to use the same import names and call patterns.
- **Use a broad `*args, **kwargs` shim**: Rejected because it would make argument
  validation less explicit and harder to test. Five named arguments plus
  validated context preserves source compatibility more clearly.

## R-007: Desired and stateless planners split by phase, not behavior

**Decision**: Split `compute_desired_plan` and `compute_stateless_plan` into
phase helpers that mirror the current order: initialize, filter/select, group by
stable name, match existing physical slots, assign unmatched reservations,
classify actions, and build diagnostics.

**Rationale**:

- `compute_desired_plan` is currently 335 lines and performs all selection,
  duplicate matching, assignment, action classification, and diagnostics setup in
  one body.
- `compute_stateless_plan` is currently 259 lines and repeats the same high-risk
  duplicate matching and action-building concepts for `ObservedSlot` and
  `DesiredReservation`.
- Phase extraction lets tests compare each phase's output to the current
  end-to-end oracle without introducing new business rules.

**Alternatives considered**:

- **Unify desired and stateless planner algorithms first**: Rejected for the
  plan stage because shared code is only safe after byte-for-byte parity tests
  exist for both model families.
- **Rewrite planner logic around a new graph/matching abstraction**: Rejected as
  too risky for a behavior-preserving decomposition of lock-code safety logic.

## R-008: Actions and diagnostics get dedicated modules

**Decision**: Move drift/action classification to `actions.py` and redacted
snapshot construction to `diagnostics.py`.

**Rationale**:

- `_build_slot_action()` is 84 lines and action assembly is also repeated inline
  in both planner bodies. Action metadata such as `matched_by`,
  `requires_confirmed_empty`, `preflight_read`, `reason`, and `blocked_reason`
  is critical to confirmed-reset-before-reapply safety.
- `_build_plan_diagnostics_snapshot()` is 97 lines and must preserve keys,
  sorting, redaction, drift-field representation, and existing carry-over keys.
- Splitting these phases makes it easier to prove that raw slot codes remain
  excluded and that diagnostics remain structurally identical.

**Alternatives considered**:

- **Leave diagnostics in planner modules**: Rejected because diagnostics are a
  significant portion of the oversized functions and are independently pinned by
  FR-012.
- **Reduce diagnostics to a new schema**: Rejected because this feature must not
  change support or sensor-visible output.

## R-009: Remove `aislop` suppression only after thresholds pass

**Decision**: Delete the file-level `aislop-ignore-file` directive only after the
new package satisfies file-size, function-length, and parameter-count thresholds.
Do not replace it with new suppressions.

**Rationale**:

- FR-016 explicitly requires removal of the file-level suppression once the
  underlying complexity problems are resolved.
- Removing the directive before extraction would create noisy failures without
  improving safety.
- New files should each stay below 400 lines and helper functions below 80 lines
  as part of the decomposition acceptance criteria.

**Alternatives considered**:

- **Keep a compatibility file with a smaller suppression**: Rejected because the
  goal is to remove hidden complexity exceptions from this engine.
- **Suppress only the compatibility shim**: Rejected because the request-object
  strategy lets the shim satisfy the gate without suppression.
