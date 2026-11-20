<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Resolve Keymaster Entities by Verified Identity

**Input**: [spec](spec.md), [plan](plan.md), [research](research.md),
[data model](data-model.md), [contract](contracts/integration.md),
[routing inventory](routing-inventory.md), and [quickstart](quickstart.md).

**Corrected design baseline**: The documentation correction merged in #957
at `d902550216ebeb26c9150c85ec4a2faaf39f715a`. Legacy-unknown provenance may
be attested to a recreated KM entry on the same original physical lock; this
does not transfer known-A work to B or turn attestation into clear/release
evidence. Protective-unknown permits lockless setup and calendar displays
but withholds new code issuance. This draft awaits the manager's independent
rubber-duck review; do not implement, commit or open a PR during this stage.

**Execution**: These are implementation-stage tasks, not authorization to
implement during the TASKS stage. Tests are required by the specification.
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

## Phase 1: Setup and characterization

- [ ] T001 Verify Python 3.14, HA 2026.11 and project test dependencies against `pyproject.toml` and `uv.lock`; prepare the existing test environment using `specs/025-resolve-keymaster-entities/quickstart.md`.
- [ ] T002 [P] Build deterministic upstream harness acquisition in `.github/workflows/build-test.yaml`, `tests/fixtures/upstream_artifacts.json` and `tests/fixtures/keymaster_releases.py`: pin packaged KM v0.6.0/v0.6.5 release asset URLs and verified SHA-256 digests, and RC v4.0.3 commit `ad8a04ee385534c84a48c0ac294b056e470e1188`; verify provenance and hashes before use, acquire into an isolated temporary CI directory before pytest, and hard-fail on absent/mismatched artifacts rather than skip. Allow the same verified artifacts to be supplied locally without pytest network access. Load actual release packages and the actual old RC loader in isolated HA test processes with a fake provider; provide synthetic original/recreated KM entries on the same physical lock and unrelated physical locks for attestation tests. Use the repository's pinned checkout/locked `uv` CI conventions, never production locks. Verify upstream licenses and preserve required notices if redistributing anything; do not vendor third-party source without license review. Distinguish tagged-source `v0.0.0` from packaged manifests and validate release properties against HA slugification.
- [ ] T003 [P] Re-scan callers and document all constructed IDs, reads, services, confirmations, listeners, owner/alias equality, pending fences, exclusions and persisted formats in `specs/025-resolve-keymaster-entities/routing-inventory.md`; classify each as routing/evidence, legacy selector or display, including new call sites.
- [ ] T004 [P] Characterize existing owner, adoption-conflict, forced-reissue, observed-PIN and redundant-owner release rules in `tests/unit/test_allocator_release_guard.py` and initial cross-entry versus post-adoption entry-wide holds in `tests/integration/test_allocator_failclosed.py`.
- [ ] T005 Inspect HA's actual storage, config-entry and lifecycle APIs and allocator/override/reissue lock acquisition; record writer quiescence, existing delayed writes and lock-order constraints in `specs/025-resolve-keymaster-entities/routing-inventory.md` before designing migration.

## Phase 2: Foundational safety and identity boundaries

**Purpose**: Complete and verify protective storage before committing
associations or attestations. Keep new behavior dormant until the coherent
activation checkpoint; do not ship routing without its lifecycle and safety
fences. Tests below require actual HA disk and fresh runtime instances where
specified.

### Storage tests and implementation

