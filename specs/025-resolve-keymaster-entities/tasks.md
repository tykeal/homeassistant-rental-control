<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Resolve Keymaster Entities by Verified Identity

**Input**: [spec](spec.md), [plan](plan.md), [research](research.md),
[data model](data-model.md), [contract](contracts/integration.md),
[routing inventory](routing-inventory.md), and [quickstart](quickstart.md).

**Reviewed implementation baseline**: The specification, design, plan and
tasks were merged through #960 at
`208578ba541f03b6187daf06e643768087b51381`. The manager's independent review
is complete and formal analysis returned GO; implementation is authorized.
Legacy-unknown provenance may be attested to a recreated KM entry on the same
original physical lock; this does not transfer known-A work to B or turn
attestation into clear/release evidence. Protective-unknown permits lockless
setup and calendar displays but withholds new code issuance. The prior
draft-stage implementation hold is closed.

**Execution**: The v0.6.1 support-floor correction in #961 was merged at
`10d3bfa42366a2cc92aa2f784c8bd836239b6da8`. Follow its stable v0.6.1 floor,
exact v0.0.0 development exception, and unchanged unknown/incomparable gating.
These are implementation-stage tasks, not authorization to implement during
the TASKS stage. Tests are required by the specification.
Use simulated/disposable fixtures, never production locks. Each `[P]` group
is parallel only after its stated prerequisites; tasks touching the same file
or using incomplete interfaces must be serialized. Write behavioral tests
before the corresponding change; characterization tests may already pass.
Use the detailed acceptance matrices in the linked documents, not just the
examples below. Do not change release, adoption, exclusion or credential
policy to make tests pass. At each later stage checkpoint, stop for the
manager's independent rubber-duck review, fix findings and obtain re-review
before progressing or committing. Keep coupled unsafe changes dormant or
together as one coherent reviewed slice.
For every future agent-assisted commit, use the actual human author's DCO
`Signed-off-by` and a `Co-authored-by` trailer identifying the actual assisting
agent with the appropriate name and email under the constitution and
repository instructions. For Copilot contributions, use
`Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
After each commit, inspect its message and verify the DCO and appropriate
coauthor trailers before proceeding; never bypass hooks. Commit task-list
updates separately from the implementation or documentation they track.

## Phase 1: Setup and characterization

- [X] T001 Verify Python 3.14, HA 2026.11 and project test dependencies against `pyproject.toml` and `uv.lock`; prepare the existing test environment using `specs/025-resolve-keymaster-entities/quickstart.md`.
- [X] T002 [P] Build deterministic upstream harness acquisition in `.github/workflows/build-test.yaml`, `tests/fixtures/upstream_artifacts.json` and `tests/fixtures/keymaster_releases.py`: pin packaged KM v0.6.1/v0.6.5 release asset URLs and verified SHA-256 digests, and RC v4.0.3 commit `ad8a04ee385534c84a48c0ac294b056e470e1188`; verify provenance and hashes before use, acquire into an isolated temporary CI directory before pytest, and hard-fail on absent/mismatched artifacts rather than skip. Allow the same verified artifacts to be supplied locally without pytest network access. Separately reproduce the v0.6.0 official package import failure using its pinned artifact and authentic dependencies; verify the missing `LARGE_LOCK_ACK_STORE_VERSION` rather than attributing an unrelated dependency failure to the floor. Import the real v0.6.1 package in a fresh HA process with authentic provider dependencies and no Keymaster, provider or constant stubs; a full import alone is not proof of runtime behavior. Then load the supported release packages and actual old RC loader in isolated HA test processes with a fake provider for runtime scenarios; provide synthetic original/recreated KM entries on the same physical lock and unrelated physical locks for attestation tests. Use the repository's pinned checkout/locked `uv` CI conventions, never production locks. Verify upstream licenses and preserve required notices if redistributing anything; do not vendor third-party source without license review. Distinguish tagged-source `v0.0.0` from packaged manifests and validate release properties against HA slugification.
- [X] T003 [P] Re-scan callers and document all constructed IDs, reads, services, confirmations, listeners, owner/alias equality, pending fences, exclusions and persisted formats in `specs/025-resolve-keymaster-entities/routing-inventory.md`; classify each as routing/evidence, legacy selector or display, including new call sites.
- [X] T004 [P] Characterize existing owner, adoption-conflict, forced-reissue, observed-PIN and redundant-owner release rules in `tests/unit/test_allocator_release_guard.py` and initial cross-entry versus post-adoption entry-wide holds in `tests/integration/test_allocator_failclosed.py`.
- [X] T005 Inspect HA's actual storage, config-entry and lifecycle APIs and allocator/override/reissue lock acquisition; record writer quiescence, existing delayed writes and lock-order constraints in `specs/025-resolve-keymaster-entities/routing-inventory.md` before designing migration.

## Phase 2: Foundational safety and identity boundaries

**Purpose**: Complete and verify protective storage before committing
associations or attestations. Keep new behavior dormant until the coherent
activation checkpoint; do not ship routing without its lifecycle and safety
fences. Tests below require actual HA disk and fresh runtime instances where
specified.

### Storage tests and implementation

- [X] T006 [P] Add HA storage characterization tests in `tests/unit/test_allocator_store.py` for swallowed Store serialization/write failures, read-only and stopping modes, delayed saves and same-instance pending-data loads; none is a durability acknowledgement.
- [ ] T007 [P] Add v2 schema and exact-byte tests in
  `tests/unit/test_registry_envelope.py` for required typed ledger,
  associations, attestations, fences, holds, tombstones and pending-config
  sections; unsupported versions; both `prepare_save_json` modes; exact
  non-ASCII bytes/digests; private permissions/ACL; atomic replacement; and
  symlink rejection. Test exact-at-limit and limit+1 for the 32 MiB registry
  and 64 KiB marker caps on both input and exact serialized output, including
  oversize target/pending/final bytes before any transaction mutation and
  no dependent dispatch. Test directory scans with exactly 4,096 total
  entries (including unrelated names), end-of-directory, and a 4,097th
  entry; over-cap/incomplete cleanup must delete none, while incomplete
  evidence scans produce persistent sanitized scan-limit/scan-failure
  status. Test `O_NOFOLLOW` and descriptor `fstat` where available; on
  Windows test parent and final reparse points, no-follow handle traversal
  and safe fail-closed behavior when unavailable (ACL alone is insufficient).
  Test true absence
  versus unreadable/corrupt/oversized paths, retained corrupt bytes,
  present-empty/invalid wrappers, valid v1 wrappers with missing or differing
  integer minors and optional key, invalid v1 payloads, and unsupported v2
  safety schemas. Compare valid v1 handling with real HA `Store` on the
  minimum version while asserting that the new raw reader never writes on
  read or treats a present empty wrapper as absence. Test bounded
  `.corrupt.*` evidence detection including protective-unknown on an
  incomplete/over-limit scan, and no calls to HA `Store.async_load`,
  `load_json` or quarantine helpers. Inject synthetic PIN-bearing
  `OSError`, `ValueError` and serialization/decoding failures, including
  sensitive exception causes/contexts and path-bearing errors; capture HA
  and RC logs and assert only fixed sanitized result codes escape any
  boundary. Assert no raw exception or chain is returned, raised to HA,
  logged, or included in status. Verify the RC path does not invoke HA
  logging wrappers. Test owned-temp cleanup under quiescence, active/pending
  protection, symlink/decoy files and scan-limit exhaustion; authoritative
  registry, marker and `.corrupt.*` files must remain untouched. Keep any
  HA-wrapper leak characterization as version-scoped research evidence, not
  a permanent assertion about future HA behavior. Never use real credentials
  or process-wide logging changes. Verify the process-shared OS lock with a
  second process holding `.storage/rental_control.code_registry.io_lock`;
  readers/writers and cleanup must fail retryably busy without mutation at the
  five-second deadline; cancellation while contended exits and drains even
  after repeated cancellation. Exercise actual nonblocking POSIX and Windows
  primitives on their CI platforms and identify actual contention errno or
  Windows error versus permission/unsupported/unknown failures; only proven
  contention returns busy. Windows binding must use
  `ctypes.WinDLL(..., use_last_error=True)` and immediately capture
  `ctypes.get_last_error()` after `LockFileEx` failure: verify real competing
  process error 33 is busy before errno translation while a real access-denied
  error 5 is unavailable. Test unsafe/unowned existing lock files are never
  chmodded or have ACLs changed. Test interrupted calls remain deadline-bounded,
  lock release and retry success, unsafe path and unsupported primitive
  failures (sanitized unavailable, not persistent protective-unknown absent
  separate ledger evidence). Test Windows empty lock file and crash after
  creation before locking, recovery after process exit, stable path identity
  without rename/unlink while participants run, and offline repair after all
  participants stop. Assert in-process reads, writes and cleanup take the
  allocator async lock before the OS lock, release in reverse, and cannot
  overlap even if the OS permits same-process reentry. Do not use process
  detection. Do not claim verified Windows protected-operation support until
  native locking and path traversal tests pass; otherwise gate protected
  operations while preserving calendar-only functionality.
- [ ] T008 [P] Add marker transition and crash/restart tests in `tests/integration/test_registry_transactions.py` using actual disk bytes and fresh HA instances: genesis-absent, quiescent v1, successive v2, prior/target digests, every pending-marker/envelope/final-marker boundary, injected writer errors before replacement and after replacement but before fsync acknowledgement, invalid predecessor/IDs/generations, exact-byte re-ack success/failure after restart (without reserialization), and a prior dispatched A fence retained during rollback of a later intent. After v1 rollback, restart with the exact finalized v1-genesis marker and validate/re-acknowledge it before retrying activation; mismatched marker or suspected external rollback fails closed. For pending genesis with explicit absent predecessor and no registry/quarantine evidence, verify rollback removes only the exact pending marker with directory fsync and performs no phantom envelope write, replacement or deletion.
- [ ] T009 [P] Add protective-unknown tests in `tests/integration/test_registry_protective_unknown.py` for raw-reader corrupt, missing, unreadable, oversized and unsupported files/sections, marker mismatches, partial history, bounded `.corrupt.*` evidence (including an incomplete/over-limit scan), suspected restore, cache loss and valid first-run/legacy absence. In particular, a valid v1 registry with a matching `.corrupt.*` sibling, or an incomplete evidence scan, and no valid marker authenticating those exact v1 bytes is protective-unknown: do not migrate/adopt, authorize an operator override, or rebuild. Assert no HA Store/load/quarantine calls for registry/marker paths, no rebuild, overwrite, reset, issuance, reuse or release despite exclusion or empty B, and preserve loaded claims, fences, corrupt bytes and repair evidence. Assert persistent sanitized status directs preservation of current registry, marker and quarantine evidence and explains external recovery is unavailable, with no supported restore or override in this release when history cannot be verified; do not claim unknowable codes are present, absent, visible or reusable. Exercise actual new-version lockless setup/reload, calendar display and check-in with typed load failures and protective-unknown, including allocator initialization before setup; neither may raise and block non-access-mutating behavior or cause KM lookup or warning. Assert new lockless code issuance remains withheld while safety state is unknown; no replacement code is invented.
  Exercise the approved verified-backup-only policy for v1 plus a quarantined
  sibling: this release has no genuine verifier or supported external recovery.
  Matching exact bytes, schema, generation, transaction and successful
  re-acknowledgement are necessary consistency checks, not evidence of
  recency or completeness. This design defines no historical external-backup
  verifier: independently verifiable authoritative evidence of all committed
  transactions and possible hardware effects through quiescence would be
  necessary, but no acceptance path is supported here. For this
  protective-unknown external-recovery case, assert recovery unavailable with
  no supported restore or override in this release, no
  resume/adoption/issuance/reuse/release or
  dependent hardware action, and preserved evidence. Test that backup success,
  checksum, timestamp, absent logs, current slots and identity attestations,
  individually or together, do not establish completeness. A markerless v1
  backup cannot be promoted by manufacturing a genesis marker in this case.
  Assert persistent sanitized status says verified completeness has not been
  established, external recovery is unavailable, markerless v1 alone cannot
  prove completeness, and there is no automatic recovery or override.
  The maintainer chose `block_and_proceed`: proceed without a staged release
  and keep affected installations blocked potentially indefinitely, including
  legacy systems that never activated v2; a separate policy decision is
  required for any riskier recovery. Intact v1 upgrades remain supported and
  must pass the existing validation, quiescence and no-corruption/partial-history
  tests. Verify fresh installs with verified absence remain supported under
  existing adoption and evidence-scan gates. The no-resume/adoption restriction
  above applies only to protective-unknown external recovery, not to intact-v1
  upgrades or healthy fresh installs under their existing gates. Do not invent
  external-recovery proof.
  In a separate pair of synthetic histories, crash after an acknowledged
  state versus externally restore the same pair after a later committed
  operation; assert the raw bytes and digests can be identical. With known
  or suspected rollback evidence, fail closed even if observations and logs
  seem favorable. An unflagged rollback of both files may be accepted as
  ordinary crash recovery and omit later safety state, potentially permitting
  unsafe PIN reuse; do not claim detection or safe recovery for that
  indistinguishable history. The maintainer explicitly accepted this residual
  risk (`accept_residual`); preventing undetected rollback requires an
  independent outside record protected from the same rollback, which this
  design does not provide.
  Independently test ordinary crash recovery under the documented
  single-writer, ordering and no-external-restore assumptions, preserving
  prior dispatched fences and never inferring hardware outcome from a marker.
  Hold the allocator async lock and, separately, a process lock for longer
  than the five-second total lock deadline. With deterministic calendar
  inputs, verify actual lockless setup, first refresh, display and
  calendar-only check-in complete through the busy fallback within that
  deadline plus scheduler tolerance, while issuance is withheld. Assert a
  concurrent event-loop heartbeat progresses and the busy request performs no
  registry/marker read/write, cleanup or dependent hardware action; lock-file
  attempts and other already-started work are not prohibited. Do not assert
  that all unrelated nonmutating reads avoid locks, or bound calendar network
  access and already-running filesystem I/O.
  Repeat with unsupported primitive, unsafe/unowned lock file and unavailable
  locking or safe Windows traversal. Actual lockless setup, first refresh,
  display and calendar-only check-in must complete without raising, with
  deterministic inputs and a progressing event-loop heartbeat. Assert bounded
  unavailable fallback, sanitized status, no registry/marker I/O or cleanup
  by that request, no shared issuance/reuse/release or new dependent
  hardware/ownership dispatch, and retained loaded claims/fences. Unavailable
  alone must not be reported as persistent protective-unknown corruption.
- [ ] T010 [P] Add actual HA/v4.0.3 loader tests in `tests/integration/test_registry_downgrade.py` against the real HA v1 `Store`: empty decoded envelope returns absent; omitted `minor_version` defaults to 1; exact v1 returns its `data`; integer top-level `version: 2` rejects before payload inspection or allocator use, including missing/malformed inner payload and lockless setup; malformed JSON is quarantined and returns absent; malformed envelope field types fail at the actual HA version/index operations and old RC wraps load failure as `ConfigEntryNotReady`. Separately test that corrupt v4.0.3 JSON may be followed by fresh v1/default state and does not enforce the marker. With surviving marker, the new RC raw reader must classify that v1/default plus marker as protective-unknown; reinstallation or HA repair confirmation alone cannot restore/validate missing state. This is downgrade characterization only; the new reader must never call Store or quarantine. Contrast payload-only bump and dropped extra v1 fields; never assert automatic safety recovery.
- [ ] T011 [P] Add concurrency, cancellation, allocation/reissue and release-tombstone tests in `tests/integration/test_registry_concurrency.py` for two RC entries, stale delayed writers, slow/failing executor writes, shutdown, no lock inversion, no abandoned write, bounded coalescing, post-replace failure and later valid owners. Inject repeated cancellation while an executor write is blocked; assert each cancellation is drained through `asyncio.shield`, the underlying worker is not cancelled, allocator/process locks remain held until its result is known, and only then cancellation propagates. On success retain the acknowledged fence but dispatch no target-dependent hardware action; on failure enter protective-unknown without raw exception leakage. Also test two-process file-lock coordination. Inject crashes before/after pending marker, envelope and final marker and before/during/after dependent set-code, name/PIN/date writes and reuse; no dependent side effect occurs without its own acknowledged protective owner/allocation or reissue transaction, and restart retains earlier owners/holds without inferring hardware dispatch.
- [ ] T012 Implement validated complete v1 ledger preservation and explicit typed v2 envelope/load outcomes in `custom_components/rental_control/allocator/store.py` and `custom_components/rental_control/allocator/models.py`. Use an RC-owned bounded raw-byte reader in the executor for registry and marker; do not call HA `Store.async_load`, `load_json`, Store save/quarantine, or any wrapper that logs paths/errors or moves corrupt bytes. Distinguish both-path verified `ENOENT` with no other evidence from unreadable, invalid UTF-8/JSON, oversized, malformed and unsupported input; preserve corrupt bytes and detect old `.corrupt.*` evidence with a bounded exact-pattern scan, treating an incomplete/over-limit scan as protective-unknown. A valid v1 plus a matching corrupt sibling, or an incomplete evidence scan, without a valid marker authenticating those exact v1 bytes is protective-unknown, not a basis to migrate/adopt or authorize an override. Keep v4.0.3's separate top-level-version downgrade and corrupt-JSON quarantine semantics. Provide persistent sanitized status directing preservation of current registry, marker and quarantine evidence and explaining external recovery is unavailable, with no supported restore or override in this release when history cannot be verified, without claims about unknowable codes. In the new version, typed protective-unknown/load failure must not raise through shared allocator initialization and block lockless entry setup or non-access-mutating calendar/check-in behavior: either bypass Keymaster allocator initialization for lockless entries with an independently enforced issuance gate or preserve a non-raising safe protective-unknown state. Keep shared issuance, reuse and release blocked while protection is unknown.
  Validate present v1/v2 wrappers and payloads; a present empty or invalid
  wrapper is not absent. Honor HA v1 missing-minor default and valid
  same-major minor compatibility without write-on-read, and do not require a
  legacy wrapper key that HA does not require; unsupported v2 protection
  schema fails closed. For busy, unsupported, unsafe or unavailable locking,
  preserve non-raising lockless setup, first refresh/display and
  calendar-only check-in while gating shared issuance/reuse/release and new
  dependent hardware/ownership dispatch; do not infer persistent corruption
  solely from lock unavailability.
  For known/suspected external restore or rollback, a matching candidate pair
  and re-acknowledgement never establish recency or completeness. Report
  external recovery unavailable and retain the gate; this release has no
  historical-backup verifier, supported restore or operator override.
- [ ] T013 Implement a private RC-owned activation-marker and exact-byte
  transaction writer in `custom_components/rental_control/allocator/store.py`
  using the existing HA runtime `atomicwrites.AtomicWriter` dependency
  directly, not HA's logging wrapper; add no package dependency. Verify
  required behavior on minimum HA 2026.11.0 and supported CI HA lines without
  relying on brittle exact signatures; fail closed if unavailable. Prepare each
  immutable snapshot once with HA `prepare_save_json` in the executor, validate
  its `wb`/bytes or `w`/text pair, normalize to exact bytes, hash those bytes
  and write the same bytes in binary overwrite mode. Retain private tempfile
  permissions (POSIX mode `0600`; verify the supported Windows ACL boundary),
  same-directory atomic replacement and file/directory fsync or platform
  write-through acknowledgement. Give temp files a verified RC-owned prefix
  with an opaque per-operation ID. Coordinate readers/writers/cleanup through
  the private process-shared OS lock with nonblocking POSIX `flock` or Windows
  `LockFileEx` fixed range beyond EOF, without content initialization; acquire
  the allocator async lock first for all in-process disk operations. Do not
  replace or remove the lock path while participants can run. Compare
  the planned predecessor digest under it before replacement. Use nonblocking
  lock attempts with one five-second monotonic deadline starting before
  asynchronous allocator-lock acquisition, bounded backoff and cancellation
  checks; keep the event loop responsive and return retryable busy without
  this request's ledger mutation for contention
  and sanitized unavailable for unsupported/unsafe locking, not persistent
  protective-unknown without independent corruption evidence. Preflight exact
  envelope and pending/final marker bytes against their read caps before
  starting the transaction. After validated
  recovery, under both shared locks and with workers quiescent, perform
  bounded cleanup of only
  regular non-symlink files matching that exact owned pattern; preserve
  authoritative files and stop cleanup on uncertainty. Validate the fixed
  `.storage` path, reject symlinked storage directories/targets, and document
  the trusted-config-directory boundary for path-based replacement. Convert
  serialization, encoding, hashing, raw read/decode, writer, replace/fsync,
  executor and cleanup `Exception` failures—including sensitive
  non-`OSError` chains—to typed allowlisted stage results without retaining,
  logging, chaining or exposing raw exceptions, filenames or tracebacks.
  Cancellation remains control flow and workers must finish before releasing
  the lock. A failure after possible replace, including directory-fsync
  failure, is ambiguous and protective-unknown; on restart validate and
  re-acknowledge exact bytes before finalization or resume. Never reserialize
  decoded data to confirm digests. Acknowledge pending marker, envelope and
  final marker in that order.
- [ ] T014 Implement idempotent marker recovery and v1 genesis migration in `custom_components/rental_control/allocator/store.py`: prove old-writer quiescence and disk/live snapshot agreement, compare digests against exact raw bytes without reserialization, re-acknowledge validated bytes before finalization/availability, roll back only an exact proven predecessor, recognize and re-acknowledge its matching finalized v1-genesis marker on restart before retrying activation, finalize only an exact validated target after successful re-ack, and for pending genesis with an absent predecessor remove only its exact pending marker after verified registry absence/no corruption evidence and directory fsync—never write/delete a phantom envelope. Otherwise enter protective-unknown; never infer or dispatch hardware work from restored bookkeeping.
- [ ] T015 Replace delayed authoritative ledger writes in `custom_components/rental_control/allocator/allocator.py`, `custom_components/rental_control/allocator/issuance.py`, `custom_components/rental_control/allocator/reissue.py` and `custom_components/rental_control/allocator/singleton.py` with allocator-wide serialized protective commits. In `custom_components/rental_control/__init__.py` and the singleton, wire T012's non-raising or safely gated lockless path so T009 can exercise real setup and calendar displays under protective-unknown. Acknowledge the complete owner/allocation/reissue state and final marker before any dependent set-code, PIN/name/date write, publication or reuse; carry transaction/generation tokens and revalidate after releasing local locks. Coalesce only non-protective latest-state snapshots, propagate failures, retain the lock until executor completion on cancellation, and avoid service/config calls or override/reissue lock acquisition under it.
- [ ] T016 Add durable original-entry/slot/owner clear fences, atomic resolution and retained release tombstones in `custom_components/rental_control/allocator/store.py` and `custom_components/rental_control/event_overrides_helpers/shell_compat.py`; on ambiguous post-replace failure freeze exposure, reload disk and recheck original evidence and guards without deleting later owners.
- [ ] T017 Implement bounded, guard-aware tombstone checkpoint pruning and PIN-free count/age pressure reporting in `custom_components/rental_control/allocator/allocator.py` and `custom_components/rental_control/allocator/diagnostics.py`; retain referenced, unvalidated or pending tombstones indefinitely and recover failed pruning through the marker protocol.
- [ ] T018 Verify T006–T017 in an isolated test configuration against `tests/unit/test_allocator_store.py`, `tests/unit/test_registry_envelope.py`, `tests/integration/test_registry_transactions.py`, `tests/integration/test_registry_protective_unknown.py`, `tests/integration/test_registry_downgrade.py` and the storage/fake-consumer portions of `tests/integration/test_registry_concurrency.py`; defer actual converted-operation assertions in that last file until T033/T051 and integrated verification at T062/T067. Keep v2 activation and all production consumers dormant until T062.

### Association, capabilities and lifecycle

- [ ] T019 [P] Add registry identity tests in `tests/unit/test_keymaster_identity.py` for exact entry/platform/domain/property/slot unique IDs using HA slugification, enabled and loaded ownership, wrong-entry/platform/slot distractors, duplicates, live state, restored flags, unavailable and synthetic masked states. For both actual KM releases test inherited child properties independently: the reset button may be available while text/date and most switches are unavailable, so no reset is allowed without verified same-slot authority and all required confirmation controls; override-parent and notifications switches are exempt from the inheritance availability gate, not from their other checks or operation authorization.
- [ ] T020 [P] Add association/config correlation tests in `tests/integration/test_association_transactions.py` for exact old/new tuples including absent versus `None`, sentinel and lockless mode, predecessor chains, multiple changes, all marker/config/flush crash boundaries, exact-prior replay, ahead/unknown/missing entries and concurrent unrelated options.
- [ ] T021 [P] Add shared legacy repair tests in `tests/integration/test_association_attestation.py` for independent proof, selection without attestation, explicit same-original-physical-lock assertion, cancel, disabled/missing/colliding candidates, scoped and contradictory concurrent attestations, shared owners and known-A work versus replacement B. Exercise legacy-unknown provenance with a recreated KM entry on the same original physical lock: separate explicit attestation and acknowledged transaction can bind it, but selection, current name, physical-ID match or PIN alone cannot; separately validate current entry and capabilities, and require fresh trustworthy original-physical-lock/exact-slot evidence plus existing guards for release. Attestation alone neither confirms clear nor authorizes release. Separately prove known-A holds never transfer to B even on the same physical lock; preserve ambiguous work and existing gates. Exercise actual config/options flow choices, initially unselected confirmation, labels, validation errors and restart persistence.
- [ ] T022 [P] Add real HA registry and lifecycle tests in `tests/integration/test_keymaster_lifecycle.py` for creation, actual rename/update/remove payloads, old values, disabled/re-enabled records, config-entry dispatcher/state hooks, late state, registration/subscription race, queued stale callbacks, ID reuse and repeated teardown.
- [ ] T023 Implement the exact capability/observation boundary in `custom_components/rental_control/keymaster_identity.py`: derive supported property IDs from KM entry ID and HA slugify, verify registry ownership, platform, unique identity, disabled/loaded status, current live state and generation; check child inheritance, authority and availability for each property, including override-parent/notifications exemptions without granting reset authority. Return typed sanitized reasons without assuming variants for non-baseline versions.
- [ ] T024 Implement per-entry lifecycle management in `custom_components/rental_control/keymaster_identity.py` and `custom_components/rental_control/__init__.py`: global registry create/update/remove coverage, config-entry dispatcher and selected-entry state hooks, invalidation before coalesced re-resolution, listener-before-snapshot race closure, atomic rebind and complete unload cleanup.
- [ ] T025 Implement committed association transactions and config tuple-chain replay in `custom_components/rental_control/allocator/store.py` and `custom_components/rental_control/coordinator_helpers/config_update.py`: preserve raw legacy selection and unrelated options, record scoped operator attestation and selected entry for unknown historical provenance, correlate transaction ID/generation, fence mismatches, retain history by default and report chain pressure without claiming HA config durability.
- [ ] T026 Implement explicit verified-entry selection and separate opt-in original-physical-lock attestation for legacy-unknown provenance, including a recreated KM entry, in `custom_components/rental_control/config_flow_helpers/steps.py`, `custom_components/rental_control/config_flow_helpers/schemas.py` and `custom_components/rental_control/migrations.py`; add matching prompts, descriptions and errors in `custom_components/rental_control/strings.json`, `custom_components/rental_control/translations/en.json` and `custom_components/rental_control/translations/fr.json`. Explain that this is a historical operator assertion, not clear or release evidence; count disabled candidates, preserve independent-proof migration and unresolved versus intentional lockless, and never transfer known A work to B.
- [ ] T027 Implement scoped shared-owner association and atomic alias/hold migration in `custom_components/rental_control/allocator/registry.py`, `custom_components/rental_control/allocator/adoption.py` and `custom_components/rental_control/allocator/allocator.py`: group only proven or explicitly cross-attested entries, reject contradictions and retain unpartitionable claims; colliding slugs do not merge locks.
- [ ] T028 Verify T019–T027 in isolated tests with `tests/unit/test_keymaster_identity.py`, `tests/integration/test_association_transactions.py`, `tests/integration/test_association_attestation.py` and `tests/integration/test_keymaster_lifecycle.py`; prove associations and attestations cannot be published without acknowledged protective storage, while production activation remains gated until T062.

## Phase 3: User Story 1 — Manage reservations regardless of entity names (P1)

**Goal**: Resolve every managed-slot operation to the verified current identity
and preserve reservation behavior. **Independent test**: Run each release's
read/adopt/issue/amend/reset lifecycle with unrelated current IDs and
distractors; assert every read, dispatch and confirmation uses the intended
entry and slot, including unmanaged-slot and area-layout checks.

- [ ] T029 [P] [US1] Add per-release full lifecycle tests in `tests/integration/test_verified_slot_lifecycle.py` for default, area-generated and arbitrary IDs, two locks with overlapping slots, distractors, managed ranges 20–21 and 23–24 with unmanaged 22/25, adoption and no false version warning.
- [ ] T030 [P] [US1] Add dispatch/confirmation tests in `tests/unit/test_keymaster_services.py` and `tests/unit/test_verified_service_helpers.py` for all name/PIN/switch/datetime/reset capabilities, wrong-slot/address distractors, preflight, each inter-await invalidation, partial effects and no whole-lock fallback; assert an available inherited-child reset is never pressed if same-slot authority or required live confirmation controls are absent.
- [ ] T031 [P] [US1] Add options and override date amendment tests in `tests/integration/test_verified_date_updates.py` for buffer recomputation, direct/override confirmation, suppression, renamed/distractor date entities and preservation of codes on date-only changes.
- [ ] T032 [US1] Route startup readability, adoption snapshots and coordinator observations through the identity boundary in `custom_components/rental_control/startup_readability.py`, `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`, `custom_components/rental_control/coordinator_helpers/keymaster_observation.py` and `custom_components/rental_control/coordinator.py`; retain existing adoption/readability gates.
- [ ] T033 [US1] Convert each set/reset/cleanup step and its confirmation in `custom_components/rental_control/keymaster_services.py`, `custom_components/rental_control/keymaster_service_helpers.py` and `custom_components/rental_control/event_overrides_helpers/shell_apply.py` to verified identity and generation preflight/revalidation; require T015's acknowledged protective allocation/reissue transaction before dependent name/PIN/date/switch writes and preserve partial progress and pending reconciliation across awaits.
- [ ] T034 [US1] Convert override set/update/clear snapshots and buffered date updates in `custom_components/rental_control/event_overrides_helpers/apply_set.py`, `custom_components/rental_control/event_overrides_helpers/apply_update.py`, `custom_components/rental_control/event_overrides_helpers/apply_clear.py` and `custom_components/rental_control/coordinator_helpers/coordinator_config_shell.py` to verified same-slot operations and confirmation.
- [ ] T035 [US1] Verify the inventory's converted read/write/confirmation paths and both baseline lifecycles in an isolated harness with `tests/integration/test_verified_slot_lifecycle.py` and `tests/integration/test_verified_date_updates.py`; run the false-version-warning assertion after T057 replaces the old warning, and rerun the complete lifecycle at T067. No routing switch is deliverable without the Phase 2 safeguards and the relevant US2/US5 integration below.

## Phase 4: User Story 2 — Recover safely when Keymaster entities change (P1)

**Goal**: Rebind once and reject stale observations across lifecycle changes.
**Independent test**: Delay setup and publication, rename/reload during an
operation, reuse the old address and verify only fresh identity-attributed
feedback can affect the original slot, with no listener leaks.

- [ ] T036 [P] [US2] Add real tracker behavior tests in `tests/integration/test_entity_id_rename.py` for every feedback property, old/new address, unrelated reset substring, deleted/recreated or disabled identity, conflicting registry records and repeated reload/unload listener counts.
- [ ] T037 [P] [US2] Add startup and in-flight recovery tests in `tests/integration/test_verified_lifecycle_recovery.py` for late registration/live states, actual restored unavailable placeholder, synthetic restored `unknown`, rename between dispatch and confirmation, stale queued callbacks and preserved partial effects.
- [ ] T038 [US2] Replace regex/substring event authorization and guessed snapshot reads in `custom_components/rental_control/state_handlers.py` with generation-stamped address-to-slot/property mapping and freshly verified feedback while preserving suppression rules.
- [ ] T039 [US2] Integrate the per-entry lifecycle manager with `custom_components/rental_control/__init__.py` and `custom_components/rental_control/startup_readability.py`; rebind on association, registry, entry and live-state changes, refresh readiness after late publication and unsubscribe old trackers.
- [ ] T040 [US2] Verify race, recovery and single-feedback behavior in `tests/integration/test_entity_id_rename.py`, `tests/integration/test_verified_lifecycle_recovery.py` and `tests/integration/test_keymaster_lifecycle.py`.

## Phase 5: User Story 3 — Preserve assignments and check-in routing (P1)

**Goal**: Preserve migration, shared ranges, public selectors and attributable
parent/child check-in. **Independent test**: Upgrade persisted multi-entry,
shared-lock, parent/child, ambiguous and lockless fixtures, restart, then
exercise both release event shapes and existing public service selectors.

- [ ] T041 [P] [US3] Add migration/restart tests in `tests/integration/test_verified_association_migration.py` for unchanged settings, ranges, active stays, holds and exclusions; unique independent proof, unresolved legacy, repeated upgrade, disabled candidates, same-name replacement and lockless operation must not touch hardware. Include legacy-unknown attestation to a recreated entry on the original physical lock and known-A non-transfer to B; contrast current-version protective-unknown lockless display with withheld new code issuance and the separately tested v4.0.3 top-level version-2 header setup block.
- [ ] T042 [P] [US3] Add real release payload tests in `tests/integration/test_verified_checkin.py` for verified physical source, parent and inherited child, overridden child, stale links, conflicting names/IDs, shared source ambiguity, missing/invalid ID, locked/zero/out-of-range events and corrected-slot follow-up.
- [ ] T043 [P] [US3] Add service compatibility tests in `tests/integration/test_verified_public_services.py` for shared/disjoint ranges, colliding aliases, `force_reissue` and `set_slot_exclusion`, sensor registry and unique attribute fallback, lockname-plus-slot, ambiguous refusal, out-of-range removal and inert dry runs.
- [ ] T044 [US3] Replace name-based parent/child discovery and event attribution in `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py` and `custom_components/rental_control/listeners.py` with verified current config-entry topology, physical lock ID, corroborating fields and same-slot inheritance; enforce each child property's authority/readiness independently and do not redirect writes or treat available reset/override/notifications as authorization.
- [ ] T045 [US3] Route automatic/manual check-in and normal/early checkout date reads through verified capabilities in `custom_components/rental_control/sensors/checkin/runtime.py` and `custom_components/rental_control/sensors/checkinsensor.py`; retain calendar-only transitions when lockless or event attribution fails. In `custom_components/rental_control/__init__.py` and `custom_components/rental_control/allocator/singleton.py`, allow current-version lockless setup and calendar displays under protective-unknown while withholding new lockless code issuance until safety state is restored, gating shared allocator issuance/reuse and preserving evidence; keep the old v4.0.3 downgrade behavior distinct.
- [ ] T046 [US3] Preserve existing entry selection and selector semantics in `custom_components/rental_control/coordinator_helpers/exclusion_targets.py`, `custom_components/rental_control/coordinator_helpers/reissue_targets.py`, `custom_components/rental_control/allocator/exclusion_service.py` and `custom_components/rental_control/allocator/reissue_service.py`; selection identifies RC only, exclusion withdrawal remains cleanup, and verified identity is required before hardware actions.
- [ ] T047 [US3] Verify migration, shared ownership and both release payloads in `tests/integration/test_verified_association_migration.py`, `tests/integration/test_verified_checkin.py`, `tests/integration/test_verified_public_services.py` and `tests/integration/test_allocator_cross_entry.py`.

## Phase 6: User Story 5 — Keep guest access and ownership safe during repair (P1)

**Goal**: Preserve guest access and original-slot fences under uncertainty,
repair and restart. **Independent test**: Break A's identity/state with an
active guest and pending clear, restart and select B; only fresh evidence on
the original A slot can resolve its fence, with separate release guards intact.

- [ ] T048 [P] [US5] Add clear/restart tests in `tests/integration/test_verified_clear_recovery.py` for A versus B, other A slot, reused address, replacement entry, same-identity rename, restart at each pre/during/post hardware and local-confirmation boundary, and no unintended retry solely from restored intent.
- [ ] T049 [P] [US5] Add empty-text and release guard tests in `tests/unit/test_verified_clear_evidence.py` for both release native `None`→`unknown`, synthetic blank/whitespace/literal `None`, absent, unavailable, restored, masked and populated values, reset event alone and failed provider clear; separately test owners, forced holds, conflict, observed PIN and redundant-owner retirement without reuse.
- [ ] T050 [P] [US5] Add uncertainty/exclusion tests in `tests/integration/test_verified_exclusion_safety.py` for active guest protection, unverified association, claimed slots, initial cross-entry and adopted entry-wide holds, eligible gate-only exclusion, raw unreadable evidence, expiry and option-save reset.
- [ ] T051 [US5] Connect durable original-slot fences to clear preflight, dispatch, fresh same-slot confirmation and recovery in `custom_components/rental_control/event_overrides_helpers/shell_compat.py`, `custom_components/rental_control/event_overrides_helpers/apply_clear.py`, `custom_components/rental_control/event_overrides_helpers/shell_cleanup.py` and `custom_components/rental_control/coordinator_helpers/store_sync.py`; commit before dispatch and commit resolution before exposing reuse. Release override/reissue locks before requesting allocator transactions, then recheck generation when reacquiring local state, including the greedy cleanup/eviction path.
- [ ] T052 [US5] Preserve separate allocator release, adoption and reissue guards in `custom_components/rental_control/allocator/release_guard.py`, `custom_components/rental_control/allocator/recovery.py`, `custom_components/rental_control/allocator/reissue_holds.py` and `custom_components/rental_control/coordinator_helpers/reissue.py`; attestation, empty observation or exclusion alone never frees a PIN.
- [ ] T053 [US5] Apply verified-association and current same-slot eligibility without widening exclusions in `custom_components/rental_control/coordinator_helpers/slot_exclusions.py`, `custom_components/rental_control/coordinator_helpers/unreadable_slots.py` and `custom_components/rental_control/allocator/exclusion_service.py`; preserve entry-wide/cross-entry gates and raw observations.
- [ ] T054 [US5] Verify safety and all hardware-call/crash boundaries with `tests/integration/test_verified_clear_recovery.py`, `tests/unit/test_verified_clear_evidence.py`, `tests/integration/test_verified_exclusion_safety.py` and `tests/integration/test_registry_transactions.py`; local empty text confirms KM state only, not physical erasure or automatic PIN release.

## Phase 7: User Story 4 — Diagnose version and capability failures separately (P2)

**Goal**: Explain compatibility and capability failures independently without
credential leakage. **Independent test**: Vary loaded version and identity/
capability readiness independently; capture normal/debug logs, notifications
and diagnostics with synthetic PINs embedded in states, payloads and errors.

- [ ] T055 [P] [US4] Add loaded-metadata/comparator tests in `tests/unit/test_keymaster_version.py` for v0.6.0/v0.6.1/v0.6.5, both unsuffixed zero forms, `v0.0.0-dev`, `v0.6.1rc1` below floor, `v0.6.2rc1` above floor, `v0.6.1+local` incomparable, missing/unparseable metadata, `7`, `2026.11`, `20260101`, `0.6`, `0.0`, `v.0.0.71`, older release and `IntegrationNotLoaded`; for unknown and below-floor cases test safe and independently blocked operations.
- [ ] T056 [P] [US4] Add privacy tests in `tests/integration/test_verified_identity_privacy.py` capturing normal/debug logs, notifications and diagnostics with synthetic PINs in state, unexpected event fields, service failures and echoed exception text, including an exception raised by the greedy cleanup/eviction path; require useful sanitized entry/slot/capability/remedy and redacted versus unavailable distinction.
- [ ] T057 [US4] Implement loaded-only metadata version diagnosis using HA's actual comparator in `custom_components/rental_control/keymaster_identity.py` and replace the guessed-reset version check in `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`: exact zero exception, three-component release shape, `<` then `>=`, otherwise unknown; version alone neither blocks nor authorizes.
- [ ] T058 [US4] Sanitize state, event, service, exception and calendar paths in `custom_components/rental_control/state_handlers.py`, `custom_components/rental_control/keymaster_services.py`, `custom_components/rental_control/keymaster_service_helpers.py`, `custom_components/rental_control/startup_readability.py`, `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`, `custom_components/rental_control/coordinator_helpers/calendar_parsing.py`, `custom_components/rental_control/calendar.py` and `custom_components/rental_control/event_overrides_helpers/shell_cleanup.py`; do not log exception tracebacks or text that may echo PINs.
- [ ] T059 [US4] Provide allowlisted diagnostics and actionable wait/enable/reselect/repair statuses in `custom_components/rental_control/diagnostics.py`, `custom_components/rental_control/coordinator_helpers/diagnostics.py`, `custom_components/rental_control/strings.json` and `custom_components/rental_control/translations/en.json`; keep association, capability and version statuses distinct. For protective-unknown, persist sanitized guidance to preserve current registry, marker and quarantine evidence and explain external recovery is unavailable, with no supported restore or override in this release when history cannot be verified; do not expose PINs, storage contents or raw exceptions or claim unknowable codes are present, absent, visible or reusable.
  When no sufficiently current verified complete marker-bearing backup has
  been established, persistently report that fact, why a markerless v1 backup
  alone cannot prove completeness, and that no supported restore or override
  exists in this release and
  issuance and release remain blocked potentially indefinitely, including on
  legacy installations that never activated v2. Also surface an
  incomplete/over-limit evidence scan without paths or contents.
- [ ] T060 [US4] Verify `tests/unit/test_keymaster_version.py`, `tests/integration/test_verified_identity_privacy.py` and `tests/unit/test_diagnostics.py` with both safe and unsafe capability cases; run the false-version-warning lifecycle assertion deferred by T035 in `tests/integration/test_verified_slot_lifecycle.py`, and confirm missing/disabled/unavailable/ambiguous reset never implies an incompatible version.

## Phase 8: Integrated activation and cross-cutting verification

- [ ] T061 Add upgrade/activation tests in `tests/integration/test_registry_activation.py` using actual on-disk v1, old delayed writers, active owners and pending-clear work: simulate each missing protective consumer, crash/restart at every marker/envelope boundary and attempt issuance, reissue, clear and reuse. No v2 activation or dependent hardware/credential action is possible until all consumer wiring and safety gates are present; completed activation preserves the ledger and fences without replaying hardware, and both RC entries recover safely.
- [ ] T062 After T018, T028, T035, T040, T047, T054 and T060 pass their stated checkpoints and T061's activation tests are written, activate v2 and all protective consumers together in `custom_components/rental_control/allocator/singleton.py`, `custom_components/rental_control/allocator/allocator.py`, `custom_components/rental_control/allocator/issuance.py`, `custom_components/rental_control/event_overrides_helpers/shell_apply.py`, `custom_components/rental_control/event_overrides_helpers/apply_clear.py` and `custom_components/rental_control/__init__.py`; run T061 and the deferred T011 operation assertions to passing as part of this task. Quiesce old v1 writers, validate disk/live state, install/finalize v2 with the marker protocol, and publish no association, attestation, release or mutation until its own protective transaction is acknowledged. Ensure a typed protective-unknown/load failure cannot raise and block new-version lockless setup or non-access-mutating calendar/check-in behavior, using T012's bypass or non-raising protective-unknown state; maintain the issuance/reuse/release gate while unknown. Prove this with T009's actual setup/reload tests, separately from v4.0.3's version-header downgrade block. Ship this as one coherent reviewed change, never as an independently deployed intermediate phase.
- [ ] T063 [P] Document operator repair, original-physical-lock attestation for legacy-unknown provenance including recreated entries, known-A non-transfer, retained holds, v4.0.3 top-level version-2 downgrade guard independent of payload validity, lockless display versus withheld new issuance during protective-unknown, corrupt downgrade caveat and the external-backup evidence gate in `README.md`. Warn that HA's generic corruption-repair confirmation, an HA auto-created default or fresh v1 registry written by v4.0.3 after quarantine is not restored safety state: stop RC/HA and preserve evidence; reinstallation/confirmation or a matching backup pair alone is not recovery. Do not expose stored content or claim auto recovery, unknowable code presence or absence, undetectable dual-file rollback detection, or physical erasure.
  Explain that candidate consistency does not prove recency or completeness;
  external recovery is unavailable in this release: independently verifiable
  authoritative evidence would be necessary, but no verifier is specified.
  Never promise a usable restore procedure or an operator override.
- [ ] T064 [P] Add an implementation-stage user-facing reno note in `releasenotes/notes/resolve-keymaster-entities.yaml` covering upgrade, remediation and downgrade limitations; warn without exposing stored content that HA's generic repair confirmation, an HA auto-created default or fresh v1 registry written by v4.0.3 after corruption quarantine is not restored safety state, and direct operators to preserve current registry, marker and quarantine evidence. Explain that external recovery is unavailable and no supported restore or override exists in this release when history cannot be verified. Validate with `uv run reno lint`.
  State that persistent status reports the problem; a markerless v1 backup
  alone cannot prove completeness, there is no supported external restore or
  override in this release,
  and issuance and release remain blocked, potentially indefinitely even for
  legacy installations that never activated v2; a different recovery path
  needs separate policy approval.
  Explain that matching backup bytes are not proof of recency or completeness,
  and no historical-backup verification mechanism is specified.
- [ ] T065 Audit and update hardcoded constructed-ID expectations and mocks across `tests/unit/test_state_handlers.py`, `tests/unit/test_keymaster_services.py`, `tests/unit/test_startup_readability.py`, `tests/unit/test_config_flow.py`, `tests/unit/test_config_flow_schemas.py`, `tests/unit/test_config_flow_validation.py`, `tests/integration/test_full_setup.py`, `tests/integration/test_entity_id_rename.py` and other affected `tests/` regression suites; replace routing assumptions with real registry-backed identities while retaining distractor and legacy selector coverage. Run the existing suites, not only new tests.
- [ ] T066 Add benchmark/regression coverage in `tests/integration/test_verified_identity_performance.py` for indexed lookup, coalesced registry storms, slow fsync, bounded tombstone batches, concurrent entries, event-loop responsiveness and refresh/code-generation cycle; compare against existing behavior without inventing a new policy threshold.
- [ ] T067 Run the complete release, topology, persistence and restart matrix in `specs/025-resolve-keymaster-entities/quickstart.md` using T002's verified real packaged KM v0.6.1/v0.6.5, actual RC v4.0.3 loader and simulated provider in the supported HA harness; validate real entity registration, publication and release event/operation contracts, audit each live inventory path and record any unreproduced issue as such. CI must exercise these tests rather than silently skip unavailable upstream artifacts.
  Release validation gate: the complete supported-floor v0.6.1 and v0.6.5
  packaged runtime matrix must pass before delivery or claiming full
  compatibility. The authentic import and the v0.6.0 negative regression in
  T002 establish neither runtime behavior nor a substitute for this gate.
- [ ] T068 Run full pytest and 95% coverage, ruff, mypy, reno lint, REUSE and `pre-commit run --all-files` using commands in `specs/025-resolve-keymaster-entities/quickstart.md`; resolve failures without bypassing hooks.
- [ ] T069 Request the manager's independent rubber-duck review, fix findings and obtain re-review of each coherent stage and final integrated diff in `specs/025-resolve-keymaster-entities/tasks.md` before any subsequent commit or PR.

## Dependencies and execution order

```text
Phase 1 -> T006–T017 -> T018 -> T019–T027 -> T028
                                      |
                         +------------+------------+
                         v                         v
                  US1 core (T029–T034)      US2 tests (T036–T037)
                         |                         |
                         +-----------> US2 implementation (T038–T040)
                         |                         |
                         +------------+------------+
                                      v
                      US3 (T041–T047) and US5 (T048–T054)
                                      |
                                      v
                              US4 (T055–T060)
                                      |
                                      v
                 activation tests T061 -> integrated activation T062
                                      |
                                      v
                         cross-cutting T063–T069
