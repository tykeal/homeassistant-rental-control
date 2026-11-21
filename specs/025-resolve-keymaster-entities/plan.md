<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Resolve Keymaster Entities by Verified Identity

**Feature**: `025-resolve-keymaster-entities` | **Date**: 2026-11-20 |
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
**Primary Dependencies**: Home Assistant >=2026.11.0, Keymaster v0.6.1/v0.6.5 entity contracts; HA's pinned AwesomeVersion comparator
**Storage**: HA config entries and existing persistent allocator/reconciliation stores
**Testing**: pytest-homeassistant, pytest, pytest-cov, unit and integration tests
**Target Platform**: Home Assistant async runtime
**Project Type**: Home Assistant custom integration
**Performance Goals**: No regression to refresh/code-generation cycle; bounded indexed lookups and no blocking event loop
**Constraints**: Fail closed, preserve public selectors and existing holds, no PINs in diagnostics; no physical-erasure claim
**Scale/Scope**: Multiple RC entries, overlapping numbers on distinct locks, disjoint ranges on shared locks, parent/child topology

The support floor is KM v0.6.1; the unsuffixed v0.0.0 development placeholder
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
   RC must own bounded raw-byte reads and writes for both registry and marker:
   do not use HA `Store.async_load`, `load_json`, Store save/quarantine,
   `write_utf8_file_atomic` or `save_json` on these paths. HA load can
   quarantine malformed JSON, log paths and error text, create a repair issue
   and return `None`; distinguish verified absence from unreadable/corrupt
   data and preserve evidence. Cap registry reads at 32 MiB, marker reads at
   64 KiB, and each directory evidence/cleanup scan at 4,096 entries; accept
   the exact file limit and fail closed at limit+1 or an incomplete absence
   scan. Count all directory entries, examine at most the 4,097th to detect
   overflow and require end-of-directory; never partially clean an overflowed
   scan. Surface incomplete evidence scans in persistent sanitized status;
   never assume a typical directory is below the bound. Use `O_NOFOLLOW`
   and descriptor `fstat` where supported; on Windows verify handle-based
   no-follow traversal of every component or fail closed without disk
   access. Validate present v1/v2 wrappers without write-on-read, retaining
   HA v1 same-major minor/default compatibility as detailed in the data
   model. Retain the distinct v4.0.3 downgrade behavior: an integer top-level
   `version: 2` is rejected before inner payload
   inspection, while malformed JSON can be quarantined and treated absent by
   that old version.
   Use an RC-owned adapter around HA's runtime `atomicwrites.AtomicWriter`,
   without a new package dependency. Verify required behavior on minimum HA
   2026.11.0 and supported CI HA lines, rather than relying on an exact API
   signature; fail closed if unavailable. Call `prepare_save_json` once per
   immutable snapshot in the executor, normalize to exact bytes, hash those
   bytes, and write the same bytes. Compare digests against exact raw bytes
   only; never reserialize decoded data for agreement. Preflight exact target
   envelope and pending/final marker bytes against their respective read caps
   before transaction mutation; oversize output fails without dispatch.
   Use a verifiable RC-owned temporary-file prefix, private permissions,
   same-directory atomic replacement and file/directory fsync or platform
   write-through acknowledgement. Cleanup only bounded exact-prefix owned
   temp files after validated recovery, under the shared async and
   process-shared OS locks with workers quiescent; cap scans at 4,096 entries,
   preserve authoritative files and stop cleanup on uncertainty. Use the same
   non-authoritative OS lock for all participating readers/writers; check the
   planned predecessor digest under it to reject stale snapshots. Use
   nonblocking POSIX `flock`/Windows `LockFileEx` with a five-second monotonic
   deadline starting before asynchronous allocator-lock acquisition; keep the
   event loop responsive and use remaining time for OS-lock retries.
   cancellation-aware bounded retry and a static retryable busy result.
   Verify real contention versus unavailable error codes on each supported
   platform; do not guess mappings. Windows ctypes uses `WinDLL` with
   `use_last_error=True` and immediately captures `get_last_error()`; raw
   lock violation 33 is busy before errno conversion, access denied 5 is
   unavailable. Require native tests before claiming verified Windows
   protected-operation support; until then gate those operations, not
   calendar-only functionality. Acquire the allocator async lock before
   the OS lock even for reads/cleanup. Windows locks a fixed range beyond EOF
   on a stable private file, without content initialization; never replace it
   while participants can run.
   Never chmod or change the ACL of an existing unsafe/unowned lock file.
   Test lockless setup, first refresh, calendar display and calendar-only
   check-in under contention and unsupported/unsafe/unavailable locking with
   a bounded non-raising fallback and event-loop heartbeat. Withhold shared
   issuance, reuse, release and new dependent hardware/ownership dispatch,
   retaining loaded claims/fences. The deadline does not bound calendar
   network fetches or already-started filesystem I/O; no blanket promise
   that all nonmutating reads avoid locks.
   Unsupported/unsafe locking fails closed without itself declaring persistent
   protective-unknown; do not access the ledger or dispatch. A cancelled
   waiter exits and drains; do not imply arbitrary started I/O cannot stall.
   Catch and sanitize exceptions from every serialization/read/write/executor
   boundary, including non-`OSError` exceptions and sensitive chains; no raw
   exception, path or traceback may reach RC/HA logs or callers. A writer
   error after possible replacement, including directory-fsync failure,
   remains ambiguous and protective-unknown. On restart, revalidate and
   re-acknowledge exact bytes before finalization or resuming; never dispatch
   on visible bytes alone. Shield/drain executor workers through repeated
   cancellation requests; keep locks until the result is known and state is
   applied, suppress target-dependent dispatch after cancellation, then
   propagate cancellation. Record prior and target exact-byte digests and
   prove crash recovery without dropping prior in-flight fences. Fsync is an
   engineering mechanism, not a user-selected durability policy or absolute
   hardware guarantee.
3. **Downgrade**: test that v4.0.3 refuses a JSON-decoded object with an
   integer top-level `version: 2` before inspecting its inner payload. The
   guard does not validate the payload or require that safety metadata be
   well-formed. This can prevent setup of **all** RC entries, including
   lockless entries; this is distinct
   from the new version, where protective-unknown preserves lockless setup and
   calendar displays but withholds new lockless code issuance. A corrupt v2
   file can instead be quarantined by HA and treated as absent by v4.0.3; its
   adoption recovery is not safe and does not reconstruct the missing safety
   metadata. Stop RC/HA and preserve the quarantined bytes and marker.
   Reinstalling compatible newer RC or restoring a matching backup pair alone
   cannot establish completeness or recency. The
   [external recovery gate](data-model.md#load-classification-and-protective-unknown-recovery)
   requires independently verifiable authoritative evidence; this release has
   no genuine historical-backup verifier and no supported external restore or
   override when history cannot be verified. The maintainer chose
   `block_and_proceed`: proceed without a staged release while affected
   installations remain blocked potentially indefinitely. Intact v1 upgrades
   and fresh installs with verified absence remain supported under their
   existing validation and adoption safety gates. Silent rollback of both files
   can be accepted as ordinary crash recovery, omit later safety state and
   permit unsafe PIN reuse. Detection is not guaranteed; known or suspected
   rollback remains blocked. The maintainer explicitly accepted this residual
   risk (`accept_residual`). Preventing undetected rollback requires an
   independent outside record protected from the same rollback, which this
   design does not provide. This guard is not
   a claim about all older versions or removed/replaced storage.
