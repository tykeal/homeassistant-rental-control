<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Specification Quality Checklist: Instance-Partitioned Static Random Door Codes

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-16
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No low-level implementation details (file layout, module names, APIs)
- [x] Focused on user value and business needs
- [x] Written for operators and implementers who must share one contract
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic and measurable
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No low-level implementation choices leak into specification

## Notes

- Design decisions were settled by the maintainer before drafting: scope
  limited to `static_random`, per-instance (not per-slot) partitioning,
  derivation from existing `start_slot` / `max_events` against a capacity
  constant defaulting to 250 with an advanced override, deterministic
  intra-instance probing ordered by reservation UID, accepted probe-order
  instability, and opt-out rollout. These are recorded as requirements and
  assumptions rather than open questions.
- Review round 2 closed a contradiction between FR-006 (disjointness
  assumes equal capacity) and the original FR-009 (capacity need not be
  set identically). Capacity is now stated as a property of the shared
  parent lock: optional to set, but mandatory to set uniformly when used.
  Added FR-009a, FR-009b, FR-023a, FR-024a, SC-009, SC-010, two edge
  cases, one assumption, and User Story 4 scenarios 4 and 5.
- A mismatched capacity override cannot be detected at runtime, because
  FR-005 forbids cross-instance visibility. This is documented as an
  accepted limitation alongside overlapping slot ranges, not solved.
- Related issues intentionally left unsolved here: #735 (force re-issue,
  needed to heal the existing live collision), #736 (persist the generated
  code), and the separate code-length issue.
- Module names, file paths, low-level implementation choices, and code
  organization details belong to the planning stage and are deliberately
  absent from this spec. Behaviour-defining rules such as UID-primary
  ordering, canonical block boundaries, probing order, and whole-space
  fallback are intentionally present because they are required for
  testability and interoperability across generation paths.
