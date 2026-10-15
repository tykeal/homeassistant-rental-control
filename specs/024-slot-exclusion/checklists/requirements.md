<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Specification Quality Checklist: Slot-Exclusion Recovery Control

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-15
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details beyond required domain concepts
- [x] Focused on user value and business needs
- [x] Written for property-manager and maintainer stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No live clarification markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details beyond required domain concepts leak into specification

## Notes

- As with the feature 022 and 023 specs, this spec names existing modules,
  functions, line ranges, commits, and issue numbers in its Problem Statement,
  Context, Durability, and Requirements sections. These are deliberate. The
  feature is a remedy for identified behaviour in identified code, and the
  durability argument is only defensible against the specific history that
  motivates it. They anchor requirements to existing behaviour rather than
  prescribing new implementation structure.
- Every code reference was verified against `cb4544c` while drafting. Two
  references supplied with the original request were corrected:
  - The UNKNOWN-status definition is not in a `reconciliation/keymaster_observation.py`
    module; no such module exists. The authoritative locations are
    `reconciliation/enums.py` lines 19-45 for the `SlotStatus.UNKNOWN`
    semantics and `reconciliation/stateless_models.py` lines 50-77 for the
    `ObservedSlot.__post_init__` derivation of `readable` and
    `ObservedSlotStatus.UNKNOWN`.
  - `update_listener` spans `__init__.py` lines 252-278, not 252-276, and
    `report_forced_hold_deferrals` spans `allocator/services.py` lines 164-225,
    not 165-224. `register_allocator_services` begins at line 39.
  All other supplied references — `allocator/issuance.py` lines 76-77, 78-79,
  158-163, 221-235 and 227-235; `coordinator_helpers/code_allocation.py` lines
  187-205 and 208-227; `allocator/allocator.py` line 60;
  `reconciliation/desired.py` lines 257-258; `allocator/diagnostics.py` lines
  18-58 — were confirmed accurate.
- Six design questions were settled by the maintainer and are stated as
  requirements rather than marked for clarification: the three refusal guards
  with no override (FR-009 through FR-011), persistence in config-entry `data`
  rather than runtime memory or the reconciliation Store (FR-029, and the
  Durability section), mandatory per-cycle re-validation with auto-clear
  (FR-020 through FR-024), the standing notification as the carrier of the
  accepted risk (FR-025, FR-030), the deferral of a repairs flow in favour of a
  service (Alternatives Considered), and the preservation of the system-wide
  scope of `_pending_adoption` (FR-016).
- The `excluded` flag is deliberately asymmetric: setting an exclusion passes
  three guards, clearing one passes none (FR-012). Making clearing harder than
  setting would be the wrong failure mode, since clearing only ever returns the
  system to fail-closed.
- The options-flow interaction documented under Durability was verified
  directly rather than assumed. `update_listener` rebuilds `data` wholesale
  from `options` and then clears `options`, so an exclusion stored in `data`
  survives restarts but not a reconfiguration. The spec records this as
  intended behaviour because the loss direction is the safe one.
- 35 functional requirements, following the predecessors' discipline of keeping
  each requirement independently testable.
- Three findings from the first Copilot review were folded in rather than
  argued away, all three verified against `cb4544c`:
  - Filtering the shared observation for every reader would have changed
    code-release evidence. `_owner_may_be_programmed`
    (`allocator/allocator.py` lines 370-394) treats a covered slot with
    neither an observed code nor unreadability as unprogrammed, and orphan
    cleanup feeds it observations from the same builder
    (`allocator/services.py` lines 98-113). Because FR-010's claim-check is
    scoped to the addressed entry, an exclusion could otherwise have caused a
    removed entry's code to be released without proof of absence. FR-016 now
    requires gate-only filtering and FR-018 forbids releasing on the strength
    of an exclusion; SC-014 and a new edge case cover it.
  - `_adoption_complete` also requires readable coded slots to be accounted
    for (`coordinator_helpers/code_allocation.py` lines 197-205), so excluding
    every unreadable slot is not by itself sufficient. FR-015 is qualified
    accordingly, with SC-015 and an edge case.
  - The affected entry does not itself surface `adoption_pending`: when
    adoption is incomplete it submits no allocation requests at all
    (`coordinator_helpers/code_allocation.py` lines 86-95), so only the healthy
    entries reach that refusal. User Story 2 and the Problem Statement now
    distinguish the two outcomes, and the independent test uses reservations
    without existing allocations so that existing-code reuse cannot mask the
    gate.
- This stage delivers the specification only. Per the established pattern in
  `specs/022-shared-code-allocator` and `specs/023-force-reissue`, `plan.md`,
  `research.md`, `data-model.md`, `quickstart.md`, and `tasks.md` are produced
  in separate later commits once the specification has been accepted. No
  implementation, and no change to anything under `custom_components/`,
  `tests/`, or `README.md`, is part of this feature's specification stage.