```

T002, T003 and T004 have distinct output files; T005 follows T003. T007–T011
may be authored in parallel after T002–T005 and the characterization in T006;
T012–T017 are ordered as listed where they share the store or allocator.
T019–T022 are independent test files after storage foundation; T023–T027
follow their tests and storage checkpoint, with T025 preceding T026–T027.
US1 tests precede its changes; US2 lifecycle tests can start after T028 but
its integrated feedback depends on US1 routing. US3 and US5 can write tests
in parallel after T028, but serialize changes to shared files and integrate
after US1/US2. US4 tests may start after T028; implement privacy/version
after operation paths stabilize; serialize T026 and T059 on translations.
T018 runs storage/fake-consumer tests only; real operation assertions from
T011 require T033/T051 and pass during T062. T035 is an isolated US1
verification, not a shipment point; its version-warning assertion runs at
T060. T061 specifies the activation tests before T062 implements activation
and makes them pass. T062 is the only activation point after all prior
checkpoints and consumer wiring; no earlier stage may ship with v2 active or
with partially converted mutation routes. T063/T064 are parallel
after T062; T065 follows earlier test-file changes, T066 may follow T062 in
its own file, and T067–T069 follow all applicable implementation and
verification. These real safety dependencies supersede the generic
template's claim that all stories can ship independently.

## Parallel examples

- **US1**: After T028, T029, T030 and T031 use separate test files; T032–T034 follow their tests and coordinate shared interfaces.
- **US2**: After T028, T036 and T037 use separate files; T038–T039 follow US1 core and their tests.
- **US3**: After T028, T041, T042 and T043 use separate files; T044–T046 follow US1/US2 and the corresponding tests.
- **US5**: After T028, T048, T049 and T050 use separate files; T051–T053 follow US1/US2 and their tests, coordinating with US3 on shared files.
- **US4**: After T028, T055 and T056 use separate files; T057–T059 follow stabilized operation paths and their tests.

## Implementation strategy

MVP validation is US1 with the complete Phase 2 protection, lifecycle
integration from US2 and the clear/release safety integration from US5;
US1 alone is not safe to ship. Story checks use an isolated test-only
configuration before activation. Deliver only coherent dormant slices until
the full integrated activation at T062, then execute cross-story verification.
Stop for maintainer direction if durability, attribution or existing safety
behavior cannot be demonstrated; do not silently introduce a policy or alter
the merged specification.
