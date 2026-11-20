<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Resolve Keymaster Entities by Verified Identity

**Branch**: `025-resolve-keymaster-entities-plan` | **Date**: 2026-11-20 |
**Spec**: [spec.md](spec.md) | **Issue**: #954

## Summary

Bind a Rental Control (RC) entry to an actual Keymaster (KM) config-entry ID,
resolve exact entry-owned registry identities to current addresses, and use
fresh evidence throughout reads, writes, subscriptions, confirmation and
check-in. Preserve existing ledger, adoption, issuance and release policy;
identity uncertainty never creates permission to modify a credential. Detect
installed version independently from capability failures. See
[research](research.md), [model](data-model.md), and
[contracts](contracts/integration.md).

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant >=2026.11.0, Keymaster v0.6.0/v0.6.5 entity contracts; HA's pinned AwesomeVersion comparator
**Storage**: HA config entries and existing persistent allocator/reconciliation stores
**Testing**: pytest-homeassistant, pytest, pytest-cov, unit and integration tests
**Target Platform**: Home Assistant async runtime
**Project Type**: Home Assistant custom integration
**Performance Goals**: No regression to refresh/code-generation cycle; bounded indexed lookups and no blocking event loop
**Constraints**: Fail closed, preserve public selectors and existing holds, no PINs in diagnostics; no physical-erasure claim
**Scale/Scope**: Multiple RC entries, overlapping numbers on distinct locks, disjoint ranges on shared locks, parent/child topology

The support floor is KM v0.6.0; the unsuffixed v0.0.0 development placeholder
is a separate exception. Version status alone never adds a hardware block.
Non-baseline versions have no assumed identity variants: use only exact
verified supported identities, or fail closed per capability.

## Constitution Check

| Principle | Before research | Post-design |
|---|---|---|
| I: quality and tests | PASS: exercise identity, races and migrations with unit/integration tests; type, docs and coverage gates | PASS in design; persistence and downgrade tests are implementation release gates |
| II: atomic commits | PASS: safe, independently passing slices planned | PASS: coupled changes ship together where separation would be unsafe; no commit in plan stage |
| III: SPDX/REUSE | PASS: artifact headers required | PASS: headers and REUSE validation |
| IV: hooks | PASS: no bypass; full pre-commit before future push | PASS: commands in quickstart |
| V: DCO/co-authorship | PASS: future commits require actual human sign-off and agent attribution | PASS: no commit made |
| VI: consistent UX | PASS: existing service and config behavior preserved; actionable repairs | PASS: explicit attestation for legacy-unknown physical-lock continuity; keep lockless calendar availability distinct from the protective-unknown issuance gate |
| VII: performance | PASS: event-loop safe, no unbounded listeners | PASS: bounded cache, coalesced rebind, performance tests if affected paths change |

No constitution exception is proposed. The maintainer chose explicit
same-original-physical-lock attestation for legacy-unknown provenance, even
when the Keymaster entry was recreated, and a v4.0.3 envelope downgrade guard.
The new version must keep lockless setup and calendar displays available during
protective-unknown while withholding new code issuance. Implementation must
still pass the persistence, downgrade and safety tests.

## Project Structure

### Documentation

```text
specs/025-resolve-keymaster-entities/
├── spec.md, checklists/requirements.md
├── plan.md, research.md, data-model.md, quickstart.md, routing-inventory.md
└── contracts/integration.md
```

`tasks.md` belongs to the later task stage and is not produced here.

### Source and tests

```text
custom_components/rental_control/
├── __init__.py, config_flow.py, config_flow_helpers/, migrations.py
├── coordinator.py, coordinator_helpers/, startup_readability.py
├── listeners.py, state_handlers.py, keymaster_services.py
├── keymaster_service_helpers.py, event_overrides.py, event_overrides_helpers/
├── allocator/, reconciliation/, diagnostics.py, strings.json, translations/
└── services.yaml
tests/unit/  tests/integration/  tests/fixtures/
```

