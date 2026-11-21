<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Specification Quality Checklist: Resolve Keymaster Entities by Verified Identity

**Purpose**: Validate specification completeness and quality before proceeding to planning.

**Created**: 2026-11-20

**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation design mandated (languages, frameworks, APIs).
- [x] Focused on user value and business needs.
- [x] User stories and outcomes are accessible to non-technical stakeholders.
- [x] All mandatory sections completed.

## Requirement Completeness

- [x] No unresolved clarification markers remain.
- [x] Requirements are testable and unambiguous; FR-007 applies the development
  exception first, requires a parseable three-component release label with an
  optional `v` and recognized suffix, then compares it to the floor.
- [x] Success criteria are measurable.
- [x] Success criteria are technology-agnostic.
- [x] All acceptance scenarios are defined, including prereleases on both
  sides of the floor, incomparable `v0.6.1+local`, and non-release shapes
  `7`, `2026.11`, `20260101`, `0.6`, `0.0`, and `v.0.0.71` as unknown
  when loaded as version metadata. The historical tag fixture makes no
  assertion about the packaged manifest and does not change safety checks.
- [x] Edge cases are identified.
- [x] Scope is clearly bounded.
- [x] Dependencies and assumptions are identified.

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria.
- [x] User scenarios cover primary flows.
- [x] Acceptance scenarios define how to verify every measurable outcome.
- [x] Implementation choices are deferred; identity evidence describes the
  upstream compatibility contract.

## Notes

- FR-001–FR-003: Stories 1–2 and the distractor, unreadable-state and partial-effect
  edge cases cover target identity, operation routing and safety fences.
- FR-004–FR-006: Stories 2–3 cover startup, rebind, migration, topology, disjoint
  slot ranges, and lockless setup/calendar availability with issuance withheld
  under protective-unknown allocator state. Story 3 also checks persistent
  sanitized guidance to preserve the registry, marker and quarantine evidence
  and explains that external recovery is unavailable with no supported restore
  or override in this release when history cannot be verified, without
  claiming unknowable codes are present, absent, visible or reusable.
- FR-007–FR-008: Story 4 covers installed-version evidence, capability
  diagnostics and PIN redaction independently. Source checks verified the
  source contracts for v0.6.1 and v0.6.5, including common unique-ID
  derivation and lock-event fields; acceptance scenarios still require
  packaged runtime verification.
- FR-009–FR-012: Stories 1, 3 and 5 cover shared-lock ownership and service
  selectors, exclusion repair without narrowing gate scope, and protection
  of active credentials and pending clears across reassociation. The shipped
  release archives and HA state conversion explain why verified live text
  `unknown` can represent local emptiness; these checks do not prove physical
  erasure or authorize PIN reuse by themselves. Acceptance cases distinguish
  a live publication from HA's unavailable restored placeholder, an observation
  invalidated by reload, and synthetic masked or restored-state inputs.
- Blank/whitespace-only text and a literal name `None` with an empty PIN
  are synthetic regression fixtures for Rental Control's existing helper.
  The baseline reset fixture uses native `None` published by HA as `unknown`.
  Local confirmation, the pending-clear slot fence, and the
  shared allocator's ownership, adoption-conflict and forced-reissue guards
  have separate acceptance checks.
- This checklist assesses the draft, not implementation results or independent
  rubber-duck approval.
- The feature uses `025`, the next unused prefix after `024-slot-exclusion`.
  The branch and spec directory share the same unique prefix.
