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
change after this plan. For legacy-unknown provenance, the maintainer selected
an explicit, auditable attestation that the selected current entry is attached
to the same original physical lock, even if its former Keymaster entry was
deleted and recreated; this is not machine proof and never transfers known-A
work. The maintainer also selected a v4.0.3 downgrade guard. Persistence and
downgrade behavior must be implemented and verified before delivery.

## Required test matrix

Build release-derived fixtures for both KM v0.6.1 and v0.6.5: actual property
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
With a protective-unknown shared allocator in the new version, verify
lockless entries still set up and display calendars without Keymaster, while
new code issuance remains withheld until safety state is restored.

Inventory each actual source path in [routing-inventory.md](routing-inventory.md)
and add a test showing correct identity provenance, not simply an absence of
lockname-shaped strings. In particular test reset substring routing with an
unrelated reset ID, raw feedback snapshot reads, startup parent discovery,
both direct and override options/buffer date-update confirmations, and
per-step helper service calls on both baseline releases.

## Persistence, upgrade and rollback tests

The following are acceptance gates for the single v2 envelope protocol,
not an assumption that HA Store already guarantees them:

1. Instrument `Store.async_save` as HA behavior characterization: a swallowed
   serialization/write error, read-only mode, delayed state, same-instance
   pending-data load and stopping state are never a durable acknowledgement.
   The new registry/marker path must not use `Store.async_load`, `load_json`,
   Store save/quarantine, `write_utf8_file_atomic` or `save_json`. Inject
   synthetic PIN-bearing `OSError`, `ValueError`, serializer, decoder and
   executor failures, including sensitive causes/contexts and path-bearing
   errors, at the RC raw reader/writer boundaries. Capture HA and RC logs and
   assert only fixed sanitized result codes escape: no synthetic token,
   filename, raw exception/chain, traceback or raw status reaches any logger
   or HA caller. Verify the raw reader distinguishes both-path verified
   `ENOENT` with no other evidence from unreadable, malformed, oversized and
   unsupported input, retains corrupt bytes, detects bounded `.corrupt.*`
   evidence, and treats incomplete/over-limit evidence scans as
   protective-unknown. Enforce 32 MiB registry / 64 KiB marker limits and a
   4,096-entry directory scan cap: exact byte limits pass, limit+1 and scan
   overflow fail closed. Count unrelated entries, require end-of-directory
   at 4,096, detect overflow with the 4,097th, and delete no candidates if
   enumeration fails or overflows. Verify persistent sanitized scan-limit or
   scan-failure status for incomplete evidence scans and maintenance status
   for cleanup; do not assume typical directory sizes. Test the same output byte caps before
   any transaction replacement, including pending and final markers, with
   no mutation or dependent dispatch on oversize. Test descriptor-based
   no-follow opening where supported; on Windows test parent/final reparse
   points and verified handle-based no-follow traversal, or fail closed
   without disk access. Test present empty/invalid v1/v2 wrappers are not
   absent; compare valid v1 missing/different minor behavior with HA 2026.11.0
   without requiring its optional key or any write-on-read.
   It never invokes HA load/quarantine helpers. Any observation
   that HA's wrapper currently leaks is version-scoped research evidence, not
   a permanent assertion about future HA behavior.
   Exercise HA `prepare_save_json` in both `wb`/bytes and `w`/text modes;
   normalize once to exact bytes, assert byte-for-byte disk contents and that
   the marker digest matches those same bytes, including non-ASCII text.
   Verify private file permissions/ACL, same-directory atomic replacement,
   bounded owned-temp cleanup, and fail-closed rejection of symlinked
   `.storage`/targets. Verify the import and actual prefix/private-temp/
   replace/fsync acknowledgement behavior of HA's runtime `AtomicWriter` on
   minimum HA 2026.11.0 and supported CI HA lines without pinning a brittle
   Python signature; fail closed if required behavior is absent. Verify the
   process-shared OS lock with two processes; a second process holding it
   must prevent writes and cleanup without relying on process detection.
   On supported POSIX/Windows CI exercise nonblocking primitives, bounded
   contention with one five-second deadline starting before async allocator
   lock acquisition, retryable busy, release/retry,
   unsafe/unsupported sanitized unavailable without persistent
   protective-unknown absent separate ledger evidence, and repeated
   cancellation of a waiting worker without hanging its drain. Identify
   observed contention errno/Windows error using actual second-process
   contention, distinguishing unsupported/permission/unknown errors. With
   `WinDLL(..., use_last_error=True)`, capture `get_last_error()` immediately
   after failure: verify raw 33 busy before errno translation and real access
   denied 5 unavailable. Never chmod/change ACL of an existing unsafe or
   unowned lock file. Windows
   tests use `LockFileEx` on a stable empty file with the same range past EOF:
   crash after creation, process exit, recovery and no replacement during
   active participants. Test offline path repair only after all stop.
   Verify each in-process operation takes the allocator async lock before the
   OS lock, releasing in reverse and preventing same-process overlap. Verified
   Windows protected-operation support cannot be claimed until native lock
   and no-follow tests pass; otherwise gate protected operations while
   preserving calendar-only functionality.
   Hold each lock separately beyond the deadline and use deterministic
   calendar inputs to verify lockless setup, first refresh, display and
   calendar-only check-in reach the busy fallback within five seconds plus
   scheduler tolerance; issuance is withheld and an event-loop heartbeat
   progresses. Assert the busy request does no registry/marker I/O, cleanup
   or dependent hardware action, not that lock attempts or already-active
   work cease. Calendar network and already-started filesystem operations
   are outside the deadline; other nonmutating reads may await the lock.
   Repeat actual lockless setup, first refresh, display and calendar-only
   check-in with unsupported primitives, unsafe/unowned lock files and
   unavailable locking or Windows safe traversal. With deterministic calendar
   data, assert bounded non-raising fallback and a progressing event-loop
   heartbeat. That request does no registry/marker I/O or cleanup; shared
   issuance, reuse, release and new dependent hardware/ownership dispatch stay
   gated, and loaded claims/fences remain. Unavailability alone must not be
   reported as persistent protective-unknown corruption.
   On failure
   before replace, during replace, or after
   replace/fsync, verify zero target-dependent dispatch and retained prior
   protection. On release failure after replacement, restart from actual disk
   bytes and prove tombstone recovery rechecks the original slot without
   losing any later valid owner.
