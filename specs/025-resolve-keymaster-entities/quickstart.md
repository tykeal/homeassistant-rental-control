<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Planning the Implementation and Tests

This is a plan, not an instruction to start tasks or implementation. Read
[spec](spec.md), [plan](plan.md), [research](research.md),
[model](data-model.md), [routing inventory](routing-inventory.md), and
[integration contracts](contracts/integration.md).
Use only simulated/disposable fixtures, never production locks. Before coding,
inspect current callers and storage implementations again; source paths may
change after this plan. The maintainer selected explicit same-original
attestation and a v4.0.3 downgrade guard. Persistence and downgrade behavior
must be implemented and verified before delivery.

## Required test matrix

Build release-derived fixtures for both KM v0.6.0 and v0.6.5: actual property
keys, HA slugification, registry ownership, event fields, available text and
native-`None`/`unknown` behavior. Exercise read, adoption, issuance,
date update, reset, confirmation and check-in for each. Include default,
area/generated and arbitrary renamed entity IDs and wrong-platform,
wrong-entry, wrong-slot, old-name-shaped and unregistered distractors.
Reproduce the two-device layout: slots 20–22 and 23–25, two managed per
calendar; assert no access to slots 22/25 and no false version warning.

Use the real HA entity registry to create and rename entities
(`async_update_entity(..., new_entity_id=...)`), disable/enable, remove and
recreate; do not simulate rename solely with `state_changed` or guessed bus
data. Test create/update/remove payloads, `old_entity_id`, changed old
values, tracker resubscription, stale callbacks, ID reuse, duplicate/conflict
injection where normal registry uniqueness precludes creation, and teardown
listener counts. Test missing and unavailable states, actual HA registry
placeholder `unavailable`/`restored: true`, synthetic restored `unknown`,
synthetic masked values, delayed KM setup, entry retry/reload, disabled and
removed entries, late entities and late live states. Assert recovery without
manual reload and no duplicate feedback. A mid-operation rename/reload must
block stale confirmation and remaining steps, preserve partial progress and
recover only with fresh evidence.

Upgrade persisted fixtures: unique and ambiguous normalized legacy values,
duplicate titles, disabled candidates, missing/replaced entries, repeated
migration, lockless entries, active stays, pending clears and selection
repair. Assert no migration writes to lock hardware. Test two distinct locks
with overlapping slots and colliding aliases, plus two RC entries with
disjoint ranges on one lock. Retain owners, observed aliases, adoption
conflicts, exclusions, forced holds and service behavior across restarts,
options saves and both dry-run services. Simulate crashes between each
config/ledger/fence persistence step and reload both entries; no code becomes
unreserved due to a partially persisted migration.

Inventory each actual source path in [routing-inventory.md](routing-inventory.md)
and add a test showing correct identity provenance, not simply an absence of
lockname-shaped strings. In particular test reset substring routing with an
unrelated reset ID, raw feedback snapshot reads, startup parent discovery,
both direct and override options/buffer date-update confirmations, and
per-step helper service calls on both baseline releases.

## Persistence, upgrade and rollback tests

The following are acceptance gates for the single v2 envelope protocol,
not an assumption that HA Store already guarantees them:

1. Instrument `Store.async_save`: a swallowed serialization/write error,
   read-only mode, delayed state, same-instance load returning pending data,
   and stopping state must never count as a durable acknowledgement. Inject
   envelope serializer failure and `WriteError` before write, during atomic
   replace, and an ambiguous error after replace/fsync; verify zero
   target-dependent new dispatch, safe logs and retained protection for any
   earlier operation already in flight. On release failure after replacement,
   restart from the actual new bytes and prove tombstone-based recovery
   rechecks the original slot without losing any later valid owner. Exercise
   HA `prepare_save_json` both `wb`/bytes and `w`/text encoder paths; pass
   mode through and assert the stored marker digest equals the actual bytes,
   including non-ASCII text and serialization errors.
2. Crash/restart before/after pending marker, after pending but before
   envelope replacement, after replacement but before final marker, after
   final marker, and before/during/after hardware call,
   after local confirmation, before/after envelope resolution, and at every
   migration/config-entry/cache update. Restart from actual disk bytes
   and fresh HA instances, not a pending Store cache. Restore pre-dispatch
   intent/fence bookkeeping without invoking hardware; observe original A
   with fresh verified evidence and allow re-dispatch only under the existing
   retry policy after all gates pass. No A hold is released by B. Exercise two RC
   entries concurrently, competing saves, shutdown and cancellation; an old
   callback or delayed write cannot overwrite newer envelope protection.
   Run both v1 genesis migration and successive v2 transactions. For exact
   prior digest restore the previous finalized/genesis marker and retry only
   the uncommitted target; for exact target digest finalize idempotently; for
   unexpected data, generation, transaction ID, absence or invalid
   predecessor, enter protective-unknown. Cover genesis-absent, quiescent v1,
   old delayed v1 writer and suspected external restore. Include an older
   already-dispatched A clear with a durable pending fence while a newer
   transaction crashes: rolling back the newer intent must retain A, not
   assume no earlier dispatch or erase partial effects.