- [ ] T006 [P] Add HA storage characterization tests in `tests/unit/test_allocator_store.py` for swallowed Store serialization/write failures, read-only and stopping modes, delayed saves and same-instance pending-data loads; none is a durability acknowledgement.
- [ ] T007 [P] Add v2 schema and exact-byte tests in `tests/unit/test_registry_envelope.py` for required typed ledger, associations, attestations, fences, holds, tombstones and pending-config sections, unsupported versions, both `prepare_save_json` modes, non-ASCII bytes, private writes and sanitized failures.
- [ ] T008 [P] Add marker transition and crash/restart tests in `tests/integration/test_registry_transactions.py` using actual disk bytes and fresh HA instances: genesis-absent, quiescent v1, successive v2, prior/target digests, every pending-marker/envelope/final-marker boundary, invalid predecessor/IDs/generations, and a prior dispatched A fence retained during rollback of a later intent.
- [ ] T009 [P] Add protective-unknown tests in `tests/integration/test_registry_protective_unknown.py` for corrupt, missing, unreadable and unsupported files/sections, marker mismatches, partial history, `.corrupt.*` quarantine, suspected restore, cache loss and valid first-run/legacy absence; assert no rebuild, overwrite, reset, issuance, reuse or release despite exclusion or empty B, and preserve loaded claims, fences and repair evidence. Confirm HA's generic corruption-repair issue without restoring anything; assert confirmation, an HA auto-created default or fresh v1 registry written by v4.0.3 is not restored safety state, and with the surviving marker v1 remains protective-unknown. Require verified sufficiently current complete protective state and matching marker, not automatic recovery. Verify persistent sanitized actionable restore status without claiming codes lost from damaged state are visible, present, absent or reusable. Exercise actual new-version lockless setup/reload, calendar display and check-in with typed load failures and protective-unknown, including allocator initialization before setup; neither may raise and block non-access-mutating behavior or cause KM lookup or warning. Assert new lockless code issuance is withheld until verified restoration and existing gates pass; no replacement code is invented.
- [ ] T010 [P] Add actual HA/v4.0.3 loader tests in `tests/integration/test_registry_downgrade.py`: a JSON-decoded object with integer top-level `version: 2` rejects before payload inspection or allocator use, including missing/malformed inner payload and lockless setup; compatible newer RC reads an intact v2 with matching marker. Separately test corrupt JSON quarantined and treated absent by old code without marker protection, and HA auto-created default or fresh v1 registry written by v4.0.3 after quarantine; with surviving marker neither is restored safety state. Reinstallation or HA's generic repair confirmation alone cannot restore or validate missing state. Contrast payload-only bump and dropped extra v1 fields; never assert automatic safety recovery.
- [ ] T011 [P] Add concurrency, cancellation, allocation/reissue and release-tombstone tests in `tests/integration/test_registry_concurrency.py` for two RC entries, stale delayed writers, slow/failing executor writes, shutdown, no lock inversion, no abandoned write, bounded coalescing, post-replace failure and later valid owners. Inject failures and crashes before/after pending marker, envelope and final marker and before/during/after dependent set-code, name/PIN/date writes and reuse; no dependent side effect occurs without its own acknowledged protective owner/allocation or reissue transaction, and restart retains earlier owners/holds without inferring hardware dispatch.
- [ ] T012 Implement validated complete v1 ledger preservation and explicit typed v2 envelope/load outcomes in `custom_components/rental_control/allocator/store.py` and `custom_components/rental_control/allocator/models.py`; distinguish true absence, valid v1/v2 and protective-unknown without moving or deleting corrupt bytes. Provide persistent sanitized status directing verified sufficiently current complete-envelope and matching-marker restoration, without claims about unknowable codes. In the new version, typed protective-unknown/load failure must not raise through shared allocator initialization and block lockless entry setup or non-access-mutating calendar/check-in behavior: either bypass Keymaster allocator initialization for lockless entries with an independently enforced issuance gate or preserve a non-raising safe protective-unknown state. Keep shared issuance, reuse and release blocked while protection is unknown.
- [ ] T013 Implement private activation-marker and exact-byte transaction writer in `custom_components/rental_control/allocator/store.py`: serialize once with HA `prepare_save_json`, hash the bytes actually written, pass `(path, json_data, True, mode)` to `write_utf8_file_atomic` via the executor, and acknowledge pending marker, envelope and final marker in that order.
- [ ] T014 Implement idempotent marker recovery and v1 genesis migration in `custom_components/rental_control/allocator/store.py`: prove old-writer quiescence and disk/live snapshot agreement, roll back only an exact proven predecessor, finalize only an exact validated target, otherwise enter protective-unknown; never infer or dispatch hardware work from restored bookkeeping.
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

- [ ] T055 [P] [US4] Add loaded-metadata/comparator tests in `tests/unit/test_keymaster_version.py` for v0.6.0/v0.6.5, both unsuffixed zero forms, `v0.0.0-dev`, `v0.6.0-rc.1`, `v0.6.1rc1`, `v0.6.0+local`, missing/unparseable metadata, `7`, `2026.11`, `20260101`, `0.6`, `0.0`, `v.0.0.71`, older release and `IntegrationNotLoaded`; for unknown and below-floor cases test safe and independently blocked operations.
- [ ] T056 [P] [US4] Add privacy tests in `tests/integration/test_verified_identity_privacy.py` capturing normal/debug logs, notifications and diagnostics with synthetic PINs in state, unexpected event fields, service failures and echoed exception text, including an exception raised by the greedy cleanup/eviction path; require useful sanitized entry/slot/capability/remedy and redacted versus unavailable distinction.
- [ ] T057 [US4] Implement loaded-only metadata version diagnosis using HA's actual comparator in `custom_components/rental_control/keymaster_identity.py` and replace the guessed-reset version check in `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`: exact zero exception, three-component release shape, `<` then `>=`, otherwise unknown; version alone neither blocks nor authorizes.
- [ ] T058 [US4] Sanitize state, event, service, exception and calendar paths in `custom_components/rental_control/state_handlers.py`, `custom_components/rental_control/keymaster_services.py`, `custom_components/rental_control/keymaster_service_helpers.py`, `custom_components/rental_control/startup_readability.py`, `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`, `custom_components/rental_control/coordinator_helpers/calendar_parsing.py`, `custom_components/rental_control/calendar.py` and `custom_components/rental_control/event_overrides_helpers/shell_cleanup.py`; do not log exception tracebacks or text that may echo PINs.
- [ ] T059 [US4] Provide allowlisted diagnostics and actionable wait/enable/reselect/repair statuses in `custom_components/rental_control/diagnostics.py`, `custom_components/rental_control/coordinator_helpers/diagnostics.py`, `custom_components/rental_control/strings.json` and `custom_components/rental_control/translations/en.json`; keep association, capability and version statuses distinct. For protective-unknown, persist sanitized guidance to restore a verified sufficiently current complete envelope and matching marker; do not expose PINs, storage contents or raw exceptions or claim unknowable codes are present, absent, visible or reusable.
- [ ] T060 [US4] Verify `tests/unit/test_keymaster_version.py`, `tests/integration/test_verified_identity_privacy.py` and `tests/unit/test_diagnostics.py` with both safe and unsafe capability cases; run the false-version-warning lifecycle assertion deferred by T035 in `tests/integration/test_verified_slot_lifecycle.py`, and confirm missing/disabled/unavailable/ambiguous reset never implies an incompatible version.