2. Crash/restart before/after pending marker, after pending but before
   envelope replacement, after replacement but before final marker, after
   final marker, and before/during/after hardware call,
   after local confirmation, before/after envelope resolution, and at every
   migration/config-entry/cache update. Restart from actual disk bytes
   and fresh HA instances, not a pending Store cache. Restore pre-dispatch
   intent/fence bookkeeping without invoking hardware; observe original A
   with fresh verified evidence and allow re-dispatch only under the existing
   retry policy after all gates pass. No A hold is released by B. Exercise two RC
   entries concurrently, competing saves, shutdown and repeated cancellation.
   Cancel while the executor worker is blocked, repeat cancellation, and prove
   `asyncio.shield` draining keeps the underlying worker and locks alive until
   its result is known; only after state is applied may cancellation propagate.
   Success keeps the acknowledged fence but dispatches no dependent hardware
   action; failure enters protective-unknown. An old callback or delayed write
   cannot overwrite newer envelope protection. Run both v1 genesis migration
   and successive v2 transactions. For exact
   prior digest re-acknowledge the exact validated prior bytes, restore the
   previous finalized/genesis marker and retry only the uncommitted target.
   Restart after v1 rollback with the exact finalized v1-genesis marker:
   re-acknowledge exact bytes and require quiescence/live-state agreement
   before activation retry; mismatch or suspected external rollback blocks.
   For exact target digest, validate and re-acknowledge those exact bytes
   before finalizing idempotently; do not reserialize to compare digests. A
   writer error after possible replacement, including directory-fsync
   failure, is immediately ambiguous/protective-unknown; visible bytes alone
   are not success. For failed re-acknowledgement or unexpected data,
   generation, transaction ID, absence or invalid predecessor, remain
   protective-unknown. Cover genesis-absent, quiescent v1,
   old delayed v1 writer and suspected external restore. Include an older
   already-dispatched A clear with a durable pending fence while a newer
   transaction crashes: rolling back the newer intent must retain A, not
   assume no earlier dispatch or erase partial effects.
   For genesis-absent rollback with the explicit absent sentinel, prove
   registry `ENOENT` and no quarantine evidence; remove only the exact pending
   marker with directory fsync and perform no envelope write or deletion.
   With valid v1 plus a quarantined sibling, preserve evidence and test the
   approved verified-backup-only policy, never risky operator override; this
   release provides no genuine verifier or supported external recovery path. A
   matching candidate's schema, transaction/generation, exact digest and
   re-acknowledgement establish consistency, not recency or completeness.
   This release has no genuine historical-backup verifier and no supported
   restore or override when history cannot be verified. Assert no
   restoration-based resume, adoption, issuance, reuse, release or dependent
   hardware action. A checksum, timestamp, absent logs,
   current slot observation or attestation cannot fill the gap, separately
   or together; markerless v1 cannot be promoted by a new genesis marker.
   Assert sanitized persistent status directs preservation of current
   registry, marker and quarantine evidence and explains external recovery
   is unavailable with no supported restore or override in this release
   when history cannot be verified. The maintainer chose to proceed without a
   staged release; the block may be indefinite, including installations that
   never activated v2. A different risk policy requires separate approval.
   Also verify intact v1 upgrades and fresh installs with verified absence
   remain supported under existing validation, quiescence, evidence-scan and
   adoption gates.
   Build two synthetic histories: an ordinary process crash and an external
   rollback of both files to the same earlier bytes after a later committed
   operation. Show byte/digest equality cannot distinguish them. With known
   or suspected rollback evidence, the second history must remain blocked
   despite matching bytes and favorable current slots/logs; do not claim the
   unflagged rollback can be detected; it may be accepted as ordinary crash
   recovery and omit later safety state, potentially allowing unsafe PIN reuse.
   The maintainer accepted this residual risk (`accept_residual`); preventing
   undetected rollback requires an independent outside record protected from
   the same rollback, which this design does not provide. Separately verify ordinary
   crash recovery under the protocol's writer, ordering and no-external-
   restore assumptions, retaining earlier fences and never inferring hardware
   completion from the marker.
