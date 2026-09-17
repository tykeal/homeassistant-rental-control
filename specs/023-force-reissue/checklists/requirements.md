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

- [ ] No live clarification markers remain
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
- Five design questions were settled by the maintainer before specification and
  are stated as requirements rather than marked for clarification: dual
  entity-or-slot targeting (FR-002), single-target only (FR-004), the
  fail-closed checked-in guard with explicit override (FR-006), release of the
  replaced code rather than retirement (FR-018 plus the Known Limitation
  section), and raw-code visibility restricted to dry-run responses (FR-022,
  FR-023).
- Two clarification markers remain, both genuine open questions rather than
  settled decisions:
  - **FR-013** — whether an accepted-but-unconsumed pending re-issue must
    survive a Home Assistant restart. Both answers are defensible: persisting it
    is more robust for an operator who restarts before the next cycle, while
    letting it lapse keeps the suppression strictly transient and avoids storing
    an override that could later fire unexpectedly. The choice affects whether
    any new persisted state is required, so it is a scope question rather than
    a detail.
  - **FR-026** — whether the calendar sensor should advertise that a forced
    re-issue is in flight. This affects the downstream captive-portal contract
    and any operator automation reading sensor attributes, so it should not be
    guessed.
- 28 functional requirements, following the predecessor's discipline of keeping
  the count tight and each requirement independently testable.