3. Inject missing, corrupt and unsupported-version v1/v2 registry, read
   failure, missing/invalid mandatory v2 safety sections, absent marker,
   marker/envelope generation or digest mismatch, deleted/replaced storage
   and discarded per-entry cache. Include HA `Store.async_load` moving bad
   JSON to `.corrupt.*` and returning `None`. For protective-unknown, with a
   prior pending-clear fence on A, an unreadable held slot, an attestation
   and a tombstone: assert no adoption-based rebuild, issuance/reuse, reset,
   release or overwrite even if B/another A slot appears empty or exclusion
   is requested. Preserve corrupt files, report actionable repair and
   validate restoration only from a demonstrably current complete snapshot.
   If no complete evidence exists the hold remains. Separately test genuine
   first-run and legacy-absent behavior with no activation/partial/quarantine
   evidence under existing adoption gates. Validate v1→v2 migration at every
   marker/envelope boundary; do not claim deletion of both files or an old
   restore is always detectable.
4. Test untouched v1 input, new-reader replay/migration twice, retained owners,
   conflict and forced holds, and competing same-slot/shared-lock owners.
   Exercise the actual v4.0.3 loader with HA and the complete v2 **envelope**:
   it must reject the higher major version and surface `ConfigEntryNotReady`
   before allocator use. Verify that lockless as well as locked RC entries
   can be blocked and that reinstalling compatible newer RC recovers existing
   v2 state. Separately run corrupt-v2 JSON through actual HA Store and the
   v4.0.3 loader: assert HA quarantines it and returns `None`, the old loader
   reports registry absent and may enter adoption recovery, and old code does
   not enforce the marker. It is not safe to rely on that recovery: preserve
   `.corrupt.*` and marker, stop RC/HA, reinstall compatible RC and follow
   verified restore/operator repair. Also show that a mere payload bump has
   different behavior and extra v1 fields may be dropped. Never delete or
   restore a stale ledger to force downgrade; do not claim protection for
   other old versions or removed/replaced files.
5. Test legacy unique normalized candidates with a removed-and-recreated
   same-name entry, historical aliases and matching observed PINs: none prove
   the original KM ID. Test genuinely independently attributable legacy
   installs; explicit selection without attestation; a distinct
   same-original-entry attestation, cancel and negative/replacement choice;
   disabled candidates, same-name collisions and no candidate. Verify the
   attestation survives restart, preserves all owners and fences of two
   disjoint-range RC entries sharing a lock, and never resets, changes or
   shortens an existing guest code. A known A hold cannot transfer to B,
   even after attestation or if slot numbers or PINs match. Verify an
   attestation alone never confirms a clear or releases a PIN.
6. Exercise envelope/config correlation using explicit `(raw legacy selection,
   verified KM entry ID, association generation, transaction ID)` tuples:
   crash before/after envelope preparation and commit, HA config update,
   delayed config flush, and durable completion. Test legacy sentinel,
   multiple committed updates while disk still has an older recorded tuple,
   exact-prior replay, expected-new match, unknown/ahead transaction,
   generation mismatch, changed association, missing entry, and simultaneous
   unrelated options changes. Only recorded ancestors replay; mismatches retain
   work and cannot cause hardware side effects. Prove an in-memory matching
   config is not treated as an acknowledged HA disk save. For the public
   API-only path, assert the prior chain is retained and pressure reported.
   If using the optional private on-disk checkpoint adapter, test known
   format, unknown HA version/envelope/entry shape, corrupt/inaccessible
   file, duplicate/missing entry, missing fields and unproven writer
   quiescence: every uncertain case retains the chain and reports pressure.
   Disk contents and private config values never appear in logs. Preserve
   documented option-save exclusion reset.
7. Interleave ordinary coalesced saves and acknowledged protective commits,
   slow/failing executor writes, cancellation, restart, unload and two RC
   entries. Older snapshots must never overtake a fence. Exercise allocator,
   override and reissue locks under adversarial scheduling without deadlock,
   event-loop blocking or abandoned writes. Benchmark fsync-heavy paths and
   verify coalescing only for genuinely non-protective metadata.