3. Inject missing, corrupt and unsupported-version v1/v2 registry, read
   failure, size overflow, missing/invalid mandatory v2 safety sections,
   absent marker, marker/envelope generation or digest mismatch,
   deleted/replaced storage and discarded per-entry cache. Exercise the
   RC-owned raw reader directly; assert it does not call HA Store/load helpers,
   move/quarantine corrupt data, log paths or collapse read/decode errors
   into absence. For protective-unknown, with a
   prior pending-clear fence on A, an unreadable held slot, an attestation
   and a tombstone: assert no adoption-based rebuild, issuance/reuse, reset,
   release or overwrite even if B/another A slot appears empty or exclusion
   is requested. Preserve corrupt files and report sanitized repair status;
   never treat a matching snapshot as proof of external recovery.
   If no complete evidence exists the hold remains. Also verify that lockless
   entry setup and calendar displays remain available in the new version while
   new code issuance is withheld until safety state is restored. Separately test
   genuine first-run and legacy-absent behavior with no
   activation/partial/quarantine evidence under existing adoption gates.
   Verify the persistent status is sanitized and directs the operator to
   preserve current registry, marker and quarantine evidence, with no supported
   external restore or override in this release when history cannot be verified;
   do not assert visibility, presence or
   absence of codes missing from damaged state. Confirm HA's generic
   storage-corruption issue from the old v4.0.3 downgrade path may be
   submitted without restoring or validating allocator state; the new raw
   reader itself does not create that issue.
   Validate v1→v2 migration at every marker/envelope boundary; do not claim
   deletion of both files or an old restore is always detectable.