**Structure Decision**: Extend existing integration and test layout; introduce
one cohesive identity/capability boundary rather than a separate server or a
new public HTTP API. The precise module name can follow existing conventions.

## Implementation Architecture and Staged Dependencies

Each stage below is a planning slice, not permission to start implementation.
Ship only coherent tested slices: never temporarily switch mutation routing
without its corresponding evidence and lifecycle fences.

1. **Baseline characterization and accounting inventory (FR-001–012)**:
   Freeze upstream-derived entity/property and payload fixtures for both
   releases; map every constructed ID and all operation/confirmation paths.
   Inventory persisted owner, alias, forced hold, pending fence and exclusion
   formats and equality rules before choosing a storage migration. Add
   characterization tests of existing release-guard behavior. Determine how
   crash-safe persistence is actually committed, including concurrent RC
   entries; do not assume two HA stores can be updated atomically.
2. **Protective persistence foundation (FR-003, 009–011)**: implement and
   test the v1→v2 authoritative registry and independent activation marker,
   typed load outcomes, protective-unknown recovery, serialized commits and
   v4.0.3 envelope guard before enabling any new persisted association or
   attestation. Preserve all v1 owners/holds and legacy fields. Introduce
   dormant helpers separately if desired; activate v2 and its consumers
   together, never a partial unsafe rollout. Follow the
   [transaction protocol](data-model.md#authoritative-registry-envelope-and-transactions);
   do not treat HA Store's delayed or swallowed-error writes as durable. In the
   new version, protective-unknown must not prevent lockless entry setup or
   calendar displays, but the shared allocator must withhold new code issuance
   until safety state is restored.
3. **Association, ledger and capability boundary (FR-001, 004, 007, 009–011)**:
   only after stage 2, persist KM entry ID and a separate explicit attestation
   that the selected entry is the same original physical lock, under a
   correlated envelope/config transaction. A unique current slug alone cannot
   prove historical intent; count disabled candidates and preserve
   independently verifiable migration.
   Coordinate evidence for shared locks without merging unrelated locks that
   collide by slug. Until provenance is established, retain old data, claims
   and holds; known A work never migrates to replacement B. For legacy-unknown
   provenance only, the operator may explicitly attest that the selected
   distinguishable current entry is the same original physical lock, even if
   the original Keymaster entry was deleted and recreated. This is an auditable
   operator assertion, not machine proof of physical continuity; validate the
   current entry and its capabilities independently, and fail closed on
   uncertainty or ambiguity. Lockless is an explicit separate state. Resolve
   exact platform, entry, unique ID, enabled/loaded status and live state, and
   expose sanitized reasons.
   Existing owner, adoption-conflict, forced-reissue, observed-PIN and
   redundant-owner rules remain the authority for PIN release.
4. **Lifecycle and feedback (FR-003, 005, 012)**: per RC entry, build a
   generation-stamped mapping from verified capability identity to current
   entity ID. Subscribe to registry create/update/remove and relevant
   config-entry lifecycle and live state; invalidate before scheduling a
   coalesced re-resolution. Atomically replace tracked state subscriptions,
   unsubscribe old ones on rebind/unload, and revalidate identity/generation
   at callback and every dispatch/confirmation. Registry-created placeholders,
   old observations and restored/masked/unavailable states are not live
   evidence. Refresh after delayed registration or live-state publication.
   See contract for HA bus vs config-entry signals and rename payloads.
5. **Convert complete operation graph (FR-002, 003, 010–012)**: route
   startup/readability, snapshot/adoption/override reads, PIN/name/switch/date
   writes and confirmation, buffered options amendments, check-in/checkout,
   per-slot reset/name cleanup, reissue/service paths and pending recovery
   through the boundary; use the complete
   [routing inventory](routing-inventory.md), including options/date updates,
   regex reset feedback, service helpers and startup parent discovery. Resolve
   all capabilities needed to perform and
   confirm before dispatch, recheck between awaits; if identity changes, stop
   remaining actions, preserve partial effects and pending reconciliation.
   A live verified same-slot name/PIN pair using the existing trimmed empty
   tokens confirms local KM state only; it resolves the slot fence, not the
   separate allocator reservation. Exclusion stays a gate-only exception,
   never filters raw unreadable evidence used for release.
6. **Check-in and services (FR-006, 009)**: match each event's required valid
   physical lock `entity_id` to one verified source entry; corroborate fields
   and reject conflicts or non-unique physical sources. Parent/child routing
   additionally requires current verified relationship and current inherited
   slot evidence; child override, unavailable inheritance or stale links fail
   closed. Do not redirect writes. Preserve calendar-only behavior, current
   service schemas, supported sensor and lockname+slot selectors and inert dry
   runs; reject ambiguous selectors rather than reinterpret them. Preserve
   exclusion removal's documented out-of-range cleanup and attribute-based RC
   sensor fallback; selection is not authorization for a KM action.
7. **Version, privacy and remediation (FR-007, 008)**: obtain HA loaded
   integration manifest metadata, catch `IntegrationNotLoaded`, and use HA's
   comparator with the exact shape,
   exception and two-comparison algorithm in the contract. Report version
   independently of identity/capability status. Use allowlisted diagnostic
   fields; never log raw states, event/service data, PINs, or exception text,
   including debug logging. Provide targeted wait/enable/reselect actions,
   update translations and release note in the implementation stage.
8. **Verification and delivery**: execute the full quickstart matrix against
   both release contracts, then full tests/coverage, lint, types, REUSE and
   hooks. Any reproduction of the issue must be described as such; area is
   not established as the cause. Perform the manager's independent rubber-duck
   review/fix/re-review for each later stage before its commit or PR.

## Complexity Tracking

No constitution violation or exception is planned. The extra identity and
generation boundaries are required to prevent cross-lock effects and stale
confirmation; reusing lockname-shaped IDs is unsafe.

## Decisions, specification alignment and proof gates

1. **Attestation and spec**: FR-004 and FR-011 distinguish legacy-unknown
   provenance from work already known to belong to entry A. For legacy-unknown
   provenance only, the operator may explicitly attest that the selected,
   distinguishable current entry is the same original physical lock, even if
   the original Keymaster entry was deleted and recreated. This statement is
   an auditable operator assertion, not machine proof of physical continuity;
   current entry/capability identity and live slot evidence are still checked
   separately. Selection, slug, address or PIN matching alone is insufficient.
   Attestation does not confirm a clear, release a PIN, replace current
   original-slot evidence, or transfer known-A work to B, even if both entries
   refer to the same physical lock. Ambiguity or uncertainty retains the hold
   and fails closed.
2. **Persistence**: implement the specified v2 envelope transaction protocol
   and prove it on actual restart, write failures, and concurrent entries.
   HA Store's normal return is not a reliable persistence acknowledgement,
   even with `atomic_writes=True`; configuration saves are delayed too.
   The chosen HA atomic-file helper uses fsync as an engineering mechanism,
   not a user-selected durability policy or an absolute disk guarantee.
   Record prior and target exact-byte digests and prove process-crash
   rollback/finalization without dropping prior in-flight fences.
3. **Downgrade**: test that v4.0.3 refuses a JSON-decoded object with an
   integer top-level `version: 2` before inspecting its inner payload. The
   guard does not validate the payload or require that safety metadata be
   well-formed. This can prevent setup of **all** RC entries, including
   lockless entries; this is distinct
   from the new version, where protective-unknown preserves lockless setup and
   calendar displays but withholds new lockless code issuance. A corrupt v2
   file can instead be quarantined by HA and treated as absent by v4.0.3; its
   adoption recovery is not safe and does not reconstruct the missing safety
   metadata. Preserve the quarantined bytes and marker, stop RC/HA, reinstall
   compatible newer RC, and restore a verified, sufficiently current complete
   envelope and matching marker. Reinstallation alone cannot reconstruct lost
   state; without a valid restore, remain protective-unknown. This guard is not
   a claim about all older versions or removed/replaced storage.
