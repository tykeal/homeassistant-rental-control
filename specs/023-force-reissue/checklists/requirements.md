<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Specification Quality Checklist: Force Re-Issue of a Door Code

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-17
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

- Like the feature 022 spec, this spec names existing modules, functions, issue
  numbers, and feature 022's own requirement numbers in its Problem Statement,
  Context, and Requirements sections. These are deliberate: the feature is a
  remedy for identified behaviour in identified code, and it composes guarantees
  that a merged predecessor already established. They anchor requirements to
  existing behaviour rather than prescribing new implementation structure.
- Seven design questions were settled by the maintainer and are stated as
  requirements rather than marked for clarification: dual entity-or-slot
  targeting (FR-002), single-target only (FR-004), the fail-closed checked-in
  guard with explicit override (FR-006), release of the replaced code rather
  than retirement (FR-018 plus the Known Limitation section), raw-code
  visibility restricted to dry-run responses (FR-022, FR-023), the narrow
  multiple-owner exemption to the release guard (FR-019), and an unchanged
  sensor attribute surface (FR-026).
- No clarification markers remain. Two were raised in the first draft and
  resolved by the maintainer:
  - **FR-013** — a pending re-issue lapses across a Home Assistant restart. The
    suppression is in-memory only and adds no persisted state; the operator
    invokes the service again. Documented under Edge Cases so the silently
    dropped pending re-issue is expected behaviour rather than a surprise.
  - **FR-026** — no attribute is added to advertise an in-flight re-issue. The
    sensor attribute surface is unchanged, so the downstream captive-portal
    contract is untouched. Recorded in Out of Scope.
- The release guard's multiple-owner condition is deliberately exempted for the
  one owner a forced re-issue re-homes. Blocking on it would forbid the only
  action that heals a duplicate, since multiple ownership of one code is the
  definition of a duplicate. The physical-state conditions are not exempted,
  the exemption does not reach any other owner of the record, and the ordinary
  release paths keep the full unmodified guard.
- 29 functional requirements, following the predecessor's discipline of keeping
  the count tight and each requirement independently testable.