## Phase 8: Integrated activation and cross-cutting verification

- [ ] T061 Add upgrade/activation tests in `tests/integration/test_registry_activation.py` using actual on-disk v1, old delayed writers, active owners and pending-clear work: simulate each missing protective consumer, crash/restart at every marker/envelope boundary and attempt issuance, reissue, clear and reuse. No v2 activation or dependent hardware/credential action is possible until all consumer wiring and safety gates are present; completed activation preserves the ledger and fences without replaying hardware, and both RC entries recover safely.
- [ ] T062 After T018, T028, T035, T040, T047, T054 and T060 pass their stated checkpoints and T061's activation tests are written, activate v2 and all protective consumers together in `custom_components/rental_control/allocator/singleton.py`, `custom_components/rental_control/allocator/allocator.py`, `custom_components/rental_control/allocator/issuance.py`, `custom_components/rental_control/event_overrides_helpers/shell_apply.py`, `custom_components/rental_control/event_overrides_helpers/apply_clear.py` and `custom_components/rental_control/__init__.py`; run T061 and the deferred T011 operation assertions to passing as part of this task. Quiesce old v1 writers, validate disk/live state, install/finalize v2 with the marker protocol, and publish no association, attestation, release or mutation until its own protective transaction is acknowledged. Ensure a typed protective-unknown/load failure cannot raise and block new-version lockless setup or non-access-mutating calendar/check-in behavior, using T012's bypass or non-raising protective-unknown state; maintain the issuance/reuse/release gate while unknown. Prove this with T009's actual setup/reload tests, separately from v4.0.3's version-header downgrade block. Ship this as one coherent reviewed change, never as an independently deployed intermediate phase.
- [ ] T063 [P] Document operator repair, original-physical-lock attestation for legacy-unknown provenance including recreated entries, known-A non-transfer, retained holds, v4.0.3 top-level version-2 downgrade guard independent of payload validity, lockless display versus withheld new issuance during protective-unknown, corrupt downgrade caveat and verified sufficiently current restore procedure in `README.md`. Warn that HA's generic corruption-repair confirmation, an HA auto-created default or fresh v1 registry written by v4.0.3 after quarantine is not restored safety state: preserve the evidence, stop RC/HA, reinstall the compatible version and restore a verified sufficiently current complete envelope and matching marker via the documented repair procedure; reinstallation/confirmation alone is not recovery. Do not expose stored content or claim auto recovery, unknowable code presence or absence, undetectable dual-file rollback detection, or physical erasure.
- [ ] T064 [P] Add an implementation-stage user-facing reno note in `releasenotes/notes/resolve-keymaster-entities.yaml` covering upgrade, remediation and downgrade limitations; warn without exposing stored content that HA's generic repair confirmation, an HA auto-created default or fresh v1 registry written by v4.0.3 after corruption quarantine is not restored safety state, and direct operators to the documented verified sufficiently current complete envelope and matching-marker repair rather than auto recovery. Validate with `uv run reno lint`.
- [ ] T065 Audit and update hardcoded constructed-ID expectations and mocks across `tests/unit/test_state_handlers.py`, `tests/unit/test_keymaster_services.py`, `tests/unit/test_startup_readability.py`, `tests/unit/test_config_flow.py`, `tests/unit/test_config_flow_schemas.py`, `tests/unit/test_config_flow_validation.py`, `tests/integration/test_full_setup.py`, `tests/integration/test_entity_id_rename.py` and other affected `tests/` regression suites; replace routing assumptions with real registry-backed identities while retaining distractor and legacy selector coverage. Run the existing suites, not only new tests.
- [ ] T066 Add benchmark/regression coverage in `tests/integration/test_verified_identity_performance.py` for indexed lookup, coalesced registry storms, slow fsync, bounded tombstone batches, concurrent entries, event-loop responsiveness and refresh/code-generation cycle; compare against existing behavior without inventing a new policy threshold.
- [ ] T067 Run the complete release, topology, persistence and restart matrix in `specs/025-resolve-keymaster-entities/quickstart.md` using T002's verified real packaged KM v0.6.0/v0.6.5, actual RC v4.0.3 loader and simulated provider in the supported HA harness; validate real entity registration, publication and release event/operation contracts, audit each live inventory path and record any unreproduced issue as such. CI must exercise these tests rather than silently skip unavailable upstream artifacts.
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