8. Test contradictory attestations for entries proven to share the same
   original lock, same-entry concurrent submissions, and two unrelated
   physical locks with colliding slugs. A conflict cannot overwrite a
   verified association or merge claims; separately proven locks remain
   distinct and ambiguous legacy claims remain held. Test tombstone
   retention, successful bounded checkpoint pruning, interrupted prune,
   old queued callbacks and indefinite retention while any relevant hold
   remains.

For a pending clear on A, try empty B, another A slot, reused address,
replacement entry, and selection of B; none release or redirect it. Then
rename the same A identity and confirm with fresh A-slot name and PIN.
Exercise both baseline normal reset publications and synthetic empty,
whitespace and literal `None` name/empty PIN cases. Test reset event alone,
missing, restored, unavailable, masked and populated values, and provider
clear failure followed by local empty text. Confirmation means local state
only. Verify separately that owners, forced-reissue holds, conflicts,
unreadable observations and observed PIN continue to prevent reuse, and that
the existing redundant-owner rule does not free a still-reserved PIN.

Test exclusion only for an eligible unreadable slot on a verified association:
it affects the existing issuance gate, not evidence, writes or holds; verify
claimed-slot refusal, range/remediation expiry, option-save behavior and
existing initial cross-entry versus post-adoption entry-wide holds.
Also test the existing RC sensor attribute fallback, disjoint range targeting,
ambiguous lockname refusal, out-of-range removal by persisted exclusion,
idempotent withdrawal and dry-run, including withdrawal during unresolved
KM identity. None of those selectors proves a KM entity identity.

Send actual v0.6.0/v0.6.5 event shapes for verified parent and inherited child,
overridden child, unrelated or removed entries, shared physical addresses,
conflicting fields, missing/invalid physical ID, invalid/zero/out-of-range
slot, locked events and stale links. Only uniquely attributable unlocks
check in the right stay. Test manual, automatic, calendar-only and
normal/early checkout paths and options/buffer recomputation with distractor
date entities; child attribution does not authorize child or parent writes.
Include a child whose reset remains available while inherited text/date
controls are unavailable, override/notification switch exemptions, and the
slot-zero followed by corrected-slot event sequence.

Version tests must use loaded metadata independently of capabilities:
`v0.6.0`, `v0.6.5`, `0.0.0`, `v0.0.0`, `v0.0.0-dev`, `v0.6.0-rc.1`,
`v0.6.1rc1`, `v0.6.0+local`, missing/unparseable metadata, `7`, `2026.11`,
`20260101`, `0.6`, `0.0`, `v.0.0.71` and a verified older release.
Cover `IntegrationNotLoaded` explicitly and use HA 2026.11's actual supplied
AwesomeVersion comparator rather than a replacement ordering implementation.
For unknown cases, test both a safe operation that can proceed and one
withheld by an independent failing check. For older releases test good and
bad capabilities; version alone adds no block. Missing/disabled/unavailable/
ambiguous reset capability never produces a version diagnosis.

Capture normal and debug logs, notifications and diagnostics with synthetic
PINs in an echoed exception and unexpected event field. Assert zero leakage,
including on failure; sanitized context must still identify the entry, slot,
capability and remedy.

## Commands for the implementation stage

Run from the worktree root; Python 3.14 is required. Install/resolve the
project's test extra with uv; use `--assert=plain` as the Python 3.14 CI
workflow does.

```bash
uv sync --extra test
uv run --extra test pytest --assert=plain tests/unit/test_config_flow.py tests/unit/test_init.py tests/unit/test_startup_readability.py tests/unit/test_state_handlers.py tests/unit/test_keymaster_services.py tests/unit/test_event_overrides_apply.py
uv run --extra test pytest --assert=plain tests/unit/test_allocator_release_guard.py tests/unit/test_allocator_store.py tests/unit/test_reissue_service.py tests/unit/test_diagnostics.py tests/unit/test_keymaster_event_diagnostics.py
uv run --extra test pytest --assert=plain tests/integration/test_entity_id_rename.py tests/integration/test_allocator_cross_entry.py tests/integration/test_reissue_deferred_release.py tests/integration/test_checkin_tracking.py
uv run --extra test pytest --assert=plain tests/ --cov=custom_components.rental_control --cov-report=term-missing
uv run ruff check custom_components/ tests/
uv run --extra test mypy --show-error-codes custom_components/ scripts/ tests/
uv run reno lint
reuse lint
pre-commit run --all-files
```

Add the new tests to targeted commands. Full coverage is governed by
`pyproject.toml` (95%); `pre-commit` is mandatory before a future push and
must not be bypassed. Test performance if calendar processing, event parsing
or code generation is affected. Add a user-visible reno note during
implementation, not this planning stage. Independently rubber-duck review,
fix and re-review each stage as directed by the manager.