4. Test untouched v1 input, new-reader replay/migration twice, retained owners,
   conflict and forced holds, and competing same-slot/shared-lock owners.
   Exercise the actual v4.0.3 loader with HA and the intended v2 **envelope**:
   it must reject a decoded object with top-level integer `version: 2` before
   inspecting the inner payload and surface `ConfigEntryNotReady` before
   allocator use. Include a v2 header whose inner payload is missing or
   malformed to verify the guard is header-based. Verify that lockless as well
   as locked RC entries
   can be blocked and that reinstalling compatible newer RC recovers existing
   v2 state. Separately run corrupt-v2 JSON through actual HA Store and the
   v4.0.3 loader as downgrade characterization only: assert HA quarantines it
   and returns `None`, the old loader reports registry absent and may enter
   adoption recovery, and old code does not enforce the marker. The new
   reader must never take this path. It is not safe to rely on old recovery:
   preserve `.corrupt.*` and marker and stop RC/HA. Verify that reinstallation
   or a consistent backup pair alone cannot reconstruct or verify missing
   safety state; absent independent authoritative completeness evidence the
   new version remains protective-unknown.
   Also show that a mere payload bump has
   different behavior and extra v1 fields may be dropped. Never delete or
   restore a stale ledger to force downgrade; do not claim protection for
   other old versions or removed/replaced files.
5. Test legacy unique normalized candidates with a removed-and-recreated
   same-name entry, historical aliases and matching observed PINs: none prove
   the original physical lock. Test genuinely independently attributable
   legacy installs; explicit selection without attestation; and explicit,
   auditable same-original-physical-lock attestation for a distinguishable
   recreated current entry, plus cancel and negative/replacement choices. Do
   not assert software can prove physical continuity from entry IDs, names or
   addresses. Test disabled candidates, same-name collisions, no candidate,
   uncertainty and ambiguity. Verify the attestation survives restart,
   preserves all owners and fences of two disjoint-range RC entries sharing a
   physical lock, and never resets, changes or shortens an existing guest code.
   A known A hold cannot transfer to B, even after attestation or if slot
   numbers or PINs match. Verify an attestation alone never confirms a clear
   or releases a PIN; fresh trustworthy evidence for the original physical
   lock and exact slot remains required.
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
8. Test contradictory attestations for entries attested to share the same
   original physical lock, same-entry concurrent submissions, and two unrelated
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

Send actual v0.6.1/v0.6.5 event shapes for verified parent and inherited child,
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
`v0.6.0`, `v0.6.1`, `v0.6.5`, `0.0.0`, `v0.0.0`, `v0.0.0-dev`,
`v0.6.1rc1`, `v0.6.2rc1`, `v0.6.1+local`, missing/unparseable metadata, `7`,
`2026.11`, `20260101`, `0.6`, `0.0`, `v.0.0.71` and a verified older release.
Cover `IntegrationNotLoaded` explicitly and use HA 2026.11's actual supplied
AwesomeVersion comparator rather than a replacement ordering implementation.
Assert v0.6.0 and v0.6.1rc1 are below the v0.6.1 floor, v0.6.2rc1 is
above it, and v0.6.1+local is incomparable and therefore unknown.
For unknown cases, test both a safe operation that can proceed and one
withheld by an independent failing check. For older releases test good and
bad capabilities; version alone adds no block. Missing/disabled/unavailable/
ambiguous reset capability never produces a version diagnosis.

Capture normal and debug logs, notifications and diagnostics with synthetic
PINs in an echoed exception and unexpected event field. Assert zero leakage,
including on failure; sanitized context must still identify the entry, slot,
capability and remedy.

## Upstream fixture acquisition and re-pinning

The upstream characterization tests require the pinned Keymaster v0.6.1 and
v0.6.5 release/tagged-source archives, the v0.6.0 rejection-characterization
archives, and the Rental Control v4.0.3 archive. Pytest never downloads them:
it requires `RC_UPSTREAM_ARTIFACTS_DIR` and verifies every artifact before
use. CI acquires into the runner's temporary directory before pytest and
exports that variable only to the test step.

For a local online run, acquire once and then use the same verified directory:

```bash
uv sync --extra test
export RC_UPSTREAM_ARTIFACTS_DIR="${TMPDIR:-/tmp}/rc-025-upstream-artifacts"
uv run --no-project --python 3.14 python tests/fixtures/keymaster_releases.py acquire \
  --output-dir "$RC_UPSTREAM_ARTIFACTS_DIR"
uv run --no-project --python 3.14 python tests/fixtures/keymaster_releases.py verify \
  --directory "$RC_UPSTREAM_ARTIFACTS_DIR"
uv run --extra test pytest --assert=plain tests/integration/test_upstream_artifacts.py
```

The authentic provider-import gate uses a separate disposable HA environment
and a resolved, hash-pinned requirements lock. It does not modify the project
environment or `uv.lock`. `keymaster_import_requirements.txt` remains the
direct dependency list copied from the exact HA 2026.11.0 integration
manifests; `keymaster_import_probe.lock` pins the complete transitive install.
The lock is compiled for CPython 3.14.2/Linux x86_64 with uv 0.13.5. Its
resolution uses the project test/development export as constraints, preserving
all compatible versions already selected in `uv.lock`; newly required
transitives are recorded with exact versions and SHA-256 hashes. Refresh only
when intentionally updating this probe, then review the lock diff and rerun
both fresh-process imports:

```bash
export RC_UPSTREAM_ARTIFACTS_DIR="${TMPDIR:-/tmp}/rc-025-upstream-artifacts"
uv --version  # Must report uv 0.13.5
uv run --no-project --python 3.14.2 python scripts/update_keymaster_import_probe_lock.py
export UV_PROJECT_ENVIRONMENT="${TMPDIR:-/tmp}/rc-025-keymaster-import-env"
uv venv --python 3.14.2 "$UV_PROJECT_ENVIRONMENT"
uv pip sync --require-hashes --python "$UV_PROJECT_ENVIRONMENT/bin/python" \
  tests/fixtures/keymaster_import_probe.lock
uv pip sync --check --require-hashes --python "$UV_PROJECT_ENVIRONMENT/bin/python" \
  tests/fixtures/keymaster_import_probe.lock
"$UV_PROJECT_ENVIRONMENT/bin/python" -I \
  tests/fixtures/keymaster_releases.py probe-import --release v0.6.1
"$UV_PROJECT_ENVIRONMENT/bin/python" -I \
  tests/fixtures/keymaster_releases.py probe-import --release v0.6.0
```

The optional requirements are the exact direct dependencies declared by HA
2026.11.0's ZHA, Z-Wave JS, USB, hardware, and supervisor manifests. The first
fresh process must import the authentic v0.6.1 package and its real providers;
the second must identify v0.6.0's missing
`LARGE_LOCK_ACK_STORE_VERSION`. Missing dependencies or any other import
failure are test failures, not evidence against the support floor. This
disposable environment does not modify `uv.lock`.

`GH_TOKEN` is optional for public acquisition. When set, the downloader uses
it only for requests whose exact origin is `https://api.github.com`; it
refuses redirects for token-bearing requests. Release and codeload downloads
remain unauthenticated. Do not put a token in a command-line argument.
Acquisition allows at most two retries per request: transient failures use
short exponential backoff, and GitHub `Retry-After`/primary rate-limit reset
delays are honored only up to five seconds. Longer server delays and
authentication, provenance or digest failures stop without fallback.

For an offline test run, point `RC_UPSTREAM_ARTIFACTS_DIR` at an already
acquired directory, run the `verify` command above, then run the desired
pytest command. Verification and pytest do not access the network. Missing or
changed files fail closed; there is no skip or latest-release fallback.

GitHub-generated codeload ZIP bytes can change even when their source commit
does not. Do not respond by weakening hash checks or blindly replacing a
digest. For a deliberate source-archive re-pin, first confirm the pinned tag
still resolves to the same full commit SHA, then compare the complete archive
path set and every extracted file byte against that commit's tree, including
the manifest and license. Only if the source tree is identical may the
codeload archive's expected size and SHA-256 be updated; retain the commit-SHA
URL and provenance check, document old/new digests and evidence in the review,
and rerun clean acquisition, verification and characterization tests. A
release-package asset mismatch is different: verify its exact release ID,
asset ID, URL and published digest, and stop for upstream/review resolution
if any differ. Never repin it to another asset or latest release as a
workaround.

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
